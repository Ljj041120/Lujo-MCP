"""
异常追踪记录的数据结构。
"""
from typing import Optional
from pydantic import BaseModel, Field


class StackFrame(BaseModel):
    file: str
    line: int
    function: str
    code_context: Optional[str] = None  # 该行源码文本


class TraceEntry(BaseModel):
    trace_id: str
    fingerprint: str
    first_seen: float
    last_seen: float
    occurrence_count: int = 1
    timestamp: float                # unix 时间戳，向后兼容，值等价于 last_seen
    exc_type: str
    message: str
    frames: list[StackFrame] = Field(default_factory=list)
    source: str = "unknown"          # 谁上报的：global_hook / fastapi_middleware / manual
    extra: dict = Field(default_factory=dict)
    trace_kind: str = "exception"    # exception | silent_failure | network_error | manual


class TraceSummary(BaseModel):
    """list_recent_traces 用的精简版，不带完整堆栈，减少上下文占用"""
    trace_id: str
    fingerprint: str
    first_seen: float
    last_seen: float
    timestamp: float               # 向后兼容，等价于 last_seen
    occurrence_count: int
    exc_type: str
    message: str
    top_frame: Optional[str] = None
