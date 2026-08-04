"""单元测试：修复上下文装配器（context_assembler.py）。

覆盖：三个子装配并发执行、各自失败静默降级、sources 结构正确。
"""

import pytest
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.context_assembler import RepairContextAssembler


@pytest.fixture
def assembler():
    return RepairContextAssembler()


def _make_debug_context():
    """构造一个含 exception.frames 的 debug context。"""
    return {
        "request_id": "r1",
        "exception": {
            "type": "ValueError",
            "message": "bad input",
            "frames": [
                {"file": "/app/foo.py", "line": 42, "function": "bar"},
                {"file": "/app/baz.py", "line": 10, "function": "qux"},
            ],
        },
    }


class TestAssembleSuccess:
    """三个子装配都成功 → 完整 sources 结构。"""

    @pytest.mark.asyncio
    async def test_assemble_returns_all_fields(self, assembler):
        ctx = _make_debug_context()
        fake_analysis = {
            "analysis": {"root_cause": "x"},
            "knowledge_base_hit": True,
        }
        fake_vector = [{"fingerprint": "fp1", "analysis": {"root_cause": "similar"}}]
        fake_git_entry = {"file": "/app/foo.py", "diff": "--- a\n+++ b\n"}

        with patch(
            "app.llm.analyzer.analyze_async", return_value=fake_analysis
        ), patch(
            "app.rag.knowledge_base.retrieve_similar", return_value=fake_vector
        ), patch(
            "app.mcp.core.git.get_recent_diff", return_value=fake_git_entry
        ):
            result = await assembler.assemble(ctx)

        assert result["debug_context"] == ctx
        assert result["prior_analysis"] == fake_analysis
        assert result["vector_recall"] == fake_vector
        # debug_context 有 2 帧，git_context 应有 2 个条目（每帧拉一次 diff）
        assert len(result["git_context"]) == 2
        assert all(entry == fake_git_entry for entry in result["git_context"])
        assert result["sources"]["knowledge_base_hit"] is True
        assert result["sources"]["vector_recall"] == fake_vector
        assert len(result["sources"]["git_context"]) == 2


class TestAnalysisDegradation:
    """analyze_async 失败 → prior_analysis=None，继续。"""

    @pytest.mark.asyncio
    async def test_analysis_failure_degrades_to_none(self, assembler):
        ctx = _make_debug_context()

        with patch(
            "app.llm.analyzer.analyze_async", side_effect=RuntimeError("LLM down")
        ), patch(
            "app.rag.knowledge_base.retrieve_similar", return_value=[]
        ), patch(
            "app.mcp.core.git.get_recent_diff", return_value=None
        ):
            result = await assembler.assemble(ctx)

        assert result["prior_analysis"] is None
        assert result["vector_recall"] == []
        assert result["git_context"] == []
        # knowledge_base_hit 应为 False（analysis 为 None）
        assert result["sources"]["knowledge_base_hit"] is False

    @pytest.mark.asyncio
    async def test_analysis_disabled_via_flag(self, assembler, monkeypatch):
        """agent_prior_analysis_enabled=False 时跳过 analyze_async。"""
        monkeypatch.setattr(
            "app.config.settings.agent_prior_analysis_enabled", False
        )
        ctx = _make_debug_context()

        with patch(
            "app.llm.analyzer.analyze_async", side_effect=AssertionError("should not be called")
        ), patch(
            "app.rag.knowledge_base.retrieve_similar", return_value=[]
        ), patch(
            "app.mcp.core.git.get_recent_diff", return_value=None
        ):
            result = await assembler.assemble(ctx)

        assert result["prior_analysis"] is None


class TestVectorRecallDegradation:
    """retrieve_similar 失败 → vector_recall=[]，继续。"""

    @pytest.mark.asyncio
    async def test_vector_failure_degrades_to_empty(self, assembler):
        ctx = _make_debug_context()

        with patch(
            "app.llm.analyzer.analyze_async", return_value=None
        ), patch(
            "app.rag.knowledge_base.retrieve_similar", side_effect=RuntimeError("qdrant down")
        ), patch(
            "app.mcp.core.git.get_recent_diff", return_value=None
        ):
            result = await assembler.assemble(ctx)

        assert result["vector_recall"] == []
        assert result["sources"]["vector_recall"] == []


class TestGitContextDegradation:
    """get_recent_diff 失败 → git_context=[]，继续。"""

    @pytest.mark.asyncio
    async def test_git_failure_degrades_to_empty(self, assembler):
        ctx = _make_debug_context()

        with patch(
            "app.llm.analyzer.analyze_async", return_value=None
        ), patch(
            "app.rag.knowledge_base.retrieve_similar", return_value=[]
        ), patch(
            "app.mcp.core.git.get_recent_diff", side_effect=RuntimeError("git timeout")
        ):
            result = await assembler.assemble(ctx)

        assert result["git_context"] == []

    @pytest.mark.asyncio
    async def test_git_only_first_3_frames(self, assembler):
        """git 上下文只拉前 3 帧，避免串行 git 调用拖慢。"""
        ctx = {
            "request_id": "r1",
            "exception": {
                "frames": [
                    {"file": f"/app/f{i}.py", "line": i, "function": "fn"}
                    for i in range(5)
                ]
            },
        }

        call_count = {"n": 0}

        def fake_get_recent_diff(file_path, commits_back=3):
            call_count["n"] += 1
            return {"file": file_path, "diff": "..."}

        with patch(
            "app.llm.analyzer.analyze_async", return_value=None
        ), patch(
            "app.rag.knowledge_base.retrieve_similar", return_value=[]
        ), patch(
            "app.mcp.core.git.get_recent_diff", side_effect=fake_get_recent_diff
        ):
            result = await assembler.assemble(ctx)

        assert call_count["n"] == 3
        assert len(result["git_context"]) == 3


class TestEmptyContext:
    """空 debug context（无 exception）不崩溃。"""

    @pytest.mark.asyncio
    async def test_no_exception_field(self, assembler):
        ctx = {"request_id": "r1"}  # 无 exception

        with patch(
            "app.llm.analyzer.analyze_async", return_value=None
        ), patch(
            "app.rag.knowledge_base.retrieve_similar", return_value=[]
        ), patch(
            "app.mcp.core.git.get_recent_diff", return_value=None
        ):
            result = await assembler.assemble(ctx)

        assert result["git_context"] == []
        assert result["debug_context"] == ctx


class TestStaticAnalysis:
    """M3：函数级静态分析（有堆栈帧 → analyze；无堆栈帧 → URL→handler 反查）。"""

    @pytest.mark.asyncio
    async def test_no_frames_returns_empty_without_url(self, assembler):
        """无堆栈帧且 URL 反查不到 → fault_locations 为空。"""
        ctx = {
            "request_id": "r1",
            "exception": {"type": "ValueError", "message": "bad"},
            "input": {"method": "GET", "path": "/api/nonexistent"},
        }
        with patch(
            "app.llm.analyzer.analyze_async", return_value=None
        ), patch(
            "app.rag.knowledge_base.retrieve_similar", return_value=[]
        ), patch(
            "app.mcp.core.git.get_recent_diff", return_value=None
        ), patch(
            "app.mcp.collectors.url_resolver.resolve_from_debug_context",
            return_value=None,
        ):
            result = await assembler.assemble(ctx)

        assert result["fault_locations"] == []
        assert result["sources"]["fault_locations"] is False

    @pytest.mark.asyncio
    async def test_no_frames_url_fallback_populates_fault_locations(self, assembler):
        """无堆栈帧 → URL 反查 handler → analyze_handler 产出 fault_locations。"""
        ctx = {
            "request_id": "r1",
            "exception": {"type": "ValueError", "message": "bad"},
            "input": {"method": "GET", "path": "/api/users/1"},
        }
        handler_info = SimpleNamespace(
            module_path="/tmp/app/api/users.py",
            function_name="get_user",
            approx_line=1,
            route_path="/api/users/{user_id}",
            methods=["GET"],
            module_dot_path="app.api.users",
        )
        with patch(
            "app.llm.analyzer.analyze_async", return_value=None
        ), patch(
            "app.rag.knowledge_base.retrieve_similar", return_value=[]
        ), patch(
            "app.mcp.core.git.get_recent_diff", return_value=None
        ), patch(
            "app.mcp.collectors.url_resolver.resolve_from_debug_context",
            return_value=handler_info,
        ), patch(
            "app.mcp.collectors.static_analyzer.analyze_handler",
            return_value=SimpleNamespace(
                file="/tmp/app/api/users.py",
                function="get_user",
                line_number=2,
                function_info=None,
                call_chain=["get_user"],
                suspicious_inputs=[],
            ),
        ):
            result = await assembler.assemble(ctx)

        assert len(result["fault_locations"]) == 1
        assert result["sources"]["fault_locations"] is True
        item = result["fault_locations"][0]
        assert item["function"] == "get_user"
        assert item["file"] == "/tmp/app/api/users.py"
        # _handler_meta 附带路由匹配信息
        assert item["_handler_meta"]["route_path"] == "/api/users/{user_id}"
        assert item["_handler_meta"]["methods"] == ["GET"]
        assert item["_handler_meta"]["module_dot_path"] == "app.api.users"

    @pytest.mark.asyncio
    async def test_frames_use_static_analyzer(self, assembler):
        """有堆栈帧 → 走 static_analyzer.analyze（不调用 url_resolver）。"""
        ctx = _make_debug_context()  # 含 exception.frames
        fault_dict = {
            "file": "/app/foo.py",
            "function": "bar",
            "line_number": 42,
            "function_info": None,
            "call_chain": ["bar"],
            "suspicious_inputs": [],
        }
        with patch(
            "app.llm.analyzer.analyze_async", return_value=None
        ), patch(
            "app.rag.knowledge_base.retrieve_similar", return_value=[]
        ), patch(
            "app.mcp.core.git.get_recent_diff", return_value=None
        ), patch(
            "app.mcp.collectors.static_analyzer.analyze",
            return_value=[
                SimpleNamespace(
                    file="/app/foo.py",
                    function="bar",
                    line_number=42,
                    function_info=None,
                    call_chain=["bar"],
                    suspicious_inputs=[],
                )
            ],
        ), patch(
            "app.mcp.collectors.url_resolver.resolve_from_debug_context",
            return_value=None,
        ) as mock_resolve:
            result = await assembler.assemble(ctx)

        assert result["fault_locations"] == [fault_dict]
        assert result["sources"]["fault_locations"] is True
        # 有帧时不应触发 URL 反查
        mock_resolve.assert_not_called()
