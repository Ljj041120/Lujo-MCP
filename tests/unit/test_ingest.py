"""ingest_error 跨语言上报工具 + /ingest/error 路由单测"""
import pytest

from app.config import settings
from app.mcp.tools import ingest_api
from app.mcp.core import trace_repo


@pytest.fixture(autouse=True)
def _redaction_on():
    saved = settings.redaction_enabled
    settings.redaction_enabled = True
    yield
    settings.redaction_enabled = saved


def test_ingest_error_persists_trace():
    res = ingest_api.tool_ingest_error(
        exc_type="NullPointerException",
        message="cannot read property x of undefined",
        frames=[
            {"file": "AuthService.js", "line": 42, "function": "login", "code_context": "user.x"},
            {"no_file": True},  # 丢弃
        ],
        source="node_service",
        extra={"runtime": "node20"},
    )
    assert res["saved"] is True
    assert res["frame_count"] == 1  # 无效帧被丢弃

    trace = trace_repo.get_trace(res["trace_id"])
    assert trace["exc_type"] == "NullPointerException"
    assert trace["source"] == "node_service"
    assert trace["trace_kind"] == "exception"
    assert trace["extra"] == {"runtime": "node20"}
    assert trace["frames"][0]["file"] == "AuthService.js"


def test_ingest_error_redacts_message():
    res = ingest_api.tool_ingest_error(
        exc_type="Error",
        message='failed with password = "secret123"',
        frames=[],
    )
    trace = trace_repo.get_trace(res["trace_id"])
    assert "secret123" not in trace["message"]
    assert "***" in trace["message"]


def test_ingest_error_minimal():
    res = ingest_api.tool_ingest_error(exc_type="Err", message="boom")
    assert res["saved"] is True
    assert res["frame_count"] == 0
    trace = trace_repo.get_trace(res["trace_id"])
    assert trace["exc_type"] == "Err"


def test_ingest_error_route_via_testclient():
    """端到端：/ingest/error 路由可用且落库。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.ingest import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.post("/ingest/error", json={
        "exc_type": "TypeError",
        "message": "x is undefined",
        "frames": [{"file": "a.js", "line": 10, "function": "f"}],
        "source": "test",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["saved"] is True
    assert body["frame_count"] == 1

    trace = trace_repo.get_trace(body["trace_id"])
    assert trace["exc_type"] == "TypeError"


def test_ingest_error_route_missing_fields_defaults():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.ingest import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # 只传 message，exc_type 缺省为 UnknownError
    resp = client.post("/ingest/error", json={"message": "something broke"})
    assert resp.status_code == 200
    assert resp.json()["saved"] is True
