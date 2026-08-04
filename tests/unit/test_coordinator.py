"""单元测试：Coordinator 编排器（coordinator.py）。

覆盖：单 Agent 串行流程、agent_trace 收集、RepairAgent 失败时降级。
"""

import pytest
from unittest.mock import patch

from app.agent.base import AgentResult, AgentStatus, AgentContext
from app.agent.coordinator import Coordinator
from app.config import settings


@pytest.fixture(autouse=True)
def _force_phase1_mode(monkeypatch):
    """强制 Phase 1 单 Agent 模式，与 .env 的多 Agent / Verify Loop 配置解耦。

    本文件测试的是 Phase 1 单 Agent 串行行为（Coordinator 未启用多 Agent DAG、
    Verify Loop）。若 .env 开启了 agent_multi_agent_enabled / agent_verify_loop_enabled，
    Coordinator 会走多 Agent 路径并调用真实 LLM，导致断言不稳定。
    这里显式关闭这两个开关，保证测试确定性。
    """
    monkeypatch.setattr(settings, "agent_multi_agent_enabled", False)
    monkeypatch.setattr(settings, "agent_verify_loop_enabled", False)


def _make_debug_context():
    return {
        "request_id": "r1",
        "exception": {"type": "ValueError", "message": "bad input"},
    }


def _make_assemble_result():
    """RepairContextAssembler.assemble 的 fake 返回值。"""
    return {
        "debug_context": _make_debug_context(),
        "prior_analysis": {"analysis": {"root_cause": "x"}, "knowledge_base_hit": False},
        "vector_recall": [{"fingerprint": "fp1"}],
        "git_context": [{"file": "/app/foo.py", "diff": "..."}],
        "sources": {
            "vector_recall": [{"fingerprint": "fp1"}],
            "git_context": [{"file": "/app/foo.py", "diff": "..."}],
            "knowledge_base_hit": False,
        },
    }


class TestCoordinatorSuccess:
    """Coordinator 正常流程。"""

    @pytest.mark.asyncio
    async def test_run_returns_repair_plan_and_trace(self):
        coord = Coordinator()

        fake_agent_result = AgentResult(
            agent_name="repair",
            status=AgentStatus.SUCCESS,
            output={
                "repair_plan": {
                    "patch": "modify line 42",
                    "affected_files": ["app/foo.py"],
                    "validation_strategy": "pytest",
                    "risk_assessment": "low",
                    "confidence": "high",
                }
            },
            started_at=1.0,
            finished_at=2.0,
            usage={"total_tokens": 100},
        )

        with patch.object(
            coord._assembler, "assemble", return_value=_make_assemble_result()
        ), patch.object(
            coord._agents["repair"], "run", return_value=fake_agent_result
        ):
            result = await coord.run(_make_debug_context())

        assert result["repair_plan"]["patch"] == "modify line 42"
        assert result["repair_plan"]["confidence"] == "high"
        assert len(result["agent_trace"]) == 1
        assert result["agent_trace"][0]["agent_name"] == "repair"
        assert result["agent_trace"][0]["status"] == "success"
        assert result["sources"]["knowledge_base_hit"] is False

    @pytest.mark.asyncio
    async def test_sources_propagated_from_assembler(self):
        """sources 字段从 assembler 透传到最终输出。"""
        coord = Coordinator()
        assemble_result = _make_assemble_result()
        assemble_result["sources"]["knowledge_base_hit"] = True

        fake_agent_result = AgentResult(
            agent_name="repair",
            status=AgentStatus.SUCCESS,
            output={"repair_plan": {"patch": "fix"}},
        )

        with patch.object(
            coord._assembler, "assemble", return_value=assemble_result
        ), patch.object(
            coord._agents["repair"], "run", return_value=fake_agent_result
        ):
            result = await coord.run(_make_debug_context())

        assert result["sources"]["knowledge_base_hit"] is True
        assert len(result["sources"]["vector_recall"]) == 1


class TestCoordinatorDegradation:
    """RepairAgent 失败时静默降级。"""

    @pytest.mark.asyncio
    async def test_agent_failed_returns_none_plan(self):
        coord = Coordinator()

        fake_agent_result = AgentResult(
            agent_name="repair",
            status=AgentStatus.FAILED,
            output={},
            error="LLM timeout",
        )

        with patch.object(
            coord._assembler, "assemble", return_value=_make_assemble_result()
        ), patch.object(
            coord._agents["repair"], "run", return_value=fake_agent_result
        ):
            result = await coord.run(_make_debug_context())

        assert result["repair_plan"] is None
        assert len(result["agent_trace"]) == 1
        assert result["agent_trace"][0]["status"] == "failed"
        assert result["agent_trace"][0]["error"] == "LLM timeout"

    @pytest.mark.asyncio
    async def test_agent_exception_caught_by_coordinator(self):
        """RepairAgent.run 抛异常（不应发生，但 Coordinator 要兜底）。"""
        coord = Coordinator()

        with patch.object(
            coord._assembler, "assemble", return_value=_make_assemble_result()
        ), patch.object(
            coord._agents["repair"], "run", side_effect=RuntimeError("unexpected")
        ):
            result = await coord.run(_make_debug_context())

        assert result["repair_plan"] is None
        assert len(result["agent_trace"]) == 1
        assert result["agent_trace"][0]["status"] == "failed"
        assert "unexpected" in result["agent_trace"][0]["error"]


class TestCoordinatorTraceStructure:
    """agent_trace 审计记录结构。"""

    @pytest.mark.asyncio
    async def test_trace_contains_duration(self):
        coord = Coordinator()

        fake_agent_result = AgentResult(
            agent_name="repair",
            status=AgentStatus.SUCCESS,
            output={"repair_plan": {"patch": "fix"}},
            started_at=10.0,
            finished_at=12.5,
        )

        with patch.object(
            coord._assembler, "assemble", return_value=_make_assemble_result()
        ), patch.object(
            coord._agents["repair"], "run", return_value=fake_agent_result
        ):
            result = await coord.run(_make_debug_context())

        trace = result["agent_trace"][0]
        assert "duration_s" in trace
        assert trace["duration_s"] == 2.5
        assert "usage" in trace

    @pytest.mark.asyncio
    async def test_trace_id_extracted_from_context(self):
        """trace_id 从 debug_context 的 request_id 提取。"""
        coord = Coordinator()
        ctx = {"request_id": "trace-abc-123", "exception": {}}

        fake_agent_result = AgentResult(
            agent_name="repair",
            status=AgentStatus.SUCCESS,
            output={"repair_plan": {"patch": "fix"}},
        )

        captured_ctx = {}

        async def fake_run(ctx: AgentContext):
            captured_ctx["trace_id"] = ctx.trace_id
            return fake_agent_result

        with patch.object(
            coord._assembler, "assemble", return_value=_make_assemble_result()
        ), patch.object(
            coord._agents["repair"], "run", side_effect=fake_run
        ):
            await coord.run(ctx)

        assert captured_ctx["trace_id"] == "trace-abc-123"
