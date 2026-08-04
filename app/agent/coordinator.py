"""Coordinator —— Agent 执行编排器（含 M4 Verify Loop）。

Phase 1：单 Agent 串行（RepairAgent only）。
Phase 2：多 Agent DAG 调度 —— RepairAgent 先行 → GitAgent / TestAgent / SecurityAgent
并行审查（依赖 repair_plan）。通过 `agent_multi_agent_enabled` 开关切换。

M4 Verify Loop（agent_verify_loop_enabled=True）：
    在 Phase 2 DAG 外层加迭代循环（默认最多 3 轮）。每轮：
      ① 执行 DAG（repair + git/test/security 并行审查）
      ② 由 _compute_verify_record 把 TestAgent + Review 信号 合成为 VerifyRecord
      ③ 由 _compute_iteration_verdict 判 passed/partial/rejected → 决定是否继续下一轮
      ④ iteration 结束时：若迭代有效（verdict != skipped），写回 KnowledgeBase
         （DebugCase.verify_count/case_confidence 递增）
    迭代结束后返回 best_iteration 的 repair_plan，同时新增：
      iterations / best_iteration / loop_final_verdict 字段。

静默降级：
- RepairAgent 失败 → repair_plan=None + 下游 Agent 自动 SKIPPED + agent_trace[FAILED]
- 下游 Agent 失败 → 对应 trace 标 FAILED/SKIPPED，不阻断其他 Agent 与最终聚合
- 任一 Agent 异常 → coordinator 防御性兜底，不抛异常穿透到 RepairQueue

零侵入约束：BaseAgent 多态接口无需变更，新增 Agent 只需继承 BaseAgent + 在 dag.py 注册。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any, Optional

from app.agent.base import (
    AgentContext,
    AgentResult,
    AgentStatus,
    AgentTrace,
    BaseAgent,
)
from app.agent.context_assembler import RepairContextAssembler
from app.agent.dag import (
    PHASE2_PARALLEL_NODES,
    build_phase2_agents,
)
from app.agent.repair_agent import RepairAgent
from app.agent.schemas import (
    IterationResult,
    IterationVerdict,
    LoopState,
    VerifyRecord,
)
from app.config import settings

logger = logging.getLogger("Lujo-MCP.agent.coordinator")

# 置信度标签 → 数值分，用于 repair_confidence 参与综合打分
_CONFIDENCE_WEIGHT = {
    "high": 1.0,
    "medium": 0.7,
    "low": 0.4,
    "": 0.3,
}


class Coordinator:
    """编排 Agent 执行流程（含 M4 Verify Loop）。

    Phase 1 单 Agent 串行；Phase 2 多 Agent DAG（RepairAgent 先行 + 三 Agent 并行审查）；
    M4 开启时在 Phase 2 DAG 外层迭代。模式切换：
      ``settings.agent_multi_agent_enabled`` → Phase 1/2
      ``settings.agent_verify_loop_enabled`` → Phase 2 基础上开启 Verify Loop 迭代
    """

    def __init__(self) -> None:
        self._assembler = RepairContextAssembler()
        self._agents: dict[str, BaseAgent] = {"repair": RepairAgent()}
        # Phase 2：注册多 Agent DAG 节点（惰性，仅在启用时生效）
        self._phase2_agents: dict[str, BaseAgent] = build_phase2_agents()

    async def run(
        self, debug_context: dict[str, Any], model: Optional[str] = None
    ) -> dict[str, Any]:
        """主入口：装配上下文 → 调度 Agent DAG（可选 Verify Loop）→ 组装最终输出。

        字段契约：
        - Phase 1/2：与旧版完全兼容（repair_plan / agent_trace / sources ...）
        - M4 开启时新增：
            iterations: list[IterationResult.to_dict()]（迭代轨迹）
            best_iteration: int（-1 表示没有成功迭代）
            loop_final_verdict: str（passed/partial/rejected/skipped）
            verify_loop_enabled: bool

        静默降级：任何 Agent 失败 → 对应 trace 标 FAILED/SKIPPED，不抛异常。
        """
        trace_id = debug_context.get("request_id") or debug_context.get("trace_id")

        # Step 1: 装配修复上下文（内含三个并发子装配，各自 fail-safe）
        repair_context = await self._assembler.assemble(debug_context)
        sources = repair_context.get("sources", {})

        # Step 2: 构造 AgentContext
        ctx = AgentContext(
            debug_context=debug_context,
            repair_context=repair_context,
            model=model,
            trace_id=trace_id,
        )

        # Step 3: 按配置调度（Phase 1 → Phase 2 → M4 Verify Loop 依次升级）
        if not settings.agent_multi_agent_enabled:
            return await self._run_phase1(ctx, sources)

        if not settings.agent_verify_loop_enabled:
            return await self._run_dag(ctx, sources)

        # M4：在 Phase 2 DAG 外层加迭代循环
        return await self._run_verify_loop(ctx, sources, trace_id)

    # ──────────────────────────────────────────────────────────────
    # Phase 1 / Phase 2 原逻辑（向后兼容，无任何行为变更）
    # ──────────────────────────────────────────────────────────────
    async def _run_phase1(
        self, ctx: AgentContext, sources: dict[str, Any]
    ) -> dict[str, Any]:
        """Phase 1：单 RepairAgent 串行（原逻辑，保持向后兼容）。"""
        agent_trace: list[dict[str, Any]] = []
        repair_plan: Optional[dict[str, Any]] = None

        repair_agent = self._agents["repair"]
        try:
            result: AgentResult = await repair_agent.run(ctx)
            if result.status == AgentStatus.SUCCESS:
                repair_plan = result.output.get("repair_plan")
            trace = BaseAgent._trace(result)
            agent_trace.append(trace.to_dict())
        except Exception as e:
            logger.exception("Coordinator: RepairAgent unexpected error")
            agent_trace.append(
                AgentTrace(
                    agent_name="repair",
                    status=AgentStatus.FAILED,
                    duration_s=0.0,
                    error=str(e),
                ).to_dict()
            )

        return {
            "repair_plan": repair_plan,
            "sources": sources,
            "agent_trace": agent_trace,
            "multi_agent_mode": False,
            "verify_loop_enabled": False,
        }

    async def _run_dag(
        self, ctx: AgentContext, sources: dict[str, Any]
    ) -> dict[str, Any]:
        """Phase 2：单轮多 Agent DAG 调度（无迭代，也供 Verify Loop 每轮内部调用）。

        返回：
            同旧版 + 新增 _verify_record_raw（给 Verify Loop 内部复用，对调用方可见但不破坏兼容）
        """
        agent_trace: list[dict[str, Any]] = []
        repair_plan: Optional[dict[str, Any]] = None

        # Layer 1: RepairAgent 先行
        repair_agent = self._phase2_agents.get("repair") or self._agents["repair"]
        try:
            repair_result = await repair_agent.run(ctx)
            if repair_result.status == AgentStatus.SUCCESS:
                repair_plan = repair_result.output.get("repair_plan")
            agent_trace.append(BaseAgent._trace(repair_result).to_dict())
        except Exception as e:
            logger.exception("Coordinator DAG: RepairAgent unexpected error")
            agent_trace.append(
                AgentTrace(
                    agent_name="repair",
                    status=AgentStatus.FAILED,
                    duration_s=0.0,
                    error=str(e),
                ).to_dict()
            )
            repair_result = None

        # 将 repair_plan 注入 repair_context，供下游 Agent 读取
        if repair_plan is not None:
            ctx.repair_context["repair_plan"] = repair_plan

        # Layer 2: GitAgent / TestAgent / SecurityAgent 并行审查
        parallel_results = await self._run_parallel_agents(ctx)

        # 聚合并行 Agent 的 trace（保持固定顺序：git → test → security）
        git_output: Optional[dict[str, Any]] = None
        test_output: Optional[dict[str, Any]] = None
        security_output: Optional[dict[str, Any]] = None
        parallel_failures = 0

        for node_name in PHASE2_PARALLEL_NODES:
            result = parallel_results.get(node_name)
            if result is None:
                continue
            agent_trace.append(BaseAgent._trace(result).to_dict())
            if result.status == AgentStatus.SUCCESS:
                if node_name == "git":
                    git_output = result.output
                elif node_name == "test":
                    test_output = result.output.get("test_plan")
                elif node_name == "security":
                    security_output = result.output.get("security_review")
            elif result.status == AgentStatus.FAILED:
                parallel_failures += 1

        # DAG 降级信号：并行节点失败数达阈值时标记，供调用方观测
        dag_degraded = parallel_failures >= settings.agent_dag_failure_threshold

        result: dict[str, Any] = {
            "repair_plan": repair_plan,
            "sources": sources,
            "agent_trace": agent_trace,
            "git_attribution": git_output,
            "test_plan": test_output,
            "security_review": security_output,
            "multi_agent_mode": True,
            "verify_loop_enabled": False,
            "dag_degraded": dag_degraded,
        }

        # 给 Verify Loop 复用的合成记录（内部字段，单轮 DAG 也暴露便于调试）
        verify = self._compute_verify_record(
            repair_plan, test_output, git_output, security_output
        )
        result["_verify_record_raw"] = verify.model_dump(mode="json")

        return result

    async def _run_parallel_agents(
        self, ctx: AgentContext
    ) -> dict[str, AgentResult]:
        """并行执行 GitAgent / TestAgent / SecurityAgent，各失败独立降级。

        使用 return_exceptions=True 确保单节点异常不影响其他节点。
        """
        tasks: list[tuple[str, Any]] = []
        for node_name in PHASE2_PARALLEL_NODES:
            agent = self._phase2_agents.get(node_name)
            if agent is None:
                continue
            tasks.append((node_name, agent.run(ctx)))

        if not tasks:
            return {}

        parallel_timeout = settings.agent_dag_parallel_timeout or settings.agent_timeout
        raw_results = await asyncio.wait_for(
            asyncio.gather(*[t[1] for t in tasks], return_exceptions=True),
            timeout=parallel_timeout,
        )

        results: dict[str, AgentResult] = {}
        for (node_name, _), raw in zip(tasks, raw_results):
            if isinstance(raw, Exception):
                # 防御性兜底：Agent 内部已 try/except，此处仅防御
                logger.exception(
                    "Coordinator DAG: %s unexpected error", node_name
                )
                results[node_name] = AgentResult(
                    agent_name=node_name,
                    status=AgentStatus.FAILED,
                    output={},
                    error=str(raw),
                    started_at=0.0,
                    finished_at=BaseAgent._now(),
                )
            elif isinstance(raw, AgentResult):
                results[node_name] = raw
        return results

    # ──────────────────────────────────────────────────────────────
    # M4 Verify Loop
    # ──────────────────────────────────────────────────────────────

    async def _run_verify_loop(
        self,
        ctx: AgentContext,
        sources: dict[str, Any],
        trace_id: Optional[str],
    ) -> dict[str, Any]:
        """M4 Agent Verify Loop：迭代执行 DAG + 验证判定 + 知识库写回。

        设计要点：
        - 每轮完整 DAG 执行（Repair → parallel review）
        - 每轮结束合成 VerifyRecord → 计算 verdict → 与 best_score 比较 → 决定下轮是否继续
        - 迭代去重：repair_patch 摘要哈希命中则本轮判定 REJECTED（避免重复生成）
        - 写回知识库：verdict ∈ {passed, partial, rejected} 时调用 _persist_kb_verify
          （对 DebugCase.verify_count/case_confidence 做增量，若指纹不存在则新建 LLM 条目）
        """
        state = LoopState(
            request_id=trace_id or "unknown",
        )

        max_iterations = max(1, int(settings.agent_verify_loop_max_iterations or 1))
        # DAG 原始 trace 用最后一轮的（迭代每轮都重新 run；旧逻辑只暴露最后一轮 agent_trace，保持兼容）
        last_dag_result: dict[str, Any] = {}
        best_repair_plan: Optional[dict[str, Any]] = None

        for iteration_idx in range(max_iterations):
            iter_started_at = time.time()

            # ── 1) 执行一次 DAG ──
            # 注意：Verify Loop 每轮重新执行 DAG（下轮会因 seen_patch_hashes 触发不同
            #       RepairAgent 提示 — 当前版本 RepairAgent 无 history，靠 loop 停止条件兜底）
            # 为保证迭代 1 的结果就是 Phase 2 输出，直接调用 _run_dag（不重复 Assembler）
            try:
                dag_result = await self._run_dag(ctx, sources)
            except Exception as e:
                logger.exception(
                    "Verify Loop: iteration %d DAG unexpected error", iteration_idx
                )
                dag_result = {
                    "repair_plan": None,
                    "sources": sources,
                    "agent_trace": [],
                    "git_attribution": None,
                    "test_plan": None,
                    "security_review": None,
                    "multi_agent_mode": True,
                    "verify_loop_enabled": False,
                    "dag_degraded": True,
                    "_verify_record_raw": VerifyRecord().model_dump(mode="json"),
                }

            last_dag_result = dag_result
            repair_plan = dag_result.get("repair_plan")
            verify = VerifyRecord.model_validate(
                dag_result.get("_verify_record_raw") or {}
            )

            # ── 2) repair_patch 去重 ──
            patch_hash = self._hash_patch(repair_plan)
            duplicate_patch = bool(patch_hash and patch_hash in state.seen_patch_hashes)
            if patch_hash:
                state.seen_patch_hashes.add(patch_hash)

            repair_confidence = ""
            if isinstance(repair_plan, dict):
                repair_confidence = str(repair_plan.get("confidence") or "").lower()

            # ── 3) 合成 verdict + 综合分 ──
            verdict, should_continue, next_focus = self._compute_iteration_verdict(
                verify=verify,
                repair_plan_generated=repair_plan is not None,
                repair_confidence=repair_confidence,
                duplicate_patch=duplicate_patch,
                iteration_idx=iteration_idx,
                max_iterations=max_iterations,
            )
            overall_score = self._compute_iteration_score(
                verify, repair_confidence, duplicate_patch
            )

            # ── 4) 累计 best（仅当 repair_plan 存在时才算候选 best）──
            is_best = repair_plan is not None and (
                overall_score > state.best_score
                or (overall_score == state.best_score and best_repair_plan is None)
            )
            if is_best:
                state.best_score = overall_score
                state.best_iteration = iteration_idx
                best_repair_plan = repair_plan

            # ── 5) 写回知识库：对 DebugCase.verify_count/case_confidence 做增量 ──
            if verdict in {IterationVerdict.PASSED, IterationVerdict.PARTIAL, IterationVerdict.REJECTED}:
                try:
                    self._persist_kb_verify(
                        ctx=ctx,
                        repair_plan=repair_plan,
                        verify=verify,
                        verdict=verdict,
                        overall_score=overall_score,
                    )
                except Exception:
                    # 知识库写回失败不得影响迭代
                    logger.warning(
                        "Verify Loop: persist_kb_verify failed (iter=%d, verdict=%s)",
                        iteration_idx,
                        verdict,
                        exc_info=True,
                    )

            # ── 6) 累积 IterationResult ──
            iter_result = IterationResult(
                iteration=iteration_idx,
                started_at=iter_started_at,
                finished_at=time.time(),
                repair_plan_generated=repair_plan is not None,
                repair_confidence=repair_confidence or "low",
                repair_patch_hash=patch_hash or "",
                verify=verify,
                verdict=verdict,
                score=overall_score,
                should_continue=should_continue,
                next_focus=next_focus,
            )
            state.iterations.append(iter_result.model_dump(mode="json"))

            # ── 7) 迭代停止条件 ──
            if verdict == IterationVerdict.PASSED or not should_continue:
                break

        # 最终返回：以 best_iteration 对应 repair_plan 为准（若 best 非最后一轮则替换）
        final_verdict = self._compute_final_verdict(state)

        # 用最后一轮 DAG 的 agent_trace/sources 保持对旧契约兼容
        result: dict[str, Any] = {
            "repair_plan": best_repair_plan,
            "sources": sources,
            "agent_trace": last_dag_result.get("agent_trace") or [],
            "git_attribution": last_dag_result.get("git_attribution"),
            "test_plan": last_dag_result.get("test_plan"),
            "security_review": last_dag_result.get("security_review"),
            "multi_agent_mode": True,
            "verify_loop_enabled": True,
            "dag_degraded": last_dag_result.get("dag_degraded", False),
            # M4 新增
            "iterations": state.iterations,
            "best_iteration": state.best_iteration,
            "best_score": state.best_score,
            "loop_final_verdict": final_verdict,
        }
        return result

    # ── Verify Loop 辅助函数 ──────────────────────────────────

    @staticmethod
    def _hash_patch(repair_plan: Optional[dict[str, Any]]) -> str:
        """对 RepairPlan.patch 求摘要哈希（用于重复修复判重）。"""
        if not isinstance(repair_plan, dict):
            return ""
        patch = repair_plan.get("patch") or ""
        if not patch:
            return ""
        # 取前 12 位，足够去重且不太长
        return hashlib.sha256(patch.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _compute_verify_record(
        repair_plan: Optional[dict[str, Any]],
        test_plan: Optional[dict[str, Any]],
        git_output: Optional[dict[str, Any]],
        security_review: Optional[dict[str, Any]],
    ) -> VerifyRecord:
        """把 DAG 输出的 Test/Git/Security 信号合成为结构化 VerifyRecord。"""
        verify = VerifyRecord()
        review_notes: list[str] = []

        # TestAgent 信号
        if isinstance(test_plan, dict) and test_plan:
            verify.test_files = [
                str(s) for s in (test_plan.get("test_files") or [])
            ]
            verify.test_cases = [
                str(s) for s in (test_plan.get("test_cases") or [])
            ]
            verify.regression_risks = [
                str(s) for s in (test_plan.get("regression_risks") or [])
            ]
            verify.validation_steps = [
                str(s) for s in (test_plan.get("validation_steps") or [])
            ]
            verify.coverage_note = str(test_plan.get("coverage_note") or "")
            # 判定 test_available：TestAgent 给了可执行验证点（非空）
            verify.test_available = bool(
                verify.test_cases or verify.validation_steps or verify.test_files
            )

            # test_quality_score：覆盖面 + 用例丰富度加权
            files_score = min(len(verify.test_files) / 2, 1.0)
            cases_score = min(len(verify.test_cases) / 3, 1.0)
            steps_score = min(len(verify.validation_steps) / 2, 1.0)
            coverage_bonus = (
                0.15
                if verify.coverage_note and "充分" in verify.coverage_note
                else 0.05
                if verify.coverage_note
                else 0.0
            )
            regression_penalty = (
                -0.1 if len(verify.regression_risks) >= 2 else 0.0
            )
            raw = (
                files_score * 0.25
                + cases_score * 0.40
                + steps_score * 0.25
                + coverage_bonus
                + regression_penalty
            )
            verify.test_quality_score = max(0.0, min(1.0, round(raw, 4)))

        # Git 信号（归因是否有风险）
        git_pass = True
        if isinstance(git_output, dict):
            blame = git_output.get("git_blame") or []
            if blame and any(
                "error" in str(b.get("summary", "")).lower() for b in blame
            ):
                git_pass = False
                review_notes.append("git_blame 中存在错误相关提交")
        verify.git_pass = git_pass

        # Security 信号
        security_pass = True
        if isinstance(security_review, dict):
            issues = security_review.get("issues") or []
            sev = [str(i.get("severity", "")).lower() for i in issues]
            if any(s in {"high", "critical"} for s in sev):
                security_pass = False
                review_notes.append(f"security_review 发现 {issues}")
        verify.security_pass = security_pass

        # review_score：两者都 pass → 1.0；任一失败 → 0.3；都失败 → 0.0
        if verify.git_pass and verify.security_pass:
            verify.review_score = 1.0
        elif verify.git_pass != verify.security_pass:
            verify.review_score = 0.3
        else:
            verify.review_score = 0.0
        verify.review_notes = review_notes

        # 无 repair_plan 时，TestAgent 多半被 SKIPPED → 整体视为无验证
        if repair_plan is None:
            verify.test_available = False
            verify.test_quality_score = 0.0

        return verify

    @staticmethod
    def _compute_iteration_score(
        verify: VerifyRecord,
        repair_confidence: str,
        duplicate_patch: bool,
    ) -> float:
        """综合分 = verify.overall_score * (0.7 + repair_confidence_weight*0.3) - dup_penalty。

        范围 0~1（dup_penalty 最多 0.8）
        """
        base = verify.overall_score()
        conf_w = _CONFIDENCE_WEIGHT.get(
            (repair_confidence or "").lower(), 0.3
        )
        score = base * (0.7 + conf_w * 0.3)
        if duplicate_patch:
            score = max(0.0, score - 0.8)
        return round(max(0.0, min(1.0, score)), 4)

    def _compute_iteration_verdict(
        self,
        *,
        verify: VerifyRecord,
        repair_plan_generated: bool,
        repair_confidence: str,
        duplicate_patch: bool,
        iteration_idx: int,
        max_iterations: int,
    ) -> tuple[str, bool, str]:
        """基于 VerifyRecord + 边界判定出 passed/partial/rejected/skipped。

        返回：(verdict, should_continue, next_focus)
        """
        # ① 无 repair_plan 生成 → 直接 SKIPPED + 停止（下轮同样失败无意义）
        if not repair_plan_generated:
            return IterationVerdict.SKIPPED, False, "RepairAgent 未生成 repair_plan"

        # ② 重复 patch → 本轮 REJECTED，且不再继续（避免无限打转）
        if duplicate_patch:
            return IterationVerdict.REJECTED, False, "本轮 patch 与历史重复"

        overall = verify.overall_score()
        conf_w = _CONFIDENCE_WEIGHT.get(
            (repair_confidence or "").lower(), 0.3
        )

        # ③ PASSED：综合分 ≥ pass 阈值 + 审查都 pass + test 可用
        #    或：综合分 ≥ high_pass 阈值（test 不可用但审查双 pass + repair 置信度高）
        pass_threshold = settings.agent_verify_loop_pass_threshold
        high_pass_threshold = settings.agent_verify_loop_high_confidence_pass_threshold
        if (
            verify.test_available
            and verify.git_pass
            and verify.security_pass
            and overall >= pass_threshold
        ) or (
            not verify.test_available
            and verify.git_pass
            and verify.security_pass
            and conf_w >= 0.7
            and overall >= high_pass_threshold
        ):
            return IterationVerdict.PASSED, False, "验证通过"

        # ④ PARTIAL：仍有可改进空间 → 下轮迭代
        partial_threshold = settings.agent_verify_loop_partial_threshold
        is_last = iteration_idx + 1 >= max_iterations
        if (
            overall >= partial_threshold
            or (verify.git_pass and verify.security_pass and overall > 0.15)
        ):
            next_focus = self._suggest_next_focus(verify, repair_confidence)
            if is_last:
                # 最后一轮仍然 partial → 本轮判 partial 但不继续
                return IterationVerdict.PARTIAL, False, next_focus + "（已达最大迭代轮数）"
            return IterationVerdict.PARTIAL, True, next_focus

        # ⑤ 剩余：不满足 partial 阈值 → REJECTED 且停止
        reason = self._suggest_next_focus(verify, repair_confidence)
        return IterationVerdict.REJECTED, False, reason

    @staticmethod
    def _suggest_next_focus(verify: VerifyRecord, repair_confidence: str) -> str:
        """构造"下轮聚焦点"提示。"""
        focuses: list[str] = []
        if not verify.test_available or verify.test_quality_score < 0.5:
            focuses.append("补充具体测试用例/验证步骤")
        if len(verify.regression_risks) >= 2:
            focuses.append("降低回归风险")
        if not verify.git_pass:
            focuses.append("解决 git 归因侧风险")
        if not verify.security_pass:
            focuses.append("修复安全审查问题")
        if (repair_confidence or "").lower() in {"low", ""}:
            focuses.append("提高修复方案置信度")
        if not focuses:
            focuses.append("提升综合验证分")
        return " / ".join(focuses[:3])

    @staticmethod
    def _compute_final_verdict(state: LoopState) -> str:
        """根据所有 iteration 求最终 loop verdict（用于 Dashboard 展示）。"""
        if not state.iterations:
            return IterationVerdict.SKIPPED
        verdicts = [it.get("verdict") or IterationVerdict.SKIPPED for it in state.iterations]
        if any(v == IterationVerdict.PASSED for v in verdicts):
            return IterationVerdict.PASSED
        if any(v == IterationVerdict.PARTIAL for v in verdicts):
            return IterationVerdict.PARTIAL
        if any(v == IterationVerdict.REJECTED for v in verdicts):
            return IterationVerdict.REJECTED
        return IterationVerdict.SKIPPED

    @staticmethod
    def _persist_kb_verify(
        *,
        ctx: AgentContext,
        repair_plan: Optional[dict[str, Any]],
        verify: VerifyRecord,
        verdict: str,
        overall_score: float,
    ) -> None:
        """把 Verify Loop 的验证信号写回 KnowledgeBase。

        写回策略（fail-safe，任何异常忽略）：
        - 有 (exception_type, exception_message) 指纹：
            若指纹不存在 → 走 analyzer._persist_analysis_to_knowledge_base 先写 LLM 条目
                                （含 case_confidence/verify_count 初始值）；
            若指纹已存在 → 直接 get → 按 DebugCase 解析 → verify_count + 1，
                                根据 verdict 调整 case_confidence。
        - 没有指纹（无堆栈静默失败等）：不写，避免污染（由 LLM 新分析路径负责）。

        置信度调整规则（verdict → Δconfidence）：
            passed : +0.05（最高 0.99）
            partial: +0.02
            rejected: -0.05（最低 0.10）
        """
        if not settings.agent_verify_loop_kb_writeback_enabled:
            return

        exc = (ctx.debug_context.get("exception") or {}) if ctx.debug_context else {}
        exc_type = exc.get("type") or ""
        exc_message = exc.get("message", "") or ""
        if not exc_type or not exc_message:
            # 无完整 exception 元数据 → 不写，避免污染 KB（由 LLM 分析路径负责）
            return

        from app.rag.debug_case import DebugCase
        from app.rag.knowledge_base import get_knowledge_entry, upsert_knowledge_entry

        fingerprint = DebugCase.compute_fingerprint(exc_type, exc_message)
        entry = get_knowledge_entry(fingerprint)

        # 没有 KB 条目 + 有 repair_plan → 先按 DebugCase.Schema 写入 LLM 条目
        if entry is None:
            if repair_plan is None:
                return
            patch = repair_plan.get("patch") or ""
            if not patch:
                return
            try:
                from app.llm.analyzer import _persist_analysis_to_knowledge_base

                synthesized_analysis = {
                    "root_cause": repair_plan.get("rationale") or exc_message,
                    "reasoning_chain": [
                        exc_message,
                        f"Verify Loop verdict={verdict} score={overall_score:.3f}",
                    ],
                    "fix": repair_plan.get("validation_strategy") or "",
                    "tags": [
                        "verify_loop",
                        f"verdict:{verdict}",
                    ],
                }
                _persist_analysis_to_knowledge_base(
                    fingerprint=fingerprint,
                    result={
                        "analysis": synthesized_analysis,
                        "context": {"exception": exc, **(ctx.debug_context or {})},
                    },
                )
                # 写回后再读一次，便于后续 verify_count/case_confidence 调整
                entry = get_knowledge_entry(fingerprint)
            except Exception:
                logger.debug(
                    "Verify Loop: seed entry create failed (fp=%s)",
                    fingerprint,
                    exc_info=True,
                )
                return

        # 有条目 → 调整 verify_count / case_confidence 并 upsert
        if entry is None:
            return

        analysis = entry.get("analysis") or {}
        try:
            # from_kb_entry 期望 KB entry 格式（字段嵌套在 analysis 内）
            # 直接传 entry 即可，from_kb_entry 会兜底缺失字段
            case = DebugCase.from_kb_entry(entry)
        except Exception:
            # 旧条目不符合 Schema → 跳过写回（不破坏已有数据）
            logger.debug(
                "Verify Loop: from_kb_entry failed (fp=%s)", fingerprint, exc_info=True,
            )
            return

        # 增量调整
        case.verify_count = int(case.verify_count or 0) + 1
        delta = {
            IterationVerdict.PASSED: +0.05,
            IterationVerdict.PARTIAL: +0.02,
            IterationVerdict.REJECTED: -0.05,
        }.get(verdict, 0.0)
        case.case_confidence = max(0.10, min(0.99, round(case.case_confidence + delta, 4)))

        # 附加 Verify Loop meta，便于后续分析
        extra_tags = [f"verify:{verdict}"]
        if not case.tags or "verify_loop" not in case.tags:
            case.tags = list(case.tags or []) + ["verify_loop"]
        for tag in extra_tags:
            if tag not in case.tags:
                case.tags.append(tag)

        kb_dict = case.to_kb_entry()
        # 合并已有 analysis 的其他字段（避免丢失 reasoning_chain / impact 等）
        merged_analysis: dict[str, Any] = dict(analysis)
        merged_analysis.update(kb_dict["analysis"])
        try:
            upsert_knowledge_entry(
                fingerprint=case.fingerprint,
                analysis=merged_analysis,
                fix_suggestion=case.fix_suggestion,
                source=case.source,
            )
        except Exception:
            logger.debug(
                "Verify Loop: KB writeback upsert failed (fp=%s)",
                case.fingerprint,
                exc_info=True,
            )
