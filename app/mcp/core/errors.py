"""近期异常存储 —— 让全局异常钩子捕获的异常可被 MCP 工具检索。

全局异常钩子（exception_hook）捕获到的异常原本只打印到 stderr，
无法被 get_debug_context / list_recent_traces 等工具取回。
本模块用一个线程安全的有限容量双端队列，把捕获到的异常（含堆栈帧）
持久化在进程内存中，供调试工具检索。

M10 增强：指纹去重 + 聚合。相同 fingerprint（exc_type + 前3帧 file:function）
的异常累加 occurrence_count 并刷新 last_seen，避免重复错误刷屏，让 AI 看到频次。
按 proj1 架构重写（非复制 proj2 SQLite 逻辑）。
"""

import time
import uuid
import hashlib
import threading
from collections import deque

# 最多保留最近 200 条，超出丢弃最旧的
_MAX = 200
_recent: deque = deque(maxlen=_MAX)
_lock = threading.Lock()


def _new_id() -> str:
    return "err-" + uuid.uuid4().hex[:12]


def compute_fingerprint(exc_type: str, frames: list[dict]) -> str:
    """用异常类型 + 关键堆栈帧（file:function，忽略行号差异）算指纹。"""
    parts = [exc_type or "Unknown"]
    for f in (frames or [])[:3]:
        parts.append(f"{f.get('file', '')}:{f.get('function', '')}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def record(exc_data: dict, source: str = "unknown") -> str:
    """记录一条捕获到的异常，返回其 error_id。

    相同 fingerprint 的异常累加 occurrence_count 并刷新 last_seen，不新建记录。
    """
    frames = exc_data.get("frames", []) or []
    fingerprint = compute_fingerprint(exc_data.get("type"), frames)
    now = time.time()

    with _lock:
        # 从最新向最旧找同指纹记录
        for e in reversed(_recent):
            if e["fingerprint"] == fingerprint:
                e["occurrence_count"] += 1
                e["last_seen"] = now
                e["timestamp"] = now  # 向后兼容，等价于 last_seen
                e["message"] = exc_data.get("message") or e["message"]
                e["frames"] = frames or e["frames"]
                e["frame_count"] = len(e["frames"])
                e["source"] = source
                e["traceback"] = exc_data.get("traceback") or e["traceback"]
                return e["error_id"]

        err_id = _new_id()
        _recent.append({
            "error_id": err_id,
            "fingerprint": fingerprint,
            "source": source,
            "timestamp": now,
            "first_seen": now,
            "last_seen": now,
            "occurrence_count": 1,
            "type": exc_data.get("type"),
            "message": exc_data.get("message"),
            "frames": frames,
            "frame_count": len(frames),
            "traceback": exc_data.get("traceback"),
        })
        return err_id


def list_recent(limit: int = 10) -> list:
    """按 last_seen 倒序返回最近 limit 条。"""
    with _lock:
        items = list(_recent)
    items.sort(key=lambda e: e.get("last_seen", 0), reverse=True)
    return items[:limit]


def get_latest() -> dict | None:
    """返回 last_seen 最大的一条。"""
    with _lock:
        if not _recent:
            return None
        return max(_recent, key=lambda e: e.get("last_seen", 0))


def get_by_id(error_id: str) -> dict | None:
    with _lock:
        for e in _recent:
            if e["error_id"] == error_id:
                return e
    return None


def search(keyword: str, since_minutes: int = 30) -> list:
    """按关键字 + 时间窗（last_seen）搜索，倒序返回。"""
    keyword = (keyword or "").lower()
    cutoff = time.time() - since_minutes * 60
    with _lock:
        items = list(_recent)
    items.sort(key=lambda e: e.get("last_seen", 0), reverse=True)
    return [
        e for e in items
        if e.get("last_seen", e.get("timestamp", 0)) >= cutoff
        and (
            keyword in (e["type"] or "").lower()
            or keyword in (e["message"] or "").lower()
        )
    ]
