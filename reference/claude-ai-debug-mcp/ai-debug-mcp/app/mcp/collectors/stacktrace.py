"""
异常堆栈捕获与格式化。

供两处调用：
1. hooks/exception_hook.py 全局钩子捕获到异常时，调用 capture_exception() 落库
2. 手动场景（比如 examples/error_demo.py）直接调用 capture_exception()
"""
import traceback
from types import TracebackType

from app.mcp.core.logs import save_trace, get_trace
from app.schemas.trace import StackFrame, TraceEntry


def _extract_frames(tb: TracebackType | None) -> list[StackFrame]:
    frames = []
    for frame_summary in traceback.extract_tb(tb):
        frames.append(StackFrame(
            file=frame_summary.filename,
            line=frame_summary.lineno or 0,
            function=frame_summary.name,
            code_context=frame_summary.line,
        ))
    return frames


def capture_exception(exc: BaseException, source: str = "manual", extra: dict | None = None) -> str:
    """把一个异常对象格式化并落库，返回 trace_id"""
    frames = _extract_frames(exc.__traceback__)
    trace_id = save_trace(
        exc_type=type(exc).__name__,
        message=str(exc),
        frames=frames,
        source=source,
        extra=extra,
    )
    return trace_id


def get_latest_stacktrace(trace_id: str | None = None) -> dict:
    """给 MCP 工具 get_stacktrace 用：返回结构化堆栈，不带代码片段（那是 code_locator 的事）"""
    entry: TraceEntry | None = get_trace(trace_id)
    if entry is None:
        return {"found": False, "message": "没有找到追踪记录，可能还没有异常发生过"}
    return {
        "found": True,
        "trace_id": entry.trace_id,
        "timestamp": entry.timestamp,
        "exc_type": entry.exc_type,
        "message": entry.message,
        "source": entry.source,
        "frames": [f.model_dump() for f in entry.frames],
    }
