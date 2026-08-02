"""MCP 工具注册入口 —— 统一注册所有工具，供 HTTP / stdio 传输复用"""

import logging

from app.mcp.protocol.server import register_tool

logger = logging.getLogger("Lujo-MCP.tools")


def register_all_tools():
    """注册全部 MCP 工具（HTTP 与 stdio 传输共用同一份业务逻辑）"""
    from app.mcp.tools.debug_api import TOOL_DEF as debug_tool, handler as debug_handler
    from app.mcp.tools.context_api import TOOL_DEF as context_tool, handler as context_handler
    from app.mcp.tools.trace_api import TOOL_DEF as trace_tool, handler as trace_handler
    from app.mcp.tools.stacktrace_api import TOOL_DEF as stacktrace_tool, handler as stacktrace_handler
    # M3-M7 新增工具
    from app.mcp.tools.network_api import (
        NETWORK_INGEST_DEF, NETWORK_TRACE_DEF,
        ingest_network_handler, get_network_trace_handler,
    )
    from app.mcp.tools.git_api import BLAME_DEF, RECENT_DIFF_DEF, blame_handler, recent_diff_handler
    from app.mcp.tools.silent_failure_api import SILENT_FAILURE_DEF, silent_failure_handler
    from app.mcp.tools.ingest_api import INGEST_ERROR_DEF, ingest_error_handler
    from app.mcp.tools.console_api import INGEST_CONSOLE_DEF, ingest_console_handler
    from app.mcp.tools.spec_api import RELATED_SPECS_DEF, related_specs_handler
    from app.mcp.tools.verify_api import VERIFY_DEF, verify_handler
    from app.mcp.tools.verify_ui_api import VERIFY_UI_DEF, verify_ui_handler
    from app.mcp.tools.auto_test_api import AUTO_TEST_DEF, auto_test_handler
    from app.mcp.tools.repair_api import (
        REPAIR_ASYNC_DEF, REPAIR_RESULT_DEF,
        repair_async_handler, repair_result_handler,
    )

    _tool_registrations = [
        ("debug", debug_tool, debug_handler),
        ("context", context_tool, context_handler),
        ("trace", trace_tool, trace_handler),
        ("stacktrace", stacktrace_tool, stacktrace_handler),
        ("ingest_network", NETWORK_INGEST_DEF, ingest_network_handler),
        ("network_trace", NETWORK_TRACE_DEF, get_network_trace_handler),
        ("blame", BLAME_DEF, blame_handler),
        ("recent_diff", RECENT_DIFF_DEF, recent_diff_handler),
        ("silent_failure", SILENT_FAILURE_DEF, silent_failure_handler),
        ("ingest_error", INGEST_ERROR_DEF, ingest_error_handler),
        ("ingest_console", INGEST_CONSOLE_DEF, ingest_console_handler),
        ("related_specs", RELATED_SPECS_DEF, related_specs_handler),
        ("verify", VERIFY_DEF, verify_handler),
        ("verify_ui", VERIFY_UI_DEF, verify_ui_handler),
        ("auto_test", AUTO_TEST_DEF, auto_test_handler),
        ("repair_async", REPAIR_ASYNC_DEF, repair_async_handler),
        ("repair_result", REPAIR_RESULT_DEF, repair_result_handler),
    ]
    for name, tool_def, handler in _tool_registrations:
        try:
            register_tool(**tool_def, handler=handler)
        except Exception as e:
            logger.error("工具注册失败: name=%s error=%s", name, str(e), exc_info=True)
            raise RuntimeError(f"工具 '{name}' 注册失败: {e}") from e


# ── MCP 工具角色需求映射（供 mcp_routes.py 在 tools/call 分发前消费）──
# 角色体系：viewer（只读）| developer（读+写）| admin（完全控制）
# 仅 HTTP 传输层强制此门控；stdio 传输依赖进程隔离，不适用
TOOL_ROLE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    # ── 写类工具（创建 trace / 触发 LLM / 外部副作用）──
    "debug":                ("admin", "developer"),
    "ingest_network":       ("admin", "developer"),
    "ingest_silent_failure": ("admin", "developer"),
    "ingest_error":         ("admin", "developer"),
    "ingest_console":       ("admin", "developer"),
    "verify":               ("admin", "developer"),
    "verify_ui":            ("admin", "developer"),
    "auto_test":            ("admin", "developer"),
    "repair_async":         ("admin", "developer"),
    # ── 只读类工具 ──
    "context":              ("admin", "developer", "viewer"),
    "trace":                ("admin", "developer", "viewer"),
    "stacktrace":           ("admin", "developer", "viewer"),
    "get_network_trace":    ("admin", "developer", "viewer"),
    "get_blame_for_frame":  ("admin", "developer", "viewer"),
    "get_recent_diff":      ("admin", "developer", "viewer"),
    "get_related_specs":    ("admin", "developer", "viewer"),
    "repair_result":        ("admin", "developer", "viewer"),
}
