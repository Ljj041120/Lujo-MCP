"""Debug Case 标准 Schema —— 结构化异常分析记录。

定义统一的异常分析数据模型，用于：
1. 知识库种子数据（30 条高频异常模式）
2. LLM 分析结论的结构化沉淀
3. Agent Verify Loop 的记忆载体
4. 知识库导入/导出的标准格式

设计原则：
- 与现有 KnowledgeBaseEntry 双向兼容（to_kb_entry / from_kb_entry）
- fingerprint 由 (exception_type, exception_message) 幂等计算，保证同案同指纹
- 所有字段向后兼容，新字段可选
- 纯数据模型，无 I/O 副作用
"""

from __future__ import annotations

import hashlib
import time
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── 枚举 ──


class ExceptionType(str, Enum):
    """常见异常类型枚举（覆盖种子知识 6 大类）。"""

    VALUE_ERROR = "ValueError"
    TYPE_ERROR = "TypeError"
    KEY_ERROR = "KeyError"
    ATTRIBUTE_ERROR = "AttributeError"
    CONNECTION_ERROR = "ConnectionError"
    IMPORT_ERROR = "ImportError"
    FILE_NOT_FOUND_ERROR = "FileNotFoundError"
    PERMISSION_ERROR = "PermissionError"
    RUNTIME_ERROR = "RuntimeError"
    STOP_ITERATION = "StopIteration"
    OTHER = "Other"


class Severity(str, Enum):
    """严重程度等级。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CaseSource(str, Enum):
    """Debug Case 来源标识。"""

    SEED = "seed"  # 种子知识（预置）
    LLM = "llm"  # LLM 分析沉淀
    MANUAL = "manual"  # 人工录入
    AGENT_VERIFY = "agent_verify"  # Agent Verify Loop 沉淀


# ── 标准 Schema ──


class DebugCase(BaseModel):
    """单条 Debug Case —— 异常分析的标准记录模型。

    字段设计：
    - fingerprint: 主键，由 (exception_type, exception_message) sha256 截断生成，幂等
    - exception_type/message: 异常元信息，用于指纹计算与分类检索
    - root_cause: 根因分析（人类可读）
    - fix_suggestion: 修复建议（可执行步骤）
    - tags: 标签体系，多维度分类（如 ["db", "async", "retry"]）
    - source_files: 参考文件路径列表
    - severity: 严重程度
    - analysis: 扩展字段，保留兼容 KnowledgeBaseEntry.analysis
    - source: 来源标识（seed/llm/manual/agent_verify）
    - created_at/updated_at: 时间戳（Unix 秒）
    """

    fingerprint: str = Field(
        ..., min_length=1, description="异常指纹，幂等主键"
    )
    exception_type: str = Field(
        ..., min_length=1, description="异常类型，如 ValueError / TypeError"
    )
    exception_message: str = Field(
        "", description="典型异常消息（可为空，用于模糊匹配）"
    )
    root_cause: str = Field(
        ..., min_length=1, description="根因分析，人类可读"
    )
    fix_suggestion: str = Field(
        ..., min_length=1, description="修复建议，可执行步骤"
    )
    tags: list[str] = Field(
        default_factory=list, description="标签列表，用于分类检索"
    )
    source_files: list[str] = Field(
        default_factory=list, description="参考文件路径列表"
    )
    severity: Severity = Field(
        Severity.MEDIUM, description="严重程度"
    )
    analysis: dict[str, Any] = Field(
        default_factory=dict,
        description="扩展字段，保留兼容 KnowledgeBaseEntry.analysis",
    )
    source: str = Field(
        CaseSource.SEED.value, description="来源标识（seed/llm/manual/agent_verify）"
    )
    case_confidence: float = Field(
        0.7, ge=0.0, le=1.0,
        description="案例置信度（种子默认 0.7，被 LLM/Agent Verify 验证成功后递增）",
    )
    verify_count: int = Field(
        0, ge=0, description="被 Agent Verify Loop 验证成功的次数（为 M4 写回铺路）",
    )
    created_at: float = Field(
        default_factory=time.time, description="创建时间戳（Unix 秒）"
    )
    updated_at: float = Field(
        default_factory=time.time, description="更新时间戳（Unix 秒）"
    )

    # ── 指纹工具 ──

    @staticmethod
    def compute_fingerprint(
        exception_type: str,
        exception_message: str,
        *,
        max_message_chars: int = 512,
    ) -> str:
        """根据异常类型 + 消息生成稳定指纹（精确匹配级）。

        规则：
        - exception_type 标准化（strip + 去模块前缀，如 'builtins.ValueError' → 'ValueError'）
        - exception_message 截断到 max_message_chars 后参与哈希
        - sha256 取前 16 字节十六进制（32 字符），碰撞概率足够低
        - 同 (type, message) 永远产出同指纹，跨进程稳定

        Args:
            exception_type: 异常类型名
            exception_message: 异常消息文本
            max_message_chars: 消息参与哈希的最大字符数（防超长消息）

        Returns:
            32 字符十六进制指纹字符串
        """
        normalized_type = exception_type.strip().split(".")[-1]
        truncated_msg = exception_message[:max_message_chars]
        payload = f"{normalized_type}::{truncated_msg}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def compute_type_fingerprint(exception_type: str) -> str:
        """按异常类型单独生成指纹（粗粒度匹配级，KB 准确度提升用）。

        适用：同类型异常但消息中含变量值（user_id=123 vs 456）导致精确指纹不同，
        退回类型级做粗召回。
        """
        normalized_type = exception_type.strip().split(".")[-1]
        return "type::" + hashlib.sha256(
            normalized_type.encode("utf-8")
        ).hexdigest()[:16]

    @staticmethod
    def normalize_message_for_similarity(message: str) -> str:
        """把异常消息标准化为语义比较文本（剥变量值，便于 Jaccard/embedding 匹配）。

        规则：
        - 去掉引号里的字符串常量
        - 把数字（整数/小数）替换为 <NUM>
        - 去掉单引号、双引号、反引号里的内容作为通用片段
        适用：`ValueError: invalid literal for int() with base 10: '123'`
        →  `ValueError invalid literal for int with base <NUM>`
        """
        import re as _re

        if not message:
            return ""
        s = message
        s = _re.sub(r"'.*?'", "'<STR>'", s)
        s = _re.sub(r'".*?"', '"<STR>"', s)
        s = _re.sub(r"`.*?`", "`<SYM>`", s)
        s = _re.sub(r"\b\d+\.\d+\b", "<NUM>", s)
        s = _re.sub(r"\b\d+\b", "<NUM>", s)
        return s

    # ── KnowledgeBaseEntry 互转 ──

    def to_kb_entry(self) -> dict[str, Any]:
        """转换为 KnowledgeBaseEntry dict 格式（向后兼容）。

        将 DebugCase 的关键字段映射到现有 KnowledgeBaseEntry 结构：
        - fingerprint/created_at/updated_at 直接映射
        - fix_suggestion 直接映射
        - source 映射
        - analysis 合并 root_cause / exception_type / tags / source_files / severity
          到一个 dict（保留扩展能力）
        - _kb_meta 保留 case_confidence / verify_count / type_fingerprint 等元信息
        """
        type_fp = DebugCase.compute_type_fingerprint(self.exception_type)
        merged_analysis = {
            **self.analysis,
            "exception_type": self.exception_type,
            "exception_message": self.exception_message,
            "root_cause": self.root_cause,
            "tags": list(self.tags),
            "source_files": list(self.source_files),
            "severity": self.severity.value if isinstance(self.severity, Severity) else self.severity,
            # 元信息放进 analysis 以便 LLM 与 KB 双向流中保留
            "_kb_meta": {
                "case_confidence": self.case_confidence,
                "verify_count": self.verify_count,
                "type_fingerprint": type_fp,
                "normalized_message": DebugCase.normalize_message_for_similarity(
                    self.exception_message
                ),
            },
        }
        return {
            "fingerprint": self.fingerprint,
            "analysis": merged_analysis,
            "fix_suggestion": self.fix_suggestion,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_kb_entry(cls, entry: dict[str, Any]) -> "DebugCase":
        """从 KnowledgeBaseEntry dict 反向构建 DebugCase。

        兼容旧格式：缺失字段使用默认值。root_cause / fix_suggestion 缺失时
        用 "<unknown>" 兜底（DebugCase 这两个字段为 min_length=1 必填）。
        """
        analysis = dict(entry.get("analysis") or {})
        kb_meta = dict(analysis.get("_kb_meta") or {})
        return cls(
            fingerprint=entry["fingerprint"],
            exception_type=analysis.get("exception_type", ExceptionType.OTHER.value),
            exception_message=analysis.get("exception_message", ""),
            root_cause=analysis.get("root_cause") or "<unknown>",
            fix_suggestion=entry.get("fix_suggestion") or "<unknown>",
            tags=list(analysis.get("tags", [])),
            source_files=list(analysis.get("source_files", [])),
            severity=analysis.get("severity", Severity.MEDIUM.value),
            case_confidence=float(kb_meta.get("case_confidence", 0.7)),
            verify_count=int(kb_meta.get("verify_count", 0)),
            analysis={
                k: v for k, v in analysis.items()
                if k not in {
                    "exception_type", "exception_message", "root_cause",
                    "tags", "source_files", "severity", "_kb_meta",
                }
            },
            source=entry.get("source", CaseSource.SEED.value),
            created_at=entry.get("created_at", time.time()),
            updated_at=entry.get("updated_at", time.time()),
        )


# ── 批量容器 ──


class DebugCaseCollection(BaseModel):
    """Debug Case 集合 —— 导入/导出的标准容器。

    用于序列化到 JSON 文件 / 从 JSON 文件反序列化。
    支持版本号向前兼容校验。
    """

    version: str = Field(
        "1.0.0", description="集合格式版本号"
    )
    exported_at: float = Field(
        default_factory=time.time, description="导出时间戳"
    )
    case_count: int = Field(
        0, ge=0, description="案例数量（导入时校验）"
    )
    cases: list[DebugCase] = Field(
        default_factory=list, description="Debug Case 列表"
    )

    def fingerprint_index(self) -> dict[str, DebugCase]:
        """构建 fingerprint → case 的索引，用于去重。"""
        return {case.fingerprint: case for case in self.cases}
