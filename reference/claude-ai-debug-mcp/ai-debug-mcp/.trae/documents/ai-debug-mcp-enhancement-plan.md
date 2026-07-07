# ai-debug-mcp 增强实施计划

## Context

当前骨架已能自动捕获 Python 异常并打包成调试上下文返回给 AI，但实际使用中存在几个瓶颈：

1. **重复刷屏**：同一个 bug 在循环里反复触发会产生大量几乎相同的 trace 记录，`list_recent_traces` 质量低。
2. **安全风险**：异常消息和源码片段里可能携带密码、token、手机号等敏感信息，会原样进入 LLM 上下文。
3. **排查慢**：AI 看不到这段代码最近一次是谁改的、最近有没有相关 diff，需要用户手动翻 git。
4. **语言单一**：只有 Python 异常钩子，其他语言无法接入。
5. **并发隐患**：SQLite 默认 journal 模式在高频写入下容易 `database is locked`。

本计划按“高影响、低耦合、可逐步交付”原则，先做能直接减少排查时间的核心能力（去重、脱敏、Git、WAL、多语言接入），再补齐测试和 Docker。

## Scope

### Phase 1（本次实施）

- 错误指纹去重 / 聚合
- 敏感信息脱敏
- Git 集成（blame + recent diff）
- SQLite WAL 模式
- 多语言错误接入（`ingest_error`）

### Phase 2（后续）

- pytest 核心链路测试
- Dockerfile

选择把 Phase 1 五项一起做，是因为它们都集中在 `logs.py` / `schemas/trace.py` / `schemas/context.py` / `mcp_server.py` 这几处，合并修改比反复改同一文件成本低；脱敏和 WAL 是顺手就能加的安全/稳定性底线。

## Schema Changes

修改 `app/mcp/core/logs.py` 中的 `init_db()`：

```sql
CREATE TABLE IF NOT EXISTS traces (
    trace_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    timestamp REAL NOT NULL,       -- 向后兼容，值等同于 last_seen
    exc_type TEXT NOT NULL,
    message TEXT NOT NULL,
    frames_json TEXT NOT NULL,
    source TEXT NOT NULL,
    extra_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traces_ts ON traces(timestamp);
CREATE INDEX IF NOT EXISTS idx_traces_fingerprint ON traces(fingerprint);
```

迁移策略（简单安全）：
- 旧表存在时，通过 `PRAGMA table_info(traces)` 检测缺失列，依次 `ALTER TABLE ADD COLUMN`。
- 新增列的默认值：`fingerprint` 为空、`occurrence_count` 为 1、`first_seen`/`last_seen` 用已有 `timestamp`。
- 不对旧数据做合并，避免误删；旧记录保持 `occurrence_count=1`，新记录自然走聚合逻辑。
- 连接时立即执行 `PRAGMA journal_mode=WAL;`。

## Module Changes

### 1. 指纹去重（`app/mcp/core/logs.py`）

新增：

```python
import hashlib

def compute_fingerprint(exc_type: str, frames: list[StackFrame]) -> str:
    parts = [exc_type]
    for f in frames[:3]:
        parts.append(f"{f.file}:{f.line}:{f.function}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
```

改造 `save_trace()` 为 upsert：
- 先算 `fingerprint`。
- 对 `message` 和每个 frame 的 `code_context` 做脱敏。
- 查询 `fingerprint` 是否已存在：存在则 `occurrence_count += 1`、`last_seen = now`、更新 `message/frames/source/extra`；不存在则新建 `trace_id`。
- 返回的仍是 `trace_id`（重复触发时返回同一个）。

`TraceEntry` / `TraceSummary` 增加 `fingerprint`、`first_seen`、`last_seen`、`occurrence_count`。
`list_recent_traces` / `search_logs` 按 `last_seen DESC` 返回聚合后的摘要。

### 2. 敏感信息脱敏

新增 `app/mcp/core/redaction.py`：

```python
import re

_SECRET_PATTERNS = [
    (re.compile(r"(?i)(password|pwd|passwd)\s*[:=]\s*['\"]?\S+['\"]?"), r'\1="***"'),
    (re.compile(r"(?i)(api[_-]?key|apikey|secret|token|access[_-]?token|auth[_-]?token|private[_-]?key)\s*[:=]\s*['\"]?\S+['\"]?"), r'\1="***"'),
    (re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)\S+", re.I), r'\1***'),
    (re.compile(r"\b1[3-9]\d{9}\b"), "***PHONE***"),
]

def redact(text: str | None) -> str | None:
    if not text:
        return text
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text
```

- `logs.py` 的 `save_trace()` 中，对 `message` 和每个 frame 的 `code_context` 脱敏后入库。
- `app/mcp/builders/context.py` 中对 `code_snippets` 的 `snippet` 再次脱敏后返回。

### 3. Git 集成

新增 `app/mcp/core/git.py`：

```python
import subprocess
from pathlib import Path

def _git_cmd(args: list[str], cwd: Path) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None

def get_blame_for_frame(file_path: str, line_no: int) -> dict | None:
    cwd = Path(file_path).parent
    out = _git_cmd(["blame", "-L", f"{line_no},{line_no}", "--porcelain", Path(file_path).name], cwd)
    if not out:
        return None
    # 解析 commit / author / author-time / summary / line_text

def get_recent_diff(file_path: str, commits_back: int = 3) -> dict | None:
    cwd = Path(file_path).parent
    out = _git_cmd(["diff", f"HEAD~{commits_back}", "--", Path(file_path).name], cwd)
    if not out:
        return None
    return {"file": file_path, "commits_back": commits_back, "diff": out}
```

失败时返回 `None`，不影响主流程。

`app/schemas/context.py` 新增：

```python
class GitBlameInfo(BaseModel):
    file: str
    line: int
    commit: str
    author: str
    date: str
    summary: str
    line_text: str

class GitDiffInfo(BaseModel):
    file: str
    commits_back: int
    diff: str
```

`DebugContext` 增加 `git_blame: list[GitBlameInfo] | None` 和 `recent_diffs: list[GitDiffInfo] | None`。

`app/mcp/builders/context.py` 中对 trace 的前 3 帧调用 blame/diff，组装进 `DebugContext`。

### 4. 多语言接入（`ingest_error`）

新增 `app/mcp/tools/ingest_api.py`：

```python
from app.mcp.core.logs import save_trace
from app.schemas.trace import StackFrame

def tool_ingest_error(
    exc_type: str,
    message: str,
    frames: list[dict],
    source: str = "ingest",
    extra: dict | None = None,
) -> dict:
    stack_frames = [StackFrame(**f) for f in frames]
    trace_id = save_trace(exc_type, message, stack_frames, source, extra or {})
    return {"trace_id": trace_id, "saved": True}
```

接受的 `frames` 每项：`{"file": str, "line": int, "function": str, "code_context": str?}`。

### 5. SQLite WAL

在 `app/mcp/core/logs.py` 的 `_conn()` 中连接成功后立即执行：

```python
conn.execute("PRAGMA journal_mode=WAL;")
```

## Tool Registration

### stdio MCP Server（`app/mcp_server.py`）

导入并注册 3 个新工具：

- `get_blame_for_frame`：`{file: str, line: int}`
- `get_recent_diff`：`{file: str, commits_back?: int}`（默认 3）
- `ingest_error`：`{exc_type: str, message: str, frames: list, source?: str, extra?: object}`

### REST 测试路由（`app/api/mcp_routes.py`）

在 `_TOOL_REGISTRY` 和 `_TOOL_DESCRIPTIONS` 中补充同样的 3 个工具映射。

## Verification

1. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```

2. 启动 FastAPI：
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

3. 测试多语言接入 + 脱敏：
   ```bash
   curl -X POST http://localhost:8000/mcp/tools/ingest_error \
     -H "Content-Type: application/json" \
     -H "X-API-Key: change-me-to-a-real-secret" \
     -d '{
       "arguments": {
         "exc_type": "NullPointerException",
         "message": "user password=supersecret token=abc123 phone=13800138000",
         "source": "java_service",
         "frames": [{"file": "app/main.py", "line": 20, "function": "login"}]
       }
     }'
   ```

4. 测试去重：连续调用两次相同请求，返回同一个 `trace_id`，第二次后 `occurrence_count=2`。

5. 测试 Git 工具：
   ```bash
   curl -X POST http://localhost:8000/mcp/tools/get_blame_for_frame \
     -H "Content-Type: application/json" \
     -H "X-API-Key: change-me-to-a-real-secret" \
     -d '{"arguments": {"file": "app/main.py", "line": 20}}'
   ```

6. 检查 WAL 模式：
   ```bash
   sqlite3 data/ai_debug_mcp.sqlite3 "PRAGMA journal_mode;"
   # 应返回 wal
   ```

7. 检查 schema：
   ```bash
   sqlite3 data/ai_debug_mcp.sqlite3 ".schema traces"
   # 应包含 fingerprint / first_seen / last_seen / occurrence_count
   ```

## Critical Files to Modify

- `app/mcp/core/logs.py`
- `app/mcp/core/redaction.py`（新增）
- `app/mcp/core/git.py`（新增）
- `app/mcp/builders/context.py`
- `app/mcp_server.py`
- `app/api/mcp_routes.py`
- `app/schemas/trace.py`
- `app/schemas/context.py`
- `app/mcp/tools/ingest_api.py`（新增）
