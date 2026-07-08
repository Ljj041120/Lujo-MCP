"""工具注册单测：验证 HTTP 侧 register_all_tools 注册了全部 10 个工具（4 旧 + 6 新）"""
from app.mcp.protocol.server import _tool_registry
from app.mcp.tools import register_all_tools


def test_all_tools_registered():
    register_all_tools()
    names = set(_tool_registry.keys())
    expected = {
        # 既有 4 个
        "debug", "context", "trace", "stacktrace",
        # M3-M9 新增 7 个
        "ingest_network", "get_network_trace",
        "get_blame_for_frame", "get_recent_diff",
        "ingest_silent_failure", "ingest_error",
        "get_related_specs",
        # V3 新增
        "verify",
    }
    missing = expected - names
    assert not missing, f"未注册的工具: {missing}"
    assert len(names) >= 12


def test_each_registered_tool_has_handler():
    register_all_tools()
    for name, tool in _tool_registry.items():
        assert callable(tool["handler"]), f"{name} handler 不可调用"
        assert tool["inputSchema"] is not None, f"{name} 缺 inputSchema"
