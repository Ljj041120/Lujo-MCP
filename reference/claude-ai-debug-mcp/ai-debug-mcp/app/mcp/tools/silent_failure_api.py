"""
MCP 工具：ingest_silent_failure —— 接收浏览器 SDK 上报的前端静默失败。
"""
from app.mcp.collectors.network import network_records_from_extra
from app.mcp.collectors.ui_event import ui_events_from_extra
from app.mcp.core.logs import save_trace, save_network_record, save_ui_event
from app.schemas.trace import StackFrame


def tool_ingest_silent_failure(
    message: str,
    frames: list[dict] | None = None,
    ui_events: list[dict] | None = None,
    network_records: list[dict] | None = None,
    expectation: dict | None = None,
    source: str = "browser_sdk",
    extra: dict | None = None,
) -> dict:
    """保存一条前端静默失败 trace，同时把 UI 事件和网络请求关联入库。"""
    frames = frames or []
    ui_events = ui_events or []
    network_records = network_records or []
    expectation = expectation or {}
    extra = extra or {}

    # 保留原始数据在 extra 中，便于后续直接从 trace 解析
    extra["ui_events"] = ui_events
    extra["network_records"] = network_records
    extra["expectation"] = expectation

    stack_frames = [StackFrame(**f) for f in frames]
    trace_id = save_trace(
        exc_type="SilentFailure",
        message=message,
        frames=stack_frames,
        source=source,
        extra=extra,
        trace_kind="silent_failure",
    )

    # 把事件和网络记录独立入库，方便按 trace_id 查询
    for event in ui_events_from_extra(extra):
        save_ui_event(event, trace_id=trace_id)
    for record in network_records_from_extra(extra):
        save_network_record(record, trace_id=trace_id)

    return {"trace_id": trace_id, "saved": True}
