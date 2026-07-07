"""共享状态后端 —— 限流计数与指标计数可插拔（内存 / Redis）

生产多实例部署时必须使用 Redis，否则每个 worker 各自计数，
限流形同虚设、指标只反映单实例。
"""

import time
import threading
from abc import ABC, abstractmethod
from typing import List, Optional

from app.config import settings


class StateStore(ABC):
    """共享状态抽象接口"""

    @abstractmethod
    def allow(self, key: str, limit: int, window: int) -> bool:
        """固定窗口限流：window 秒内最多 limit 次，返回是否放行"""

    @abstractmethod
    def incr(self, key: str, by: int = 1) -> int:
        """整数计数器自增，返回当前值"""

    @abstractmethod
    def incr_float(self, key: str, by: float) -> float:
        """浮点计数器自增，返回当前值"""

    @abstractmethod
    def get(self, key: str) -> float:
        """读取计数（整数或浮点）"""

    @abstractmethod
    def keys(self, prefix: str) -> List[str]:
        """列出指定前缀的所有键"""

    def close(self) -> None:
        pass


class MemoryStateStore(StateStore):
    """内存实现（开发 / 单机默认）"""

    def __init__(self):
        self._data: dict[str, float] = {}
        self._timestamps: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window: int) -> bool:
        now = time.time()
        with self._lock:
            ts = self._timestamps.setdefault(key, [])
            cutoff = now - window
            self._timestamps[key] = [t for t in ts if t > cutoff]
            if len(self._timestamps[key]) >= limit:
                return False
            self._timestamps[key].append(now)
        return True

    def incr(self, key: str, by: int = 1) -> int:
        with self._lock:
            self._data[key] = self._data.get(key, 0) + by
            return int(self._data[key])

    def incr_float(self, key: str, by: float) -> float:
        with self._lock:
            self._data[key] = self._data.get(key, 0.0) + by
            return float(self._data[key])

    def get(self, key: str) -> float:
        with self._lock:
            return self._data.get(key, 0)

    def keys(self, prefix: str) -> List[str]:
        with self._lock:
            return [k for k in self._data if k.startswith(prefix)]


class RedisStateStore(StateStore):
    """Redis 实现（多实例共享）"""

    def __init__(self, url: str):
        import redis
        self._r = redis.Redis.from_url(url, socket_timeout=2, decode_responses=True)

    def allow(self, key: str, limit: int, window: int) -> bool:
        try:
            count = self._r.incr(key)
            if count == 1:
                self._r.expire(key, window)
            return count <= limit
        except Exception:
            # Redis 不可用时降级放行，避免阻塞正常请求
            return True

    def incr(self, key: str, by: int = 1) -> int:
        return int(self._r.incr(key, by))

    def incr_float(self, key: str, by: float) -> float:
        return float(self._r.incrbyfloat(key, by))

    def get(self, key: str) -> float:
        val = self._r.get(key)
        return float(val) if val is not None else 0.0

    def keys(self, prefix: str) -> List[str]:
        return [k for k in self._r.scan_iter(match=f"{prefix}*")]

    def close(self) -> None:
        try:
            self._r.close()
        except Exception:
            pass


_store: Optional[StateStore] = None
_store_lock = threading.Lock()


def get_state_store() -> StateStore:
    """根据配置返回状态后端（懒初始化 + 线程安全）"""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                if settings.state_backend == "redis":
                    _store = RedisStateStore(settings.redis_url)
                else:
                    _store = MemoryStateStore()
    return _store
