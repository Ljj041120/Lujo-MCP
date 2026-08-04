"""AI Debug Agent 数据模型 —— 请求/方案/任务的 Pydantic 契约。

与 analyzer.py 的 {root_cause, impact, fix, confidence} 不同，
RepairPlan 聚焦"可执行修复方案"：patch + affected_files + validation_strategy。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, Field


class RepairRequest(BaseModel):
    """修复请求：与 AnalyzeRequest 对称，复用 request_id 拉取 trace。"""

    request_id: str = Field(..., description="请求 ID 或 trace_id")
    model: Optional[str] = Field(None, description="指定 LLM 模型，留空回退 settings.agent_model")


class RepairPlan(BaseModel):
    """RepairAgent 输出的结构化修复方案。"""

    patch: str = Field(..., description="具体代码修改方案：文件路径、修改位置、修改前/后片段、动作")
    affected_files: list[str] = Field(
        default_factory=list, description="受影响的文件列表"
    )
    validation_strategy: str = Field(..., description="验证策略：单测/集成测/手动验证步骤")
    risk_assessment: str = Field(..., description="风险评估：副作用、回归风险、影响范围")
    confidence: str = Field("low", description="置信度：high/medium/low")
    rationale: str = Field("", description="修复思路的推理过程")


class Sources(BaseModel):
    """修复方案的信息来源追溯。"""

    vector_recall: list[dict[str, Any]] = Field(
        default_factory=list, description="向量召回的历史相似修复"
    )
    git_context: list[dict[str, Any]] = Field(
        default_factory=list, description="git 近期 diff 上下文"
    )
    knowledge_base_hit: bool = Field(False, description="知识库精确指纹是否命中")


class RepairJob(BaseModel):
    """异步修复任务状态。结构对称 AnalysisQueue 的 job。"""

    job_id: str
    status: str  # pending | running | done | failed
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float
    finished_at: Optional[float] = None


# ═══════════════════════════════════════════════════════════════
# M4 Agent Verify Loop
# ═══════════════════════════════════════════════════════════════

class IterationVerdict(str):
    """迭代判定结论枚举。用字符串避免 Enum 序列化麻烦。"""

    PASSED = "passed"            # 验证通过：TestAgent + Review 信号均达标
    PARTIAL = "partial"          # 部分满足：可下轮迭代改善
    REJECTED = "rejected"        # 不满足：停止迭代，返回当前结果
    SKIPPED = "skipped"          # 无验证信号可用（TestAgent SKIPPED 等）


class VerifyRecord(BaseModel):
    """单次迭代的验证记录（来自 TestAgent / Review Agent 的结构化信号）。

    字段设计：
    - test_*：TestAgent 产出的"可执行测试规范"信号
    - review_*：Review（Git/Security）的"审查是否通过"信号
    - confidence_*：各信号置信度，最终合成综合评分用
    """

    # TestAgent 信号
    test_files: list[str] = Field(default_factory=list)
    test_cases: list[str] = Field(default_factory=list)
    regression_risks: list[str] = Field(default_factory=list)
    validation_steps: list[str] = Field(default_factory=list)
    coverage_note: str = Field("")
    test_available: bool = Field(False, description="TestAgent 是否返回有效验证策略")

    # Review 信号
    git_pass: bool = Field(True)
    security_pass: bool = Field(True)
    review_notes: list[str] = Field(default_factory=list)

    # 置信度评分（0~1）
    test_quality_score: float = Field(
        0.0, ge=0.0, le=1.0, description="TestAgent 策略质量分"
    )
    review_score: float = Field(
        1.0, ge=0.0, le=1.0, description="审查通过分（Git/Security）"
    )

    def overall_score(self) -> float:
        """综合分 = test*0.7 + review*0.3（无 test 信号时退化为 review 分）。"""
        if self.test_available:
            return round(self.test_quality_score * 0.7 + self.review_score * 0.3, 4)
        return self.review_score


class IterationResult(BaseModel):
    """单次修复迭代结果（供 Dashboard / KnowledgeBase 写回消费）。"""

    iteration: int = Field(..., ge=0)
    started_at: float
    finished_at: float

    # DAG 执行摘要
    repair_plan_generated: bool = Field(False)
    repair_confidence: str = Field("low", description="RepairPlan.confidence")
    repair_patch_hash: str = Field("", description="patch 摘要哈希，用于下轮判定是否重复")

    # 验证结论
    verify: VerifyRecord = Field(default_factory=VerifyRecord)
    verdict: str = Field(IterationVerdict.SKIPPED, description="passed/partial/rejected/skipped")
    score: float = Field(0.0, description="verify.overall_score，0~1")

    # 下轮建议
    should_continue: bool = Field(False)
    next_focus: str = Field("", description="下轮迭代建议聚焦点（如：补充测试用例/降低回归风险）")


@dataclass(slots=True)
class LoopState:
    """Verify Loop 运行时状态（跨迭代累计，dataclass 便于字段追加）。"""

    request_id: str
    iterations: list[dict[str, Any]] = field(default_factory=list)
    # 指纹集合：用于下轮判定是否重复修复（避免 LLM 在同一 patch 上打转）
    seen_patch_hashes: set[str] = field(default_factory=set)
    best_score: float = 0.0
    best_iteration: int = -1
    kb_entry_fingerprint: Optional[str] = None
