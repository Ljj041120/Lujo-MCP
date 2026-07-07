"""可观测性模块 —— Prometheus 指标 + 请求计数器"""

import time
import threading
import logging
from collections import defaultdict
from typing import Dict, Tuple

from fastapi import APIRouter, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("ai-debug-mcp.metrics")

# ── 线程安全指标存储 ──
_counter_lock = threading.Lock()
_request_total: Dict[Tuple[str, str, int], int] = defaultdict(int)
_error_total: Dict[Tuple[str, str], int] = defaultdict(int)
_latency_sum: Dict[str, float] = defaultdict(float)
_latency_count: Dict[str, int] = defaultdict(int)

router = APIRouter(tags=["observability"])


class MetricsMiddleware(BaseHTTPMiddleware):
    """记录每个请求的指标（计数 + 延迟）"""

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        method = request.method
        path = request.url.path

        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            status = 500
            raise
        finally:
            elapsed = time.time() - start
            with _counter_lock:
                _request_total[(method, path, status)] += 1
                if status >= 500:
                    _error_total[(method, path)] += 1
                _latency_sum[path] += elapsed
                _latency_count[path] += 1

        return response


def _render_prometheus() -> str:
    """生成 Prometheus 文本格式指标"""
    lines = []

    lines.append("# HELP http_requests_total Total HTTP requests by method/path/status")
    lines.append("# TYPE http_requests_total counter")
    with _counter_lock:
        for (method, path, status), count in _request_total.items():
            lines.append(
                f'http_requests_total{{method="{method}",path="{path}",status="{status}"}} {count}'
            )

        lines.append("# HELP http_errors_total Total HTTP 5xx errors by method/path")
        lines.append("# TYPE http_errors_total counter")
        for (method, path), count in _error_total.items():
            lines.append(
                f'http_errors_total{{method="{method}",path="{path}"}} {count}'
            )

        lines.append("# HELP http_request_duration_seconds_sum Request latency sum (seconds)")
        lines.append("# TYPE http_request_duration_seconds_sum counter")
        for path, total in _latency_sum.items():
            lines.append(
                f'http_request_duration_seconds_sum{{path="{path}"}} {round(total, 4)}'
            )

        lines.append("# HELP http_request_duration_seconds_count Request latency count")
        lines.append("# TYPE http_request_duration_seconds_count counter")
        for path, count in _latency_count.items():
            lines.append(
                f'http_request_duration_seconds_count{{path="{path}"}} {count}'
            )

    return "\n".join(lines) + "\n"


@router.get("/metrics")
def metrics():
    """Prometheus 指标端点"""
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(_render_prometheus())


def setup_observability(app):
    """注册可观测性路由和中间件"""
    app.include_router(router)
    app.add_middleware(MetricsMiddleware)
    logger.info("可观测性模块已启用 (/metrics)")
