"""silent_failure 工具 + inbound 网络采集中间件单测"""
import pytest

from app.config import settings
from app.mcp.tools import silent_failure_api
from app.mcp.core import trace_repo


@pytest.fixture(autouse=True)
def _redaction_on():
    saved = settings.redaction_enabled
    settings.redaction_enabled = True
    yield
    settings.redaction_enabled = saved


def test_ingest_silent_failure_persists_trace_and_links():
    res = silent_failure_api.tool_ingest_silent_failure(
        message="点击 #submit 后未跳转",
        frames=[{"file": "App.tsx", "line": 42, "function": "onClick"}],
        ui_events=[
            {"event_type": "click", "target_selector": "#submit", "route_path": "/page"},
            {"event_type": "route_change", "route_path": "/page"},
        ],
        network_records=[
            {"method": "post", "url": "http://x/api/submit", "status_code": 200, "duration_ms": 30},
        ],
        expectation={"type": "route_change", "to": "/done", "withinMs": 1000},
        source="browser_sdk",
    )
    assert res["saved"] is True
    assert res["ui_events"] == 2
    assert res["network_records"] == 1
    tid = res["trace_id"]

    # trace_kind 与 extra 落库
    trace = trace_repo.get_trace(tid)
    assert trace["trace_kind"] == "silent_failure"
    assert trace["exc_type"] == "SilentFailure"
    assert trace["extra"]["expectation"]["to"] == "/done"

    # UI 事件链关联
    events = trace_repo.get_ui_events(tid)
    assert len(events) == 2
    assert events[0]["event_type"] == "click"

    # 网络请求链关联
    records = trace_repo.get_network_records(tid)
    assert len(records) == 1
    assert records[0]["method"] == "POST"


def test_ingest_silent_failure_redacts_linked_records():
    res = silent_failure_api.tool_ingest_silent_failure(
        message="silent",
        ui_events=[{"event_type": "click", "payload_json": 'password = "pw"'}],
        network_records=[{"url": "http://x/?token=secret"}],
    )
    tid = res["trace_id"]
    assert "pw" not in trace_repo.get_ui_events(tid)[0]["payload_json"]
    assert "secret" not in trace_repo.get_network_records(tid)[0]["url"]


def test_ingest_silent_failure_drops_invalid_frames():
    res = silent_failure_api.tool_ingest_silent_failure(
        message="m",
        frames=[{"file": "ok.py", "line": 1}, {"no_file": True}, {"file": "x", "line": "bad"}],
    )
    trace = trace_repo.get_trace(res["trace_id"])
    assert len(trace["frames"]) == 1
    assert trace["frames"][0]["file"] == "ok.py"


def test_ingest_silent_failure_minimal_message_only():
    res = silent_failure_api.tool_ingest_silent_failure(message="just a message")
    assert res["saved"] is True
    assert res["ui_events"] == 0
    assert res["network_records"] == 0
    trace = trace_repo.get_trace(res["trace_id"])
    assert trace["trace_kind"] == "silent_failure"


# ── inbound 网络采集中间件（经 TestClient）──
def test_network_capture_middleware_records_inbound():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.middleware_network import NetworkCaptureMiddleware

    settings.network_capture_enabled = True
    try:
        app = FastAPI()

        @app.get("/ping")
        def ping():
            return {"ok": True}

        app.add_middleware(NetworkCaptureMiddleware)
        client = TestClient(app)

        resp = client.get("/ping")
        assert resp.status_code == 200
        rid = resp.headers.get("X-Debug-Request-Id")
        assert rid and rid.startswith("inbound-")

        records = trace_repo.get_network_records(rid)
        assert len(records) == 1
        assert records[0]["direction"] == "inbound"
        assert records[0]["method"] == "GET"
        assert records[0]["status_code"] == 200
    finally:
        settings.network_capture_enabled = False


def test_network_capture_middleware_disabled_by_default():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.middleware_network import NetworkCaptureMiddleware

    settings.network_capture_enabled = False
    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    app.add_middleware(NetworkCaptureMiddleware)
    client = TestClient(app)

    resp = client.get("/ping")
    assert resp.status_code == 200
    assert "X-Debug-Request-Id" not in resp.headers  # 关闭时不记录
