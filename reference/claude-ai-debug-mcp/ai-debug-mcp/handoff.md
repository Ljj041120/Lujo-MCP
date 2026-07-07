# Handoff

## 2026-07-07 Phase 2：规范驱动提示

### 已完成

- [x] 数据模型扩展：`SpecSnippet`、`DebugContext.related_specs`、`TraceEntry.trace_kind`
- [x] 存储层迁移：`logs.py` 新增 `trace_kind` 列并兼容旧 DB
- [x] 规范采集器：`app/mcp/collectors/spec.py` 实现扫描、解析、标签匹配、缓存、脱敏
- [x] MCP 工具：`app/mcp/tools/spec_api.py` 暴露 `get_related_specs`
- [x] 上下文注入：`build_debug_context` 自动为前 3 帧匹配并注入相关规范
- [x] 工具注册：`mcp_server.py` 与 `api/mcp_routes.py` 同步注册
- [x] 测试覆盖：`tests/test_spec.py`、`tests/test_context_flow.py`、`tests/test_logs.py` 共 20 个用例
- [x] 依赖补充：`requirements.txt` 增加 pytest、httpx

### 验证结果

- `pytest tests/`：20 passed
- FastAPI TestClient：带 `package.json` 标记的项目中，`get_debug_context` 返回 `related_specs`

### 关键修复

- `_find_project_root` 限制在用户主目录内搜索，避免把 `C:\Users\ASUS\package.json` 误判为项目根导致扫描整个用户目录。
- `_find_project_root` 对不存在的文件按后缀识别为文件，返回其父目录。
- 规范发现跳过 `.trae`、`.idea`、`.vscode` 等 IDE/工具目录。

### 待后续

- Phase 3 前端静默失败捕获
- 更智能的规范相关性排序（当前按扩展名 + 关键词匹配）

## 2026-07-07 Phase 2 收尾 + Phase 3：前端静默失败捕获

### 已完成

- [x] Phase 2 收尾：可配置脱敏规则（`REDACTION_ENABLED`、`REDACTION_EXTRA_PATTERNS`）
- [x] Phase 2 收尾：`Dockerfile` 与 `.dockerignore` 容器化
- [x] 数据模型扩展：`UIEvent`、`NetworkRecord`、`DebugContext.ui_events` / `network_trace`
- [x] 存储层扩展：`network_records` / `ui_events` 表，支持 save/get 与 TTL 清理
- [x] 采集辅助层：`app/mcp/collectors/network.py`、`ui_event.py`
- [x] MCP 工具：`ingest_silent_failure`、`ingest_network`、`get_network_trace`
- [x] 上下文注入：`build_debug_context` 为静默失败 trace 注入 UI 事件与网络请求链
- [x] FastAPI 接入层：`/ingest/error`、`/ingest/silent-failure`、`/ingest/network`
- [x] FastAPI 中间件增强：记录 inbound 请求到 `network_records`
- [x] 浏览器 SDK：`sdk/js/ai-debug-sdk.ts` + `README.md`
- [x] 工具注册：`mcp_server.py` 与 `api/mcp_routes.py` 同步注册新工具
- [x] 测试覆盖：新增 `tests/test_redaction.py`、`tests/test_silent_failure.py`、`tests/test_network.py`

### 验证结果

- `python -m pytest tests/`：33 passed
- FastAPI TestClient：`/ingest/silent-failure` → `get_debug_context` 返回 `ui_events` 与 `network_trace`
- Docker 构建：当前环境无运行中的 Docker daemon，未能在本地验证镜像构建

### 关键决策

- 静默失败判定采用“用户显式标记期望行为”，避免 MVP 阶段误报。
- 后端中间件仅记录 inbound 请求，避免侵入业务代码；outbound 由浏览器 SDK 或 `/ingest/network` 上报。
- 网络请求 response body 在 SDK 端截断 10KB，后端入库前再次脱敏。
- `network_records` / `ui_events` 与 `traces` 共用 `trace_ttl_seconds` 清理策略。

### 待后续

- 更智能的规范相关性排序（当前按扩展名 + 关键词匹配）
- sourcemap 解析，将前端编译后位置映射回源码
- 生产环境 Docker 镜像验证与 CI 构建
