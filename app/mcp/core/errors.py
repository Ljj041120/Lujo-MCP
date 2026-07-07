"""近期异常存储 —— 让全局异常钩子捕获的异常可被 MCP 工具检索。

全局异常钩子（exception_hook）捕获到的异常原本只打印到 stderr，
无法被 get_debug_context / list_recent_traces 等工具取回。
本模块用一个线程安全的有限容量双端队列，把捕获到的异常（含堆栈帧）
持久化在进程内存中，供调试工具检索。
"""

import time
import uuid
import threading
from collections import deque

# 最多保留最近 200 条，超出丢弃最旧的
_MAX = 200
_recent: deque = deque(maxlen=_MAX)
_lock = threading.Lock()


def _new_id() -> str:
    return "err-" + uuid.uuid4().hex[:12]


def record(exc_data: dict, source: str = "unknown") -> str:
    """记录一条捕获到的异常，返回其 error_id。"""
    err_id = _new_id()
    entry = {
        "error_id": err_id,
        "source": source,
        "timestamp": time.time(),
        "type": exc_data.get("type"),
        "message": exc_data.get("message"),
        "frames": exc_data.get("frames", []),
        "frame_count": len(exc_data.get("frames", [])),
        "traceback": exc_data.get("traceback"),
    }
    with _lock:
        _recent.append(entry)
    return err_id


def list_recent(limit: int = 10) -> list:
    with _lock:
        items = list(_recent)
    items.reverse()
    return items[:limit]


def get_latest() -> dict | None:
    with _lock:
        return _recent[-1] if _recent else None


def get_by_id(error_id: str) -> dict | None:
    with _lock:
        for e in reversed(_recent):
            if e["error_id"] == error_id:
                return e
    return None


def search(keyword: str, since_minutes: int = 30) -> list:
    keyword = (keyword or "").lower()
    cutoff = time.time() - since_minutes * 60
    with _lock:
        items = list(_recent)
    items.reverse()
    return [
        e
        for e in items
        if e["timestamp"] >= cutoff
        and (
            keyword in (e["type"] or "").lower()
            or keyword in (e["message"] or "").lower()
        )
    ]
