"""单元测试：JSON-RPC 协议"""
import pytest
from app.mcp.protocol.jsonrpc import (
    parse_request,
    make_response,
    make_error,
    JSONRPCRequest,
    METHOD_NOT_FOUND,
)


class TestJSONRPC:

    def test_parse_valid_request(self):
        raw = '{"jsonrpc":"2.0","id":1,"method":"ping","params":{}}'
        req = parse_request(raw)
        assert req.jsonrpc == "2.0"
        assert req.id == 1
        assert req.method == "ping"

    def test_parse_invalid_json(self):
        with pytest.raises(ValueError):
            parse_request("{invalid}")

    def test_make_response(self):
        resp = make_response(1, {"status": "ok"})
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        assert resp["result"] == {"status": "ok"}

    def test_make_error(self):
        resp = make_error(None, METHOD_NOT_FOUND, "Method not found")
        assert resp["jsonrpc"] == "2.0"
        assert resp["error"]["code"] == METHOD_NOT_FOUND
        assert "Method not found" in resp["error"]["message"]


class TestMCPServerDispatch:

    def test_dispatch_initialize(self):
        from app.mcp.protocol.server import dispatch
        req = JSONRPCRequest(jsonrpc="2.0", id=1, method="initialize", params={})
        resp = dispatch(req)
        assert resp["id"] == 1
        assert "protocolVersion" in resp["result"]
        assert "capabilities" in resp["result"]

    def test_dispatch_unknown_method(self):
        from app.mcp.protocol.server import dispatch
        req = JSONRPCRequest(jsonrpc="2.0", id=1, method="nonexistent", params={})
        resp = dispatch(req)
        assert "error" in resp
        assert resp["error"]["code"] == METHOD_NOT_FOUND

    def test_dispatch_ping(self):
        from app.mcp.protocol.server import dispatch
        req = JSONRPCRequest(jsonrpc="2.0", id=0, method="ping")
        resp = dispatch(req)
        assert resp["id"] == 0
        assert "error" not in resp
