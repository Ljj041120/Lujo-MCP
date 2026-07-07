"""追踪日志模块 —— 封装存储层的便捷 API"""

import time
import uuid

from app.mcp.core.storage.factory import get_trace_store


def create_request_id() -> str:
    return str(uuid.uuid4())


def add_log(request_id: str, step: str, data=None) -> None:
    store = get_trace_store()
    store.save_entry(request_id, {
        "timestamp": time.time(),
        "step": step,
        "data": data,
    })


def get_logs(request_id: str) -> list[dict]:
    store = get_trace_store()
    return store.get_entries(request_id)


def delete_logs(request_id: str) -> None:
    store = get_trace_store()
    store.delete(request_id)
