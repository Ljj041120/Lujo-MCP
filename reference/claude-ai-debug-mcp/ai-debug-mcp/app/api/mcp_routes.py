"""
/mcp/* —— REST 版本的工具调用接口。

注意：这不是真正的MCP协议（真正的MCP协议走 app/mcp_server.py 的 stdio + JSON-RPC）。
这里存在的目的是方便你在没有MCP客户端的情况下，用curl/Postman快速验证
每个工具的业务逻辑是否正确，本质是 mcp/tools/ 下函数的HTTP包装。
"""
from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import verify_api_key
from app.mcp.tools.stacktrace_api import tool_get_stacktrace
from app.mcp.tools.trace_api import tool_list_recent_traces, tool_search_logs
from app.mcp.tools.context_api import tool_get_debug_context
from app.mcp.tools.debug_api import tool_get_runtime_snapshot, tool_analyze_with_llm
from app.mcp.tools.ingest_api import tool_ingest_error
from app.mcp.tools.git_api import tool_get_blame_for_frame, tool_get_recent_diff
from app.mcp.tools.spec_api import tool_get_related_specs
from app.mcp.tools.silent_failure_api import tool_ingest_silent_failure
from app.mcp.tools.network_api import tool_ingest_network, tool_get_network_trace
from app.schemas.mcp_tool import ToolCallRequest, ToolListResponse, ToolDescriptor, ToolCallResponse

router = APIRouter(prefix="/mcp", tags=["mcp-debug-only"], dependencies=[Depends(verify_api_key)])

_TOOL_REGISTRY = {
    "get_stacktrace": lambda args: tool_get_stacktrace(args.get("trace_id")),
    "get_runtime_snapshot": lambda args: tool_get_runtime_snapshot(),
    "search_logs": lambda args: tool_search_logs(
        args["keyword"], args.get("since_minutes", 30), args.get("limit", 20)
    ),
    "get_debug_context": lambda args: tool_get_debug_context(args.get("trace_id")),
    "list_recent_traces": lambda args: tool_list_recent_traces(args.get("limit", 10)),
    "ingest_error": lambda args: tool_ingest_error(
        exc_type=args["exc_type"],
        message=args["message"],
        frames=args.get("frames", []),
        source=args.get("source", "http_ingest"),
        extra=args.get("extra", {}),
    ),
    "get_blame_for_frame": lambda args: tool_get_blame_for_frame(args["file"], args["line"]),
    "get_recent_diff": lambda args: tool_get_recent_diff(args["file"], args.get("commits_back", 3)),
    "get_related_specs": lambda args: tool_get_related_specs(args["file"]),
    "ingest_silent_failure": lambda args: tool_ingest_silent_failure(
        message=args["message"],
        frames=args.get("frames", []),
        ui_events=args.get("ui_events", []),
        network_records=args.get("network_records", []),
        expectation=args.get("expectation", {}),
        source=args.get("source", "browser_sdk"),
        extra=args.get("extra", {}),
    ),
    "ingest_network": lambda args: tool_ingest_network(
        record=args["record"],
        trace_id=args.get("trace_id"),
        request_id=args.get("request_id"),
    ),
    "get_network_trace": lambda args: tool_get_network_trace(args["trace_id"]),
    "analyze_with_llm": lambda args: tool_analyze_with_llm(args.get("trace_id")),
}

_TOOL_DESCRIPTIONS = {
    "get_stacktrace": "获取最近一次异常堆栈",
    "get_runtime_snapshot": "获取运行时快照",
    "search_logs": "按关键字搜索追踪日志",
    "get_debug_context": "获取完整调试上下文（核心工具）",
    "list_recent_traces": "列出最近的追踪记录摘要",
    "ingest_error": "任意语言/进程主动上报错误",
    "get_blame_for_frame": "查询文件/行最后一次修改的 commit 和作者",
    "get_recent_diff": "获取文件最近 N 次 commit 的 diff",
    "get_related_specs": "查询与文件相关的项目规范片段",
    "ingest_silent_failure": "上报前端静默失败",
    "ingest_network": "单条上报网络请求记录",
    "get_network_trace": "查询 trace 关联的网络请求链",
    "analyze_with_llm": "【可选】调用内置LLM分析根因",
}


@router.post("/tools/list", response_model=ToolListResponse)
def list_tools():
    return ToolListResponse(tools=[
        ToolDescriptor(name=name, description=desc, input_schema={})
        for name, desc in _TOOL_DESCRIPTIONS.items()
    ])


@router.post("/tools/{tool_name}", response_model=ToolCallResponse)
def call_tool(tool_name: str, req: ToolCallRequest):
    fn = _TOOL_REGISTRY.get(tool_name)
    if fn is None:
        raise HTTPException(status_code=404, detail=f"未知工具: {tool_name}")
    try:
        result = fn(req.arguments)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"缺少必要参数: {e}")
    except Exception as e:
        return ToolCallResponse(content=None, error=str(e))
    return ToolCallResponse(content=result)
