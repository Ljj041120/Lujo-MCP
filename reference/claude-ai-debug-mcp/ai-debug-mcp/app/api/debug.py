"""
/api/debug/* —— 给人用的调试面板接口（不是MCP协议）。
接入Trae/Codex场景下用不到这里，这套是独立使用/手动测试时的入口。
"""
from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import verify_api_key
from app.mcp.builders.context import build_debug_context
from app.mcp.collectors.runtime import get_runtime_snapshot
from app.mcp.core.session import session_manager
from app.llm.analyzer import analyze_with_llm, AnalyzerUnavailableError
from app.schemas.debug import (
    DebugRunRequest, DebugRunResponse,
    DebugAnalyzeRequest, DebugAnalyzeResponse,
)

router = APIRouter(prefix="/api/debug", tags=["debug"], dependencies=[Depends(verify_api_key)])


@router.post("/run", response_model=DebugRunResponse)
def run_debug(req: DebugRunRequest):
    context = build_debug_context(req.trace_id)
    if context is None:
        raise HTTPException(status_code=404, detail="没有找到追踪记录")
    return DebugRunResponse(context=context)


@router.post("/analyze", response_model=DebugAnalyzeResponse)
def analyze_debug(req: DebugAnalyzeRequest):
    context = build_debug_context(req.trace_id)
    if context is None:
        raise HTTPException(status_code=404, detail="没有找到追踪记录")
    try:
        analysis = analyze_with_llm(context)
    except AnalyzerUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return DebugAnalyzeResponse(context=context, analysis=analysis)


@router.get("/runtime")
def runtime_snapshot():
    return get_runtime_snapshot()


@router.get("/session")
def active_sessions():
    return [
        {
            "session_id": s.session_id,
            "created_at": s.created_at,
            "last_active_at": s.last_active_at,
            "trace_ids": s.trace_ids,
            "tool_calls": s.tool_calls,
        }
        for s in session_manager.list_active()
    ]
