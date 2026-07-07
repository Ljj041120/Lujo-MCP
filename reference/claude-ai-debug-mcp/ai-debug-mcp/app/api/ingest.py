"""
/ingest/* —— 外部服务/浏览器 SDK 原始接入端点。

与 /mcp/tools 不同，这里不需要包装成 MCP 工具调用格式，
直接接收业务数据结构，便于前端 SDK 和非 Python 服务上报。
"""
from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import verify_api_key
from app.mcp.tools.ingest_api import tool_ingest_error
from app.mcp.tools.silent_failure_api import tool_ingest_silent_failure
from app.mcp.tools.network_api import tool_ingest_network

router = APIRouter(prefix="/ingest", tags=["ingest"], dependencies=[Depends(verify_api_key)])


@router.post("/error")
def ingest_error(req: dict):
    """外部服务主动上报异常，复用 ingest_error 工具逻辑。"""
    try:
        result = tool_ingest_error(
            exc_type=req.get("exc_type", "UnknownError"),
            message=req.get("message", ""),
            frames=req.get("frames", []),
            source=req.get("source", "http_ingest"),
            extra=req.get("extra", {}),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"上报失败: {e}")
    return result


@router.post("/silent-failure")
def ingest_silent_failure(req: dict):
    """浏览器 SDK 上报静默失败。"""
    try:
        result = tool_ingest_silent_failure(
            message=req.get("message", ""),
            frames=req.get("frames", []),
            ui_events=req.get("ui_events", []),
            network_records=req.get("network_records", []),
            expectation=req.get("expectation", {}),
            source=req.get("source", "browser_sdk"),
            extra=req.get("extra", {}),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"上报失败: {e}")
    return result


@router.post("/network")
def ingest_network(req: dict):
    """前端 SDK 或中间件单条上报网络请求记录。"""
    try:
        result = tool_ingest_network(
            record=req.get("record", {}),
            trace_id=req.get("trace_id"),
            request_id=req.get("request_id"),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"上报失败: {e}")
    return result
