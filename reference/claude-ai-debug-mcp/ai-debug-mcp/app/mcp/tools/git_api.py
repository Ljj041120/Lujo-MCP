"""
MCP 工具：get_blame_for_frame / get_recent_diff
"""
from app.mcp.core.git import get_blame_for_frame, get_recent_diff


def tool_get_blame_for_frame(file: str, line: int) -> dict:
    result = get_blame_for_frame(file, line)
    return {"found": result is not None, "blame": result}


def tool_get_recent_diff(file: str, commits_back: int = 3) -> dict:
    result = get_recent_diff(file, commits_back)
    return {"found": result is not None, "diff": result}
