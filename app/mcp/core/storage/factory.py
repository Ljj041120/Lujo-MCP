"""存储工厂 —— 根据配置自动选择后端"""

from app.config import settings
from app.mcp.core.storage.base import TraceStorage, SessionStorage

_trace_store: TraceStorage = None   # type: ignore
_session_store: SessionStorage = None  # type: ignore


def get_trace_store() -> TraceStorage:
    global _trace_store
    if _trace_store is None:
        if settings.storage_backend == "postgresql":
            from app.mcp.core.storage.pg_store import PGTraceStore
            _trace_store = PGTraceStore()
        else:
            from app.mcp.core.storage.memory_store import MemoryTraceStore
            _trace_store = MemoryTraceStore()
    return _trace_store


def get_session_store() -> SessionStorage:
    global _session_store
    if _session_store is None:
        if settings.storage_backend == "postgresql":
            from app.mcp.core.storage.pg_store import PGSessionStore
            _session_store = PGSessionStore()
        else:
            from app.mcp.core.storage.memory_store import MemorySessionStore
            _session_store = MemorySessionStore()
    return _session_store
