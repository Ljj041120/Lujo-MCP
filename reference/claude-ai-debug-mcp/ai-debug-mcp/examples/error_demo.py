"""
演示脚本：手动触发一个异常 -> 捕获 -> 构建调试上下文 -> 打印。

运行方式（在项目根目录）：
    python -m examples.error_demo

用于验证 collectors/builders 这条链路是否工作正常，
不依赖 mcp_server.py 或 FastAPI，最快的手动验证方式。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.mcp.collectors.stacktrace import capture_exception
from app.mcp.builders.context import build_debug_context


def divide(a, b):
    return a / b


def run_buggy_logic():
    numbers = [10, 5, 0, 2]
    total = 0
    for n in numbers:
        total += divide(100, n)  # n=0 时这里会抛 ZeroDivisionError
    return total


def main():
    try:
        run_buggy_logic()
    except Exception as e:
        trace_id = capture_exception(e, source="manual_demo")
        print(f"✅ 已捕获异常，trace_id = {trace_id}\n")

        context = build_debug_context(trace_id)
        print("=== 完整调试上下文（这就是AI会拿到的数据）===")
        print(json.dumps(context.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
