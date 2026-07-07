"""
FastAPI 入口 —— 人用的调试面板 / 手动测试用。

真正给 Trae/Codex 之类 AI agent 用的是 app/mcp_server.py（stdio + JSON-RPC）,
这个服务和 mcp_server.py 是两个独立进程，共享同一套底层逻辑（app/mcp/*）。

启动方式：
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import logging
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings
from app.api import debug as debug_router
from app.api import ingest as ingest_router
from app.api import mcp_routes
from app.mcp.core.logs import save_network_record
from app.mcp.hooks.exception_hook import install_global_hook
from app.mcp.collectors.stacktrace import capture_exception
from app.schemas.context import NetworkRecord

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("ai-debug-mcp")

app = FastAPI(title="ai-debug-mcp", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境建议改成具体域名白名单
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def capture_request_trace(request: Request, call_next):
    """
    FastAPI 请求处理链路中间件：
    1. 记录每条 inbound 请求的 method/path/status/duration；
    2. 兜底捕获未捕获异常并落库（全局 sys.excepthook 捕不到这种）。
    """
    start = time.time()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception as exc:
        try:
            capture_exception(exc, source="fastapi_middleware", extra={"path": str(request.url)})
        except Exception:
            pass
        logger.exception("请求处理异常: %s", request.url)
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})
    finally:
        try:
            duration_ms = (time.time() - start) * 1000
            save_network_record(
                NetworkRecord(
                    timestamp=time.time(),
                    direction="inbound",
                    method=request.method,
                    url=str(request.url),
                    status_code=status_code,
                    duration_ms=round(duration_ms, 2),
                ),
            )
        except Exception:
            # 记录网络请求自身失败不应影响业务响应
            pass


@app.on_event("startup")
def on_startup():
    install_global_hook()
    logger.info("ai-debug-mcp FastAPI 面板已启动，端口 %s", settings.api_port)


app.include_router(debug_router.router)
app.include_router(mcp_routes.router)
app.include_router(ingest_router.router)


@app.get("/")
def health_check():
    return {"status": "ok", "service": "ai-debug-mcp"}
