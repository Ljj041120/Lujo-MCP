"""单元测试：URL → handler 反查解析器（url_resolver.py）。

M3 回归覆盖（交接文档要求）：
- resolve 通配路径匹配（FastAPI {param} 通配）
- resolve_from_debug_context 多级优先级提取
- 方法过滤 / 路径清理 / 静默降级
"""

from types import SimpleNamespace

from app.mcp.collectors.url_resolver import (
    _route_matches,
    resolve,
    resolve_from_debug_context,
)


# ---------------------------------------------------------------------------
# _route_matches：路径模板匹配
# ---------------------------------------------------------------------------


def test_route_matches_exact():
    assert _route_matches("/api/users", "/api/users") is True
    assert _route_matches("/api/users", "/api/orders") is False


def test_route_matches_wildcard_param():
    assert _route_matches("/api/users/{user_id}", "/api/users/123") is True
    assert _route_matches("/api/users/{user_id}", "/api/users/abc-1") is True


def test_route_matches_wildcard_not_match_extra_segment():
    assert _route_matches("/api/users/{user_id}", "/api/users/123/orders") is False


def test_route_matches_multiple_params():
    assert _route_matches(
        "/api/orgs/{org_id}/users/{user_id}", "/api/orgs/o1/users/u1"
    ) is True


def test_route_matches_does_not_cross_slash():
    assert _route_matches("/{a}", "/x/y") is False


# ---------------------------------------------------------------------------
# resolve：mock 路由表反查
# ---------------------------------------------------------------------------


def _make_route(path, methods, endpoint_name="get_user", module="app.config"):
    """构造一个伪 Starlette route 对象。

    module 用真实可导入模块（app.config），保证 _handler_to_module_path
    能成功 importlib.import_module 并抽取源文件路径。
    """
    return SimpleNamespace(
        path=path,
        methods={m.upper() for m in methods},
        endpoint=SimpleNamespace(
            __name__=endpoint_name, __module__=module
        ),
    )


def _patch_routes(monkeypatch, routes):
    """替换 _get_fastapi_routes 返回 mock 路由列表。"""
    monkeypatch.setattr(
        "app.mcp.collectors.url_resolver._get_fastapi_routes",
        lambda: [(r, "") for r in routes],
    )


def test_resolve_finds_handler_by_exact_path(monkeypatch):
    _patch_routes(
        monkeypatch,
        [_make_route("/api/users", ["GET"], "get_users")],
    )
    info = resolve("GET", "/api/users")
    assert info is not None
    assert info.function_name == "get_users"
    assert info.route_path == "/api/users"
    assert "GET" in info.methods


def test_resolve_matches_wildcard_path(monkeypatch):
    _patch_routes(
        monkeypatch,
        [_make_route("/api/users/{user_id}", ["GET"], "get_user")],
    )
    info = resolve("GET", "/api/users/123")
    assert info is not None
    assert info.function_name == "get_user"
    assert info.route_path == "/api/users/{user_id}"


def test_resolve_ignores_query_string_and_trailing_slash(monkeypatch):
    _patch_routes(
        monkeypatch,
        [_make_route("/api/users", ["GET"], "get_users")],
    )
    assert resolve("GET", "/api/users?x=1#frag") is not None
    assert resolve("GET", "/api/users/") is not None


def test_resolve_method_filter(monkeypatch):
    _patch_routes(
        monkeypatch,
        [_make_route("/api/users", ["POST"], "create_user")],
    )
    # GET 不匹配 POST 路由 → None
    assert resolve("GET", "/api/users") is None
    # POST 匹配 → 命中
    assert resolve("POST", "/api/users") is not None


def test_resolve_returns_none_for_unmatched_path(monkeypatch):
    _patch_routes(
        monkeypatch,
        [_make_route("/api/users", ["GET"], "get_users")],
    )
    assert resolve("GET", "/api/orders") is None


def test_resolve_returns_none_for_empty_args():
    assert resolve("", "/api/users") is None
    assert resolve("GET", "") is None


# ---------------------------------------------------------------------------
# resolve_from_debug_context：多级优先级提取
# ---------------------------------------------------------------------------


def test_resolve_from_debug_context_uses_input_path(monkeypatch):
    _patch_routes(
        monkeypatch,
        [_make_route("/api/users/{user_id}", ["GET"], "get_user")],
    )
    ctx = {
        "input": {"method": "GET", "path": "/api/users/777"},
        "network_trace": [],
    }
    info = resolve_from_debug_context(ctx)
    assert info is not None
    assert info.function_name == "get_user"


def test_resolve_from_debug_context_falls_back_to_network_trace(monkeypatch):
    _patch_routes(
        monkeypatch,
        [_make_route("/api/items", ["GET"], "list_items")],
    )
    ctx = {
        "input": {},
        "network_trace": [
            {"direction": "inbound", "method": "GET", "url": "/api/items"},
        ],
    }
    info = resolve_from_debug_context(ctx)
    assert info is not None
    assert info.function_name == "list_items"


def test_resolve_from_debug_context_uses_exception_extra(monkeypatch):
    _patch_routes(
        monkeypatch,
        [_make_route("/api/health", ["GET"], "healthz")],
    )
    ctx = {
        "input": {},
        "network_trace": [],
        "exception": {"extra": {"method": "GET", "path": "/api/health"}},
    }
    info = resolve_from_debug_context(ctx)
    assert info is not None
    assert info.function_name == "healthz"


def test_resolve_from_debug_context_returns_none_when_all_miss(monkeypatch):
    _patch_routes(monkeypatch, [])
    ctx = {"input": {}, "network_trace": [], "exception": {}}
    assert resolve_from_debug_context(ctx) is None


def test_resolve_from_debug_context_url_to_path(monkeypatch):
    """完整 URL 也能被 _url_to_path 提取出路径。"""
    _patch_routes(
        monkeypatch,
        [_make_route("/api/users", ["GET"], "get_users")],
    )
    ctx = {
        "input": {"method": "GET", "url": "https://example.com:8080/api/users?x=1"},
    }
    info = resolve_from_debug_context(ctx)
    assert info is not None
    assert info.function_name == "get_users"