# 迁移计划 v2（严格遵守 proj1 架构）

> 角色：高级后端架构师。第一步：**不写代码**，只分析 + 出计划。
> 主项目 proj1 = `app/`；参考项目 proj2 = `reference/claude-ai-debug-mcp/ai-debug-mcp/`（只读）。
> 核心原则：**不改变 proj1 核心架构，不推倒重写，不直接复制 proj2，按 proj1 架构重新实现。**

---

## ✅ 迁移完成总结（M1–M10，2026-07-08）

**已迁移好用特性（按 proj1 架构重新实现，70 单测全绿）**：redaction、trace_repo（复用 TraceStorage）、network/ui_event 采集、git 归因（白名单+超时）、silent_failure 采集、跨语言 ingest_error、inbound 网络中间件、build_debug_context（注入 code/git/network/ui/runtime/related_specs）、规范驱动采集+注入、指纹去重+occurrence_count 聚合、双传输 13 工具注册。

**严格保留 proj1 既有（未动）**：TraceStorage/SessionStorage 抽象、MemoryStore/PGStore、middleware.py 安全栈、error_handlers、metrics/health、protocol/transports、测试结构。

**评估后不搬（不好用/不适用）**：
- proj2 的 tenacity 重试 / `AnalyzerUnavailableError` —— proj1 已有等效重试+fallback，引入为多余依赖。
- 浏览器 SDK TS 文件 —— 前端制品，proj1 是后端服务；后端 `/ingest/*` 已就绪，前端要用再单独引。
- Playwright 自动遍历（FR14）—— 前端自动化，未建。
- FR15 的 `verify` 自动断言 / `specs` 存储 CRUD —— 待后续（采集+注入已就绪）。

**真实功能缺口已补**：M10 指纹去重（此前漏掉，现已补齐）。

---

## 0. 硬约束（不可违反）

**必须保留不动**：TraceStorage/SessionStorage 抽象层、MemoryStore/PGStore、middleware.py 安全中间件体系、error_handlers.py、metrics/health、测试结构。
**绝不**：用 proj2 的 SQLite `logs.py` 替换 proj1 存储；删除已有功能；降低安全性。

---

## 1. proj1 当前目录结构（关键部分）

```
app/
├── main.py                  # FastAPI 入口 + lifespan + /health /debug
├── mcp_server.py            # stdio MCP 入口（6 工具）
├── config.py                # pydantic-settings 统一配置
├── middleware.py            # ★安全中间件栈（不动）
├── error_handlers.py        # ★全局异常处理（不动）
├── observability.py         # ★metrics/health（不动）
├── api/
│   ├── debug.py             # /api/debug/* REST
│   ├── mcp_routes.py        # /mcp Streamable HTTP（不动）
│   └── auth.py              # API Key 校验
├── llm/analyzer.py
├── schemas/
│   ├── context.py           # DebugContext/CodeSnippet/RuntimeSnapshot
│   ├── trace.py
│   └── __init__.py
└── mcp/
    ├── core/
    │   ├── logs.py          # add_log/get_logs（基于 TraceStorage）
    │   ├── session.py       # 会话管理（不动）
    │   ├── errors.py        # 近期异常存储（上轮 FR11，保留）
    │   └── storage/
    │       ├── base.py      # ★TraceStorage/SessionStorage ABC（不动）
    │       ├── memory_store.py  # ★MemoryTraceStore/MemorySessionStore（不动）
    │       ├── pg_store.py      # ★PGTraceStore/PGSessionStore（不动）
    │       └── factory.py       # ★get_trace_store/get_session_store（不动）
    ├── builders/context.py  # build_context
    ├── collectors/
    │   ├── stacktrace.py
    │   ├── code_locator.py  # FR11 已接线
    │   └── runtime.py
    ├── hooks/exception_hook.py
    ├── protocol/{server.py,jsonrpc.py}   # ★JSON-RPC 分发（不动）
    ├── transports/{session.py,sse.py}    # ★（不动）
    └── tools/
        ├── __init__.py      # register_all_tools（HTTP 侧注册）
        ├── debug_api.py / context_api.py / trace_api.py / stacktrace_api.py
```

---

## 2. proj2 对应功能文件（待迁移，只读参考）

| 功能 | proj2 文件 | 价值 |
| --- | --- | --- |
| 脱敏 | `app/mcp/core/redaction.py` | 入库前统一脱敏 |
| 网络采集 | `app/mcp/collectors/network.py` | 解析+截断+脱敏 NetworkRecord |
| UI 事件采集 | `app/mcp/collectors/ui_event.py` | 解析+脱敏 UIEvent |
| Git 归因 | `app/mcp/core/git.py` | blame/recent diff（超时+失败返回 None） |
| 静默失败 | `app/mcp/tools/silent_failure_api.py` | 编排 ui+network+trace |
| 网络工具 | `app/mcp/tools/network_api.py` | ingest_network/get_network_trace |
| 跨语言上报 | `app/mcp/tools/ingest_api.py` | ingest_error |
| ingest 路由 | `app/api/ingest.py` | /ingest/* |
| Schema | `app/schemas/context.py` | NetworkRecord/UIEvent/GitBlameInfo/GitDiffInfo |
| context 注入 | `app/mcp/builders/context.py` | 注入 git/network/ui |
| 浏览器 SDK | `sdk/js/ai-debug-sdk.ts` | 前端静默失败采集（独立，可选后续） |

> proj2 用 SQLite `logs.py`（save_trace/get_trace/独立表）——**proj1 不采用**，改在现有 TraceStorage 之上重新实现等价 API。

---

## 3. 架构对策（关键设计）

**network / ui_event 复用现有存储，零改动后端**：
- 把网络记录存为 `add_log(trace_id, "network", record_dict)`，UI 事件存为 `add_log(trace_id, "ui_event", event_dict)`。
- `get_network_records(trace_id)` = `[e["data"] for e in get_entries(trace_id) if e["step"]=="network"]`；`get_ui_events` 同理。
- Memory 与 PG 后端天然支持（都是 request_id+step+data），**无需改 base/memory/pg/factory**。

**新建 `app/mcp/core/trace_repo.py`**：在 `TraceStorage` + `errors.py` 之上重新实现 proj2 的 `save_trace/get_trace/save_network_record/get_network_records/save_ui_event/get_ui_events`，给上层工具/采集器一个统一入口。`save_trace` 复用 `errors.record()`（异常帧+trace_kind）。

**脱敏**：新建 `app/mcp/core/redaction.py`（按 proj1 风格重写，非复制），在采集器/trace_repo 入库前统一调用。

**Git 安全**：重写 `app/mcp/core/git.py`，加 `git_path_whitelist` 白名单 + 超时，防任意路径执行。

---

## 4. 文件分类

### 4.1 新增（NEW）
| 文件 | 作用 |
| --- | --- |
| `app/mcp/core/redaction.py` | 脱敏模块 |
| `app/mcp/core/trace_repo.py` | 在 TraceStorage 之上实现 save_trace/get_trace/save_network_record/save_ui_event 等 |
| `app/mcp/collectors/network.py` | NetworkRecord 解析+截断+脱敏 |
| `app/mcp/collectors/ui_event.py` | UIEvent 解析+脱敏 |
| `app/mcp/core/git.py` | git blame / recent diff（带白名单+超时） |
| `app/mcp/tools/network_api.py` | ingest_network / get_network_trace 工具 |
| `app/mcp/tools/git_api.py` | get_blame_for_frame / get_recent_diff 工具 |
| `app/mcp/tools/silent_failure_api.py` | ingest_silent_failure 工具 |
| `app/mcp/tools/ingest_api.py` | ingest_error 工具 |
| `app/api/ingest.py` | /ingest/error /ingest/silent-failure /ingest/network 路由 |
| `app/middleware_network.py` | inbound 请求采集中间件（独立，不动 middleware.py） |
| `tests/unit/test_redaction.py` 等 | 各模块单测 |

### 4.2 修改（MODIFY，仅增量，不改已有逻辑）
| 文件 | 改动 |
| --- | --- |
| `app/config.py` | 新增 `redaction_enabled`/`redaction_extra_patterns`/`git_timeout`/`git_path_whitelist` |
| `app/schemas/context.py` | 新增 NetworkRecord/UIEvent/GitBlameInfo/GitDiffInfo；DebugContext 增 `network_trace`/`ui_events`/`git_blame`/`recent_diffs` 字段 |
| `app/schemas/__init__.py` | 导出新模型 |
| `app/mcp/builders/context.py` | 增量：构建时可选注入 git/network/ui（保留现有 `build_context`） |
| `app/mcp/tools/__init__.py` | 注册新工具（HTTP 侧） |
| `app/mcp_server.py` | 注册新工具（stdio 侧） |
| `app/main.py` | include ingest 路由 + 挂载 network 中间件 |

### 4.3 绝不动（DO NOT TOUCH）
- `app/mcp/core/storage/{base,memory_store,pg_store,factory}.py`
- `app/mcp/core/session.py`、`app/mcp/core/logs.py`、`app/mcp/core/errors.py`
- `app/middleware.py`、`app/error_handlers.py`、`app/observability.py`
- `app/mcp/protocol/*`、`app/mcp/transports/*`、`app/api/mcp_routes.py`
- 现有 `app/mcp/tools/{debug,context,trace,stacktrace}_api.py` 的已有逻辑（仅 `__init__.py` 注册增量）
- 现有 `tests/`（不删，只加新）

---

## 5. 模块顺序（每模块少量文件，含验证）

| # | 模块 | 文件 | 验证 |
| --- | --- | --- | --- |
| **M1** | 脱敏基础 | `core/redaction.py` + `config.py`(增键) + `schemas/context.py`(增模型) + `schemas/__init__.py` | 单测：密码/token/手机号被掩码；开关关闭时原样 |
| **M2** | trace_repo | `core/trace_repo.py`（save_trace/get_trace/save_network_record/get_network_records/save_ui_event/get_ui_events） | 单测：存取一致；network/ui 按 trace_id 过滤 |
| **M3** | network 采集 | `collectors/network.py` + `tools/network_api.py` + `api/ingest.py`(骨架) | 单测：截断 10KB、脱敏 url/body |
| **M4** | ui_event 采集 | `collectors/ui_event.py` +（并入 silent_failure） | 单测：解析+脱敏 payload |
| **M5** | git 归因 | `core/git.py` + `tools/git_api.py` + `config.py`(git_*) | 单测：非白名单路径拒绝；超时返回 None |
| **M6** | silent_failure | `tools/silent_failure_api.py` + `api/ingest.py`(/ingest/silent-failure) + `middleware_network.py` + `main.py` | 集成：上报后 get_debug_context 含 ui/network |
| **M7** | ingest_error | `tools/ingest_api.py` + `api/ingest.py`(/ingest/error) | 单测：跨语言帧落库 |
| **M8** | context 注入 + 双传输注册 + 文档 | `builders/context.py` + `tools/__init__.py` + `mcp_server.py` + `tests/` + PRD/DESIGN | 工具两侧可调；context 含 git/network/ui |

---

## 6. 安全与质量保障

- ingest 路由复用 proj1 `verify_api_key`（不绕过鉴权）。
- git 命令加超时 + 路径白名单，防任意文件探测。
- 脱敏在入库前统一执行（network body/url、ui payload、trace message）。
- 所有新模块失败降级，不阻断主调试流程（沿用 proj1 风格）。
- 不动安全中间件栈、异常处理、metrics。

---

## 7. 每模块交付格式（实施时遵循）

- **修改了什么**：文件清单 + 关键改动。
- **为什么这样设计**：架构决策（为何复用 TraceStorage、为何独立中间件等）。
- **如何测试**：具体命令/用例。
