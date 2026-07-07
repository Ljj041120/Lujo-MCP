"""单元测试：存储层"""
import pytest
import time

from app.mcp.core.storage.memory_store import MemoryTraceStore, MemorySessionStore


# ════════════════════════════════════════════
#  内存存储测试（始终可用）
# ════════════════════════════════════════════

class TestMemoryTraceStore:

    def setup_method(self):
        self.store = MemoryTraceStore()

    def test_save_and_get(self):
        self.store.save_entry("rid-1", {"timestamp": 1.0, "step": "start", "data": {"a": 1}})
        self.store.save_entry("rid-1", {"timestamp": 2.0, "step": "end", "data": {"b": 2}})

        entries = self.store.get_entries("rid-1")
        assert len(entries) == 2
        assert entries[0]["step"] == "start"
        assert entries[1]["step"] == "end"

    def test_delete(self):
        self.store.save_entry("rid-2", {"timestamp": 1.0, "step": "test", "data": None})
        self.store.delete("rid-2")
        assert self.store.get_entries("rid-2") == []

    def test_cleanup_expired(self):
        self.store.save_entry("rid-old", {"timestamp": time.time() - 7200, "step": "old", "data": None})
        self.store.save_entry("rid-new", {"timestamp": time.time(), "step": "new", "data": None})

        count = self.store.cleanup_expired(ttl_seconds=3600)
        assert count == 1
        assert self.store.get_entries("rid-old") == []
        assert len(self.store.get_entries("rid-new")) == 1


class TestMemorySessionStore:

    def setup_method(self):
        self.store = MemorySessionStore()

    def test_save_get_delete(self):
        self.store.save("s-1", {"session_id": "s-1", "created_at": time.time(), "metadata": {}})
        s = self.store.get("s-1")
        assert s is not None
        assert s["session_id"] == "s-1"

        self.store.delete("s-1")
        assert self.store.get("s-1") is None

    def test_list_active(self):
        now = time.time()
        self.store.save("s-active", {"session_id": "s-active", "created_at": now, "last_active": now})
        self.store._store["s-stale"] = {
            "session_id": "s-stale",
            "created_at": now - 7200,
            "last_active": now - 7200,
        }

        active = self.store.list_active(ttl_seconds=3600)
        assert len(active) == 1, f"Expected 1 active, got: {active}"
        assert active[0]["session_id"] == "s-active"


# ════════════════════════════════════════════
#  PostgreSQL 存储测试（需要 PG 运行，否则跳过）
# ════════════════════════════════════════════

psycopg2 = pytest.importorskip("psycopg2", reason="psycopg2 未安装，跳过 PG 测试")

PG_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "ai_debug_mcp_test",
    "user": "postgres",
    "password": "",
}


def _pg_available() -> bool:
    """检测 PostgreSQL 是否可用"""
    try:
        conn = psycopg2.connect(**PG_CONFIG)
        conn.close()
        return True
    except Exception:
        return False


_pg_skip = not _pg_available()


@pytest.mark.skipif(_pg_skip, reason="PostgreSQL 不可用，跳过 PG 测试")
class TestPGTraceStore:

    def setup_method(self):
        from app.mcp.core.storage.pg_store import PGTraceStore
        # 临时覆盖连接参数
        import app.mcp.core.storage.pg_store as mod
        self._orig_pool = mod._pool
        self._orig_get_pool = mod._get_pool

        def _test_pool():
            return psycopg2.pool.ThreadedConnectionPool(2, 5, **PG_CONFIG)

        mod._get_pool = _test_pool
        mod._pool = None
        self.store = PGTraceStore()
        # 清理残留
        conn = mod._get_pool().getconn()
        conn.execute("DELETE FROM traces")
        conn.execute("DELETE FROM sessions")
        conn.commit()
        mod._get_pool().putconn(conn)

    def teardown_method(self):
        import app.mcp.core.storage.pg_store as mod
        if mod._pool:
            mod._pool.closeall()
            mod._pool = None
        mod._get_pool = self._orig_get_pool
        mod._pool = self._orig_pool

    def test_save_and_get(self):
        self.store.save_entry("rid-pg-1", {"timestamp": 1.0, "step": "start", "data": {"key": "val"}})
        self.store.save_entry("rid-pg-1", {"timestamp": 2.0, "step": "end", "data": None})

        entries = self.store.get_entries("rid-pg-1")
        assert len(entries) == 2
        assert entries[0]["step"] == "start"
        assert entries[0]["data"] == {"key": "val"}
        assert entries[1]["data"] is None

    def test_delete(self):
        self.store.save_entry("rid-pg-del", {"timestamp": 1.0, "step": "x", "data": None})
        self.store.delete("rid-pg-del")
        assert self.store.get_entries("rid-pg-del") == []

    def test_cleanup_expired(self):
        self.store.save_entry("rid-pg-old", {"timestamp": time.time() - 7200, "step": "old", "data": None})
        self.store.save_entry("rid-pg-new", {"timestamp": time.time(), "step": "new", "data": None})

        count = self.store.cleanup_expired(ttl_seconds=3600)
        assert count >= 1


@pytest.mark.skipif(_pg_skip, reason="PostgreSQL 不可用，跳过 PG 测试")
class TestPGSessionStore:

    def setup_method(self):
        from app.mcp.core.storage.pg_store import PGSessionStore
        import app.mcp.core.storage.pg_store as mod
        self._orig_pool = mod._pool
        self._orig_get_pool = mod._get_pool

        def _test_pool():
            return psycopg2.pool.ThreadedConnectionPool(2, 5, **PG_CONFIG)

        mod._get_pool = _test_pool
        mod._pool = None
        self.store = PGSessionStore()
        conn = mod._get_pool().getconn()
        conn.execute("DELETE FROM sessions")
        conn.commit()
        mod._get_pool().putconn(conn)

    def teardown_method(self):
        import app.mcp.core.storage.pg_store as mod
        if mod._pool:
            mod._pool.closeall()
            mod._pool = None
        mod._get_pool = self._orig_get_pool
        mod._pool = self._orig_pool

    def test_save_get_delete(self):
        now = time.time()
        self.store.save("s-pg-1", {"session_id": "s-pg-1", "created_at": now, "metadata": {"role": "test"}})
        s = self.store.get("s-pg-1")
        assert s is not None
        assert s["session_id"] == "s-pg-1"
        assert s["metadata"] == {"role": "test"}

        self.store.delete("s-pg-1")
        assert self.store.get("s-pg-1") is None

    def test_list_active(self):
        now = time.time()
        self.store.save("s-pg-active", {"session_id": "s-pg-active", "created_at": now, "metadata": {}})

        # 用 save 的 ON CONFLICT 写入过期 session
        import app.mcp.core.storage.pg_store as mod
        conn = mod._get_pool().getconn()
        conn.execute(
            "INSERT INTO sessions (session_id, created_at, last_active, metadata) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (session_id) DO UPDATE SET last_active = EXCLUDED.last_active",
            ("s-pg-stale", now - 7200, now - 7200, "{}"),
        )
        conn.commit()
        mod._get_pool().putconn(conn)

        active = self.store.list_active(ttl_seconds=3600)
        assert len(active) >= 1
        ids = [s["session_id"] for s in active]
        assert "s-pg-active" in ids
        assert "s-pg-stale" not in ids
