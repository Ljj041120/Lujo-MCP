"""
全局异常自动捕获钩子。

不装这个的话，AI 只能通过手动调用 capture_exception 才能记录到错误——
实际使用中你更需要的是"项目里随便哪里报错了，都自动被记下来，
AI 随时能通过 list_recent_traces 看到最新情况"。

覆盖两类场景：
1. 同步代码里没被 try/except 捕获、一路抛到顶层的异常 -> sys.excepthook
2. asyncio 任务里没被 await 捕获的异常 -> asyncio 的 exception handler
FastAPI 请求处理中的异常单独在 api 层用 middleware 捕获（因为 Starlette
会在中间件链路里吞掉一部分异常信息，不会都跑到 sys.excepthook）。
"""
import asyncio
import sys
from types import TracebackType

from app.mcp.collectors.stacktrace import capture_exception
from app.mcp.core.errors import record as record_error

_installed = False


def install_global_hook():
    """在应用启动时调用一次即可，幂等。"""
    global _installed
    if _installed:
        return

    original_hook = sys.excepthook

    def _hook(exc_type: type[BaseException], exc_value: BaseException, tb: TracebackType | None):
        try:
            record_error(capture_exception(exc_value, source="global_hook"), source="global_hook")
        except Exception:
            # 捕获流程本身绝不能再抛异常，否则会掩盖原始报错
            pass
        original_hook(exc_type, exc_value, tb)

    sys.excepthook = _hook

    def _asyncio_handler(loop, context):
        exc = context.get("exception")
        if exc is not None:
            try:
                record_error(
                    capture_exception(exc, source="asyncio_loop", extra={"message": context.get("message", "")}),
                    source="asyncio_loop",
                )
            except Exception:
                pass
        loop.default_exception_handler(context)

    try:
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(_asyncio_handler)
    except RuntimeError:
        # 当前没有运行中的事件循环，跳过；FastAPI启动后会有自己的loop，
        # 建议在 lifespan/startup 事件里再调用一次 install_global_hook()
        pass

    _installed = True
