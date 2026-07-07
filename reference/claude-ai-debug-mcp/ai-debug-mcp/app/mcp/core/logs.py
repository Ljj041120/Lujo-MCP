"""
追踪日志存储层。

用 SQLite 而不是纯内存字典，原因：
- 服务/进程重启后追踪记录不丢
- 支持简单的按关键字/时间过滤查询
- 带 TTL 清理，避免长期运行内存/磁盘无限增长

注意：SQLite 对多进程并发写入支持有限，如果之后要横向扩展多个 worker，
建议换成 Postgres/Redis，这里作为单机部署场景的轻量方案。
"""
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from app.config import settings
from app.mcp.core.redaction import redact
from app.schemas.context import NetworkRecord, UIEvent
from app.schemas.trace import TraceEntry, StackFrame, TraceSummary

_lock = Lock()


def _ensure_db_dir():
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def _conn():
    _ensure_db_dir()
    conn = sqlite3.connect(settings.db_path, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def _migrate_v1_to_v2(conn: sqlite3.Connection):
    """从旧表结构迁移到新表结构，不删除旧数据。"""
    columns = _existing_columns(conn, "traces")

    if "fingerprint" not in columns:
        conn.execute("ALTER TABLE traces ADD COLUMN fingerprint TEXT")
    if "first_seen" not in columns:
        conn.execute("ALTER TABLE traces ADD COLUMN first_seen REAL DEFAULT 0")
    if "last_seen" not in columns:
        conn.execute("ALTER TABLE traces ADD COLUMN last_seen REAL DEFAULT 0")
    if "occurrence_count" not in columns:
        conn.execute("ALTER TABLE traces ADD COLUMN occurrence_count INTEGER DEFAULT 1")

    # 回填旧记录：用当时的 timestamp 作为 first_seen/last_seen，并计算 fingerprint
    rows = conn.execute(
        "SELECT trace_id, timestamp, exc_type, frames_json FROM traces WHERE fingerprint IS NULL"
    ).fetchall()
    for trace_id, timestamp, exc_type, frames_json in rows:
        frames = [StackFrame(**f) for f in json.loads(frames_json)]
        fingerprint = compute_fingerprint(exc_type, frames)
        conn.execute(
            "UPDATE traces SET fingerprint = ?, first_seen = ?, last_seen = ?, occurrence_count = ? "
            "WHERE trace_id = ?",
            (fingerprint, timestamp, timestamp, 1, trace_id),
        )


def _migrate_v2_to_v3(conn: sqlite3.Connection):
    """补充 trace_kind 列，旧记录默认视为 exception。"""
    columns = _existing_columns(conn, "traces")
    if "trace_kind" not in columns:
        conn.execute("ALTER TABLE traces ADD COLUMN trace_kind TEXT DEFAULT 'exception'")
    # 回填旧记录
    conn.execute("UPDATE traces SET trace_kind = 'exception' WHERE trace_kind IS NULL OR trace_kind = ''")


def init_db():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS traces (
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
                extra_json TEXT NOT NULL,
                trace_kind TEXT DEFAULT 'exception'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_ts ON traces(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_fingerprint ON traces(fingerprint)")
        _migrate_v1_to_v2(conn)
        _migrate_v2_to_v3(conn)
        _init_network_and_ui_tables(conn)


def _init_network_and_ui_tables(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS network_records (
            record_id TEXT PRIMARY KEY,
            trace_id TEXT,
            request_id TEXT,
            timestamp REAL NOT NULL,
            direction TEXT NOT NULL,
            method TEXT,
            url TEXT,
            status_code INTEGER,
            request_body TEXT,
            response_body TEXT,
            duration_ms REAL,
            extra_json TEXT NOT NULL DEFAULT '{}'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_network_trace ON network_records(trace_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_network_request ON network_records(request_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ui_events (
            event_id TEXT PRIMARY KEY,
            trace_id TEXT,
            timestamp REAL NOT NULL,
            event_type TEXT NOT NULL,
            target_selector TEXT,
            component_name TEXT,
            route_path TEXT,
            payload_json TEXT,
            extra_json TEXT NOT NULL DEFAULT '{}'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ui_trace ON ui_events(trace_id)")


def compute_fingerprint(exc_type: str, frames: list[StackFrame]) -> str:
    """用异常类型 + 关键堆栈帧（file:function，忽略行号差异）算指纹。"""
    parts = [exc_type]
    for frame in frames[:3]:
        parts.append(f"{frame.file}:{frame.function}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _redact_frame(frame: StackFrame) -> StackFrame:
    return StackFrame(
        file=frame.file,
        line=frame.line,
        function=frame.function,
        code_context=redact(frame.code_context),
    )


def save_trace(exc_type: str, message: str, frames: list[StackFrame],
               source: str = "unknown", extra: dict | None = None,
               trace_kind: str = "exception") -> str:
    """保存或聚合一条异常追踪记录，返回 trace_id。相同 fingerprint 会累加 occurrence_count。"""
    fingerprint = compute_fingerprint(exc_type, frames)
    redacted_message = redact(message) or ""
    redacted_frames = [_redact_frame(f) for f in frames]
    now = time.time()
    extra = extra or {}

    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT trace_id, occurrence_count, first_seen FROM traces "
            "WHERE fingerprint = ? ORDER BY last_seen DESC LIMIT 1",
            (fingerprint,),
        ).fetchone()

        if row:
            trace_id, occurrence_count, first_seen = row
            conn.execute(
                "UPDATE traces SET occurrence_count = ?, last_seen = ?, timestamp = ?, "
                "message = ?, frames_json = ?, source = ?, extra_json = ?, trace_kind = ? WHERE trace_id = ?",
                (
                    occurrence_count + 1,
                    now,
                    now,
                    redacted_message,
                    json.dumps([f.model_dump() for f in redacted_frames], ensure_ascii=False),
                    source,
                    json.dumps(extra, ensure_ascii=False),
                    trace_kind,
                    trace_id,
                ),
            )
        else:
            trace_id = uuid.uuid4().hex[:12]
            conn.execute(
                "INSERT INTO traces (trace_id, fingerprint, first_seen, last_seen, occurrence_count, "
                "timestamp, exc_type, message, frames_json, source, extra_json, trace_kind) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    trace_id,
                    fingerprint,
                    now,
                    now,
                    1,
                    now,
                    exc_type,
                    redacted_message,
                    json.dumps([f.model_dump() for f in redacted_frames], ensure_ascii=False),
                    source,
                    json.dumps(extra, ensure_ascii=False),
                    trace_kind,
                ),
            )

    _cleanup_expired()
    return trace_id


def _row_to_entry(row) -> TraceEntry:
    (
        trace_id, fingerprint, first_seen, last_seen, occurrence_count,
        timestamp, exc_type, message, frames_json, source, extra_json,
        trace_kind,
    ) = row
    frames = [StackFrame(**f) for f in json.loads(frames_json)]
    return TraceEntry(
        trace_id=trace_id,
        fingerprint=fingerprint,
        first_seen=first_seen,
        last_seen=last_seen,
        occurrence_count=occurrence_count,
        timestamp=timestamp,
        exc_type=exc_type,
        message=message,
        frames=frames,
        source=source,
        extra=json.loads(extra_json),
        trace_kind=trace_kind or "exception",
    )


def get_trace(trace_id: str | None = None) -> TraceEntry | None:
    """取指定 trace_id，不传则取最新一条（按 last_seen）"""
    with _conn() as conn:
        if trace_id:
            row = conn.execute(
                "SELECT trace_id, fingerprint, first_seen, last_seen, occurrence_count, "
                "timestamp, exc_type, message, frames_json, source, extra_json, trace_kind "
                "FROM traces WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT trace_id, fingerprint, first_seen, last_seen, occurrence_count, "
                "timestamp, exc_type, message, frames_json, source, extra_json, trace_kind "
                "FROM traces ORDER BY last_seen DESC LIMIT 1"
            ).fetchone()
    return _row_to_entry(row) if row else None


def list_recent_traces(limit: int = 10) -> list[TraceSummary]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT trace_id, fingerprint, first_seen, last_seen, occurrence_count, "
            "timestamp, exc_type, message, frames_json "
            "FROM traces ORDER BY last_seen DESC LIMIT ?",
            (limit,),
        ).fetchall()
    summaries = []
    for (
        trace_id, fingerprint, first_seen, last_seen, occurrence_count,
        timestamp, exc_type, message, frames_json,
    ) in rows:
        frames = json.loads(frames_json)
        top_frame = None
        if frames:
            f = frames[0]
            top_frame = f"{f['file']}:{f['line']} in {f['function']}"
        summaries.append(TraceSummary(
            trace_id=trace_id,
            fingerprint=fingerprint,
            first_seen=first_seen,
            last_seen=last_seen,
            occurrence_count=occurrence_count,
            timestamp=timestamp,
            exc_type=exc_type,
            message=message,
            top_frame=top_frame,
        ))
    return summaries


def search_logs(keyword: str, since_minutes: int = 30, limit: int = 20) -> list[TraceSummary]:
    since_ts = time.time() - since_minutes * 60
    with _conn() as conn:
        rows = conn.execute(
            "SELECT trace_id, fingerprint, first_seen, last_seen, occurrence_count, "
            "timestamp, exc_type, message, frames_json FROM traces "
            "WHERE last_seen >= ? AND (message LIKE ? OR exc_type LIKE ?) "
            "ORDER BY last_seen DESC LIMIT ?",
            (since_ts, f"%{keyword}%", f"%{keyword}%", limit),
        ).fetchall()
    results = []
    for (
        trace_id, fingerprint, first_seen, last_seen, occurrence_count,
        timestamp, exc_type, message, frames_json,
    ) in rows:
        frames = json.loads(frames_json)
        top_frame = None
        if frames:
            f = frames[0]
            top_frame = f"{f['file']}:{f['line']} in {f['function']}"
        results.append(TraceSummary(
            trace_id=trace_id,
            fingerprint=fingerprint,
            first_seen=first_seen,
            last_seen=last_seen,
            occurrence_count=occurrence_count,
            timestamp=timestamp,
            exc_type=exc_type,
            message=message,
            top_frame=top_frame,
        ))
    return results


def save_network_record(record: NetworkRecord, trace_id: str | None = None,
                        request_id: str | None = None, extra: dict | None = None) -> str:
    """保存一条网络请求记录，返回 record_id。"""
    record_id = record.record_id or uuid.uuid4().hex[:12]
    extra = extra or {}
    with _lock, _conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO network_records
            (record_id, trace_id, request_id, timestamp, direction, method, url,
             status_code, request_body, response_body, duration_ms, extra_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                trace_id,
                request_id,
                record.timestamp,
                record.direction,
                record.method,
                redact(record.url),
                record.status_code,
                redact(record.request_body),
                redact(record.response_body),
                record.duration_ms,
                json.dumps(extra, ensure_ascii=False),
            ),
        )
    return record_id


def save_ui_event(event: UIEvent, trace_id: str | None = None,
                  extra: dict | None = None) -> str:
    """保存一条前端 UI 事件，返回 event_id。"""
    event_id = event.event_id or uuid.uuid4().hex[:12]
    extra = extra or {}
    with _lock, _conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO ui_events
            (event_id, trace_id, timestamp, event_type, target_selector,
             component_name, route_path, payload_json, extra_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                trace_id,
                event.timestamp,
                event.event_type,
                event.target_selector,
                event.component_name,
                event.route_path,
                redact(event.payload_json),
                json.dumps(extra, ensure_ascii=False),
            ),
        )
    return event_id


def get_network_records(trace_id: str) -> list[NetworkRecord]:
    """查询与某条 trace 关联的所有网络请求记录。"""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT record_id, trace_id, request_id, timestamp, direction, method, url,
                   status_code, request_body, response_body, duration_ms, extra_json
            FROM network_records
            WHERE trace_id = ?
            ORDER BY timestamp
            """,
            (trace_id,),
        ).fetchall()
    return [
        NetworkRecord(
            record_id=row[0],
            timestamp=row[3],
            direction=row[4],
            method=row[5],
            url=row[6],
            status_code=row[7],
            request_body=row[8],
            response_body=row[9],
            duration_ms=row[10],
        )
        for row in rows
    ]


def get_ui_events(trace_id: str) -> list[UIEvent]:
    """查询与某条 trace 关联的所有 UI 事件。"""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT event_id, trace_id, timestamp, event_type, target_selector,
                   component_name, route_path, payload_json, extra_json
            FROM ui_events
            WHERE trace_id = ?
            ORDER BY timestamp
            """,
            (trace_id,),
        ).fetchall()
    return [
        UIEvent(
            event_id=row[0],
            timestamp=row[2],
            event_type=row[3],
            target_selector=row[4],
            component_name=row[5],
            route_path=row[6],
            payload_json=row[7],
        )
        for row in rows
    ]


def _cleanup_expired():
    """清理超过 TTL 的旧记录，每次写入后触发一次，成本很低"""
    cutoff = time.time() - settings.trace_ttl_seconds
    with _conn() as conn:
        conn.execute("DELETE FROM traces WHERE last_seen < ?", (cutoff,))
        conn.execute("DELETE FROM network_records WHERE timestamp < ?", (cutoff,))
        conn.execute("DELETE FROM ui_events WHERE timestamp < ?", (cutoff,))


init_db()
