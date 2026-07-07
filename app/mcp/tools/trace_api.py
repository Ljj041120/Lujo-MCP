"""MCP 追踪工具 —— 获取原始追踪日志 / 检索近期捕获的异常"""

from app.mcp.core.logs import get_logs
from app.mcp.core.errors import list_recent, search as search_errors

TOOL_DEF = {
    "name": "trace",
    "description": "获取请求的完整原始追踪日志（时间、步骤、数据的时序列表）",
    "inputSchema": {
        "type": "object",
        "properties": {
            "request_id": {"type": "string", "description": "请求 ID"},
        },
        "required": ["request_id"],
    },
}


def handler(arguments: dict) -> dict:
    """MCP 工具 handler"""
    request_id = arguments.get("request_id", "")
    trace = get_logs(request_id)
    return {
        "request_id": request_id,
        "trace": trace,
        "step_count": len(trace),
    }


def invoke(body) -> dict:
    return handler({"request_id": body.request_id})


def list_recent_traces(limit: int = 10) -> list:
    """列出最近被自动捕获的异常摘要（不含完整堆栈）。"""
    items = list_recent(limit)
    return [
        {
            "error_id": e["error_id"],
            "type": e["type"],
            "message": e["message"],
            "source": e["source"],
            "timestamp": e["timestamp"],
            "frame_count": e["frame_count"],
        }
        for e in items
    ]


def search_logs(keyword: str, since_minutes: int = 30) -> list:
    """按关键字 + 时间窗搜索近期捕获的异常。"""
    items = search_errors(keyword, since_minutes)
    return [
        {
            "error_id": e["error_id"],
            "type": e["type"],
            "message": e["message"],
            "source": e["source"],
            "timestamp": e["timestamp"],
        }
        for e in items
    ]
