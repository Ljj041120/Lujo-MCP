# ai-debug-mcp 产品需求文档

## 1. 产品概述

ai-debug-mcp 是一款面向 AI 编程助手（Trae / Codex / Claude Desktop 等）的 **调试上下文自动组装引擎**。它解决的核心痛点是：

> 开发者遇到 bug 时，80% 的时间花在"找文件、查日志、写提示词、核对规范"上，而不是真正思考和修复。

产品定位不是替代 AI 推理，而是做 AI 的**调试副驾驶**：在错误发生的瞬间，自动把异常、源码、运行状态、Git 变更、项目规范、前端行为、网络请求等上下文一次性打包好，并以符合项目规范的方式交给 AI，让 AI 直接给出可落地的修复建议。

## 2. 目标用户

- 使用 AI 助手写前后端代码，但厌烦反复复制粘贴上下文、手动找文件的开发者。
- 需要维护 UI 交互质量，但经常被"点了没反应、没有报错"这类静默失败困扰的前端开发者。
- 推行"规范驱动开发"，希望 AI 输出自动符合项目约定的技术负责人。
- 多技术栈团队（Python / Node / Go / Java / Rust），希望统一错误观测入口。

## 3. 核心问题

### 3.1 查错流程繁琐
当前遇到代码报错，典型路径是：
1. 看终端报错 / 浏览器控制台
2. 根据堆栈找文件、找行号
3. 把错误信息、相关代码、期望行为手动组织成 prompt
4. 丢给 AI
5. AI 可能缺少上下文，反复追问

**结果**：大量时间花在"信息搬运"，而不是"解决问题"。

### 3.2 规范驱动开发难落地
团队有代码规范、API 规范、UI 规范，但：
- AI 不知道这些规范，生成的修复可能风格不符、命名不对、越界修改。
- 用户每次都要在 prompt 里重复"请按我们项目的 REST 规范写"、"请参考 components/README.md"。

### 3.3 前端静默失败难以定位
很多前端问题不会抛出异常：
- 点击按钮没反应，控制台无报错
- AI 检查代码说"语法没问题、API 调用正常"
- 实际是事件未绑定、条件分支遗漏、响应式未触发、权限/状态判断错误

**现有工具**：只能捕获显式异常，无法记录"用户期望发生但实际未发生"的行为。

### 3.4 信息噪音与安全
- 同一个 bug 反复触发会刷屏，浪费 AI 上下文窗口。
- 异常消息里可能携带密码、token、手机号，直接进 LLM 有泄露风险。

## 4. 产品目标

| 目标 | 说明 |
|---|---|
| **一键出上下文** | 错误发生时，AI 自动拿到定位问题所需的全部信息，无需用户手动搬运。 |
| **规范即提示** | 项目规范自动注入 AI prompt，修复建议天然符合团队约定。 |
| **看得见静默失败** | 前端点击、路由跳转、网络请求等行为可被记录和回放，帮助定位"无报错但有问题"。 |
| **去重降噪** | 同一个错误多次触发只保留聚合记录，避免刷屏。 |
| **安全合规** | 敏感信息自动脱敏后再进入 LLM 上下文。 |
| **多语言接入** | 任何语言/进程都能通过统一接口上报错误和行为事件。 |

## 5. 功能需求

### 5.1 异常自动采集（已实现）

- Python 全局异常钩子（`sys.excepthook` + `asyncio` exception handler）。
- FastAPI / 后端请求链路中间件兜底捕获。
- 任意语言通过 `ingest_error` MCP 工具或 HTTP 接口主动上报。
- 采集内容：异常类型、消息、完整堆栈、源码片段、运行时快照、Git 变更。

### 5.2 错误去重 / 聚合（已实现）

- 基于 `异常类型 + 前 3 帧 file:function` 计算 fingerprint。
- 相同 fingerprint 复用同一个 `trace_id`，累加 `occurrence_count`。
- `list_recent_traces` 返回聚合摘要。

### 5.3 敏感信息脱敏（已实现）

- 对 `message`、`frame.code_context`、`code_snippet.snippet` 入库和返回前脱敏。
- 覆盖 password / token / api_key / secret / authorization bearer / 手机号等模式。

### 5.4 Git 集成（已实现）

- `get_blame_for_frame`：报错行最后是谁、哪次 commit 改的。
- `get_recent_diff`：报错文件最近 N 次 commit 的 diff。
- `get_debug_context` 自动附带前 3 帧的 blame 和 diff。

### 5.5 规范驱动提示（Spec-Driven Prompting）

这是降低"反复写规范"成本的关键能力。

- **规范索引**：自动识别项目中的规范文件，如 `CONVENTION.md`、`API_SPEC.md`、`components/README.md`、`.cursorrules`、`.eslintrc` 等。
- **规范关联**：根据错误所在的文件路径/技术栈，自动挑选最相关的规范片段。
- **规范注入**：在构造 `DebugContext` 时，把相关规范作为 `specs` 字段一并返回给 AI。
- **修复后校验**：未来可提供 `verify_against_spec(diff)` 工具，让 AI 自检修复是否符合规范。

**MVP 实现**：
1. 扫描项目根目录下的 `*.md` 文件，按文件名/路径关键词（如 `API`、`COMPONENT`、`STYLE`）建立索引。
2. 根据报错文件的扩展名（`.vue`、`.ts`、`.py`）匹配对应规范文件。
3. 规范片段长度限制在 2000 tokens 以内，优先取标题和最近相关段落。
4. 将规范文本作为 `related_specs` 注入 `DebugContext`。

### 5.6 前端静默失败捕获

解决"点了没反应、没有报错"的问题。

**MVP 实现**：
1. 提供一个轻量级浏览器 SDK，仅在开发/测试环境启用。
2. 监听 `click`、`submit`、`popstate` 事件，记录：
   - 元素 selector、组件名、时间戳
   - 事件触发后 2 秒内是否有路由变化、DOM 变化、网络请求
3. 如果用户标记"此操作应该产生 X 结果"但系统未检测到对应变化，则生成 `silent_failure` trace。
4. `silent_failure` trace 包含：触发元素、最近 5 条网络请求、控制台日志、当前路由、相关源码位置（通过 sourcemap 或组件名反查）。

**第一期不做的**：
- 自动推断"应该发生什么"（先由用户显式标记期望行为，降低误报）。
- 生产环境全量录制（避免隐私和性能风险）。

### 5.7 网络请求追踪

- 后端：在 FastAPI 中间件层记录请求出入参、状态码、耗时。
- 前端：通过 SDK 拦截 `fetch` / `XMLHttpRequest`，记录请求 URL、参数、响应、错误。
- 当 AI 说"API 无报错"但实际行为异常时，用户可调用 `get_network_trace_for_trace(trace_id)` 查看完整请求链。

**MVP 实现**：
1. 后端 FastAPI 中间件记录请求路径、方法、状态码、耗时、异常类型（如有）。
2. 前端 SDK 记录 `fetch` 的 URL、status、response 摘要（过长则截断）。
3. 通过 `trace_id` 或 `request_id` 将前后端请求关联。

### 5.8 调试上下文打包

`get_debug_context` 返回的结构：

```python
class DebugContext(BaseModel):
    trace: TraceEntry
    runtime: Optional[RuntimeSnapshot]
    code_snippets: list[CodeSnippet]
    git_blame: Optional[list[GitBlameInfo]]
    recent_diffs: Optional[list[GitDiffInfo]]
    related_specs: Optional[list[SpecSnippet]]   # 规范驱动提示
    network_trace: Optional[list[NetworkRecord]] # 网络请求链
    ui_events: Optional[list[UIEvent]]           # 前端行为事件
```

### 5.9 可选 LLM 分析

- `analyze_with_llm` 工具仅在宿主客户端无推理能力时使用。

## 6. 用户场景

### 场景 A：后端报错一键定位
后端抛异常 → ai-debug-mcp 自动捕获 → AI 拿到堆栈、源码、Git blame、最近 diff → AI 判断"是昨天那次 commit 改出来的"，给出修复建议。

### 场景 B：前端点击无反应
用户点击"提交"按钮没反应，控制台无报错。浏览器 SDK 记录了点击事件和随后的网络请求。系统生成 silent_failure trace。AI 看到：
- 点击了 `.submit-btn`
- 绑定的 `handleSubmit` 被调用
- 请求 `/api/order` 返回 200
- 但 Pinia store 里的 `orderList` 没有更新（因为 mutation 名字写错）

AI 直接指出："请求成功但状态更新逻辑有误，请检查 `store/orderStore.ts:42` 的 mutation 名称是否与模块定义一致。"

### 场景 C：规范自动约束
团队规定所有 API 错误必须统一返回 `{ code, message, data }`。某个新接口直接抛了 500，AI 在分析时看到 `API_SPEC.md` 的相关段落，给出的修复方案会包含"按规范包装错误响应"，而不是简单 try-catch。

### 场景 D：跨语言统一观测
Python 后端、Node.js BFF、Java 定时任务都把错误通过 `ingest_error` 上报到同一个 ai-debug-mcp。AI 在 Trae 里用 `list_recent_traces` 看到全链路错误聚合，不再需要在多个日志系统间切换。

## 7. 数据模型

### TraceEntry

```python
class TraceEntry(BaseModel):
    trace_id: str
    fingerprint: str
    first_seen: float
    last_seen: float
    occurrence_count: int = 1
    timestamp: float
    exc_type: str
    message: str
    frames: list[StackFrame]
    source: str = "unknown"      # global_hook / fastapi_middleware / ingest / browser_sdk
    extra: dict
    trace_kind: str = "exception"  # exception | silent_failure | network_error | manual
```

### StackFrame

```python
class StackFrame(BaseModel):
    file: str
    line: int
    function: str
    code_context: Optional[str]
```

### DebugContext

```python
class DebugContext(BaseModel):
    trace: TraceEntry
    runtime: Optional[RuntimeSnapshot]
    code_snippets: list[CodeSnippet]
    git_blame: Optional[list[GitBlameInfo]]
    recent_diffs: Optional[list[GitDiffInfo]]
    related_specs: Optional[list[SpecSnippet]]
    network_trace: Optional[list[NetworkRecord]]
    ui_events: Optional[list[UIEvent]]
```

## 8. MCP 工具清单

| 工具名 | 说明 |
|---|---|
| `get_stacktrace` | 获取最近一次异常堆栈。 |
| `get_debug_context` | 【核心】一次性获取完整调试上下文。 |
| `list_recent_traces` | 列出最近错误/事件摘要（已聚合）。 |
| `search_logs` | 按关键字搜索历史记录。 |
| `get_runtime_snapshot` | 获取进程运行时快照。 |
| `ingest_error` | 任意语言主动上报错误或事件。 |
| `ingest_silent_failure` | 上报前端静默失败（期望行为 vs 实际行为）。 |
| `get_blame_for_frame` | 查询指定文件/行的 Git blame。 |
| `get_recent_diff` | 获取指定文件最近 N 次 commit 的 diff。 |
| `get_related_specs` | 根据文件路径获取相关项目规范。 |
| `get_network_trace` | 获取与某条 trace 关联的网络请求链。 |
| `analyze_with_llm` | 【可选】调用内置 LLM 分析。 |

## 9. REST 接口

- `POST /mcp/tools/list`
- `POST /mcp/tools/{tool_name}`
- `POST /ingest/error`（外部服务错误上报）
- `POST /ingest/silent-failure`（前端静默失败上报）
- `POST /ingest/network`（网络请求事件上报）

## 10. 非功能需求

| 维度 | 要求 |
|---|---|
| 稳定性 | 采集流程自身不能抛异常；失败时 graceful degradation。 |
| 向后兼容 | 旧 `trace_id` 可查询；schema 自动迁移。 |
| 隐私安全 | 敏感信息在落库和返回前双重脱敏。 |
| 性能 | 单次采集/入库 < 50ms；context 构建 < 300ms。 |
| 可扩展 | 采集器、规范解析器、网络追踪器可插拔。 |
| 部署 | 单机 SQLite 默认；可选 Postgres/Redis。 |

## 11. 路线图

### Phase 1：核心调试上下文（已完成）

- [x] 异常自动采集与多语言 `ingest_error`
- [x] 错误 fingerprint 去重与聚合
- [x] 敏感信息脱敏
- [x] Git blame / recent diff 集成
- [x] SQLite WAL 并发加固

### Phase 2：规范驱动与质量（MVP）

- [ ] 项目规范文件自动索引与关联
- [ ] `related_specs` 注入 `DebugContext`
- [ ] pytest 核心链路测试
- [ ] Dockerfile 与容器化文档
- [ ] 更灵活的脱敏规则配置

### Phase 3：前端静默失败（MVP）

- [ ] 浏览器 JS SDK：点击/路由/网络/控制台采集
- [ ] `ingest_silent_failure` 工具与数据模型
- [ ] 用户显式标记期望行为，系统检测未达成时生成 trace
- [ ] UI 事件与网络 trace 注入 `DebugContext`

### Phase 4：规模化与生态

- [ ] Postgres / Redis 后端
- [ ] 与 Sentry、LogRocket、Chrome DevTools 集成
- [ ] 自动从 GitHub/GitLab PR 拉取上下文
- [ ] 修复方案自动校验规范合规性

## 12. 成功指标

- 开发者从"发现错误"到"拿到 AI 修复建议"的平均时间缩短 50% 以上。
- 同类重复错误不刷屏，AI 上下文窗口占用下降 50% 以上。
- AI 给出的修复建议中，因"不符合项目规范"被驳回的比例下降 60% 以上。
- 前端"点了没反应"类问题首次定位成功率 > 70%。
- 敏感信息零泄露。

## 13. 风险与假设

- **假设**：用户项目有基本 Git 管理，否则 Git 工具返回空。
- **假设**：前端静默失败采集需要用户主动在开发/测试环境接入 SDK，生产环境默认不开启。
- **风险**：规范文件可能过大，需做切片和长度限制。
- **风险**：脱敏规则无法 100% 覆盖，需持续迭代并提示用户不要在 `extra` 中放 secrets。
- **风险**：fingerprint 忽略行号，可能导致同一函数不同行错误被聚合；通过保留最新 message/frames 缓解。

---

## 14. 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                      MCP 客户端 (Trae/Codex)                 │
│                    调用 get_debug_context 等工具              │
└───────────────────────┬─────────────────────────────────────┘
                        │ stdio / JSON-RPC
┌───────────────────────▼─────────────────────────────────────┐
│                    app/mcp_server.py                         │
│                    注册并分发 MCP 工具                        │
└───────────────────────┬─────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│   采集层      │ │   上下文构建  │ │   存储层      │
│ collectors/  │ │ builders/    │ │ core/logs.py │
│ - stacktrace │ │ - code_locator│ │ SQLite + WAL │
│ - runtime    │ │ - git        │ │              │
│ - code_locator│ │ - redaction  │ │              │
└──────────────┘ └──────────────┘ └──────────────┘
        ▲               ▲               ▲
        │               │               │
        └───────────────┴───────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ 外部接入      │ │ 浏览器 SDK   │ │ FastAPI 面板 │
│ ingest_error │ │ (未来)       │ │ /api/debug   │
│ HTTP POST    │ │              │ │ /mcp/tools   │
└──────────────┘ └──────────────┘ └──────────────┘
```

**核心设计原则**：
- 存储层单一：所有采集器最终走 `save_trace` 落库。
- 上下文统一：所有信息最终通过 `build_debug_context` 打包。
- 失败隔离：采集器自己抛异常不能影响主程序。

## 15. MVP 边界

| 能力 | MVP 内 | MVP 外（后续） |
|---|---|---|
| 异常采集 | Python 钩子 + ingest_error HTTP | 浏览器 SDK 自动异常捕获 |
| 去重 | file:function fingerprint | 基于 AST 或语义指纹 |
| 脱敏 | 正则规则 | AI 辅助识别敏感字段 |
| Git 集成 | blame + diff | PR 关联、issue 关联 |
| 规范驱动 | 扫描 md/json 规范文件并注入 | 向量检索、规范自动校验修复 |
| 静默失败 | 用户显式标记期望行为 | AI 自动推断期望行为 |
| 网络追踪 | 后端 FastAPI 中间件 + 前端 SDK 手动上报 | 自动分布式链路追踪 |

**为什么这样切 MVP**：
- 先解决"有规范但 AI 不知道"和"手动搬运上下文"这两个最高频痛点。
- 静默失败先让用户标记期望，避免误报淹没真实问题。
- 不依赖 sourcemap 解析，先通过 selector / 组件名反查源码。

## 16. 关键决策记录（ADR）

### ADR-1：fingerprint 忽略行号
- **决策**：指纹基于 `exc_type + file:function`，不包含 line。
- **原因**：代码编辑会移动行号，若包含 line 会导致同一 bug 因日常重构产生大量重复记录。
- **代价**：同一函数内不同行的错误会被聚合。通过保留最新 message/frames 缓解。

### ADR-2：SQLite 作为默认存储
- **决策**：单机默认 SQLite，WAL 模式。
- **原因**：零配置、文件即数据、适合个人/小团队本地使用。
- **代价**：高并发多进程写入仍可能锁冲突。未来通过 Postgres 插件扩展。

### ADR-3：stdio MCP 为主入口
- **决策**：Trae/Codex 通过 `python -m app.mcp_server` 启动 stdio server。
- **原因**：MCP 协议原生支持，无需网络配置，隔离性好。
- **代价**：无法远程部署；FastAPI 面板作为辅助入口保留。

### ADR-4：敏感信息在入库前脱敏
- **决策**：`save_trace` 中先脱敏再写入数据库。
- **原因**：避免敏感数据落盘，即使 DB 文件泄露也安全。
- **补充**：返回给 AI 前再次脱敏，双重保险。

## 17. 验证计划

每个痛点都要有可量化的验证方式：

| 痛点 | 验证方法 | 通过标准 |
|---|---|---|
| 查错流程繁琐 | 记录"发现异常 → AI 给出修复建议"的步数/时间 | 步数从 5+ 降到 2 步以内 |
| 规范驱动难落地 | 让 AI 修复 10 个同类问题，人工检查是否符合规范 | 规范符合率 > 80% |
| 前端静默失败 | 构造 5 个"点了没反应"用例，测试首次定位成功率 | 成功率 > 70% |
| 重复错误刷屏 | 模拟同一异常触发 50 次，查看 `list_recent_traces` | 只返回 1 条聚合记录 |
| 敏感信息泄露 | 用含 password/token/手机号 的异常测试入库和返回 | 明文不出现 |

## 18. 待解决问题

- **规范文件关联算法**：按文件名关键词匹配的准确率如何？是否需要引入 LLM 做相关性判断？
- **静默失败判定标准**：用户未标记期望行为时，是否完全不做检测？还是只做"点击后无任何网络请求/DOM 变化"的保守检测？
- **sourcemap 支持**：前端 SDK 采集到的是编译后代码位置，如何映射回源码？是否优先支持 Vite 项目的 sourcemap？
- **性能上限**：当规范文件很大时，`build_debug_context` 如何避免超过 LLM 上下文限制？是否需要做 chunking 和摘要？
