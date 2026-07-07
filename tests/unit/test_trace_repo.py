"""trace_repo 统一存取层单测"""
import pytest

from app.config import settings
from app.mcp.core import trace_repo


@pytest.fixture(autouse=True)
def _redaction_on():
    saved = settings.redaction_enabled
    settings.redaction_enabled = True
    yield
    settings.redaction_enabled = saved


def test_save_and_get_trace():
    frames = [{"file": "a.py", "line": 10, "function": "f"}]
    tid = trace_repo.save_trace("ValueError", "bad value", frames, source="ingest")
    assert tid

    got = trace_repo.get_trace(tid)
    assert got is not None
    assert got["trace_id"] == tid
    assert got["exc_type"] == "ValueError"
    assert got["message"] == "bad value"
    assert got["frames"] == frames
    assert got["trace_kind"] == "exception"
    assert got["source"] == "ingest"


def test_trace_kind_and_extra_persisted():
    tid = trace_repo.save_trace(
        "SilentFailure", "click no response", [],
        source="browser_sdk", extra={"expectation": "route_change"},
        trace_kind="silent_failure",
    )
    got = trace_repo.get_trace(tid)
    assert got["trace_kind"] == "silent_failure"
    assert got["extra"] == {"expectation": "route_change"}


def test_get_trace_none_when_missing():
    assert trace_repo.get_trace("does-not-exist") is None


def test_network_record_save_and_get():
    tid = trace_repo.save_trace("E", "m", [])
    rid = trace_repo.save_network_record(
        {"method": "GET", "url": "http://x/api", "status_code": 200, "duration_ms": 12.5},
        trace_id=tid,
    )
    records = trace_repo.get_network_records(tid)
    assert len(records) == 1
    assert records[0]["record_id"] == rid
    assert records[0]["method"] == "GET"
    assert records[0]["trace_id"] == tid
    assert records[0]["direction"] == "outbound"


def test_network_redaction_at_storage_boundary():
    tid = trace_repo.save_trace("E", "m", [])
    trace_repo.save_network_record(
        {"url": "http://x/?token=secret", "request_body": 'password = "pw"', "response_body": "ok"},
        trace_id=tid,
    )
    rec = trace_repo.get_network_records(tid)[0]
    assert "secret" not in rec["url"]
    assert "pw" not in rec["request_body"]
    assert rec["response_body"] == "ok"


def test_ui_event_save_and_get():
    tid = trace_repo.save_trace("E", "m", [])
    eid = trace_repo.save_ui_event(
        {"event_type": "click", "target_selector": "#btn", "route_path": "/page"},
        trace_id=tid,
    )
    events = trace_repo.get_ui_events(tid)
    assert len(events) == 1
    assert events[0]["event_id"] == eid
    assert events[0]["event_type"] == "click"
    assert events[0]["trace_id"] == tid


def test_ui_event_redaction_at_storage_boundary():
    tid = trace_repo.save_trace("E", "m", [])
    trace_repo.save_ui_event(
        {"event_type": "submit", "payload_json": 'password = "pw"'},
        trace_id=tid,
    )
    ev = trace_repo.get_ui_events(tid)[0]
    assert "pw" not in ev["payload_json"]


def test_network_and_ui_isolated_by_step():
    """同一 trace_id 下 network 与 ui_event 互不混入。"""
    tid = trace_repo.save_trace("E", "m", [])
    trace_repo.save_network_record({"url": "http://a"}, trace_id=tid)
    trace_repo.save_ui_event({"event_type": "click"}, trace_id=tid)
    assert len(trace_repo.get_network_records(tid)) == 1
    assert len(trace_repo.get_ui_events(tid)) == 1
