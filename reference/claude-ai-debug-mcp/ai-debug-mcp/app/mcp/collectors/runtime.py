"""
运行时快照采集：进程CPU/内存/线程/Python版本等，帮助AI判断
是不是资源问题（比如内存泄漏、线程爆炸）导致的异常。
"""
import os
import platform
import sys

import psutil

from app.schemas.context import RuntimeSnapshot

# 只挑这几个非敏感的环境变量暴露给AI，避免泄露密钥类信息
_SAFE_ENV_KEYS = ["PYTHONPATH", "ENV", "APP_ENV", "VIRTUAL_ENV"]


def get_runtime_snapshot() -> RuntimeSnapshot:
    proc = psutil.Process(os.getpid())
    with proc.oneshot():
        cpu = proc.cpu_percent(interval=0.1)
        mem_mb = proc.memory_info().rss / (1024 * 1024)
        threads = proc.num_threads()
        try:
            open_files = len(proc.open_files())
        except (psutil.AccessDenied, NotImplementedError):
            open_files = -1

    env_hint = {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}

    return RuntimeSnapshot(
        pid=os.getpid(),
        cpu_percent=cpu,
        memory_mb=round(mem_mb, 2),
        thread_count=threads,
        open_files=open_files,
        python_version=platform.python_version(),
        env_hint=env_hint,
    )
