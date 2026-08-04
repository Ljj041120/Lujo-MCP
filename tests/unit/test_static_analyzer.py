"""M3 StaticAnalyzer 静态分析单元测试。

覆盖：
- `analyze()` 堆栈帧分析（行号匹配 / 空帧 / 单帧 / 多帧调用链）
- `analyze_source_code()` 源码字符串分析（无堆栈场景 / 语法错误 / 函数未找到）
- `analyze_handler()` 模块路径+函数名反查（行号精确命中 / 行号不匹配按名 fallback）
- 可疑输入推断（Optional 无默认值 / 无类型注解）
- 复杂度与内部调用提取
"""

from __future__ import annotations

import os

import pytest

from app.mcp.collectors.static_analyzer import (
    analyze,
    analyze_handler,
    analyze_source_code,
)


def _write_source(tmp_path: pytest.TempPathFactory, source: str) -> str:
    path = os.path.join(str(tmp_path), "module.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(source)
    return path


_SOURCE = """\
from typing import Optional

def helper():
    return 1

def get_user(user_id):
    return db.get(user_id)

def process(user_id: Optional[int] = None):
    data = get_user(user_id)
    for item in data:
        if item is None:
            break
    return data
"""


def test_analyze_empty_frames_returns_empty_list():
    assert analyze([]) == []


def test_analyze_frame_line_hit_returns_fault_location(tmp_path):
    path = _write_source(tmp_path, _SOURCE)
    frames = [
        {"file": path, "function": "get_user", "line": 6},
    ]
    results = analyze(frames)
    assert len(results) == 1
    loc = results[0]
    assert loc.function == "get_user"
    assert loc.line_number == 6
    assert loc.function_info is not None
    assert loc.function_info.name == "get_user"


def test_analyze_multiple_frames_builds_call_chain(tmp_path):
    path = _write_source(tmp_path, _SOURCE)
    frames = [
        {"file": path, "function": "process", "line": 9},
        {"file": path, "function": "get_user", "line": 6},
    ]
    results = analyze(frames)
    assert len(results) == 2
    assert results[0].call_chain == ["process", "get_user"]


def test_analyze_frame_skips_invalid_frame(tmp_path):
    path = _write_source(tmp_path, _SOURCE)
    frames = [
        {"file": "", "function": "", "line": 0},
        {"file": path, "function": "helper", "line": 3},
    ]
    results = analyze(frames)
    assert len(results) == 1
    assert results[0].function == "helper"


def test_analyze_missing_file_returns_empty(tmp_path):
    frames = [
        {"file": os.path.join(str(tmp_path), "does_not_exist.py"), "function": "x", "line": 1},
    ]
    assert analyze(frames) == []


def test_analyze_extracts_internal_calls(tmp_path):
    path = _write_source(tmp_path, _SOURCE)
    frames = [{"file": path, "function": "process", "line": 9}]
    results = analyze(frames)
    assert len(results) == 1
    calls = results[0].function_info.internal_calls
    assert "get_user" in calls


def test_analyze_source_code_returns_fault_location():
    loc = analyze_source_code(_SOURCE, "get_user")
    assert loc is not None
    assert loc.function == "get_user"
    assert loc.function_info is not None
    assert loc.call_chain == ["get_user"]


def test_analyze_source_code_empty_source_returns_none():
    assert analyze_source_code("", "get_user") is None


def test_analyze_source_code_empty_name_returns_none():
    assert analyze_source_code(_SOURCE, "") is None


def test_analyze_source_code_syntax_error_returns_none():
    assert analyze_source_code("def broken(:\n", "broken") is None


def test_analyze_source_code_missing_function_returns_none():
    assert analyze_source_code(_SOURCE, "not_defined") is None


def test_analyze_source_code_async_function():
    src = (
        "async def fetch_user():\n"
        "    return await db.get(1)\n"
    )
    loc = analyze_source_code(src, "fetch_user")
    assert loc is not None
    assert loc.function == "fetch_user"


def test_analyze_source_code_infers_suspicious_input():
    src = (
        "def render(user_id: Optional[str]):\n"
        "    return user_id.upper()\n"
    )
    loc = analyze_source_code(src, "render")
    assert loc is not None
    assert any(
        s["variable"] == "user_id" for s in loc.suspicious_inputs
    )


def test_analyze_handler_line_hit_via_approx_line(tmp_path):
    path = _write_source(tmp_path, _SOURCE)
    loc = analyze_handler(module_path=path, function_name="get_user", approx_line=6)
    assert loc is not None
    assert loc.function == "get_user"
    assert loc.line_number == 6


def test_analyze_handler_falls_back_to_name_when_line_mismatch(tmp_path):
    path = _write_source(
        tmp_path,
        "import os\n\n"
        "def helper():\n"
        "    return 1\n\n"
        "def get_user(user_id):\n"
        "    return db.get(user_id)\n\n"
        "def another():\n"
        "    return 2\n",
    )
    loc = analyze_handler(module_path=path, function_name="get_user", approx_line=1)
    assert loc is not None
    assert loc.function == "get_user"
    assert loc.line_number == 6


def test_analyze_handler_async_support(tmp_path):
    path = _write_source(
        tmp_path,
        "async def get_profile(uid):\n"
        "    return await store.get(uid)\n",
    )
    loc = analyze_handler(module_path=path, function_name="get_profile", approx_line=1)
    assert loc is not None
    assert loc.function == "get_profile"


def test_analyze_handler_missing_args_returns_none():
    assert analyze_handler(module_path="", function_name="x") is None
    assert analyze_handler(module_path="/tmp/x.py", function_name="") is None


def test_analyze_handler_missing_file_returns_none():
    assert analyze_handler(module_path="/tmp/not_exist.py", function_name="x") is None