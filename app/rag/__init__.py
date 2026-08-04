"""RAG（检索增强生成）领域模块。

负责向量存储抽象、Qdrant 语义检索后端、知识库缓存与召回、
Debug Case 标准 Schema 与种子知识。

M2 扩展（2026-08-04）：
- debug_case: DebugCase / DebugCaseCollection 标准 Schema
- seed_data: 30 条高频异常模式种子知识
- knowledge_base: 增加 import/export 文件 I/O + merge/upsert/overwrite 模式
"""

from app.rag.debug_case import (
    CaseSource,
    DebugCase,
    DebugCaseCollection,
    ExceptionType,
    Severity,
)
from app.rag.knowledge_base import (
    IMPORT_MODE_MERGE,
    IMPORT_MODE_OVERWRITE,
    IMPORT_MODE_UPSERT,
    KnowledgeBaseEntry,
    KnowledgeBaseStore,
    clear_knowledge_base,
    export_knowledge_base,
    get_knowledge_base,
    get_knowledge_entry,
    import_knowledge_base,
    load_seed_cases,
    retrieve_similar,
    upsert_knowledge_entry,
)
from app.rag.seed_data import SEED_CASES, SEED_CASES_BY_TYPE, SEED_CASE_COUNT

__all__ = [
    # debug_case
    "CaseSource",
    "DebugCase",
    "DebugCaseCollection",
    "ExceptionType",
    "Severity",
    # knowledge_base
    "IMPORT_MODE_MERGE",
    "IMPORT_MODE_OVERWRITE",
    "IMPORT_MODE_UPSERT",
    "KnowledgeBaseEntry",
    "KnowledgeBaseStore",
    "clear_knowledge_base",
    "export_knowledge_base",
    "get_knowledge_base",
    "get_knowledge_entry",
    "import_knowledge_base",
    "load_seed_cases",
    "retrieve_similar",
    "upsert_knowledge_entry",
    # seed_data
    "SEED_CASES",
    "SEED_CASES_BY_TYPE",
    "SEED_CASE_COUNT",
]
