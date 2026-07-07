"""
MCP 工具：get_stacktrace
纯数据采集，不做任何 AI 推理判断。
"""
from app.mcp.collectors.stacktrace import get_latest_stacktrace


def tool_get_stacktrace(trace_id: str | None = None) -> dict:
    return get_latest_stacktrace(trace_id)
