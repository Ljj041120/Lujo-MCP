from app.config import settings
from app.rag.knowledge_base import EVICTION_POLICY, KnowledgeBaseStore


# ---------------------------------------------------------------------------
# 原有：KnowledgeBaseStore 精确指纹存储测试
# ---------------------------------------------------------------------------


def test_upsert_adds_and_queries_entry():
    store = KnowledgeBaseStore(max_entries=2)

    entry = store.upsert(
        fingerprint="fp-1",
        analysis={"root_cause": "db timeout", "confidence": "high"},
        fix_suggestion="add retry",
        source="llm",
    )

    assert entry["fingerprint"] == "fp-1"
    assert entry["analysis"]["root_cause"] == "db timeout"
    assert entry["fix_suggestion"] == "add retry"
    assert entry["source"] == "llm"
    assert store.get("fp-1") == entry


def test_query_returns_none_for_missing_fingerprint():
    store = KnowledgeBaseStore(max_entries=2)

    assert store.get("missing-fp") is None


def test_upsert_updates_existing_entry_and_preserves_created_at():
    store = KnowledgeBaseStore(max_entries=2)

    original = store.upsert(
        fingerprint="fp-1",
        analysis={"root_cause": "old"},
        fix_suggestion="old fix",
        source="llm",
    )
    updated = store.upsert(
        fingerprint="fp-1",
        analysis={"root_cause": "new"},
        fix_suggestion="new fix",
        source="knowledge_base",
    )

    assert store.size() == 1
    assert updated["created_at"] == original["created_at"]
    assert updated["updated_at"] >= original["updated_at"]
    assert updated["analysis"]["root_cause"] == "new"
    assert updated["fix_suggestion"] == "new fix"
    assert updated["source"] == "knowledge_base"


def test_evicts_least_recently_used_entry_when_capacity_exceeded():
    store = KnowledgeBaseStore(max_entries=2)

    store.upsert(
        fingerprint="fp-1",
        analysis={"root_cause": "first"},
        fix_suggestion="fix 1",
        source="llm",
    )
    store.upsert(
        fingerprint="fp-2",
        analysis={"root_cause": "second"},
        fix_suggestion="fix 2",
        source="llm",
    )

    assert EVICTION_POLICY == "lru"
    assert store.get("fp-1") is not None

    store.upsert(
        fingerprint="fp-3",
        analysis={"root_cause": "third"},
        fix_suggestion="fix 3",
        source="llm",
    )

    assert store.get("fp-1") is not None
    assert store.get("fp-2") is None
    assert store.get("fp-3") is not None


# ---------------------------------------------------------------------------
# Phase 7：retrieve_similar 向量检索 fallback 测试
# ---------------------------------------------------------------------------


def test_retrieve_similar_returns_results_when_vector_store_has_docs(monkeypatch):
    """vector_store 有 doc 时 retrieve_similar 返回 doc 列表"""
    from app.rag.knowledge_base import retrieve_similar
    from app.rag.vector_store import InProcessVectorStore

    store = InProcessVectorStore()
    store.add([{
        "fingerprint": "fp-1",
        "analysis": {"root_cause": "database timeout problem"},
    }])
    monkeypatch.setattr("app.rag.knowledge_base.get_vector_store", lambda: store)

    results = retrieve_similar("database timeout problem")
    assert len(results) >= 1
    assert results[0]["fingerprint"] == "fp-1"


def test_retrieve_similar_returns_empty_when_vector_store_disabled(monkeypatch):
    """vector_store 关闭时（NullVectorStore）retrieve_similar 返回 []"""
    from app.rag.knowledge_base import retrieve_similar
    from app.rag.vector_store import NullVectorStore

    monkeypatch.setattr("app.rag.knowledge_base.get_vector_store", lambda: NullVectorStore())
    assert retrieve_similar("anything") == []


def test_retrieve_similar_uses_default_top_k_when_none(monkeypatch):
    """top_k=None 时使用 settings.vector_store_top_k 作为默认值"""
    from app.rag.knowledge_base import retrieve_similar
    from app.rag.vector_store import InProcessVectorStore

    store = InProcessVectorStore()
    for i in range(5):
        store.add([{
            "fingerprint": f"fp-{i}",
            "analysis": {"root_cause": "database timeout problem"},
        }])
    monkeypatch.setattr("app.rag.knowledge_base.get_vector_store", lambda: store)
    monkeypatch.setattr(settings, "vector_store_top_k", 2)

    results = retrieve_similar("database timeout problem")
    assert len(results) <= 2


def test_retrieve_similar_respects_explicit_top_k(monkeypatch):
    """显式 top_k 参数优先于 settings.vector_store_top_k"""
    from app.rag.knowledge_base import retrieve_similar
    from app.rag.vector_store import InProcessVectorStore

    store = InProcessVectorStore()
    for i in range(5):
        store.add([{
            "fingerprint": f"fp-{i}",
            "analysis": {"root_cause": "database timeout problem"},
        }])
    monkeypatch.setattr("app.rag.knowledge_base.get_vector_store", lambda: store)
    monkeypatch.setattr(settings, "vector_store_top_k", 10)

    results = retrieve_similar("database timeout problem", top_k=1)
    assert len(results) == 1


# ---------------------------------------------------------------------------
# M3：KB ↔ 向量索引双写同步（瓶颈 A + D 修复）
# ---------------------------------------------------------------------------


def test_upsert_syncs_entry_to_vector_store(monkeypatch):
    """upsert 后应同步写入 vector_store（锁外写）。"""
    from app.rag.vector_store import InProcessVectorStore

    store = KnowledgeBaseStore(max_entries=10)
    vec = InProcessVectorStore()
    monkeypatch.setattr("app.rag.knowledge_base.get_vector_store", lambda: vec)
    monkeypatch.setattr(settings, "kb_vector_index_autosync", True)

    store.upsert(
        fingerprint="fp-1",
        analysis={
            "root_cause": "db timeout",
            "exception_type": "ConnectionError",
            "exception_message": "connection refused",
        },
        fix_suggestion="add retry",
        source="llm",
    )
    assert len(vec._docs) == 1


def test_upsert_vector_sync_disabled_when_flag_off(monkeypatch):
    """kb_vector_index_autosync=False → 不写向量索引。"""
    from app.rag.vector_store import InProcessVectorStore

    store = KnowledgeBaseStore(max_entries=10)
    vec = InProcessVectorStore()
    monkeypatch.setattr("app.rag.knowledge_base.get_vector_store", lambda: vec)
    monkeypatch.setattr(settings, "kb_vector_index_autosync", False)

    store.upsert(
        fingerprint="fp-1",
        analysis={"root_cause": "db timeout", "exception_type": "ConnectionError"},
        fix_suggestion="add retry",
        source="llm",
    )
    assert len(vec._docs) == 0


def test_sync_all_rebuilds_vector_index(monkeypatch):
    """_sync_all_to_vector_store 全量重建向量索引。"""
    from app.rag.vector_store import InProcessVectorStore

    store = KnowledgeBaseStore(max_entries=10)
    vec = InProcessVectorStore()
    monkeypatch.setattr("app.rag.knowledge_base.get_vector_store", lambda: vec)
    # 先关闭 autosync，避免 upsert 时逐条写入，专注验证全量重建
    monkeypatch.setattr(settings, "kb_vector_index_autosync", False)

    store.upsert(
        fingerprint="fp-a",
        analysis={"root_cause": "a", "exception_type": "ValueError"},
        fix_suggestion="fix a",
        source="llm",
    )
    store.upsert(
        fingerprint="fp-b",
        analysis={"root_cause": "b", "exception_type": "TypeError"},
        fix_suggestion="fix b",
        source="llm",
    )
    assert len(vec._docs) == 0  # autosync 关闭，向量索引为空

    monkeypatch.setattr(settings, "kb_vector_index_autosync", True)
    n = store._sync_all_to_vector_store()
    assert n == 2
    assert len(vec._docs) == 2


def test_sync_all_empty_when_no_entries(monkeypatch):
    """KB 为空时全量重建返回 0。"""
    from app.rag.vector_store import InProcessVectorStore

    store = KnowledgeBaseStore(max_entries=10)
    vec = InProcessVectorStore()
    monkeypatch.setattr("app.rag.knowledge_base.get_vector_store", lambda: vec)
    monkeypatch.setattr(settings, "kb_vector_index_autosync", True)
    assert store._sync_all_to_vector_store() == 0


def test_kb_entry_to_vector_doc_extracts_compact_fields(monkeypatch):
    """_kb_entry_to_vector_doc 提取精简字段（含 _kb_meta 归一化信息）。"""
    entry = {
        "fingerprint": "fp-1",
        "analysis": {
            "root_cause": "db timeout",
            "exception_type": "ConnectionError",
            "exception_message": "connection refused to 192.168.1.1",
            "_kb_meta": {
                "normalized_message": "connection refused to <IP>",
                "type_fingerprint": "tf-1",
                "case_confidence": 0.9,
                "verify_count": 3,
            },
            "tags": ["db", "timeout"],
        },
        "fix_suggestion": "add retry",
        "source": "llm",
    }
    doc = KnowledgeBaseStore._kb_entry_to_vector_doc(entry)
    assert doc["normalized_message"] == "connection refused to <IP>"
    assert doc["type_fingerprint"] == "tf-1"
    assert doc["case_confidence"] == 0.9
    assert doc["verify_count"] == 3
    assert doc["exception_type"] == "ConnectionError"
    assert doc["message"] == "connection refused to 192.168.1.1"


# ---------------------------------------------------------------------------
# M3：三级 fallback 种子匹配（analyzer._try_seed_case_match）
# ---------------------------------------------------------------------------


def _seed_entry(entry_type, message, mtype="exact"):
    """构造一个内存种子 KB entry（含 type_fingerprint）。"""
    from app.rag.debug_case import DebugCase

    fp = DebugCase.compute_fingerprint(entry_type, message)
    return {
        "fingerprint": fp,
        "analysis": {
            "root_cause": "seed root cause",
            "exception_type": entry_type,
            "exception_message": message,
            "fix": "seed fix",
            "confidence": "high",
            "_kb_meta": {
                "normalized_message": DebugCase.normalize_message_for_similarity(message),
                "type_fingerprint": DebugCase.compute_type_fingerprint(entry_type),
                "case_confidence": 0.9,
                "verify_count": 0,
            },
            "tags": ["seed"],
        },
        "fix_suggestion": "seed fix",
    }


def test_seed_match_exact_fingerprint(monkeypatch):
    """档 1：精确 (type, message) 指纹命中。"""
    from app.llm import analyzer as analyzer_mod

    entry = _seed_entry("ValueError", "division by zero")
    monkeypatch.setattr(
        analyzer_mod, "get_knowledge_entry",
        lambda fp: entry if fp == entry["fingerprint"] else None,
    )
    context = {"exception": {"type": "ValueError", "message": "division by zero"}}
    result = analyzer_mod._try_seed_case_match(context)
    assert result is not None
    assert result["analysis_source"] == "knowledge_base_seed"
    assert result["knowledge_base_hit"] is True
    assert result["analysis"]["_seed_match_level"] == "exact"


def test_seed_match_normalized_fingerprint(monkeypatch):
    """档 2：归一化 message 指纹命中变量值差异。"""
    from app.llm import analyzer as analyzer_mod

    # 种子存的是归一化后的消息
    entry = _seed_entry("ValueError", "invalid value for field <NUM>")
    monkeypatch.setattr(
        analyzer_mod, "get_knowledge_entry",
        lambda fp: entry if fp == entry["fingerprint"] else None,
    )
    # 查询带变量值 → 归一化后应命中
    context = {
        "exception": {"type": "ValueError", "message": "invalid value for field 42"}
    }
    result = analyzer_mod._try_seed_case_match(context)
    assert result is not None
    assert result["analysis"]["_seed_match_level"] == "normalized"


def test_seed_match_type_level_jaccard(monkeypatch):
    """档 3：同 exception_type 粗粒度 Jaccard 命中。"""
    from app.llm import analyzer as analyzer_mod
    from app.rag.knowledge_base import KnowledgeBaseStore

    # 种子库：同类型但消息不同
    seed = _seed_entry("ValueError", "cannot convert float to int")
    store = KnowledgeBaseStore(max_entries=10)
    store.upsert(
        fingerprint=seed["fingerprint"],
        analysis=seed["analysis"],
        fix_suggestion=seed["fix_suggestion"],
        source="seed",
    )
    # _try_type_level_seed_match 内部局部导入 get_knowledge_base，
    # 需 patch 模块级 app.rag.knowledge_base.get_knowledge_base
    monkeypatch.setattr(
        "app.rag.knowledge_base.get_knowledge_base", lambda: store
    )
    monkeypatch.setattr(
        analyzer_mod, "get_knowledge_entry", lambda fp: None
    )
    monkeypatch.setattr(settings, "kb_type_level_fallback", True)
    monkeypatch.setattr(settings, "kb_seed_jaccard_min_score", 0.25)

    # 查询消息与种子有语义重叠但非精确匹配
    context = {
        "exception": {
            "type": "ValueError",
            "message": "cannot convert string to int",
        }
    }
    result = analyzer_mod._try_seed_case_match(context)
    assert result is not None
    assert result["analysis"]["_seed_match_level"] == "type_level"
    assert "_seed_match_fingerprint" in result["analysis"]


def test_seed_match_returns_none_when_no_hit(monkeypatch):
    """三级全 miss → 返回 None（进入向量检索/LLM 链路）。"""
    from app.llm import analyzer as analyzer_mod

    monkeypatch.setattr(analyzer_mod, "get_knowledge_entry", lambda fp: None)
    monkeypatch.setattr(settings, "kb_type_level_fallback", True)
    context = {"exception": {"type": "RuntimeError", "message": "unknown thing"}}
    result = analyzer_mod._try_seed_case_match(context)
    assert result is None
