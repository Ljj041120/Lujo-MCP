"""
会话管理器。

轻量实现：内存字典 + TTL 过期清理。
用途：记录一次"调试会话"关联了哪些 trace_id / 工具调用历史，方便
   GET /api/debug/session 之类的接口查看当前活跃的调试会话有哪些。

注意：这是进程内内存结构，多 worker 部署时各进程互不可见。
如果之后要多 worker 横向扩展，需要换成 Redis/DB 存储 session。
单进程（stdio MCP server 场景本身就是单进程）下这个实现是够用的。
"""
import time
import uuid
from dataclasses import dataclass, field
from threading import Lock

from app.config import settings


@dataclass
class Session:
    session_id: str
    created_at: float
    last_active_at: float
    trace_ids: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = Lock()

    def create_session(self) -> Session:
        sid = uuid.uuid4().hex[:10]
        now = time.time()
        session = Session(session_id=sid, created_at=now, last_active_at=now)
        with self._lock:
            self._sessions[sid] = session
        return session

    def touch(self, session_id: str, trace_id: str | None = None, tool_name: str | None = None):
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return
            session.last_active_at = time.time()
            if trace_id:
                session.trace_ids.append(trace_id)
            if tool_name:
                session.tool_calls.append(tool_name)

    def list_active(self) -> list[Session]:
        self._cleanup_expired()
        with self._lock:
            return list(self._sessions.values())

    def _cleanup_expired(self):
        cutoff = time.time() - settings.session_ttl_seconds
        with self._lock:
            expired = [sid for sid, s in self._sessions.items() if s.last_active_at < cutoff]
            for sid in expired:
                del self._sessions[sid]


session_manager = SessionManager()
