"""
/ingest/* —— 外部服务 / 浏览器 SDK 原始接入端点。

与 /mcp/tools 不同，这里直接接收业务数据结构，便于前端 SDK 和非 Python 服务上报。
鉴权由 AuthMiddleware 统一兜底（fail-closed + 恒定时间比较），不在此重复实现，
保持 proj1 安全中间件体系不变。
"""
from fastapi import APIRouter, HTTPException

from app.mcp.tools.network_api import tool_ingest_network, tool_get_network_trace
from app.mcp.tools.silent_failure_api import tool_ingest_silent_failure
from app.mcp.tools.ingest_api import tool_ingest_error

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/network")
def ingest_network(req: dict):
    """单条网络请求记录上报。"""
    try:
        return tool_ingest_network(
            record=req.get("record", {}),
            trace_id=req.get("trace_id"),
            request_id=req.get("request_id"),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"上报失败: {e}")


@router.get("/network/{trace_id}")
def get_network_trace(trace_id: str):
    """查询某 trace 关联的网络请求记录。"""
    return tool_get_network_trace(trace_id)


@router.post("/silent-failure")
def ingest_silent_failure(req: dict):
    """浏览器 SDK 上报静默失败。"""
    try:
        return tool_ingest_silent_failure(
            message=req.get("message", ""),
            frames=req.get("frames"),
            ui_events=req.get("ui_events"),
            network_records=req.get("network_records"),
            expectation=req.get("expectation"),
            source=req.get("source", "browser_sdk"),
            extra=req.get("extra"),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"上报失败: {e}")


@router.post("/error")
def ingest_error(req: dict):
    """外部服务主动上报异常，复用 ingest_error 工具逻辑。"""
    try:
        return tool_ingest_error(
            exc_type=req.get("exc_type", "UnknownError"),
            message=req.get("message", ""),
            frames=req.get("frames", []),
            source=req.get("source", "http_ingest"),
            extra=req.get("extra"),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"上报失败: {e}")
