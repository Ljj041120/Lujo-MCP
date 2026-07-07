"""SSE 广播中心 —— 服务端主动向客户端推送消息（notifications）"""

import json
import asyncio
import logging
from typing import Dict, List

logger = logging.getLogger("ai-debug-mcp.mcp.sse")


class SSEHub:
    """按 session 维护 asyncio.Queue 列表，支持跨线程发布"""

    def __init__(self):
        self._queues: Dict[str, List[asyncio.Queue]] = {}

    def subscribe(self, session_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._queues.setdefault(session_id, []).append(q)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue) -> None:
        qs = self._queues.get(session_id)
        if qs and q in qs:
            qs.remove(q)

    def publish(self, session_id: str, message: dict) -> None:
        """从任意线程发布 server→client 消息（用于未来的 notifications）"""
        qs = self._queues.get(session_id)
        if not qs:
            return
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return
        for q in list(qs):
            loop.call_soon_threadsafe(q.put_nowait, message)

    @staticmethod
    def format_event(message: dict) -> str:
        return f"event: message\ndata: {json.dumps(message, ensure_ascii=False)}\n\n"


# 全局单例
hub = SSEHub()
