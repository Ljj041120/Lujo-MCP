"""单元测试：Dashboard API 端点"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dashboard import router
from app.mcp.core import trace_repo
from app.mcp.tools.verify_api import verify_handler


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestDashboardStats:

    def test_stats_empty(self, client):
        """统计接口返回正确结构"""
        resp = client.get("/api/dashboard/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert "total_traces" in body
        assert "silent_failures" in body
        assert "exceptions" in body
        assert "spec_count" in body
        assert isinstance(body["total_traces"], int)
        assert isinstance(body["silent_failures"], int)
        assert isinstance(body["exceptions"], int)
        assert isinstance(body["spec_count"], int)

    def test_stats_with_traces(self, client):
        """有数据时统计正确"""
        trace_repo.save_trace("ValueError", "bad value", [
            {"file": "a.py", "line": 1, "function": "f"}
        ], source="test")
        trace_repo.save_trace("SilentFailure", "no response", [],
                              trace_kind="silent_failure", source="test")

        resp = client.get("/api/dashboard/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_traces"] >= 2


class TestDashboardTraces:

    def test_list_traces(self, client):
        trace_repo.save_trace("TypeError", "x is None", [
            {"file": "b.py", "line": 2, "function": "g"}
        ], source="test")

        resp = client.get("/api/dashboard/traces?limit=10")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert body["traces"][0]["type"] == "TypeError"
        assert "trace_id" in body["traces"][0]

    def test_trace_with_verify(self, client):
        """trace 含 verify 结果"""
        tid = trace_repo.save_trace("E", "m", [], source="test")
        verify_handler({
            "actual": {"status_code": 200, "body": {"name": "Bob"}},
            "spec": {"kind": "api", "expect": {"body_rules": {"name": "Alice"}}},
            "trace_id": tid,
        })

        resp = client.get("/api/dashboard/traces?limit=10")
        assert resp.status_code == 200
        body = resp.json()
        # 找对应的 trace
        found = [t for t in body["traces"] if t["trace_id"] == tid]
        assert len(found) == 1
        assert found[0]["verify_count"] == 1
        assert found[0]["has_silent_failure"] is True


class TestDashboardTraceDetail:

    def test_trace_detail(self, client):
        tid = trace_repo.save_trace("ValueError", "test error", [
            {"file": "app/config.py", "line": 9, "function": "Settings"}
        ], source="test")

        resp = client.get(f"/api/dashboard/trace/{tid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["trace_id"] == tid
        assert body["trace_kind"] == "exception"
        assert body["exception"]["type"] == "ValueError"

    def test_trace_detail_not_found(self, client):
        resp = client.get("/api/dashboard/trace/no-such-trace")
        assert resp.status_code == 404

    def test_trace_detail_with_spec_diffs(self, client):
        """detail 含 spec_diffs"""
        tid = trace_repo.save_trace("E", "m", [], source="test")
        verify_handler({
            "actual": {"status_code": 200, "body": {"ok": True}},
            "spec": {"kind": "api", "expect": {"body_rules": {"ok": False}}},
            "trace_id": tid,
        })

        resp = client.get(f"/api/dashboard/trace/{tid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["spec_diffs"] is not None
        assert len(body["spec_diffs"]) == 1
        assert body["spec_diffs"][0]["silent_failure"] is True
