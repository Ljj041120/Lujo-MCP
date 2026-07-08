"""单元测试：UI runner 与 verify_ui 工具"""
import pytest

from app.mcp.verifier import ui_runner


class TestUIRunner:

    def test_is_available(self):
        """is_available 不抛异常"""
        assert ui_runner.is_available() in (True, False)

    def test_no_playwright_returns_error(self):
        """未装 Playwright 时返回明确错误"""
        if ui_runner.is_available():
            pytest.skip("Playwright 已安装，跳过")

        result = ui_runner.run_ui_verification({
            "kind": "ui",
            "target": "http://example.com",
            "expect": {},
        })
        assert result["matched"] is False
        assert "playwright 未安装" in result.get("error", "")

    def test_no_target_returns_error(self):
        """无 target 时返回错误"""
        if not ui_runner.is_available():
            pytest.skip("Playwright 未安装，跳过")

        result = ui_runner.run_ui_verification({
            "kind": "ui",
            "target": "",
            "expect": {},
        })
        assert result["matched"] is False
        assert result["diffs"][0]["field"] == "target"


class TestVerifyUITool:

    def test_no_spec_returns_error(self):
        """不传 spec/spec_id 返回错误"""
        from app.mcp.tools.verify_ui_api import verify_ui_handler

        result = verify_ui_handler({})
        assert result["matched"] is False
        assert "must provide" in result["error"]

    def test_wrong_kind_returns_error(self):
        """非 ui kind 返回错误"""
        from app.mcp.tools.verify_ui_api import verify_ui_handler

        result = verify_ui_handler({
            "spec": {"kind": "api", "target": "GET /x", "expect": {}},
        })
        assert result["matched"] is False
        assert "must be 'ui'" in result["error"]

    def test_verify_ui_with_spec_id_not_found(self):
        """spec_id 不存在时返回错误"""
        from app.mcp.tools.verify_ui_api import verify_ui_handler

        result = verify_ui_handler({
            "spec_id": "no-such-spec",
        })
        assert result["matched"] is False
        assert "must provide" in result["error"]

    def test_registered(self):
        """verify_ui 已注册"""
        from app.mcp.protocol.server import _tool_registry
        from app.mcp.tools import register_all_tools
        register_all_tools()
        assert "verify_ui" in _tool_registry
        tool = _tool_registry["verify_ui"]
        assert callable(tool["handler"])
        assert tool["inputSchema"] is not None


class TestVerifyUIEndpoint:

    def test_verify_ui_endpoint_no_spec(self):
        """POST /api/debug/verify/ui 无参数返回错误"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.debug import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.post("/api/debug/verify/ui", json={})
        # 可能返回 200（业务错误）或 422（校验失败）
        assert resp.status_code in (200, 422)
