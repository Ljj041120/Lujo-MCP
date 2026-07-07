"""
前端 UI 事件解析器。

把浏览器 SDK 上报的原始 dict 转换为 UIEvent，并在入库前脱敏 payload。
"""
import time

from app.mcp.core.redaction import redact
from app.schemas.context import UIEvent


def ui_events_from_extra(extra: dict) -> list[UIEvent]:
    """从 extra['ui_events'] 解析 UIEvent 列表，失败时返回空列表。"""
    raw_events = extra.get("ui_events") if extra else None
    if not raw_events:
        return []

    events: list[UIEvent] = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        try:
            events.append(
                UIEvent(
                    event_id=raw.get("event_id"),
                    timestamp=raw.get("timestamp") or time.time(),
                    event_type=raw.get("event_type") or "click",
                    target_selector=raw.get("target_selector"),
                    component_name=raw.get("component_name"),
                    route_path=redact(raw.get("route_path")),
                    payload_json=redact(raw.get("payload_json")),
                )
            )
        except Exception:
            continue
    return events
