"""
MCP 工具：网络请求追踪 —— 查询/上报与某条 trace 相关的网络记录。
"""
import time

from app.mcp.core.logs import get_network_records, save_network_record
from app.mcp.core.redaction import redact
from app.schemas.context import NetworkRecord


def tool_ingest_network(
    record: dict,
    trace_id: str | None = None,
    request_id: str | None = None,
) -> dict:
    """单条网络记录上报，返回 record_id。"""
    network_record = NetworkRecord(
        record_id=record.get("record_id"),
        timestamp=record.get("timestamp") or time.time(),
        direction=record.get("direction") or "outbound",
        method=record.get("method"),
        url=redact(record.get("url")),
        status_code=record.get("status_code"),
        request_body=redact(record.get("request_body")),
        response_body=redact(record.get("response_body")),
        duration_ms=record.get("duration_ms"),
    )
    record_id = save_network_record(network_record, trace_id=trace_id, request_id=request_id)
    return {"record_id": record_id, "saved": True}


def tool_get_network_trace(trace_id: str) -> dict:
    """查询指定 trace_id 关联的所有网络请求记录。"""
    records = get_network_records(trace_id)
    return {
        "found": bool(records),
        "count": len(records),
        "records": [r.model_dump() for r in records],
    }
