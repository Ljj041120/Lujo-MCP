"""
MCP 工具：ingest_error —— 供任意语言/进程主动上报错误。

非 Python 运行时（Node.js / Go / Rust / Java 等）可以把自身异常解析后，
按与 StackFrame 一致的结构上报，复用同一套存储、去重、脱敏和调试上下文逻辑。
"""
from app.mcp.core.logs import save_trace
from app.schemas.trace import StackFrame


def tool_ingest_error(
    exc_type: str,
    message: str,
    frames: list[dict],
    source: str = "ingest",
    extra: dict | None = None,
) -> dict:
    """接收外部上报的错误，落库后返回 trace_id。"""
    stack_frames = [StackFrame(**f) for f in frames]
    trace_id = save_trace(
        exc_type=exc_type,
        message=message,
        frames=stack_frames,
        source=source,
        extra=extra or {},
    )
    return {"trace_id": trace_id, "saved": True}
