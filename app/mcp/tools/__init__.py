"""MCP 工具注册入口 —— 统一注册所有工具，供 HTTP / stdio 传输复用"""

from app.mcp.protocol.server import register_tool


def register_all_tools():
    """注册全部 MCP 工具（HTTP 与 stdio 传输共用同一份业务逻辑）"""
    from app.mcp.tools.debug_api import TOOL_DEF as debug_tool, handler as debug_handler
    from app.mcp.tools.context_api import TOOL_DEF as context_tool, handler as context_handler
    from app.mcp.tools.trace_api import TOOL_DEF as trace_tool, handler as trace_handler
    from app.mcp.tools.stacktrace_api import TOOL_DEF as stacktrace_tool, handler as stacktrace_handler

    register_tool(**debug_tool, handler=debug_handler)
    register_tool(**context_tool, handler=context_handler)
    register_tool(**trace_tool, handler=trace_handler)
    register_tool(**stacktrace_tool, handler=stacktrace_handler)
