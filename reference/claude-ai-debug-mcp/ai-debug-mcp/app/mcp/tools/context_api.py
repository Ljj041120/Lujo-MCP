"""
MCP 工具：get_debug_context —— 核心工具，一次调用打包好一切给AI。
"""
from app.mcp.builders.context import build_debug_context


def tool_get_debug_context(trace_id: str | None = None) -> dict:
    context = build_debug_context(trace_id)
    if context is None:
        return {"found": False, "message": "没有找到追踪记录，可能还没有异常发生过或trace_id不存在"}
    result = context.model_dump()
    result["found"] = True
    return result
