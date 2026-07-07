"""
FastAPI /api/debug/* 接口用的请求/响应结构。
"""
from typing import Optional
from pydantic import BaseModel

from app.schemas.context import DebugContext


class DebugRunRequest(BaseModel):
    trace_id: Optional[str] = None  # 不填则取最新一条


class DebugRunResponse(BaseModel):
    context: DebugContext


class DebugAnalyzeRequest(BaseModel):
    trace_id: Optional[str] = None


class LLMAnalysisResult(BaseModel):
    root_cause: str
    fix_suggestion: str
    confidence: float  # 0~1
    caveats: Optional[str] = None


class DebugAnalyzeResponse(BaseModel):
    context: DebugContext
    analysis: LLMAnalysisResult
