"""单元测试：M4 Agent Verify Loop（coordinator.py 的 M4 部分 + schemas.py M4 模型）。

覆盖：
- VerifyRecord.overall_score 合成逻辑
- Coordinator._compute_verify_record：TestAgent / Git / Security 信号 → VerifyRecord
- Coordinator._compute_iteration_verdict：passed/partial/rejected/skipped 判定
- Coordinator._compute_iteration_score：综合分计算 + dup_penalty
- Coordinator._hash_patch：patch 去重
- Coordinator._persist_kb_verify：KB 写回 verify_count/case_confidence 递增
- Coordinator.run 三层开关：Phase1 → Phase2 → M4 Verify Loop 切换
- M4 端到端：迭代轨迹 + best_iteration + loop_final_verdict
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.base import AgentContext, AgentResult, AgentStatus
from app.agent.coordinator import Coordinator
from app.agent.schemas import (
    IterationResult,
    IterationVerdict,
    LoopState,
    VerifyRecord,
)


# ──────────────────────────────────────────────────────────────
# VerifyRecord.overall_score
# ──────────────────────────────────────────────────────────────

class TestVerifyRecordScore:
    """VerifyRecord.overall_score 合成分计算。"""

    def test_no_test_signal_returns_review_score(self):
        """test_available=False → overall = review_score。"""
        v = VerifyRecord(test_available=False, review_score=1.0)
        assert v.overall_score() == 1.0

    def test_with_test_signal_blends(self):
        """test_available=True → overall = test*0.7 + review*0.3。"""
        v = VerifyRecord(
            test_available=True, test_quality_score=1.0, review_score=1.0
        )
        assert v.overall_score() == 1.0

        v2 = VerifyRecord(
            test_available=True, test_quality_score=0.5, review_score=0.6
        )
        assert abs(v2.overall_score() - (0.5 * 0.7 + 0.6 * 0.3)) < 0.001


# ──────────────────────────────────────────────────────────────
# _compute_verify_record
# ──────────────────────────────────────────────────────────────

class TestComputeVerifyRecord:
    """Coordinator._compute_verify_record 信号合成。"""

    def test_full_success_signals(self):
        """TestAgent + Git + Security 全部成功 → test_available + 双 pass。"""
        test_plan = {
            "test_files": ["test_foo.py", "test_bar.py"],
            "test_cases": ["case1", "case2", "case3"],
            "regression_risks": ["risk1"],
            "validation_steps": ["step1"],
            "coverage_note": "测试覆盖充分",
        }
        git_output = {"git_blame": [{"summary": "fix typo"}]}
        security_output = {"issues": []}

        v = Coordinator._compute_verify_record(
            repair_plan={"patch": "fix"},
            test_plan=test_plan,
            git_output=git_output,
            security_review=security_output,
        )
        assert v.test_available is True
        assert v.git_pass is True
        assert v.security_pass is True
        assert v.review_score == 1.0
        # test_quality_score 应该 > 0.5（3 cases + 2 files + coverage_bonus）
        assert v.test_quality_score > 0.5

    def test_no_repair_plan_disables_test(self):
        """repair_plan=None → test_available=False（TestAgent 被 SKIPPED）。"""
        v = Coordinator._compute_verify_record(
            repair_plan=None,
            test_plan={"test_cases": ["c1"]},
            git_output=None,
            security_review=None,
        )
        assert v.test_available is False
        assert v.test_quality_score == 0.0

    def test_git_blame_error_fails_git(self):
        """git_blame 含 error → git_pass=False。"""
        v = Coordinator._compute_verify_record(
            repair_plan={"patch": "fix"},
            test_plan=None,
            git_output={"git_blame": [{"summary": "Error in commit"}]},
            security_review=None,
        )
        assert v.git_pass is False
        assert v.review_score == 0.3  # 一 pass 一 fail

    def test_security_high_severity_fails(self):
        """security issues 有 high → security_pass=False。"""
        v = Coordinator._compute_verify_record(
            repair_plan={"patch": "fix"},
            test_plan=None,
            git_output=None,
            security_review={"issues": [{"severity": "high"}]},
        )
        assert v.security_pass is False
        assert v.review_score == 0.3

    def test_both_review_fail(self):
        """git + security 都 fail → review_score=0.0。"""
        v = Coordinator._compute_verify_record(
            repair_plan={"patch": "fix"},
            test_plan=None,
            git_output={"git_blame": [{"summary": "error: crash"}]},
            security_review={"issues": [{"severity": "critical"}]},
        )
        assert v.git_pass is False
        assert v.security_pass is False
        assert v.review_score == 0.0

    def test_many_regression_risks_penalty(self):
        """regression_risks >= 2 → test_quality_score 被惩罚。"""
        v = Coordinator._compute_verify_record(
            repair_plan={"patch": "fix"},
            test_plan={
                "test_files": ["t.py"],
                "test_cases": ["c1"],
                "regression_risks": ["r1", "r2", "r3"],
                "validation_steps": ["s1"],
            },
            git_output=None,
            security_review=None,
        )
        # 惩罚 -0.1 应该体现在分数上
        assert v.test_quality_score >= 0.0
        # 对比无惩罚
        v2 = Coordinator._compute_verify_record(
            repair_plan={"patch": "fix"},
            test_plan={
                "test_files": ["t.py"],
                "test_cases": ["c1"],
                "regression_risks": [],
                "validation_steps": ["s1"],
            },
            git_output=None,
            security_review=None,
        )
        assert v2.test_quality_score > v.test_quality_score


# ──────────────────────────────────────────────────────────────
# _compute_iteration_verdict
# ──────────────────────────────────────────────────────────────

class TestComputeIterationVerdict:
    """Coordinator._compute_iteration_verdict 判定逻辑。"""

    def _make_coord(self):
        return Coordinator()

    def test_no_repair_plan_skipped(self):
        coord = self._make_coord()
        verify = VerifyRecord()
        verdict, cont, _ = coord._compute_iteration_verdict(
            verify=verify,
            repair_plan_generated=False,
            repair_confidence="low",
            duplicate_patch=False,
            iteration_idx=0,
            max_iterations=3,
        )
        assert verdict == IterationVerdict.SKIPPED
        assert cont is False

    def test_duplicate_patch_rejected(self):
        coord = self._make_coord()
        verify = VerifyRecord(test_available=True, test_quality_score=0.9)
        verdict, cont, _ = coord._compute_iteration_verdict(
            verify=verify,
            repair_plan_generated=True,
            repair_confidence="high",
            duplicate_patch=True,
            iteration_idx=0,
            max_iterations=3,
        )
        assert verdict == IterationVerdict.REJECTED
        assert cont is False

    def test_passed_with_test_available(self):
        """test 可用 + 审查双 pass + overall >= 0.7 → PASSED。"""
        coord = self._make_coord()
        verify = VerifyRecord(
            test_available=True,
            test_quality_score=0.9,
            git_pass=True,
            security_pass=True,
            review_score=1.0,
        )
        verdict, cont, _ = coord._compute_iteration_verdict(
            verify=verify,
            repair_plan_generated=True,
            repair_confidence="high",
            duplicate_patch=False,
            iteration_idx=0,
            max_iterations=3,
        )
        assert verdict == IterationVerdict.PASSED
        assert cont is False

    def test_passed_without_test_high_confidence(self):
        """test 不可用 + 审查双 pass + confidence>=medium + overall>=0.85 → PASSED。"""
        coord = self._make_coord()
        verify = VerifyRecord(
            test_available=False,
            git_pass=True,
            security_pass=True,
            review_score=1.0,
        )
        # overall_score = 1.0 (无 test 时退化为 review_score)
        verdict, cont, _ = coord._compute_iteration_verdict(
            verify=verify,
            repair_plan_generated=True,
            repair_confidence="medium",
            duplicate_patch=False,
            iteration_idx=0,
            max_iterations=3,
        )
        assert verdict == IterationVerdict.PASSED

    def test_partial_continues(self):
        """overall >= partial_threshold 但未达 pass → PARTIAL + continue。"""
        coord = self._make_coord()
        verify = VerifyRecord(
            test_available=True,
            test_quality_score=0.4,
            git_pass=True,
            security_pass=True,
            review_score=1.0,
        )
        # overall = 0.4*0.7 + 1.0*0.3 = 0.58
        verdict, cont, focus = coord._compute_iteration_verdict(
            verify=verify,
            repair_plan_generated=True,
            repair_confidence="medium",
            duplicate_patch=False,
            iteration_idx=0,
            max_iterations=3,
        )
        assert verdict == IterationVerdict.PARTIAL
        assert cont is True
        assert focus != ""

    def test_partial_last_iteration_stops(self):
        """最后一轮 partial → 不继续。"""
        coord = self._make_coord()
        verify = VerifyRecord(
            test_available=True,
            test_quality_score=0.4,
            git_pass=True,
            security_pass=True,
            review_score=1.0,
        )
        verdict, cont, _ = coord._compute_iteration_verdict(
            verify=verify,
            repair_plan_generated=True,
            repair_confidence="medium",
            duplicate_patch=False,
            iteration_idx=2,  # 第 3 轮（max=3）
            max_iterations=3,
        )
        assert verdict == IterationVerdict.PARTIAL
        assert cont is False

    def test_low_score_rejected(self):
        """overall < partial_threshold 且无 review 双 pass → REJECTED。"""
        coord = self._make_coord()
        verify = VerifyRecord(
            test_available=False,
            git_pass=False,
            security_pass=False,
            review_score=0.0,
        )
        verdict, cont, _ = coord._compute_iteration_verdict(
            verify=verify,
            repair_plan_generated=True,
            repair_confidence="low",
            duplicate_patch=False,
            iteration_idx=0,
            max_iterations=3,
        )
        assert verdict == IterationVerdict.REJECTED
        assert cont is False


# ──────────────────────────────────────────────────────────────
# _compute_iteration_score
# ──────────────────────────────────────────────────────────────

class TestComputeIterationScore:
    """Coordinator._compute_iteration_score 综合分。"""

    def test_high_confidence_boosts_score(self):
        """repair_confidence=high → 分数提升。"""
        verify = VerifyRecord(
            test_available=True,
            test_quality_score=0.8,
            review_score=1.0,
        )
        score_high = Coordinator._compute_iteration_score(
            verify, "high", False
        )
        score_low = Coordinator._compute_iteration_score(
            verify, "low", False
        )
        assert score_high > score_low

    def test_duplicate_patch_penalty(self):
        """duplicate_patch=True → 扣 0.8。"""
        verify = VerifyRecord(
            test_available=True,
            test_quality_score=0.9,
            review_score=1.0,
        )
        score = Coordinator._compute_iteration_score(
            verify, "high", True
        )
        assert score < 0.2  # 被惩罚后很低

    def test_score_in_range(self):
        """分数在 [0, 1] 范围内。"""
        verify = VerifyRecord(
            test_available=True,
            test_quality_score=0.5,
            review_score=0.5,
        )
        for conf in ["high", "medium", "low", ""]:
            for dup in [True, False]:
                score = Coordinator._compute_iteration_score(
                    verify, conf, dup
                )
                assert 0.0 <= score <= 1.0


# ──────────────────────────────────────────────────────────────
# _hash_patch
# ──────────────────────────────────────────────────────────────

class TestHashPatch:
    """Coordinator._hash_patch 去重哈希。"""

    def test_same_patch_same_hash(self):
        p1 = {"patch": "modify line 42"}
        p2 = {"patch": "modify line 42"}
        assert Coordinator._hash_patch(p1) == Coordinator._hash_patch(p2)

    def test_different_patch_different_hash(self):
        p1 = {"patch": "modify line 42"}
        p2 = {"patch": "modify line 43"}
        assert Coordinator._hash_patch(p1) != Coordinator._hash_patch(p2)

    def test_none_plan_empty_hash(self):
        assert Coordinator._hash_patch(None) == ""

    def test_empty_patch_empty_hash(self):
        assert Coordinator._hash_patch({"patch": ""}) == ""
        assert Coordinator._hash_patch({}) == ""


# ──────────────────────────────────────────────────────────────
# _suggest_next_focus
# ──────────────────────────────────────────────────────────────

class TestSuggestNextFocus:
    """Coordinator._suggest_next_focus 聚焦点提示。"""

    def test_low_test_quality_suggests_tests(self):
        verify = VerifyRecord(test_available=True, test_quality_score=0.3)
        focus = Coordinator._suggest_next_focus(verify, "high")
        assert "测试用例" in focus or "验证步骤" in focus

    def test_low_confidence_suggested(self):
        verify = VerifyRecord(
            test_available=True, test_quality_score=0.9, review_score=1.0
        )
        focus = Coordinator._suggest_next_focus(verify, "low")
        assert "置信度" in focus

    def test_many_risks_suggested(self):
        verify = VerifyRecord(
            test_available=True,
            test_quality_score=0.9,
            review_score=1.0,
            regression_risks=["r1", "r2"],
        )
        focus = Coordinator._suggest_next_focus(verify, "high")
        assert "回归风险" in focus


# ──────────────────────────────────────────────────────────────
# _compute_final_verdict
# ──────────────────────────────────────────────────────────────

class TestComputeFinalVerdict:
    """Coordinator._compute_final_verdict 最终判定。"""

    def test_empty_iterations_skipped(self):
        state = LoopState(request_id="r1")
        assert Coordinator._compute_final_verdict(state) == IterationVerdict.SKIPPED

    def test_passed_wins(self):
        state = LoopState(request_id="r1")
        state.iterations = [
            {"verdict": IterationVerdict.PARTIAL},
            {"verdict": IterationVerdict.PASSED},
        ]
        assert Coordinator._compute_final_verdict(state) == IterationVerdict.PASSED

    def test_partial_wins_over_rejected(self):
        state = LoopState(request_id="r1")
        state.iterations = [
            {"verdict": IterationVerdict.REJECTED},
            {"verdict": IterationVerdict.PARTIAL},
        ]
        assert Coordinator._compute_final_verdict(state) == IterationVerdict.PARTIAL

    def test_all_rejected(self):
        state = LoopState(request_id="r1")
        state.iterations = [
            {"verdict": IterationVerdict.REJECTED},
            {"verdict": IterationVerdict.REJECTED},
        ]
        assert Coordinator._compute_final_verdict(state) == IterationVerdict.REJECTED


# ──────────────────────────────────────────────────────────────
# _persist_kb_verify
# ──────────────────────────────────────────────────────────────

class TestPersistKbVerify:
    """Coordinator._persist_kb_verify 知识库写回。"""

    def test_no_exception_skips(self):
        """无 exception type/message → 不写回。"""
        ctx = AgentContext(
            debug_context={"exception": {}},
            repair_context={},
        )
        # 不应抛异常
        Coordinator._persist_kb_verify(
            ctx=ctx,
            repair_plan={"patch": "fix"},
            verify=VerifyRecord(),
            verdict=IterationVerdict.PASSED,
            overall_score=0.9,
        )

    def test_kb_writeback_disabled_skips(self, monkeypatch):
        """kb_writeback_enabled=False → 跳过。"""
        monkeypatch.setattr(
            "app.config.settings.agent_verify_loop_kb_writeback_enabled", False
        )
        ctx = AgentContext(
            debug_context={"exception": {"type": "ValueError", "message": "bad"}},
            repair_context={},
        )
        # 不应触达 KB
        with patch("app.rag.knowledge_base.get_knowledge_entry") as mock_get:
            Coordinator._persist_kb_verify(
                ctx=ctx,
                repair_plan={"patch": "fix"},
                verify=VerifyRecord(),
                verdict=IterationVerdict.PASSED,
                overall_score=0.9,
            )
            mock_get.assert_not_called()

    def test_existing_entry_verify_count_increments(self, monkeypatch):
        """已有 KB 条目 → verify_count +1 + case_confidence 调整。"""
        monkeypatch.setattr(
            "app.config.settings.agent_verify_loop_kb_writeback_enabled", True
        )
        from app.rag.debug_case import DebugCase

        # 构造一个已有 KB 条目
        exc_type = "ValueError"
        exc_message = "bad input"
        fp = DebugCase.compute_fingerprint(exc_type, exc_message)
        existing_case = DebugCase(
            fingerprint=fp,
            exception_type=exc_type,
            exception_message=exc_message,
            root_cause="root",
            fix_suggestion="fix",
            tags=["seed"],
            source="seed",
            case_confidence=0.7,
            verify_count=2,
        )
        existing_entry = existing_case.to_kb_entry()

        ctx = AgentContext(
            debug_context={"exception": {"type": exc_type, "message": exc_message}},
            repair_context={},
        )

        captured_upserts = []
        with patch(
            "app.rag.knowledge_base.get_knowledge_entry", return_value=existing_entry
        ), patch(
            "app.rag.knowledge_base.upsert_knowledge_entry",
            side_effect=lambda **kw: captured_upserts.append(kw),
        ):
            Coordinator._persist_kb_verify(
                ctx=ctx,
                repair_plan={"patch": "fix"},
                verify=VerifyRecord(test_available=True, test_quality_score=0.9),
                verdict=IterationVerdict.PASSED,
                overall_score=0.9,
            )

        assert len(captured_upserts) == 1
        upserted = captured_upserts[0]
        assert upserted["fingerprint"] == fp
        # verify_count 应该 +1 → 3
        analysis = upserted["analysis"]
        kb_meta = analysis.get("_kb_meta") or {}
        assert kb_meta.get("verify_count") == 3
        # passed → case_confidence +0.05 → 0.75
        assert abs(kb_meta.get("case_confidence", 0) - 0.75) < 0.01
        # tags 应包含 verify_loop + verify:passed
        assert "verify_loop" in analysis.get("tags", [])
        assert "verify:passed" in analysis.get("tags", [])

    def test_rejected_decreases_confidence(self, monkeypatch):
        """verdict=rejected → case_confidence -0.05。"""
        monkeypatch.setattr(
            "app.config.settings.agent_verify_loop_kb_writeback_enabled", True
        )
        from app.rag.debug_case import DebugCase

        exc_type = "ValueError"
        exc_message = "bad"
        fp = DebugCase.compute_fingerprint(exc_type, exc_message)
        existing_case = DebugCase(
            fingerprint=fp,
            exception_type=exc_type,
            exception_message=exc_message,
            root_cause="r",
            fix_suggestion="f",
            case_confidence=0.8,
            verify_count=5,
        )
        ctx = AgentContext(
            debug_context={"exception": {"type": exc_type, "message": exc_message}},
            repair_context={},
        )
        captured = []
        with patch(
            "app.rag.knowledge_base.get_knowledge_entry",
            return_value=existing_case.to_kb_entry(),
        ), patch(
            "app.rag.knowledge_base.upsert_knowledge_entry",
            side_effect=lambda **kw: captured.append(kw),
        ):
            Coordinator._persist_kb_verify(
                ctx=ctx,
                repair_plan={"patch": "fix"},
                verify=VerifyRecord(),
                verdict=IterationVerdict.REJECTED,
                overall_score=0.1,
            )
        assert len(captured) == 1
        kb_meta = captured[0]["analysis"].get("_kb_meta", {})
        assert kb_meta["verify_count"] == 6
        assert abs(kb_meta["case_confidence"] - 0.75) < 0.01  # 0.8 - 0.05

    def test_no_entry_with_repair_plan_creates_entry(self, monkeypatch):
        """指纹不存在 + 有 repair_plan → 先创建 LLM 条目再写回。"""
        monkeypatch.setattr(
            "app.config.settings.agent_verify_loop_kb_writeback_enabled", True
        )
        from app.rag.debug_case import DebugCase

        exc_type = "ValueError"
        exc_message = "new error"
        fp = DebugCase.compute_fingerprint(exc_type, exc_message)

        ctx = AgentContext(
            debug_context={"exception": {"type": exc_type, "message": exc_message}},
            repair_context={},
        )

        # 第一次 get_knowledge_entry 返回 None（不存在）
        # _persist_analysis_to_knowledge_base 内部会写 KB
        # 第二次 get_knowledge_entry 返回新创建的条目
        new_case = DebugCase(
            fingerprint=fp,
            exception_type=exc_type,
            exception_message=exc_message,
            root_cause="from repair",
            fix_suggestion="fix it",
            case_confidence=0.9,
            verify_count=0,
            source="llm",
        )
        new_entry = new_case.to_kb_entry()

        get_call_count = [0]

        def mock_get(fp_):
            get_call_count[0] += 1
            if get_call_count[0] == 1:
                return None
            return new_entry

        captured = []
        with patch(
            "app.rag.knowledge_base.get_knowledge_entry", side_effect=mock_get
        ), patch(
            "app.rag.knowledge_base.upsert_knowledge_entry",
            side_effect=lambda **kw: captured.append(kw),
        ), patch(
            "app.llm.analyzer._persist_analysis_to_knowledge_base"
        ):
            Coordinator._persist_kb_verify(
                ctx=ctx,
                repair_plan={"patch": "fix", "rationale": "reason"},
                verify=VerifyRecord(),
                verdict=IterationVerdict.PARTIAL,
                overall_score=0.5,
            )

        # 应该有至少 1 次 upsert（写回 verify_count+1）
        assert len(captured) >= 1
        last = captured[-1]
        kb_meta = last["analysis"].get("_kb_meta", {})
        assert kb_meta.get("verify_count") == 1  # 0 + 1
        # partial → +0.02 → 0.92
        assert abs(kb_meta.get("case_confidence", 0) - 0.92) < 0.01


# ──────────────────────────────────────────────────────────────
# Coordinator.run 三层开关
# ──────────────────────────────────────────────────────────────

class TestCoordinatorThreeLayerSwitch:
    """Coordinator.run 按 settings 切换 Phase1 / Phase2 / M4 Loop。"""

    @pytest.mark.asyncio
    async def test_phase1_when_both_disabled(self, monkeypatch):
        """两个开关都 False → Phase 1 单 Agent。"""
        monkeypatch.setattr("app.config.settings.agent_multi_agent_enabled", False)
        monkeypatch.setattr("app.config.settings.agent_verify_loop_enabled", False)

        coord = Coordinator()
        with patch.object(
            coord._assembler, "assemble", new=AsyncMock(return_value={"sources": {}})
        ), patch.object(
            coord._agents["repair"], "run", new=AsyncMock(
                return_value=AgentResult(
                    agent_name="repair", status=AgentStatus.SUCCESS,
                    output={"repair_plan": {"patch": "fix"}},
                    started_at=0.0, finished_at=1.0,
                )
            )
        ):
            result = await coord.run({"request_id": "r1"})

        assert result["multi_agent_mode"] is False
        assert result["verify_loop_enabled"] is False

    @pytest.mark.asyncio
    async def test_phase2_when_multi_enabled_loop_disabled(self, monkeypatch):
        """multi_agent=True + verify_loop=False → Phase 2 DAG（无迭代）。"""
        monkeypatch.setattr("app.config.settings.agent_multi_agent_enabled", True)
        monkeypatch.setattr("app.config.settings.agent_verify_loop_enabled", False)

        coord = Coordinator()
        with patch.object(
            coord._assembler, "assemble", new=AsyncMock(return_value={"sources": {}})
        ), patch.object(
            coord._phase2_agents["repair"], "run", new=AsyncMock(
                return_value=AgentResult(
                    agent_name="repair", status=AgentStatus.SUCCESS,
                    output={"repair_plan": {"patch": "fix"}},
                    started_at=0.0, finished_at=1.0,
                )
            )
        ), patch.object(
            coord._phase2_agents["git"], "run", new=AsyncMock(
                return_value=AgentResult(
                    agent_name="git", status=AgentStatus.SUCCESS,
                    output={"attribution": "ok"},
                    started_at=0.0, finished_at=1.0,
                )
            )
        ), patch.object(
            coord._phase2_agents["test"], "run", new=AsyncMock(
                return_value=AgentResult(
                    agent_name="test", status=AgentStatus.SUCCESS,
                    output={"test_plan": {}},
                    started_at=0.0, finished_at=1.0,
                )
            )
        ), patch.object(
            coord._phase2_agents["security"], "run", new=AsyncMock(
                return_value=AgentResult(
                    agent_name="security", status=AgentStatus.SUCCESS,
                    output={"security_review": {}},
                    started_at=0.0, finished_at=1.0,
                )
            )
        ):
            result = await coord.run({"request_id": "r1"})

        assert result["multi_agent_mode"] is True
        assert result["verify_loop_enabled"] is False
        # Phase 2 也暴露 _verify_record_raw（内部字段）
        assert "_verify_record_raw" in result

    @pytest.mark.asyncio
    async def test_m4_loop_when_both_enabled(self, monkeypatch):
        """multi_agent=True + verify_loop=True → M4 Verify Loop。"""
        monkeypatch.setattr("app.config.settings.agent_multi_agent_enabled", True)
        monkeypatch.setattr("app.config.settings.agent_verify_loop_enabled", True)
        monkeypatch.setattr(
            "app.config.settings.agent_verify_loop_max_iterations", 1
        )
        monkeypatch.setattr(
            "app.config.settings.agent_verify_loop_kb_writeback_enabled", False
        )

        coord = Coordinator()
        with patch.object(
            coord._assembler, "assemble", new=AsyncMock(return_value={"sources": {}})
        ), patch.object(
            coord._phase2_agents["repair"], "run", new=AsyncMock(
                return_value=AgentResult(
                    agent_name="repair", status=AgentStatus.SUCCESS,
                    output={"repair_plan": {"patch": "fix", "confidence": "high"}},
                    started_at=0.0, finished_at=1.0,
                )
            )
        ), patch.object(
            coord._phase2_agents["git"], "run", new=AsyncMock(
                return_value=AgentResult(
                    agent_name="git", status=AgentStatus.SUCCESS,
                    output={"attribution": "ok"},
                    started_at=0.0, finished_at=1.0,
                )
            )
        ), patch.object(
            coord._phase2_agents["test"], "run", new=AsyncMock(
                return_value=AgentResult(
                    agent_name="test", status=AgentStatus.SUCCESS,
                    output={"test_plan": {
                        "test_files": ["t.py"],
                        "test_cases": ["c1", "c2", "c3"],
                        "validation_steps": ["s1", "s2"],
                        "regression_risks": [],
                        "coverage_note": "充分覆盖",
                    }},
                    started_at=0.0, finished_at=1.0,
                )
            )
        ), patch.object(
            coord._phase2_agents["security"], "run", new=AsyncMock(
                return_value=AgentResult(
                    agent_name="security", status=AgentStatus.SUCCESS,
                    output={"security_review": {"issues": []}},
                    started_at=0.0, finished_at=1.0,
                )
            )
        ):
            result = await coord.run({"request_id": "r1"})

        assert result["verify_loop_enabled"] is True
        assert "iterations" in result
        assert len(result["iterations"]) == 1
        assert result["best_iteration"] == 0
        assert result["best_score"] > 0.0
        # 高质量 test + 审查双 pass → 应该 PASSED
        assert result["loop_final_verdict"] == IterationVerdict.PASSED

    @pytest.mark.asyncio
    async def test_m4_loop_repair_failed_skipped(self, monkeypatch):
        """M4 开启但 RepairAgent 失败 → SKIPPED verdict。"""
        monkeypatch.setattr("app.config.settings.agent_multi_agent_enabled", True)
        monkeypatch.setattr("app.config.settings.agent_verify_loop_enabled", True)
        monkeypatch.setattr(
            "app.config.settings.agent_verify_loop_max_iterations", 2
        )
        monkeypatch.setattr(
            "app.config.settings.agent_verify_loop_kb_writeback_enabled", False
        )

        coord = Coordinator()
        with patch.object(
            coord._assembler, "assemble", new=AsyncMock(return_value={"sources": {}})
        ), patch.object(
            coord._phase2_agents["repair"], "run", new=AsyncMock(
                return_value=AgentResult(
                    agent_name="repair", status=AgentStatus.FAILED,
                    output={}, error="LLM down",
                    started_at=0.0, finished_at=1.0,
                )
            )
        ), patch.object(
            coord._phase2_agents["git"], "run", new=AsyncMock(
                return_value=AgentResult(
                    agent_name="git", status=AgentStatus.SUCCESS,
                    output={"attribution": "ok"},
                    started_at=0.0, finished_at=1.0,
                )
            )
        ), patch.object(
            coord._phase2_agents["test"], "run", new=AsyncMock(
                return_value=AgentResult(
                    agent_name="test", status=AgentStatus.SKIPPED,
                    output={}, error="no plan",
                    started_at=0.0, finished_at=1.0,
                )
            )
        ), patch.object(
            coord._phase2_agents["security"], "run", new=AsyncMock(
                return_value=AgentResult(
                    agent_name="security", status=AgentStatus.SKIPPED,
                    output={}, error="no plan",
                    started_at=0.0, finished_at=1.0,
                )
            )
        ):
            result = await coord.run({"request_id": "r1"})

        assert result["verify_loop_enabled"] is True
        # repair_plan 为 None → SKIPPED → 不继续迭代（应只 1 轮）
        assert len(result["iterations"]) == 1
        assert result["iterations"][0]["verdict"] == IterationVerdict.SKIPPED
        assert result["best_iteration"] == -1
        assert result["loop_final_verdict"] == IterationVerdict.SKIPPED
