"""单元测试：规范存储 spec_store"""
import pytest
from app.mcp.verifier import spec_store


@pytest.fixture(autouse=True)
def _isolate_spec_store():
    """每个用例前后清空 spec_store，避免跨用例污染。"""
    spec_store.clear()
    yield
    spec_store.clear()


class TestCreateAndGet:

    def test_create_returns_id(self):
        spec_id = spec_store.create({
            "kind": "api",
            "target": "GET /api/user",
            "expect": {"status": 200, "body_rules": {"name": "Alice"}},
        })
        assert spec_id.startswith("spec-")

    def test_create_with_explicit_id(self):
        spec_id = spec_store.create({
            "id": "my-spec-1",
            "kind": "api",
            "target": "GET /api/user",
            "expect": {"status": 200},
        })
        assert spec_id == "my-spec-1"

    def test_get_existing(self):
        spec_id = spec_store.create({
            "kind": "api",
            "target": "POST /api/login",
            "expect": {"status": 200},
        })
        spec = spec_store.get(spec_id)
        assert spec is not None
        assert spec["id"] == spec_id
        assert spec["kind"] == "api"
        assert spec["target"] == "POST /api/login"
        assert spec["expect"] == {"status": 200}
        assert "created_at" in spec
        assert "updated_at" in spec

    def test_get_nonexistent(self):
        assert spec_store.get("no-such-id") is None


class TestUpdate:

    def test_update_partial(self):
        spec_id = spec_store.create({
            "kind": "api",
            "target": "GET /api/user",
            "expect": {"status": 200},
        })
        updated = spec_store.update(spec_id, {"expect": {"status": 201}})
        assert updated is not None
        assert updated["expect"] == {"status": 201}
        assert updated["kind"] == "api"  # 未改的字段保留

        # 确认内存也更新了
        again = spec_store.get(spec_id)
        assert again["expect"] == {"status": 201}

    def test_update_nonexistent(self):
        result = spec_store.update("no-such-id", {"kind": "ui"})
        assert result is None

    def test_update_id_immutable(self):
        spec_id = spec_store.create({"kind": "api", "target": "x", "expect": {}})
        spec_store.update(spec_id, {"id": "hacked"})
        spec = spec_store.get(spec_id)
        assert spec["id"] == spec_id  # id 没被改


class TestDelete:

    def test_delete_existing(self):
        spec_id = spec_store.create({"kind": "api", "target": "x", "expect": {}})
        assert spec_store.delete(spec_id) is True
        assert spec_store.get(spec_id) is None

    def test_delete_nonexistent(self):
        assert spec_store.delete("no-such-id") is False


class TestListSpecs:

    def test_list_all(self):
        spec_store.create({"kind": "api", "target": "GET /a", "expect": {}})
        spec_store.create({"kind": "ui", "target": "click #btn", "expect": {}})
        specs = spec_store.list_specs()
        assert len(specs) == 2

    def test_list_filter_by_kind(self):
        spec_store.create({"kind": "api", "target": "GET /a", "expect": {}})
        spec_store.create({"kind": "ui", "target": "click #btn", "expect": {}})
        spec_store.create({"kind": "api", "target": "GET /b", "expect": {}})

        api_specs = spec_store.list_specs(kind="api")
        assert len(api_specs) == 2
        assert all(s["kind"] == "api" for s in api_specs)

    def test_list_filter_by_target(self):
        spec_store.create({"kind": "api", "target": "GET /api/user", "expect": {}})
        spec_store.create({"kind": "api", "target": "GET /api/order", "expect": {}})

        user_specs = spec_store.list_specs(target="user")
        assert len(user_specs) == 1
        assert user_specs[0]["target"] == "GET /api/user"

    def test_list_empty(self):
        assert spec_store.list_specs() == []


class TestAssertEngineIntegration:

    def test_spec_from_store_works_with_assert(self):
        """spec_store 存的 spec 能直接喂给 assert_behavior"""
        from app.mcp.verifier.assert_engine import assert_behavior

        spec_id = spec_store.create({
            "kind": "api",
            "target": "GET /api/user",
            "expect": {"status": 200, "body_rules": {"name": "Alice"}},
        })
        spec = spec_store.get(spec_id)

        actual = {"status_code": 200, "body": {"name": "Alice"}}
        result = assert_behavior(actual, spec)
        assert result["matched"] is True
