"""
统一 trace 存取层 —— 在现有 TraceStorage + errors 近期缓冲之上，
重新实现 proj2 的 save_trace / get_trace / save_network_record /
get_network_records / save_ui_event / get_ui_events 等接口。

设计原则（按 proj1 架构，非复制 proj2 SQLite）：
- 不引入新存储后端，不修改 TraceStorage/SessionStorage 抽象与 MemoryStore/PGStore。
- trace 记录（异常帧）复用 errors.record / get_by_id（近期异常缓冲），
  使 ingest 的错误也出现在既有 list_recent_traces / search_logs 工具里。
- network / ui_event 作为带特殊 step（"network" / "ui_event"）的 trace 条目，
  复用 logs.add_log / get_logs —— Memory 与 PG 后端零改动即可用。
- 脱敏在存储边界统一执行（url/body/payload/message），落实 redaction "写入存储前统一处理"。
"""
import time
import uuid
import logging
from typing import Optional

from app.mcp.core.logs import add_log, get_logs
from app.mcp.core.errors import record as _record_error, get_by_id as _get_error, get_latest as _get_latest
from app.mcp.core.redaction import redact

logger = logging.getLogger("ai-debug-mcp.trace_repo")

# trace 条目 step 命名
_STEP_META = "trace_meta"      # trace_kind / extra 元信息
_STEP_NETWORK = "network"
_STEP_UI = "ui_event"
_STEP_CONSOLE = "console"


def _new_id(prefix: str = "rec") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ── trace（异常/静默失败）──
def save_trace(
    exc_type: str,
    message: str,
    frames: list[dict],
    source: str = "ingest",
    extra: Optional[dict] = None,
    trace_kind: str = "exception",
) -> str:
    """保存一条 trace（复用 errors 近期缓冲），返回 trace_id。

    trace_id 同时作为 network / ui_event 的关联键。
    """
    extra = extra or {}
    frames = frames or []
    exc_data = {
        "type": exc_type,
        "message": redact(message) or "",
        "frames": frames,
        "traceback": "",
        "frame_count": len(frames),
    }
    trace_id = _record_error(exc_data, source=source)

    # 非 exception 或携带 extra 时，把元信息落到 TraceStorage 便于 get_trace 合并
    if trace_kind != "exception" or extra:
        try:
            add_log(trace_id, _STEP_META, {
                "trace_kind": trace_kind,
                "extra": extra,
                "ts": time.time(),
            })
        except Exception:
            logger.exception("写入 trace_meta 失败 (trace_id=%s)", trace_id)

    return trace_id


def get_trace(trace_id: Optional[str] = None) -> Optional[dict]:
    """取指定 trace_id，不传则取最新一条。"""
    err = _get_error(trace_id) if trace_id else _get_latest()
    if not err:
        return None

    meta = {}
    try:
        for entry in get_logs(err["error_id"]):
            if entry.get("step") == _STEP_META:
                meta = entry.get("data") or {}
                break
    except Exception:
        pass

    return {
        "trace_id": err["error_id"],
        "timestamp": err["timestamp"],
        "exc_type": err["type"],
        "message": err["message"],
        "frames": err["frames"],
        "frame_count": err["frame_count"],
        "source": err["source"],
        "fingerprint": err.get("fingerprint"),
        "occurrence_count": err.get("occurrence_count", 1),
        "first_seen": err.get("first_seen", err["timestamp"]),
        "last_seen": err.get("last_seen", err["timestamp"]),
        "trace_kind": meta.get("trace_kind", "exception"),
        "extra": meta.get("extra", {}),
    }


# ── network ──
def save_network_record(
    record: dict,
    trace_id: Optional[str] = None,
    request_id: Optional[str] = None,
    extra: Optional[dict] = None,
) -> str:
    """保存一条网络请求记录，返回 record_id。

    存储边界统一脱敏 url / request_body / response_body。
    """
    key = trace_id or request_id or _new_id("net")
    record_id = record.get("record_id") or _new_id("net")
    payload = dict(record)
    payload["record_id"] = record_id
    payload["trace_id"] = trace_id
    payload["request_id"] = request_id
    payload["timestamp"] = payload.get("timestamp") or time.time()
    payload["direction"] = payload.get("direction") or "outbound"
    # 入库前脱敏
    payload["url"] = redact(payload.get("url"))
    payload["request_body"] = redact(payload.get("request_body"))
    payload["response_body"] = redact(payload.get("response_body"))
    if extra:
        payload["extra"] = extra

    add_log(key, _STEP_NETWORK, payload)
    return record_id


def get_network_records(trace_id: str) -> list[dict]:
    """查询与某 trace 关联的所有网络请求记录（按时间顺序）。"""
    return [e["data"] for e in get_logs(trace_id) if e.get("step") == _STEP_NETWORK]


# ── ui_event ──
def save_ui_event(
    event: dict,
    trace_id: Optional[str] = None,
    extra: Optional[dict] = None,
) -> str:
    """保存一条前端 UI 事件，返回 event_id。

    存储边界统一脱敏 route_path / payload_json。
    """
    key = trace_id or _new_id("ui")
    event_id = event.get("event_id") or _new_id("ui")
    payload = dict(event)
    payload["event_id"] = event_id
    payload["trace_id"] = trace_id
    payload["timestamp"] = payload.get("timestamp") or time.time()
    payload["event_type"] = payload.get("event_type") or "click"
    # 入库前脱敏
    payload["route_path"] = redact(payload.get("route_path"))
    payload["payload_json"] = redact(payload.get("payload_json"))
    if extra:
        payload["extra"] = extra

    add_log(key, _STEP_UI, payload)
    return event_id


def get_ui_events(trace_id: str) -> list[dict]:
    """查询与某 trace 关联的所有 UI 事件（按时间顺序）。"""
    return [e["data"] for e in get_logs(trace_id) if e.get("step") == _STEP_UI]


# ── console ──
def save_console_log(
    level: str = "info",
    message: str = "",
    source: str = "browser_sdk",
    extra: Optional[dict] = None,
    trace_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> str:
    """保存一条控制台日志，返回 record_id。

    存储边界统一脱敏 message。
    """
    key = trace_id or request_id or _new_id("console")
    record_id = _new_id("console")
    payload = {
        "record_id": record_id,
        "trace_id": trace_id,
        "request_id": request_id,
        "timestamp": time.time(),
        "level": level or "info",
        "message": redact(message or ""),
        "source": source,
    }
    if extra:
        payload["extra"] = extra

    add_log(key, _STEP_CONSOLE, payload)
    return record_id


def get_console_logs(trace_id: str) -> list[dict]:
    """查询与某 trace 关联的所有控制台日志（按时间顺序）。"""
    return [e["data"] for e in get_logs(trace_id) if e.get("step") == _STEP_CONSOLE]
