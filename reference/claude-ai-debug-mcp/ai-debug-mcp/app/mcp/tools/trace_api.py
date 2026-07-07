"""
MCP 工具：list_recent_traces / search_logs
"""
from app.mcp.core.logs import list_recent_traces, search_logs


def tool_list_recent_traces(limit: int = 10) -> list[dict]:
    return [s.model_dump() for s in list_recent_traces(limit)]


def tool_search_logs(keyword: str, since_minutes: int = 30, limit: int = 20) -> list[dict]:
    return [s.model_dump() for s in search_logs(keyword, since_minutes, limit)]
