"""
MCP 工具：get_runtime_snapshot / analyze_with_llm（可选）
"""
from app.mcp.collectors.runtime import get_runtime_snapshot
from app.mcp.builders.context import build_debug_context
from app.llm.analyzer import analyze_with_llm, AnalyzerUnavailableError


def tool_get_runtime_snapshot() -> dict:
    return get_runtime_snapshot().model_dump()


def tool_analyze_with_llm(trace_id: str | None = None) -> dict:
    """
    可选工具：仅在宿主客户端本身不具备AI推理能力时使用。
    正常情况（接入Trae/Codex等agentic工具）建议直接用 get_debug_context，
    让宿主AI自行分析，不要调这个,避免重复推理和额外的LLM花费。
    """
    context = build_debug_context(trace_id)
    if context is None:
        return {"found": False, "message": "没有找到追踪记录"}
    try:
        analysis = analyze_with_llm(context)
    except AnalyzerUnavailableError as e:
        return {"found": True, "context": context.model_dump(), "analysis_error": str(e)}
    return {"found": True, "context": context.model_dump(), "analysis": analysis.model_dump()}
