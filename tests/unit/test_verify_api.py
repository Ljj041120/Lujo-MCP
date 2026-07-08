"""单元测试：verify 工具"""
import pytest
from app.mcp.tools.verify_api import verify_handler
from app.mcp.verifier import spec_store


@pytest.fixture(autouse=True)
def _isolate_spec_store():
    spec_store.clear()
    yield
    spec_store.clear()


class TestVerifyWithInlineSpec:

    def test_match(self):
        result = verify_handler({
            "actual": {"status_code": 200, "body": {"name": "Alice"}},
            "spec": {
                "kind": "api",
                "target": "GET /api/user",
                "expect": {"status": 200, "body_rules": {"name": "Alice"}},
            },
        })
        assert result["matched"] is True
        assert result["diffs"] == []
        assert result["silent_failure"] is False

    def test_silent_failure(self):
        result = verify_handler({
            "actual": {"status_code": 200, "body": {"name": "Bob"}},
            "spec": {
                "kind": "api",
                "expect": {"body_rules": {"name": "Alice"}},
            },
        })
        assert result["matched"] is False
        assert result["silent_failure"] is True

    def test_trace_id_passthrough(self):
        result = verify_handler({
            "actual": {"status_code": 200, "body": {}},
            "spec": {"kind": "api", "expect": {"status": 200}},
            "trace_id": "trace-xyz",
        })
        assert result["trace_id"] == "trace-xyz"


class TestVerifyWithSpecId:

    def test_spec_id_found(self):
        spec_id = spec_store.create({
            "kind": "api",
            "target": "GET /api/user",
            "expect": {"status": 200, "body_rules": {"name": "Alice"}},
        })
        result = verify_handler({
            "actual": {"status_code": 200, "body": {"name": "Alice"}},
            "spec_id": spec_id,
        })
        assert result["matched"] is True
        assert result["spec_id"] == spec_id

    def test_spec_id_not_found(self):
        result = verify_handler({
            "actual": {"status_code": 200, "body": {}},
            "spec_id": "no-such-spec",
        })
        assert result["matched"] is False
        assert "not found" in result["error"]

    def test_spec_overrides_spec_id(self):
        """同时传 spec 和 spec_id 时，spec 优先"""
        spec_store.create({
            "id": "stored-spec",
            "kind": "api",
            "expect": {"status": 200},
        })
        result = verify_handler({
            "actual": {"status_code": 404, "body": {}},
            "spec": {"kind": "api", "expect": {"status": 404}},
            "spec_id": "stored-spec",
        })
        assert result["matched"] is True  # 用的是 inline spec


class TestVerifyErrors:

    def test_no_spec_no_spec_id(self):
        result = verify_handler({
            "actual": {"status_code": 200, "body": {}},
        })
        assert result["matched"] is False
        assert "must provide" in result["error"]

    def test_empty_actual(self):
        result = verify_handler({
            "actual": {},
            "spec": {"kind": "api", "expect": {"status": 200}},
        })
        assert result["matched"] is False
        assert result["diffs"][0]["field"] == "status_code"
        assert result["diffs"][0]["actual"] is None


class TestVerifyRegistration:

    def test_verify_registered(self):
        from app.mcp.protocol.server import _tool_registry
        from app.mcp.tools import register_all_tools
        register_all_tools()
        assert "verify" in _tool_registry
        tool = _tool_registry["verify"]
        assert callable(tool["handler"])
        assert tool["inputSchema"] is not None
