"""按错误指纹存取历史分析结论的最小知识库模块。

M2 扩展（2026-08-04）：
- KnowledgeBaseStore 增加 export_all / import_entries / bulk_upsert 方法
- 模块级新增 export_knowledge_base / import_knowledge_base 文件 I/O
- 支持 merge / upsert / overwrite 三种导入模式，基于 fingerprint 去重
- 与 DebugCase Schema 双向兼容（to_kb_entry / from_kb_entry）
"""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings
from app.rag.vector_store import get_vector_store

logger = logging.getLogger("Lujo-MCP.knowledge-base")

DEFAULT_MAX_ENTRIES = 100
EVICTION_POLICY = "lru"

# 导入模式常量
IMPORT_MODE_MERGE = "merge"        # 已存在的 fingerprint 跳过（保留旧数据）
IMPORT_MODE_UPSERT = "upsert"     # 已存在的更新，不存在的新增
IMPORT_MODE_OVERWRITE = "overwrite"  # 先清空再导入
_VALID_IMPORT_MODES = frozenset({
    IMPORT_MODE_MERGE, IMPORT_MODE_UPSERT, IMPORT_MODE_OVERWRITE,
})


@dataclass(slots=True)
class KnowledgeBaseEntry:
    fingerprint: str
    analysis: dict[str, Any]
    fix_suggestion: str
    source: str
    created_at: float
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "analysis": copy.deepcopy(self.analysis),
            "fix_suggestion": self.fix_suggestion,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class KnowledgeBaseStore:
    """基于进程内 OrderedDict 的最小知识库实现。

    M3 扩展（2026-08-04）：KB 写入同步写回 vector_store，解决双写不同步
    导致"向量召回查不到种子/沉淀"的问题（瓶颈 A + D）。
    """

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES):
        if max_entries <= 0:
            raise ValueError("max_entries must be greater than 0")
        self.max_entries = max_entries
        self.eviction_policy = EVICTION_POLICY
        self._entries: "OrderedDict[str, KnowledgeBaseEntry]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, fingerprint: str) -> dict[str, Any] | None:
        if not fingerprint:
            return None

        with self._lock:
            entry = self._entries.get(fingerprint)
            if entry is None:
                return None
            self._entries.move_to_end(fingerprint)
            return entry.to_dict()

    # ── 内部：vector_store 同步写 ──

    def _vector_sync_enabled(self) -> bool:
        """按 feature flag 判断是否启用 KB ↔ 向量索引双写同步。"""
        try:
            return bool(getattr(settings, "kb_vector_index_autosync", True))
        except Exception:
            return True

    def _sync_entry_to_vector_store(self, entry_dict: dict[str, Any]) -> None:
        """同步单条 KB entry 到 vector_store（静默降级，永不抛异常）。"""
        if not self._vector_sync_enabled():
            return
        try:
            doc = self._kb_entry_to_vector_doc(entry_dict)
            get_vector_store().add([doc])
        except Exception:
            logger.debug(
                "KB → vector store sync failed (fingerprint=%s)",
                entry_dict.get("fingerprint"),
                exc_info=True,
            )

    def _sync_all_to_vector_store(self) -> int:
        """把 KB 内所有条目全量重建向量索引（种子加载后/import overwrite 后调用）。

        Returns:
            写入 vector_store 的条目数
        """
        if not self._vector_sync_enabled():
            return 0
        with self._lock:
            dicts = [entry.to_dict() for entry in self._entries.values()]
        if not dicts:
            return 0
        try:
            docs = [self._kb_entry_to_vector_doc(d) for d in dicts]
            get_vector_store().add(docs)
            return len(docs)
        except Exception:
            logger.warning("KB full vector index rebuild failed", exc_info=True)
            return 0

    @staticmethod
    def _kb_entry_to_vector_doc(entry_dict: dict[str, Any]) -> dict[str, Any]:
        """把 KB entry dict 转成 vector store 友好的比较文档（用于 Jaccard/embedding 召回）。

        提取：fingerprint + exception_type + exception_message（含 normalized_message）
        + root_cause + fix_suggestion + tags，减少 LLM 噪声字段，提升向量召回准确度。
        """
        analysis = dict(entry_dict.get("analysis") or {})
        kb_meta = dict(analysis.get("_kb_meta") or {})
        exc_msg = analysis.get("exception_message", "") or ""
        normalized = kb_meta.get("normalized_message") or exc_msg
        return {
            "_kb_fingerprint": entry_dict.get("fingerprint", ""),
            "exception_type": analysis.get("exception_type", ""),
            "message": exc_msg,
            "normalized_message": normalized,
            "type_fingerprint": kb_meta.get("type_fingerprint", ""),
            "root_cause": analysis.get("root_cause", ""),
            "fix_suggestion": entry_dict.get("fix_suggestion", ""),
            "tags": list(analysis.get("tags", [])),
            "source": entry_dict.get("source", ""),
            "case_confidence": float(kb_meta.get("case_confidence", 0.7)),
            "verify_count": int(kb_meta.get("verify_count", 0)),
        }

    def upsert(
        self,
        *,
        fingerprint: str,
        analysis: dict[str, Any],
        fix_suggestion: str,
        source: str,
    ) -> dict[str, Any]:
        if not fingerprint:
            raise ValueError("fingerprint is required")
        if not isinstance(analysis, dict):
            raise ValueError("analysis must be a dict")
        if not source:
            raise ValueError("source is required")

        now = time.time()
        with self._lock:
            existing = self._entries.get(fingerprint)
            created_at = existing.created_at if existing else now

            entry = KnowledgeBaseEntry(
                fingerprint=fingerprint,
                analysis=copy.deepcopy(analysis),
                fix_suggestion=fix_suggestion,
                source=source,
                created_at=created_at,
                updated_at=now,
            )
            self._entries[fingerprint] = entry
            self._entries.move_to_end(fingerprint)

            if existing is None and len(self._entries) > self.max_entries:
                evicted_fingerprint, _ = self._entries.popitem(last=False)
                logger.info(
                    "Knowledge base entry evicted",
                    extra={
                        "fingerprint": evicted_fingerprint,
                        "policy": self.eviction_policy,
                        "max_entries": self.max_entries,
                    },
                )

            result = entry.to_dict()

        # 写锁外部同步向量索引（避免锁内 I/O）
        self._sync_entry_to_vector_store(result)
        return result

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
        # vector store 无按 fingerprint 删除语义，保持其单例独立
        # （下次 rebuild/reimport 会追加写入，Jaccard 重复项仅轻微影响召回排序）

    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    # ── M2: 批量导入/导出 ──

    def export_all(self) -> list[dict[str, Any]]:
        """导出全部知识库条目为 dict 列表（深拷贝）。

        Returns:
            list[dict]：所有条目的 dict 表示，按 LRU 顺序（最旧在前）
        """
        with self._lock:
            return [entry.to_dict() for entry in self._entries.values()]

    def _exists(self, fingerprint: str) -> bool:
        """检查 fingerprint 是否存在（不更新 LRU 顺序）。

        内部方法，供 import_entries 在 merge 模式下判断存在性，
        避免调用 self.get() 触发 move_to_end 影响 LRU 淘汰语义。
        """
        with self._lock:
            return fingerprint in self._entries

    def bulk_upsert(self, entries: list[dict[str, Any]]) -> int:
        """批量 upsert（已存在则更新，不存在则新增）。

        Args:
            entries: KnowledgeBaseEntry dict 列表，每项需包含
                fingerprint / analysis / fix_suggestion / source

        Returns:
            实际写入条目数
        """
        count = 0
        for entry in entries:
            try:
                self.upsert(
                    fingerprint=entry["fingerprint"],
                    analysis=entry.get("analysis") or {},
                    fix_suggestion=entry.get("fix_suggestion", ""),
                    source=entry.get("source", "import"),
                )
                count += 1
            except (KeyError, ValueError) as e:
                logger.warning(
                    "Skip invalid KB entry during bulk_upsert: %s", e,
                    extra={"fingerprint": entry.get("fingerprint", "<missing>")},
                )
        return count

    def import_entries(
        self,
        entries: list[dict[str, Any]],
        mode: str = IMPORT_MODE_MERGE,
    ) -> dict[str, int]:
        """按指定模式批量导入条目（基于 fingerprint 去重）。

        Args:
            entries: KnowledgeBaseEntry dict 列表
            mode: 导入模式
                - "merge": 已存在的 fingerprint 跳过（保留旧数据，常用于种子加载）
                - "upsert": 已存在的更新，不存在的新增
                - "overwrite": 先清空再 upsert 所有

        Returns:
            统计 dict：{"total": N, "inserted": N, "updated": N, "skipped": N}
        """
        if mode not in _VALID_IMPORT_MODES:
            raise ValueError(
                f"Invalid import mode: {mode!r}, "
                f"expected one of {sorted(_VALID_IMPORT_MODES)}"
            )

        stats = {"total": len(entries), "inserted": 0, "updated": 0, "skipped": 0}

        if mode == IMPORT_MODE_OVERWRITE:
            self.clear()

        for entry in entries:
            fingerprint = entry.get("fingerprint")
            if not fingerprint:
                stats["skipped"] += 1
                logger.warning("Skip KB entry without fingerprint: %s", entry)
                continue

            # 用 _exists 检查存在性，避免 self.get() 触发 LRU move_to_end
            existing = self._exists(fingerprint)
            if existing and mode == IMPORT_MODE_MERGE:
                stats["skipped"] += 1
                continue

            try:
                self.upsert(
                    fingerprint=fingerprint,
                    analysis=entry.get("analysis") or {},
                    fix_suggestion=entry.get("fix_suggestion", ""),
                    source=entry.get("source", "import"),
                )
                if existing:
                    stats["updated"] += 1
                else:
                    stats["inserted"] += 1
            except ValueError as e:
                stats["skipped"] += 1
                logger.warning(
                    "Skip invalid KB entry during import: %s", e,
                    extra={"fingerprint": fingerprint},
                )

        # overwrite / merge+upsert 完成后重建向量索引（与 KB 内存状态重新对齐）
        if mode == IMPORT_MODE_OVERWRITE:
            self._sync_all_to_vector_store()

        logger.info(
            "Knowledge base import completed: mode=%s stats=%s", mode, stats,
        )
        return stats


_knowledge_base = KnowledgeBaseStore()


def get_knowledge_base() -> KnowledgeBaseStore:
    return _knowledge_base


def get_knowledge_entry(fingerprint: str) -> dict[str, Any] | None:
    return _knowledge_base.get(fingerprint)


def upsert_knowledge_entry(
    *,
    fingerprint: str,
    analysis: dict[str, Any],
    fix_suggestion: str,
    source: str,
) -> dict[str, Any]:
    return _knowledge_base.upsert(
        fingerprint=fingerprint,
        analysis=analysis,
        fix_suggestion=fix_suggestion,
        source=source,
    )


def clear_knowledge_base() -> None:
    _knowledge_base.clear()


def retrieve_similar(query_text: str, top_k: int | None = None) -> list[dict[str, Any]]:
    """向量检索 fallback：精确指纹 miss 后按相似度召回历史分析。

    委托给当前 VectorStore 后端；vector store 关闭时（NullVectorStore）返回 []。
    调用方应在精确指纹匹配（get_knowledge_entry）miss 后调用本函数作为二级 fallback。

    Args:
        query_text: 查询文本（通常是当前调试上下文的 JSON 序列化结果）
        top_k: 召回数量；None 时使用 settings.vector_store_top_k

    Returns:
        list[dict]：相似 doc 列表，按相似度降序；无命中时返回 []
    """
    effective_top_k = top_k if top_k is not None else settings.vector_store_top_k
    pairs = get_vector_store().search(query_text, effective_top_k)
    return [doc for doc, _score in pairs]


# ── M2: 知识库文件导入/导出 ──


def export_knowledge_base(filepath: str | Path) -> dict[str, Any]:
    """导出全部知识库到 JSON 文件。

    使用 DebugCaseCollection 标准格式（version + exported_at + case_count + cases），
    与 DebugCase Schema 对齐，便于跨系统交换。

    Args:
        filepath: 目标 JSON 文件路径

    Returns:
        导出统计 dict：{"exported": N, "filepath": str, "version": str}

    Raises:
        OSError: 文件写入失败（权限/磁盘满等）
    """
    # 延迟导入避免循环依赖
    from app.rag.debug_case import DebugCase, DebugCaseCollection

    entries = _knowledge_base.export_all()
    cases = [DebugCase.from_kb_entry(entry) for entry in entries]
    collection = DebugCaseCollection(
        version="1.0.0",
        exported_at=time.time(),
        case_count=len(cases),
        cases=cases,
    )

    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(collection.model_dump_json(indent=2))

    stats = {
        "exported": len(cases),
        "filepath": str(path),
        "version": collection.version,
    }
    logger.info("Knowledge base exported: %s", stats)
    return stats


def import_knowledge_base(
    filepath: str | Path,
    mode: str = IMPORT_MODE_MERGE,
) -> dict[str, int]:
    """从 JSON 文件导入知识库。

    支持 DebugCaseCollection 格式（优先）与裸 list[dict] 格式（向后兼容）。
    基于 fingerprint 去重，按 mode 决定冲突处理策略。

    Args:
        filepath: 源 JSON 文件路径
        mode: 导入模式（merge/upsert/overwrite）

    Returns:
        统计 dict：{"total": N, "inserted": N, "updated": N, "skipped": N}

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: mode 非法或 JSON 格式不可识别
        json.JSONDecodeError: 文件非合法 JSON
    """
    from app.rag.debug_case import DebugCase, DebugCaseCollection

    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Knowledge base file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    # 解析两种格式：DebugCaseCollection dict / 裸 list
    if isinstance(raw, list):
        # 裸 list[dict] 格式：每项是 KnowledgeBaseEntry dict
        entries = raw
    elif isinstance(raw, dict) and "cases" in raw:
        # DebugCaseCollection 格式：转换为 KB entry dict
        collection = DebugCaseCollection.model_validate(raw)
        entries = [case.to_kb_entry() for case in collection.cases]
    else:
        raise ValueError(
            "Unrecognized knowledge base file format: expected list or "
            "DebugCaseCollection dict with 'cases' field"
        )

    return _knowledge_base.import_entries(entries, mode=mode)


def load_seed_cases(cases: list[Any]) -> dict[str, int]:
    """加载 DebugCase 列表到知识库（merge 模式，仅插入不存在的）。

    种子加载专用：已存在的 fingerprint 跳过，避免覆盖用户分析沉淀。
    加载完成后调用 `_sync_all_to_vector_store` 重建向量索引（使种子在 L2 召回可用）。
    典型用法：
        from app.rag.seed_data import SEED_CASES
        load_seed_cases(SEED_CASES)

    Args:
        cases: DebugCase 对象列表（或 dict 列表，将自动转换）

    Returns:
        统计 dict：{"total": N, "inserted": N, "updated": N, "skipped": N,
                     "vector_synced": N}
    """
    from app.rag.debug_case import DebugCase

    entries: list[dict[str, Any]] = []
    for case in cases:
        if isinstance(case, DebugCase):
            entries.append(case.to_kb_entry())
        elif isinstance(case, dict):
            # 假设已是 KB entry dict 格式
            entries.append(case)
        else:
            logger.warning("Skip unsupported seed case type: %s", type(case))

    stats = _knowledge_base.import_entries(entries, mode=IMPORT_MODE_MERGE)
    # 种子加载完毕后重建向量索引，保证种子在 L2 向量召回通道可用（瓶颈 A）
    synced = _knowledge_base._sync_all_to_vector_store()
    stats["vector_synced"] = synced
    logger.info(
        "Seed case loading complete: stats=%s vector_index_synced=%d",
        stats, synced,
    )
    return stats
