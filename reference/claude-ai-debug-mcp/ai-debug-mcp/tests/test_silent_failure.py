"""
测试前端静默失败链路：上报、存储、上下文注入。
"""
from app.mcp.builders.context import build_debug_context
from app.mcp.core.logs import get_network_records, get_trace, get_ui_events
from app.mcp.tools.silent_failure_api import tool_ingest_silent_failure


def test_ingest_silent_failure_returns_trace_id(fresh_db):
    result = tool_ingest_silent_failure(
        message="点击 .submit-btn 后未跳转",
        frames=[{"file": "src/views/Order.vue", "line": 42, "function": "submitOrder"}],
    )
    assert result["saved"]
    trace_id = result["trace_id"]
    assert trace_id

    entry = get_trace(trace_id)
    assert entry.trace_kind == "silent_failure"
    assert entry.exc_type == "SilentFailure"


def test_silent_failure_stores_ui_events_and_network_records(fresh_db):
    result = tool_ingest_silent_failure(
        message="点击 .submit-btn 后未跳转",
        frames=[{"file": "src/views/Order.vue", "line": 42, "function": "submitOrder"}],
        ui_events=[{"event_type": "click", "target_selector": ".submit-btn"}],
        network_records=[{"direction": "outbound", "method": "POST", "url": "/api/order", "status_code": 200}],
        expectation={"type": "route_change", "to": "/success", "withinMs": 2000},
    )
    trace_id = result["trace_id"]

    ui_events = get_ui_events(trace_id)
    assert len(ui_events) == 1
    assert ui_events[0].event_type == "click"
    assert ui_events[0].target_selector == ".submit-btn"

    network_records = get_network_records(trace_id)
    assert len(network_records) == 1
    assert network_records[0].method == "POST"
    assert network_records[0].status_code == 200


def test_debug_context_for_silent_failure_includes_ui_events_and_network_trace(fresh_db):
    result = tool_ingest_silent_failure(
        message="点击 .submit-btn 后未跳转",
        frames=[{"file": "src/views/Order.vue", "line": 42, "function": "submitOrder"}],
        ui_events=[{"event_type": "click", "target_selector": ".submit-btn"}],
        network_records=[{"direction": "outbound", "method": "POST", "url": "/api/order", "status_code": 200}],
    )
    trace_id = result["trace_id"]

    context = build_debug_context(trace_id)
    assert context is not None
    assert context.ui_events is not None
    assert len(context.ui_events) == 1
    assert context.network_trace is not None
    assert len(context.network_trace) == 1


def test_regular_exception_context_has_no_ui_events(fresh_db):
    from app.mcp.core.logs import save_trace
    from app.schemas.trace import StackFrame

    trace_id = save_trace(
        exc_type="ValueError",
        message="something wrong",
        frames=[StackFrame(file="main.py", line=1, function="run")],
    )
    context = build_debug_context(trace_id)
    assert context.ui_events is None
    assert context.network_trace is None
