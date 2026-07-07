"""
网络请求记录解析器。

把前端 SDK 或外部系统上报的原始 dict 转换为 NetworkRecord，
并在入库前做截断与脱敏。
"""
import time

from app.mcp.core.redaction import redact
from app.schemas.context import NetworkRecord

_MAX_BODY_CHARS = 10 * 1024  # 10KB


def _truncate_body(text: str | None) -> str | None:
    if not text:
        return text
    if len(text) > _MAX_BODY_CHARS:
        return text[:_MAX_BODY_CHARS] + "\n...（已截断）"
    return text


def network_records_from_extra(extra: dict) -> list[NetworkRecord]:
    """从 extra['network_records'] 解析 NetworkRecord 列表，失败时返回空列表。"""
    raw_records = extra.get("network_records") if extra else None
    if not raw_records:
        return []

    records: list[NetworkRecord] = []
    for raw in raw_records:
        if not isinstance(raw, dict):
            continue
        try:
            body = _truncate_body(raw.get("response_body"))
            records.append(
                NetworkRecord(
                    record_id=raw.get("record_id"),
                    timestamp=raw.get("timestamp") or time.time(),
                    direction=raw.get("direction") or "outbound",
                    method=raw.get("method"),
                    url=redact(raw.get("url")),
                    status_code=raw.get("status_code"),
                    request_body=redact(_truncate_body(raw.get("request_body"))),
                    response_body=redact(body),
                    duration_ms=raw.get("duration_ms"),
                )
            )
        except Exception:
            continue
    return records
