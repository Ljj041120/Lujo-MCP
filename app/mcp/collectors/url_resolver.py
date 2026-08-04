"""URL → handler 反查解析器（Task 13: 无堆栈场景下的故障定位入口）。

当调试上下文缺少异常堆栈帧（"无报错但功能不对"）时，
从 debug_context.input / network_trace 中提取 HTTP 方法 + 路径，
反查 FastAPI 路由表找到对应的 handler 函数，
返回 (module_path, function_name, approx_line) 三元组，
供 static_analyzer.analyze_handler() 做源码静态分析。

设计原则：
- 零副作用：调用 create_app() 只是构造 app 实例，不启动 lifespan / 不监听端口
- 失败静默降级：反查失败返回 None，上游继续用降级分析
- 结果可缓存：同一 process 内路由表不变，做一次懒加载
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional

logger = logging.getLogger("ai-debug-mcp.mcp.collectors.url_resolver")


@dataclass(slots=True)
class HandlerInfo:
    """URL 反查到的 handler 信息（三元组 + 辅助元信息）。"""

    module_path: str       # 源文件绝对路径
    function_name: str     # handler 函数名
    approx_line: int       # handler 定义起始行号（inspect.getsourcelines）
    route_path: str        # 匹配到的路由模板（如 "/api/users/{user_id}"）
    methods: list[str]     # 匹配到的 HTTP 方法列表（如 ["GET", "POST"]）
    module_dot_path: str   # Python 点路径（如 "app.api.dashboard"）


# ── FastAPI 路由表懒加载 ──


@lru_cache(maxsize=1)
def _get_fastapi_routes() -> list[tuple[Any, str]]:
    """懒加载 FastAPI app 路由表，缓存到进程生命周期。

    返回 list[(route_obj, route_prefix)]，route_prefix 是 router 挂载前缀。
    失败返回空列表（不抛异常）。
    """
    try:
        # 延迟导入，避免循环依赖
        from app.main import create_app

        app = create_app()
    except Exception:
        logger.warning("URL resolver: create_app 失败，路由反查不可用", exc_info=True)
        return []

    routes: list[tuple[Any, str]] = []
    try:
        for route in getattr(app, "routes", []) or []:
            routes.append((route, ""))
    except Exception:
        pass
    return routes


@lru_cache(maxsize=1)
def _get_module_search_roots() -> list[str]:
    """返回 Python 源码搜索根目录（优先取 cwd / app 父目录）。"""
    roots = []
    cwd = os.getcwd()
    if cwd:
        roots.append(cwd)
    try:
        import app as _app_pkg
        pkg_file = _app_pkg.__file__
        if pkg_file:
            pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(pkg_file)))
            if pkg_root not in roots:
                roots.append(pkg_root)
    except Exception:
        pass
    return roots


# ── 路径匹配工具 ──


_PATH_PARAM_RE = re.compile(r"\{([^}]+)\}")


def _route_matches(route_path_template: str, request_path: str) -> bool:
    """判断路由模板是否匹配请求路径（支持 FastAPI 风格 {param} 通配）。

    例："/api/users/{user_id}" 匹配 "/api/users/123"。
    """
    pattern = "^" + _PATH_PARAM_RE.sub(r"[^/]+", re.escape(route_path_template)) + "$"
    return bool(re.match(pattern, request_path))


def _handler_to_module_path(handler: Any) -> Optional[HandlerInfo]:
    """从 handler 可调用对象抽取 HandlerInfo。"""
    try:
        function_name = getattr(handler, "__name__", "")
        if not function_name:
            return None
        module_dot = getattr(handler, "__module__", "")
        if not module_dot:
            return None
        module = importlib.import_module(module_dot)
        module_file = getattr(module, "__file__", None)
        if not module_file:
            return None
        module_path = os.path.abspath(module_file)
        try:
            _, lineno = inspect.getsourcelines(handler)
        except (OSError, TypeError):
            lineno = 1
        return HandlerInfo(
            module_path=module_path,
            function_name=function_name,
            approx_line=lineno,
            route_path="",  # 由调用方回填
            methods=[],
            module_dot_path=module_dot,
        )
    except Exception:
        logger.debug("URL resolver: 无法解析 handler 源文件位置", exc_info=True)
        return None


# ── 公共入口 ──


def resolve(method: str, path: str) -> Optional[HandlerInfo]:
    """按 HTTP 方法 + URL 路径反查 handler 信息。

    例：
        resolve("GET", "/api/users/123")
        → HandlerInfo(module_path=".../app/api/users.py",
                       function_name="get_user",
                       approx_line=42,
                       route_path="/api/users/{user_id}",
                       methods=["GET"],
                       module_dot_path="app.api.users")

    Args:
        method: HTTP 方法（GET / POST / PUT / DELETE 等，大小写不敏感）
        path: URL 路径（如 "/api/users/123"，不含 query string）

    Returns:
        HandlerInfo 或 None（路由不匹配 / 解析失败）
    """
    if not method or not path:
        return None
    method_upper = method.upper()
    # 去掉 query / fragment
    clean_path = path.split("?", 1)[0].split("#", 1)[0]
    # 去掉末尾斜杠（但保留根路径）
    if len(clean_path) > 1 and clean_path.endswith("/"):
        clean_path = clean_path[:-1]

    for route, prefix in _get_fastapi_routes():
        # 只处理 Starlette Route（HTTP 方法路由，跳过 Mount / WebSocketRoute）
        route_methods = getattr(route, "methods", None)
        if not route_methods:
            continue
        route_path = getattr(route, "path", "")
        if not route_path:
            continue
        full_route_path = (prefix.rstrip("/") or "") + route_path
        if not _route_matches(full_route_path, clean_path):
            continue
        if method_upper not in {m.upper() for m in route_methods}:
            # 如果是所有方法通配（None）也接受
            if None not in route_methods:
                continue
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        info = _handler_to_module_path(endpoint)
        if info is None:
            continue
        info.route_path = full_route_path
        info.methods = sorted({m.upper() for m in route_methods if m is not None}) or ["*"]
        return info
    return None


def resolve_from_debug_context(debug_context: dict[str, Any]) -> Optional[HandlerInfo]:
    """从 debug_context 提取 method + path 反查 handler。

    查找优先级：
    1. debug_context.input["method"] + debug_context.input["path"]
    2. debug_context.input["method"] + debug_context.input["url"]（去掉 host/scheme）
    3. debug_context.network_trace[0]["method"] + ["url"]（network_record 第一项）
    4. debug_context.exception["extra"]["path"]（error handler 注入）
    5. debug_context.flow 中含 request_start 的记录

    全部 miss → 返回 None。
    """
    candidates: list[tuple[str, str]] = []

    input_data = debug_context.get("input") or {}
    if isinstance(input_data, dict):
        method = input_data.get("method") or input_data.get("http_method")
        path = input_data.get("path") or input_data.get("request_path")
        url = input_data.get("url") or ""
        if method and path:
            candidates.append((str(method), str(path)))
        elif method and url:
            candidates.append((str(method), _url_to_path(url)))

    network_trace = debug_context.get("network_trace") or []
    if isinstance(network_trace, list) and network_trace:
        for rec in network_trace:
            if not isinstance(rec, dict):
                continue
            if rec.get("direction") != "inbound":
                continue
            method = rec.get("method")
            url = rec.get("url") or rec.get("path")
            if method and url:
                candidates.append((str(method), _url_to_path(str(url))))
                break

    exc = debug_context.get("exception") or {}
    if isinstance(exc, dict):
        extra = exc.get("extra") or {}
        if isinstance(extra, dict):
            method = extra.get("method") or extra.get("http_method")
            path = extra.get("path") or extra.get("request_path")
            if method and path:
                candidates.append((str(method), str(path)))

    for method, path in candidates:
        info = resolve(method, path)
        if info is not None:
            return info
    return None


def _url_to_path(url: str) -> str:
    """从完整 URL 中抽取出路径部分（去掉 scheme/host/port）。"""
    if not url:
        return ""
    # 含协议的完整 URL："https://host:port/a/b?x=1#f" → "/a/b"
    if "://" in url:
        try:
            from urllib.parse import urlparse

            parsed = urlparse(url)
            return parsed.path or "/"
        except Exception:
            pass
    # 去掉 query / fragment
    return url.split("?", 1)[0].split("#", 1)[0] or "/"
