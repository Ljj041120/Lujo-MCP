# ai-debug-mcp 完善计划：Phase 2 收尾 + Phase 3 前端静默失败捕获

## 1. 摘要

本计划依据 `PRD.md` 与 `DESIGN.md`，在已完成 Phase 1（去重/脱敏/Git/WAL/多语言接入）和 Phase 2 规范驱动提示的基础上，补齐 Phase 2 剩余项（Dockerfile、可配置脱敏规则），并实现 Phase 3 MVP：前端静默失败捕获。最终让 `get_debug_context` 在异常之外，也能返回前端 UI 事件链与网络请求链，解决“点了没反应、没有报错”的痛点。

## 2. 当前状态分析

- **已完成并通过测试**：Phase 1 核心能力 + Phase 2 规范驱动提示，`python -m pytest tests/` 20 passed。
- **Phase 2 仍缺失**：
  - Dockerfile 容器化（PRD 12.2）。
  - 脱敏规则可配置（PRD 9.1 / 5.3 提到“未来通过 `settings.redaction_patterns` 扩展”）。
- **Phase 3 未开始**：
  - 无 `UIEvent` / `NetworkRecord` 数据模型。
  - 无 `network_records` / `ui_events` 数据表。
  - 无 `ingest_silent_failure` 工具与 `/ingest/silent-failure` 端点。
  - 无 `get_network_trace` 工具。
  - 浏览器 SDK 不存在。
  - `DebugContext` 未注入 `ui_events` / `network_trace`。

## 3. 拟修改文件与实现方案

### 3.1 Phase 2 收尾

#### 3.1.1 可配置脱敏规则

- **文件**：`app/config.py`
  - 新增配置项：
    - `redaction_enabled: bool = True`
    - `redaction_extra_patterns: list[str] = Field(default_factory=list)`（额外正则，Python raw string 形式）
- **文件**：`app/mcp/core/redaction.py`
  - 保留默认 `_SECRET_PATTERNS`。
  - 新增 `_compile_extra_patterns(patterns: list[str]) -> list[tuple[re.Pattern, str]]`。
  - `redact(text)` 在应用默认规则后，再应用 `settings.redaction_extra_patterns` 中的规则。
  - 当 `settings.redaction_enabled == False` 时直接返回原文（仅用于特定测试/调试场景）。
- **文件**：`.env.example`
  - 追加：
    ```bash
    REDACTION_ENABLED=true
    REDACTION_EXTRA_PATTERNS=[]
    ```
- **文件**：`tests/test_redaction.py`（新建）
  - 测试默认规则仍生效。
  - 测试额外正则可命中并替换。
  - 测试关闭开关后不再脱敏。

#### 3.1.2 Dockerfile

- **新建**：`Dockerfile`
  - Base: `python:3.11-slim`
  - 安装 git（Git 工具依赖）。
  - 复制 `requirements.txt` 后 `pip install`。
  - 复制 `app/`、`examples/`、`sdk/js/`（如有）。
  - `ENV PYTHONUNBUFFERED=1`，暴露 8000。
  - `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`。
- **新建**：`.dockerignore`
  - 忽略 `.git/`、`__pycache__/`、`.pytest_cache/`、`.trae/`、`data/`、`.env`、`node_modules/` 等。

### 3.2 Phase 3 前端静默失败捕获

#### 3.2.1 数据模型扩展

- **文件**：`app/schemas/context.py`
  - 新增：
    ```python
    class UIEvent(BaseModel):
        event_id: Optional[str] = None
        timestamp: float
        event_type: str          # click / submit / route_change
        target_selector: Optional[str] = None
        component_name: Optional[str] = None
        route_path: Optional[str] = None
        payload_json: Optional[str] = None

    class NetworkRecord(BaseModel):
        record_id: Optional[str] = None
        timestamp: float
        direction: str           # inbound / outbound
        method: Optional[str] = None
        url: Optional[str] = None
        status_code: Optional[int] = None
        request_body: Optional[str] = None
        response_body: Optional[str] = None
        duration_ms: Optional[float] = None
    ```
  - `DebugContext` 追加：
    ```python
    network_trace: Optional[list[NetworkRecord]] = None
    ui_events: Optional[list[UIEvent]] = None
    ```

#### 3.2.2 存储层扩展

- **文件**：`app/mcp/core/logs.py`
  - `init_db()` 中新增两张表：
    - `network_records`：字段见 DESIGN 5.2，主键 `record_id TEXT`，索引 `idx_network_trace`、`idx_network_request`。
    - `ui_events`：字段见 DESIGN 5.2，主键 `event_id TEXT`，索引 `idx_ui_trace`。
  - 新增函数：
    - `save_network_record(record: NetworkRecord) -> str`
    - `save_ui_event(event: UIEvent) -> str`
    - `get_network_records(trace_id: str) -> list[NetworkRecord]`
    - `get_ui_events(trace_id: str) -> list[UIEvent]`
  - `_cleanup_expired()` 同步清理两张表中 `timestamp < cutoff` 的记录。
  - 所有入库文本（URL、body、payload）先走 `redact()`。

#### 3.2.3 采集/解析辅助层

- **新建**：`app/mcp/collectors/network.py`
  - `network_records_from_extra(extra: dict) -> list[NetworkRecord]`：把 SDK/用户上报的原始 dict 转为模型列表，过滤并截断 `response_body` 到 10KB，调用 `redact()`。
- **新建**：`app/mcp/collectors/ui_event.py`
  - `ui_events_from_extra(extra: dict) -> list[UIEvent]`：同理转换 UI 事件并脱敏。

#### 3.2.4 MCP 工具

- **新建**：`app/mcp/tools/silent_failure_api.py`
  - `tool_ingest_silent_failure(message, frames=[], ui_events=[], network_records=[], expectation=None, source="browser_sdk", extra=None) -> dict`
  - 逻辑：
    1. 将 `frames` 转成 `StackFrame`。
    2. 调用 `save_trace(exc_type="SilentFailure", ..., trace_kind="silent_failure", extra={"ui_events": ..., "network_records": ..., "expectation": ...})`。
    3. 将 `ui_events` 和 `network_records` 逐个写入对应表（关联 `trace_id`）。
    4. 返回 `{"trace_id": trace_id, "saved": True}`。
- **新建**：`app/mcp/tools/network_api.py`
  - `tool_get_network_trace(trace_id: str) -> dict`：调用 `get_network_records(trace_id)`，返回 `{"found": bool, "records": [...]}`。
  - `tool_ingest_network(record: dict) -> dict`（可选，便于前端 SDK 单条上报）：调用 `save_network_record` 后返回 `record_id`。

#### 3.2.5 上下文构建层

- **文件**：`app/mcp/builders/context.py`
  - 在 `build_debug_context` 中，追加：
    ```python
    network_trace = None
    ui_events = None
    if entry.trace_kind == "silent_failure" or entry.extra.get("ui_events") or entry.extra.get("network_records"):
        ui_events = get_ui_events(entry.trace_id) or ui_events_from_extra(entry.extra)
        network_trace = get_network_records(entry.trace_id) or network_records_from_extra(entry.extra)
    ```
  - 将 `ui_events` 和 `network_trace` 传入 `DebugContext`。
  - 失败隔离：任何解析/查询异常都不影响主上下文返回。

#### 3.2.6 FastAPI 接入层

- **新建**：`app/api/ingest.py`
  - 路由前缀 `/ingest`，依赖 `verify_api_key`。
  - 端点：
    - `POST /ingest/error`：调用 `tool_ingest_error`。
    - `POST /ingest/silent-failure`：调用 `tool_ingest_silent_failure`。
    - `POST /ingest/network`：调用 `tool_ingest_network`。
  - 请求体 Pydantic 模型可内联定义（保持简单），错误时返回 400/500。
- **文件**：`app/main.py`
  - `app.include_router(ingest_router)`。
  - 将 `capture_request_exceptions` 中间件增强为 `capture_request_trace`：在正常请求结束后记录一条 `inbound` 网络记录（method、path、status_code、duration_ms），不读取响应体以避免流式响应问题。

#### 3.2.7 浏览器 SDK

- **新建**：`sdk/js/ai-debug-sdk.ts`
  - 提供 `AIDebug` 类：
    - `init(config)`：仅当 `environment` 为 `dev`/`test` 时启用。
    - 监听 `click`、`submit`、`popstate`/`hashchange`。
    - 拦截 `fetch` 与 `XMLHttpRequest`，记录 URL、method、status、duration，response body 截断 10KB。
    - `expectAfterClick(selector, expectation)`：用户显式标记期望行为。
    - 在 `withinMs` 内检测路由变化/DOM 变化/网络请求，若未命中则通过 `POST /ingest/silent-failure` 上报。
    - 不上报 input 内容、cookie、localStorage。
  - **新建**：`sdk/js/README.md`：使用方式与初始化示例。

#### 3.2.8 工具注册

- **文件**：`app/mcp_server.py`
  - `list_tools()` 追加 `ingest_silent_failure`、`get_network_trace`（如实现了 `ingest_network` 也一并注册）。
  - `call_tool()` 添加对应分支。
- **文件**：`app/api/mcp_routes.py`
  - `_TOOL_REGISTRY` 与 `_TOOL_DESCRIPTIONS` 追加同上工具。

#### 3.2.9 测试

- **新建**：`tests/test_redaction.py`（Phase 2 收尾）。
- **新建**：`tests/test_silent_failure.py`
  - `test_ingest_silent_failure_returns_trace_id`
  - `test_silent_failure_stores_ui_events_and_network_records`
  - `test_debug_context_for_silent_failure_includes_ui_events_and_network_trace`
- **新建**：`tests/test_network.py`
  - `test_save_and_get_network_record`
  - `test_get_network_trace_tool`
  - `test_ingest_network_endpoint`（FastAPI TestClient）
- **修改**：`tests/test_context_flow.py`
  - 追加一个静默失败链路测试（可选）。

## 4. 关键决策与假设

1. **静默失败判定**：MVP 采用“用户显式标记期望行为”模式，不在无标记情况下自动推断，避免误报（符合 PRD 5.6/7.2）。
2. **Sourcemap 延后**：Phase 3 不包含 sourcemap 解析，源码位置先通过组件名/selector 或堆栈文件粗略定位（PRD 15 说明）。
3. **网络请求体截断**：SDK 端 response body 超过 10KB 截断；后端入库前再次脱敏。
4. **后端中间件只记录 inbound**：不记录 outbound，避免修改业务代码；如需 outbound 可通过 `/ingest/network` 主动上报。
5. **数据清理复用 TTL**：`network_records` 与 `ui_events` 按各自 `timestamp` 与 `trace_ttl_seconds` 清理。
6. **Docker 数据持久化**：默认 `DB_PATH=./data/...`，容器运行时应挂载 volume 到 `/app/data`。

## 5. 验证步骤

1. 单元/集成测试：
   ```bash
   python -m pytest tests/ -q
   # 目标：新增测试后全部通过
   ```
2. Dockerfile 构建：
   ```bash
   docker build -t ai-debug-mcp .
   ```
3. FastAPI 端到端验证（启动服务后）：
   ```bash
   curl -X POST http://localhost:8000/ingest/silent-failure \
     -H "Content-Type: application/json" \
     -H "X-API-Key: change-me-to-a-real-secret" \
     -d '{
       "message": "点击 .submit-btn 后 2 秒内未跳转到 /success",
       "frames": [{"file":"src/views/OrderSubmit.vue","line":42,"function":"submitOrder"}],
       "ui_events": [{"event_type":"click","target_selector":".submit-btn","timestamp":0}],
       "network_records": [{"direction":"outbound","method":"POST","url":"/api/order","status_code":200,"timestamp":0}]
     }'
   ```
4. 用返回的 `trace_id` 请求：
   ```bash
   curl -X POST http://localhost:8000/mcp/tools/get_debug_context \
     -H "Content-Type: application/json" \
     -H "X-API-Key: change-me-to-a-real-secret" \
     -d '{"arguments": {"trace_id": "<trace_id>"}}'
   ```
   验证返回体包含 `ui_events` 和 `network_trace`。
5. 脱敏配置验证：
   - 在 `.env` 中设置 `REDACTION_EXTRA_PATTERNS=["(cvv)\\s*[:=]\\s*\\S+"]`，重启后测试该模式被命中。

## 6. 预期产出

- 新增/修改代码文件：约 12–15 个。
- 新增测试：3 个测试文件，约 10–15 个用例。
- 全部测试通过，Docker 镜像可构建，FastAPI 端点可用，浏览器 SDK 可直接复制到前端项目使用。
