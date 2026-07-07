"""
测试存储层：去重、脱敏、schema 迁移。
"""
import json
import sqlite3

from app.config import settings
from app.mcp.core import logs
from app.mcp.core.logs import save_trace, get_trace, compute_fingerprint
from app.schemas.trace import StackFrame


def test_save_trace_returns_trace_id(fresh_db):
    trace_id = save_trace(
        exc_type="ValueError",
        message="something wrong",
        frames=[StackFrame(file="main.py", line=10, function="run", code_context="run()")],
    )
    assert trace_id
    assert isinstance(trace_id, str)


def test_save_trace_deduplicates_by_fingerprint(fresh_db):
    frames = [StackFrame(file="main.py", line=10, function="run", code_context="run()")]
    t1 = save_trace("ValueError", "error 1", frames, source="test")
    t2 = save_trace("ValueError", "error 2", frames, source="test")

    # 相同 fingerprint 应复用 trace_id
    assert t1 == t2

    entry = get_trace(t1)
    assert entry.occurrence_count == 2
    assert entry.trace_kind == "exception"


def test_save_trace_redacts_secret(fresh_db):
    trace_id = save_trace(
        exc_type="ValueError",
        message="password = 'super_secret'",
        frames=[StackFrame(file="main.py", line=1, function="run", code_context="password = 'super_secret'")],
    )
    entry = get_trace(trace_id)
    assert "super_secret" not in entry.message
    assert "***" in entry.message


def test_trace_kind_persists(fresh_db):
    trace_id = save_trace(
        exc_type="SilentFailure",
        message="click no response",
        frames=[StackFrame(file="ui.py", line=1, function="click", code_context="def click()")],
        trace_kind="silent_failure",
    )
    entry = get_trace(trace_id)
    assert entry.trace_kind == "silent_failure"


def test_migration_adds_trace_kind(temp_db_path):
    """模拟旧 schema 数据库，验证 init_db 迁移后补齐 trace_kind 列。"""
    # 创建旧 schema 表（不含 trace_kind）
    conn = sqlite3.connect(temp_db_path)
    conn.execute("""
        CREATE TABLE traces (
            trace_id TEXT PRIMARY KEY,
            fingerprint TEXT,
            first_seen REAL DEFAULT 0,
            last_seen REAL DEFAULT 0,
            occurrence_count INTEGER DEFAULT 1,
            timestamp REAL NOT NULL,
            exc_type TEXT NOT NULL,
            message TEXT NOT NULL,
            frames_json TEXT NOT NULL,
            source TEXT NOT NULL,
            extra_json TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO traces (trace_id, timestamp, exc_type, message, frames_json, source, extra_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("old123", 1.0, "ValueError", "old error", "[]", "test", "{}"),
    )
    conn.commit()
    conn.close()

    # 切换 settings 并迁移
    original_db_path = settings.db_path
    settings.db_path = temp_db_path
    try:
        logs.init_db()
        entry = get_trace("old123")
        assert entry is not None
        assert entry.trace_kind == "exception"
    finally:
        settings.db_path = original_db_path


def test_compute_fingerprint_ignores_line_number():
    frames1 = [StackFrame(file="main.py", line=10, function="run", code_context="a")]
    frames2 = [StackFrame(file="main.py", line=20, function="run", code_context="b")]
    assert compute_fingerprint("ValueError", frames1) == compute_fingerprint("ValueError", frames2)
