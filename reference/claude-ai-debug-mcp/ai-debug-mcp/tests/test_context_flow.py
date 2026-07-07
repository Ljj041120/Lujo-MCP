"""
测试完整调试上下文链路：save_trace -> build_debug_context。
"""
from pathlib import Path

from app.mcp.builders.context import build_debug_context
from app.mcp.collectors.spec import reload_specs
from app.mcp.core.logs import save_trace
from app.schemas.trace import StackFrame


def test_debug_context_includes_related_specs(fresh_db, temp_project):
    reload_specs(temp_project)

    api_file = temp_project / "src" / "api" / "user.py"
    api_file.parent.mkdir(parents=True)
    api_file.write_text("def get_user():\n    pass\n", encoding="utf-8")

    trace_id = save_trace(
        exc_type="ValueError",
        message="user not found",
        frames=[
            StackFrame(
                file=str(api_file),
                line=1,
                function="get_user",
                code_context="def get_user():",
            )
        ],
        source="test",
    )

    context = build_debug_context(trace_id)
    assert context is not None
    assert context.trace.trace_id == trace_id
    assert context.related_specs is not None
    assert len(context.related_specs) > 0
    # 报错文件是 .py，应匹配 API 规范
    assert any("API" in s.summary for s in context.related_specs)


def test_debug_context_for_vue_file_matches_ui_spec(fresh_db, temp_project):
    reload_specs(temp_project)

    vue_file = temp_project / "src" / "components" / "UserCard.vue"
    vue_file.parent.mkdir(parents=True)
    vue_file.write_text("<template></template>\n", encoding="utf-8")

    trace_id = save_trace(
        exc_type="TypeError",
        message="prop type mismatch",
        frames=[
            StackFrame(
                file=str(vue_file),
                line=1,
                function="setup",
                code_context="<template></template>",
            )
        ],
        source="test",
    )

    context = build_debug_context(trace_id)
    assert context is not None
    assert context.related_specs is not None
    assert any("组件" in s.summary for s in context.related_specs)


def test_debug_context_no_specs_when_no_files(fresh_db, tmp_path):
    reload_specs(tmp_path)

    py_file = tmp_path / "main.py"
    py_file.write_text("print(1)\n", encoding="utf-8")

    trace_id = save_trace(
        exc_type="SyntaxError",
        message="invalid syntax",
        frames=[
            StackFrame(
                file=str(py_file),
                line=1,
                function="<module>",
                code_context="print(1)",
            )
        ],
        source="test",
    )

    context = build_debug_context(trace_id)
    assert context is not None
    # 临时目录下没有规范文件
    assert context.related_specs is None or len(context.related_specs) == 0
