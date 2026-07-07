# ai-debug-mcp 设计文档

## 1. 设计目标

本设计文档把 [PRD.md](./PRD.md) 中的产品需求转化为可落地的技术方案，目标读者为参与开发的工程师。文档覆盖：

- 已实现的 Phase 1 架构回顾
- Phase 2（规范驱动提示）的详细设计
- Phase 3（前端静默失败捕获）的详细设计
- 存储、接口、安全、扩展性等横切关注点

设计原则：
- **单一存储**：所有 trace 最终通过 `save_trace` 写入 SQLite，统一 schema。
- **统一上下文**：所有信息最终通过 `build_debug_context` 打包给 AI。
- **失败隔离**：任何采集器抛异常都不能影响被调试的主程序。
- **渐进增强**：Phase 2/3 作为采集器和构建器的插件加入，不改 Phase 1 核心流程。

## 2. 架构总览

```
┌────────────────────────────────────────────────────────────────┐
│                    MCP 客户端 (Trae / Codex)                    │
│                   调用 get_debug_context 等工具                 │
└────────────────────────┬───────────────────────────────────────┘
                         │ stdio / JSON-RPC
┌────────────────────────▼───────────────────────────────────────┐
│                     app/mcp_server.py                           │
│                     注册并分发 MCP 工具                          │
└────────────────────────┬───────────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│    采集层      │ │  上下文构建层  │ │    存储层      │
│  collectors/  │ │   builders/   │ │  core/logs.py │
│  - stacktrace │ │  - context    │ │  SQLite + WAL │
│  - runtime    │ │  - git        │ │               │
│  - code_locator│ │  - redaction  │ │               │
│  - spec       │ │  - spec       │ │               │
│  - network    │ │  - network    │ │               │
│  - ui_event   │ │  - ui_event   │ │               │
└───────────────┘ └───────────────┘ └───────────────┘
         ▲               ▲               ▲
         │               │               │
         └───────────────┴───────────────┘
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│   外部接入      │ │  浏览器 SDK    │ │  FastAPI 面板  │
│  ingest_error │ │  (Phase 3)     │ │  /api/debug    │
│  HTTP POST    │ │                │ │  /mcp/tools    │
└───────────────┘ └───────────────┘ └───────────────┘
```

## 3. 核心组件

### 3.1 采集层（collectors/）

负责从各种来源收集原始数据，不直接操作存储。

| 模块 | 职责 | 状态 |
|---|---|---|
| `stacktrace.py` | 把 Python 异常对象格式化为 `StackFrame` 列表 | 已实现 |
| `runtime.py` | 采集进程 CPU/内存/线程/Python 版本 | 已实现 |
| `code_locator.py` | 根据 file:line 读取附近源码 | 已实现 |
| `git.py` | 调用 `git blame` / `git diff` | 已实现 |
| `spec.py` | 扫描并解析项目规范文件 | Phase 2 |
| `network.py` | 记录前后端网络请求 | Phase 2/3 |
| `ui_event.py` | 接收浏览器 SDK 上报的 UI 事件 | Phase 3 |

### 3.2 上下文构建层（builders/）

| 模块 | 职责 | 状态 |
|---|---|---|
| `context.py` | 整合所有采集器结果，生成 `DebugContext` | 已实现，持续增强 |

### 3.3 存储层（core/logs.py）

- 统一入口：`save_trace(...)`
- 统一出口：`get_trace(...)`、`list_recent_traces(...)`、`search_logs(...)`
- 去重：`compute_fingerprint(exc_type, frames)`
- 脱敏：`redact(...)` 在 `save_trace` 中调用
- 并发：WAL 模式 + 线程锁

### 3.4 工具层（tools/）

MCP 工具函数，保持薄层，只负责参数解包和调用底层模块。

| 模块 | 职责 | 状态 |
|---|---|---|
| `stacktrace_api.py` | `get_stacktrace` 工具 | 已实现 |
| `trace_api.py` | `list_recent_traces` / `search_logs` | 已实现 |
| `context_api.py` | `get_debug_context` | 已实现 |
| `debug_api.py` | `get_runtime_snapshot` / `analyze_with_llm` | 已实现 |
| `ingest_api.py` | `ingest_error` | 已实现 |
| `git_api.py` | `get_blame_for_frame` / `get_recent_diff` | 已实现 |
| `spec_api.py` | `get_related_specs` | Phase 2 |
| `network_api.py` | `get_network_trace` | Phase 2/3 |
| `silent_failure_api.py` | `ingest_silent_failure` | Phase 3 |

## 4. 数据流

### 4.1 异常捕获流程

```
Python 异常 / ingest_error HTTP
        │
        ▼
┌─────────────────┐
│   stacktrace    │  解析 exc_type / message / frames
│   / ingest_api  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   compute_fingerprint  生成指纹
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   redact()      │  脱敏 message / frames
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   save_trace()  │  upsert SQLite
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ build_debug_context │ 按需组装完整上下文
└─────────────────┘
```

### 4.2 DebugContext 构建流程

```
get_trace(trace_id)
    │
    ├── get_runtime_snapshot()
    ├── get_snippets_for_frames()
    │       └── redact(snippet)
    ├── get_blame_for_frame() × 前 3 帧
    ├── get_recent_diff() × 前 3 帧
    ├── get_related_specs() (Phase 2)
    ├── get_network_trace() (Phase 2/3)
    └── get_ui_events()     (Phase 3)
    │
    ▼
DebugContext
```

## 5. 存储设计

### 5.1 traces 表

```sql
CREATE TABLE traces (
    trace_id TEXT PRIMARY KEY,
    fingerprint TEXT,
    first_seen REAL DEFAULT 0,
    last_seen REAL DEFAULT 0,
    occurrence_count INTEGER DEFAULT 1,
    timestamp REAL NOT NULL,
    exc_type TEXT NOT NULL,
    message TEXT NOT NULL,
    frames_json TEXT NOT NULL,
    source TEXT NOT NULL,
    extra_json TEXT NOT NULL
);
```

### 5.2 Phase 2/3 新增表

#### network_records 表

```sql
CREATE TABLE network_records (
    record_id TEXT PRIMARY KEY,
    trace_id TEXT,
    request_id TEXT,
    timestamp REAL NOT NULL,
    direction TEXT NOT NULL,      -- inbound / outbound
    method TEXT,
    url TEXT,
    status_code INTEGER,
    request_body TEXT,
    response_body TEXT,
    duration_ms REAL,
    extra_json TEXT
);
CREATE INDEX idx_network_trace ON network_records(trace_id);
CREATE INDEX idx_network_request ON network_records(request_id);
```

#### ui_events 表

```sql
CREATE TABLE ui_events (
    event_id TEXT PRIMARY KEY,
    trace_id TEXT,
    timestamp REAL NOT NULL,
    event_type TEXT NOT NULL,     -- click / submit / route_change
    target_selector TEXT,
    component_name TEXT,
    route_path TEXT,
    payload_json TEXT,
    extra_json TEXT
);
CREATE INDEX idx_ui_trace ON ui_events(trace_id);
```

#### spec_snippets 缓存表（可选）

```sql
CREATE TABLE spec_snippets (
    spec_id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    file_mtime REAL NOT NULL,
    summary TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT NOT NULL
);
CREATE INDEX idx_spec_tags ON spec_snippets(tags);
```

## 6. Phase 2：规范驱动提示设计

### 6.1 目标

让 AI 在分析错误时自动看到与报错文件相关的项目规范，减少用户手动复制规范到 prompt 的次数。

### 6.2 规范文件发现

扫描以下文件：

```python
SPEC_CANDIDATES = [
    "CONVENTION.md",
    "API_SPEC.md",
    "COMPONENT_SPEC.md",
    "STYLE_GUIDE.md",
    "README.md",
    ".cursorrules",
    "docs/**/*.md",
]
```

排除：
- `node_modules/`
- `.git/`
- 大于 1MB 的文件

### 6.3 规范标签提取

根据文件名和一级标题提取标签：

| 文件名/标题关键词 | 标签 | 匹配文件扩展名 |
|---|---|---|
| API / REST / HTTP | api | `.py`, `.ts`, `.js`, `.java`, `.go` |
| COMPONENT / UI / Vue / React | ui | `.vue`, `.tsx`, `.jsx`, `.svelte` |
| STYLE / CSS / SCSS | style | `.css`, `.scss`, `.less` |
| PYTHON / BACKEND | backend | `.py` |
| DATABASE / SQL / ORM | db | `.py`, `.sql`, `.prisma` |

### 6.4 与报错文件匹配

```python
def match_specs(error_file: str, specs: list[SpecSnippet]) -> list[SpecSnippet]:
    ext = Path(error_file).suffix
    matched = []
    for spec in specs:
        if ext in spec.target_extensions:
            matched.append(spec)
    # 限制总长度，避免超过上下文窗口
    return trim_specs(matched, max_tokens=2000)
```

### 6.5 内容切片

对每个规范文件：
1. 按二级标题（`## `）切分成 chunk。
2. 取前 3 个 chunk（通常是总览和最重要的规则）。
3. 每个 chunk 限制 800 字符。
4. 总长度限制 2000 tokens（按字符数估算，1 token ≈ 4 字符）。

### 6.6 缓存策略

- 启动时扫描一次，按 `file_mtime` 判断是否需要重新读取。
- 提供 `reload_specs()` 工具或 API，供规范文件更新后手动刷新。
- 默认缓存 5 分钟，可配置。

### 6.7 接口设计

#### MCP 工具

```json
{
  "name": "get_related_specs",
  "inputSchema": {
    "type": "object",
    "properties": {
      "file_path": {"type": "string"}
    },
    "required": ["file_path"]
  }
}
```

返回：

```json
{
  "found": true,
  "specs": [
    {
      "file": "API_SPEC.md",
      "summary": "错误响应必须返回 {code, message, data}",
      "content": "..."
    }
  ]
}
```

#### 自动注入

`build_debug_context` 中对 trace 的每个 frame 调用 `get_related_specs(frame.file)`，合并去重后注入 `DebugContext.related_specs`。

### 6.8 新增/修改文件

- 新增 `app/mcp/collectors/spec.py`
- 新增 `app/mcp/tools/spec_api.py`
- 修改 `app/schemas/context.py`：新增 `SpecSnippet`
- 修改 `app/mcp/builders/context.py`：注入 related_specs
- 修改 `app/mcp_server.py`：注册 `get_related_specs`
- 修改 `app/api/mcp_routes.py`：注册 REST 版本

## 7. Phase 3：前端静默失败捕获设计

### 7.1 目标

捕获"点击无反应、无报错、但用户期望有结果"的前端问题。

### 7.2 浏览器 SDK 设计

```typescript
interface AIDebugConfig {
  endpoint: string;           // ai-debug-mcp 接收端
  project?: string;
  captureClicks?: boolean;
  captureNetwork?: boolean;
  captureConsole?: boolean;
  captureRoute?: boolean;
  environment?: 'dev' | 'test'; // 默认 dev，生产环境建议不开启
}

interface UIEvent {
  event_type: 'click' | 'submit' | 'route_change';
  target_selector: string;
  component_name?: string;
  route_path?: string;
  timestamp: number;
  trace_id?: string;
}
```

#### 点击事件采集

```typescript
document.addEventListener('click', (e) => {
  const event: UIEvent = {
    event_type: 'click',
    target_selector: generateSelector(e.target),
    component_name: getComponentName(e.target),
    route_path: window.location.pathname,
    timestamp: Date.now(),
  };
  buffer.push(event);
  flush();
});
```

#### 期望行为标记 API

```typescript
aiDebug.expectAfterClick('.submit-btn', {
  type: 'route_change',
  to: '/success',
  withinMs: 2000,
});
```

#### 静默失败检测

```typescript
function checkSilentFailure(event: UIEvent, expectation: Expectation): boolean {
  // 在 expectation.withinMs 内检查是否发生期望行为
  // 若未发生且无异常，则生成 silent_failure
}
```

### 7.3 后端接收

新增端点：

```http
POST /ingest/silent-failure
Content-Type: application/json

{
  "exc_type": "SilentFailure",
  "message": "点击 .submit-btn 后 2 秒内未跳转到 /success",
  "frames": [
    {"file": "src/views/OrderSubmit.vue", "line": 42, "function": "submitOrder"}
  ],
  "source": "browser_sdk",
  "extra": {
    "ui_events": [...],
    "network_records": [...],
    "console_logs": [...],
    "expectation": {...}
  }
}
```

后端复用 `tool_ingest_error`，将 `trace_kind` 设为 `silent_failure`。

### 7.4 上下文打包

`build_debug_context` 对 `trace_kind == 'silent_failure'` 的 trace：
1. 从 `extra.ui_events` 解析 UI 事件。
2. 从 `extra.network_records` 解析网络请求。
3. 将 `ui_events` 和 `network_trace` 注入 `DebugContext`。
4. 对事件中的源码位置调用 `get_code_snippet`。

### 7.5 网络请求追踪设计

#### 前端拦截

```typescript
const originalFetch = window.fetch;
window.fetch = async (...args) => {
  const start = performance.now();
  const response = await originalFetch(...args);
  const record = {
    url: args[0],
    method: args[1]?.method || 'GET',
    status: response.status,
    duration_ms: performance.now() - start,
  };
  networkBuffer.push(record);
  return response;
};
```

#### 后端中间件

FastAPI 中间件 `capture_request_exceptions` 已捕获异常，增强为同时记录正常请求：

```python
@app.middleware("http")
async def capture_request_trace(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    # 记录 network_records
    return response
```

### 7.6 新增/修改文件

- 新增 `sdk/js/ai-debug-sdk.ts`（浏览器 SDK）
- 新增 `app/mcp/tools/silent_failure_api.py`
- 新增 `app/mcp/tools/network_api.py`
- 修改 `app/schemas/context.py`：新增 `UIEvent`、`NetworkRecord`
- 修改 `app/schemas/trace.py`：支持 `trace_kind`
- 修改 `app/mcp/builders/context.py`：注入 ui_events / network_trace
- 修改 `app/main.py`：增强中间件记录正常请求
- 修改 `app/mcp_server.py`：注册新工具
- 修改 `app/api/mcp_routes.py`：注册 REST 版本

## 8. 接口设计

### 8.1 MCP 工具接口

#### get_debug_context

```json
{
  "name": "get_debug_context",
  "inputSchema": {
    "type": "object",
    "properties": {
      "trace_id": {"type": "string"}
    }
  }
}
```

#### ingest_error

```json
{
  "name": "ingest_error",
  "inputSchema": {
    "type": "object",
    "properties": {
      "exc_type": {"type": "string"},
      "message": {"type": "string"},
      "frames": {"type": "array", "items": {"type": "object"}},
      "source": {"type": "string", "default": "ingest"},
      "extra": {"type": "object", "default": {}}
    },
    "required": ["exc_type", "message"]
  }
}
```

#### get_related_specs（Phase 2）

见 6.7。

#### ingest_silent_failure（Phase 3）

```json
{
  "name": "ingest_silent_failure",
  "inputSchema": {
    "type": "object",
    "properties": {
      "message": {"type": "string"},
      "frames": {"type": "array", "items": {"type": "object"}},
      "ui_events": {"type": "array", "items": {"type": "object"}},
      "network_records": {"type": "array", "items": {"type": "object"}},
      "expectation": {"type": "object"}
    },
    "required": ["message"]
  }
}
```

### 8.2 REST 接口

所有 MCP 工具均有对应 REST 端点：

```http
POST /mcp/tools/{tool_name}
X-API-Key: change-me-to-a-real-secret

{
  "arguments": {...}
}
```

新增原始接入端点：

```http
POST /ingest/error
POST /ingest/silent-failure
POST /ingest/network
```

## 9. 安全设计

### 9.1 脱敏

- 入库前：在 `save_trace` 中调用 `redact()`。
- 返回前：在 `build_debug_context` 中对 `code_snippets` 再次脱敏。
- 规则可配置：未来通过 `settings.redaction_patterns` 扩展。

### 9.2 API 鉴权

- FastAPI 面板和 REST 接口使用 `X-API-Key` 头部鉴权。
- stdio MCP server 通过进程隔离，不直接暴露网络端口。

### 9.3 浏览器 SDK 隐私

- 默认只在 `dev` / `test` 环境启用。
- 不采集 input 内容、cookie、localStorage。
- 网络请求 response body 超过 10KB 截断。

## 10. 扩展性设计

### 10.1 采集器插件化

新增采集器只需实现：

```python
def collect(trace_id: str, entry: TraceEntry) -> SomeContext:
    ...
```

然后在 `build_debug_context` 中注册调用。

### 10.2 存储后端插件化

当前 `core/logs.py` 直接依赖 SQLite。未来抽象为：

```python
class TraceStore(ABC):
    @abstractmethod
    def save(self, entry: TraceEntry) -> str: ...
    @abstractmethod
    def get(self, trace_id: str) -> TraceEntry | None: ...
```

实现 `SQLiteTraceStore` 和 `PostgresTraceStore`。

### 10.3 规范解析器插件化

支持自定义规范解析器：

```python
class SpecParser(ABC):
    @abstractmethod
    def can_parse(self, file_path: str) -> bool: ...
    @abstractmethod
    def parse(self, content: str) -> SpecSnippet: ...
```

## 11. 测试策略

### 11.1 单元测试

- `tests/test_logs.py`：去重、脱敏、schema 迁移。
- `tests/test_redaction.py`：敏感模式覆盖。
- `tests/test_git.py`：git blame/diff 解析（mock subprocess）。
- `tests/test_spec.py`（Phase 2）：规范文件扫描与匹配。

### 11.2 集成测试

- `tests/test_context_flow.py`：完整链路 `save_trace → get_trace → build_debug_context`。
- `tests/test_api.py`：FastAPI TestClient 调用 `/mcp/tools/*`。

### 11.3 端到端测试

- 启动 FastAPI，用 curl 调用 `ingest_error` → `get_debug_context`。
- 浏览器 SDK 在测试页面触发点击，验证 `silent_failure` 生成。

## 12. 部署方案

### 12.1 本地开发

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

MCP 配置：

```json
{
  "mcpServers": {
    "ai-debug-mcp": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/path/to/ai-debug-mcp"
    }
  }
}
```

### 12.2 Docker（Phase 2）

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY examples/ ./examples/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 12.3 多环境配置

通过 `.env` 文件区分环境：

```bash
# .env.dev
DB_PATH=./data/ai_debug_mcp_dev.sqlite3
LOG_LEVEL=DEBUG

# .env.prod
DB_PATH=/data/ai_debug_mcp.sqlite3
LOG_LEVEL=INFO
API_KEY=<strong-secret>
```

## 13. 性能考虑

- `save_trace` 目标 < 50ms，主要开销在 SQLite 写入和 git 命令（异步/超时处理）。
- `build_debug_context` 目标 < 300ms，主要开销在读取源码、git 命令、规范扫描。
- 规范文件扫描在启动时完成，不阻塞每次 context 构建。
- 前端 SDK 事件采集使用缓冲区 + 防抖，避免每条事件都发 HTTP。

## 14. 待决策事项

- 规范文件关联是否引入 LLM 做相关性排序？（MVP 先用关键词匹配，成本低）
- 静默失败是否支持"无期望标记"的保守检测？（MVP 不支持，避免误报）
- sourcemap 解析是否作为 Phase 3 子任务？（建议延后到 Phase 3.5）
- 网络请求 response body 截断阈值多少合适？（建议 10KB）
