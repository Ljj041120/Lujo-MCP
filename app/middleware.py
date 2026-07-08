"""中间件层 —— 鉴权、CORS、速率限制、请求体限流、安全头、请求追踪"""

import time
import logging
import asyncio
import hmac
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import settings
from app.state.store import get_state_store

logger = logging.getLogger("ai-debug-mcp.middleware")


# ── API Key 鉴权中间件 ──
class AuthMiddleware(BaseHTTPMiddleware):
    """简单的 Bearer Token / X-API-Key 鉴权（fail-closed）"""

    PUBLIC_PATHS = ("/", "/health", "/metrics")

    def __init__(self, app):
        super().__init__(app)
        self.api_key = settings.api_key
        self.enabled = self.api_key is not None

    @staticmethod
    def _extract_key(request: Request) -> str:
        auth_header = request.headers.get("Authorization", "")
        api_key_header = request.headers.get("X-API-Key", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:]
        if api_key_header:
            return api_key_header
        return ""

    async def dispatch(self, request: Request, call_next):
        if not self.enabled:
            return await call_next(request)

        # 健康检查与指标端点免鉴权（不泄露敏感信息）
        if request.url.path in self.PUBLIC_PATHS:
            return await call_next(request)

        # 恒定时间比较，避免时序攻击
        key = self._extract_key(request)
        if not hmac.compare_digest(key, self.api_key or ""):
            return JSONResponse(status_code=401, content={"detail": "Invalid API key"})

        return await call_next(request)


# ── 请求体大小限制中间件 ──
class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """防御超大请求体导致的 OOM / DoS（按 Content-Length 硬检查）"""

    async def dispatch(self, request: Request, call_next):
        limit = settings.max_body_size

        # 带 Content-Length 的请求：先做硬检查
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > limit:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"请求体过大，限制 {limit} 字节"},
                    )
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "无效的 Content-Length"})

        # 注：不在中间件层流式消费 body。
        # 在 BaseHTTPMiddleware 中读取 body 并靠 request._receive 重放，
        # 在 Starlette 新版下会失效，导致下游路由收到空 body（422 missing）。
        # 因此仅靠 Content-Length 硬检查，body 读取交给路由层。
        return await call_next(request)


# ── 安全响应头中间件 ──
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """为所有响应补充基础安全头"""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-XSS-Protection", "1; mode=block")
        return response


# ── 速率限制中间件 ──
class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            client_ip = request.client.host if request.client else "unknown"
            store = get_state_store()
            if not store.allow(f"ratelimit:{client_ip}", settings.rate_limit_per_minute, 60):
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests, please slow down"},
                )
        except Exception:
            logger.exception("RateLimitMiddleware 异常，降级放行")

        return await call_next(request)


# ── 请求追踪中间件 ──
class TraceMiddleware(BaseHTTPMiddleware):
    """给每个请求注入 trace_id，放入 response header；异常时也能记录日志"""

    async def dispatch(self, request: Request, call_next):
        import uuid
        trace_id = str(uuid.uuid4())
        request.state.trace_id = trace_id

        start = time.time()
        try:
            response: Response = await call_next(request)
        except Exception:
            elapsed = time.time() - start
            logger.exception(
                f"{request.method} {request.url.path} 异常",
                extra={"trace_id": trace_id, "elapsed_ms": round(elapsed * 1000, 2)},
            )
            raise

        elapsed = time.time() - start
        response.headers["X-Trace-Id"] = trace_id
        response.headers["X-Response-Time"] = f"{elapsed:.3f}s"

        logger.info(
            "request", extra={
                "trace_id": trace_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "elapsed_ms": round(elapsed * 1000, 2),
            }
        )
        return response


def setup_middleware(app: FastAPI):
    """在 FastAPI app 上批量注册中间件（顺序：外→内）"""
    # CORS
    if settings.cors_origins == "*":
        allow_origins = ["*"]
        allow_credentials = False  # 规范不允许 * 与 credentials 同时使用
    else:
        allow_origins = settings.cors_origins.split(",")
        allow_credentials = True

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AuthMiddleware)
    app.add_middleware(MaxBodySizeMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(TraceMiddleware)
