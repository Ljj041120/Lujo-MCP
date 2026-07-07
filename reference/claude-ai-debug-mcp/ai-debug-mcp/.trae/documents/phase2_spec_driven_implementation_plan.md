# Phase 2 规范驱动提示实施计划

## 上下文

用户希望基于已完成的 [PRD.md](../../PRD.md) 和 [DESIGN.md](../../DESIGN.md) 完善代码。当前 Phase 1（异常采集、去重、脱敏、Git 集成、多语言 ingest_error）已实现并通过验证。

本次要落地的是 Phase 2：规范驱动提示（Spec-Driven Prompting），即让 AI 在分析错误时自动看到与报错文件相关的项目规范，减少用户反复手动复制规范到 prompt 的时间。

## 目标

1. 自动扫描项目中的规范文件（`*.md`、`.cursorrules`、JSON 规范等）。
2. 根据报错文件的扩展名，自动匹配最相关的规范片段。
3. 将匹配到的规范片段注入 `DebugContext`，让 `get_debug_context` 一次性返回。
4. 新增独立的 `get_related_specs` MCP/REST 工具，支持手动查询。
5. 同步扩展 `TraceEntry` 支持 `trace_kind`，为 Phase 3 的 `silent_failure` 预留字段。
6. 补齐核心链路测试。

## 推荐方案

采用"采集器 + 工具 + 上下文注入"三层增量改造：

- **采集层**：新增 `app/mcp/collectors/spec.py`，负责规范文件发现、解析、标签提取、缓存。
- **工具层**：新增 `app/mcp/tools/spec_api.py`，暴露 `get_related_specs`。
- **上下文层**：修改 `app/mcp/builders/context.py`，为 trace 的每个 frame 调用规范匹配，合并去重后注入 `DebugContext`。
- **存储层**：修改 `app/mcp/core/logs.py`，新增 `trace_kind` 列并做迁移。
- **注册层**：同步修改 `app/mcp_server.py` 和 `app/api/mcp_routes.py`。

## 具体改动

### 1. 数据模型（app/schemas/context.py）

新增 `SpecSnippet`：

```python
class SpecSnippet(BaseModel):
    file: str
    summary: str
    content: str
    tags: list[str] = Field(default_factory=list)
    target_extensions: list[str] = Field(default_factory=list)
```

`DebugContext` 新增字段：

```python
related_specs: Optional[list[SpecSnippet]] = None
```

### 2. TraceEntry 扩展（app/schemas/trace.py）

新增字段：

```python
trace_kind: str = "exception"  # exception | silent_failure | network_error | manual
```

### 3. 存储迁移（app/mcp/core/logs.py）

- 建表 SQL 增加 `trace_kind TEXT DEFAULT 'exception'`。
- 新增 `_migrate_v2_to_v3()`，为旧表补齐 `trace_kind` 列。
- `save_trace` 签名增加 `trace_kind: str = "exception"`。
- `_row_to_entry` 读取时回传 `trace_kind`。

### 4. 规范采集器（app/mcp/collectors/spec.py）

核心函数：

```python
def discover_spec_files(project_root: str | Path) -> list[Path]: ...
def parse_spec_file(path: Path) -> SpecSnippet | None: ...
def match_specs(error_file: str, specs: list[SpecSnippet], max_tokens: int = 2000) -> list[SpecSnippet]: ...
def get_related_specs(file_path: str, project_root: str | Path | None = None) -> list[SpecSnippet]: ...
def reload_specs() -> None: ...
```

实现要点：
- 扫描候选文件：`CONVENTION.md`、`API_SPEC.md`、`*.md`、`.cursorrules` 等。
- 排除 `node_modules/`、`.git/`、大于 1MB 的文件。
- 按文件名/一级标题关键词提取 `tags` 和 `target_extensions`。
- 按二级标题切 chunk，限制总长度约 2000 tokens。
- 启动时扫描并缓存，按 `mtime` 增量刷新。
- 内容经 `redact()` 脱敏后使用。

### 5. MCP 工具（app/mcp/tools/spec_api.py）

```python
def tool_get_related_specs(file_path: str) -> dict:
    specs = get_related_specs(file_path)
    return {"found": bool(specs), "specs": [s.model_dump() for s in specs]}
```

### 6. 上下文构建（app/mcp/builders/context.py）

在 `build_debug_context` 中：
1. 遍历 `entry.frames[:3]`。
2. 对每个 frame 调用 `get_related_specs(frame.file)`。
3. 按 `file` 去重，合并后按总长度裁剪。
4. 注入 `DebugContext.related_specs`。

### 7. 注册新工具

- `app/mcp_server.py`：`list_tools()` 和 `call_tool()` 中注册 `get_related_specs`。
- `app/api/mcp_routes.py`：`_TOOL_REGISTRY` 和 `_TOOL_DESCRIPTIONS` 中增加映射。

### 8. 测试

新增/补充：
- `tests/test_spec.py`：规范扫描、标签提取、扩展名匹配、长度裁剪、脱敏。
- `tests/test_context_flow.py`：完整链路 `save_trace → build_debug_context`，断言 `related_specs` 正确注入。
- `tests/test_logs.py`：旧 schema 迁移后 `trace_kind` 补齐且旧数据可读。

## 验证方式

1. 单元测试：`pytest tests/test_spec.py tests/test_context_flow.py tests/test_logs.py`
2. FastAPI 验证：
   - 启动 `uvicorn app.main:app --reload`
   - 调用 `POST /mcp/tools/ingest_error` 上报一条带 frames 的错误
   - 调用 `POST /mcp/tools/get_debug_context`，检查返回中包含 `related_specs`
3. MCP 验证：在 Trae/Codex 中调用 `get_related_specs` 工具。

## 范围控制（本次不做）

- Phase 3 前端静默失败捕获。
- 规范文件的向量检索 / LLM 相关性排序。
- `trace_kind` 在 UI 层面的过滤展示。
- 自动修复后规范校验。

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 规范文件过大 | 跳过 >1MB 文件；按二级标题切 chunk；总长度限制 2000 tokens |
| 匹配不准确 | MVP 用扩展名+标签匹配；保留 `tags` 字段便于后续升级 |
| IO 开销 | 启动扫描缓存，按 `mtime` 增量刷新 |
| 旧 DB 迁移失败 | 仅 `ALTER TABLE ADD COLUMN`，`TraceEntry` 字段设默认值 |
| stdio 与 REST 不一致 | 两处注册同步更新，测试覆盖 REST 端点 |
