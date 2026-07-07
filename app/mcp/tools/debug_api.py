"""MCP 调试工具 —— 一键运行调试流程 / 运行时快照 / 调试上下文 / LLM 分析"""

import sys

from app.mcp.core.logs import create_request_id, add_log, get_logs
from app.mcp.builders.context import build_context
from app.mcp.collectors.runtime import collect_runtime_snapshot
from app.mcp.collectors.stacktrace import capture_exception, format_trace_for_ai
from app.mcp.collectors.code_locator import get_snippets_for_frames
from app.mcp.core.errors import get_by_id, get_latest
from app.llm.analyzer import analyze

TOOL_DEF = {
    "name": "debug",
    "description": "执行完整调试流程：接收请求数据，记录执行链路，返回结构化调试上下文",
    "inputSchema": {
        "type": "object",
        "properties": {
            "payload": {"type": "object", "description": "要调试的请求数据"},
            "metadata": {"type": "object", "description": "附加元数据"},
        },
    },
}


def handler(arguments: dict) -> dict:
    """MCP 工具 handler（接收 dict 参数，返回 dict 结果）"""
    payload = arguments.get("payload", {})
    metadata = arguments.get("metadata")

    request_id = create_request_id()
    add_log(request_id, "mcp_debug_start", payload)
    add_log(request_id, "mcp_processing", {"metadata": metadata})
    result = {"echo": payload, "status": "success"}
    add_log(request_id, "mcp_response_ready", result)

    trace = get_logs(request_id)
    context = build_context(request_id, trace)

    return {
        "request_id": request_id,
        "result": result,
        "trace": trace,
        "context": context,
    }


# 兼容旧调用方式
def invoke(body) -> dict:
    return handler({"payload": getattr(body, "arguments", {}).get("payload", {})})


def get_runtime_snapshot() -> dict:
    """获取当前进程运行时快照（CPU/内存/线程等）。"""
    return collect_runtime_snapshot()


def _build_context_from_error(error_id: str | None) -> dict:
    """基于近期捕获的异常构建调试上下文（含源码片段）。"""
    err = get_by_id(error_id) if error_id else get_latest()
    if err is None:
        # 退而求其次：尝试取当前线程未捕获异常
        exc_info = sys.exc_info()
        if exc_info[1] is not None:
            err = capture_exception(exc_info[1])
        else:
            return {"message": "暂无捕获到的错误上下文"}

    frames = err.get("frames", [])
    code_snippets = (
        [s.model_dump() for s in get_snippets_for_frames(frames)]
        if frames
        else []
    )
    exception = {
        "type": err.get("type"),
        "message": err.get("message"),
        "traceback": err.get("traceback"),
        "frames": frames,
        "frame_count": err.get("frame_count", len(frames)),
    }
    return {
        "request_id": err.get("error_id"),
        "flow": ["error"],
        "input": None,
        "output": None,
        "errors": [{"type": err.get("type"), "message": err.get("message")}],
        "exception": exception,
        "code_snippets": code_snippets,
        "runtime": collect_runtime_snapshot(),
    }


def get_debug_context(trace_id: str | None = None) -> dict:
    """【核心工具】一次性获取某次错误的完整调试上下文：异常堆栈 + 运行时快照 + 源码片段。"""
    return _build_context_from_error(trace_id)


def analyze_with_llm(trace_id: str | None = None) -> dict:
    """对指定/最近捕获的异常做 LLM 根因分析。"""
    context = _build_context_from_error(trace_id)
    if "message" in context and "exception" not in context:
        return context
    try:
        return analyze(context)
    except RuntimeError as e:
        return {"error": f"LLM 分析失败: {e}"}
