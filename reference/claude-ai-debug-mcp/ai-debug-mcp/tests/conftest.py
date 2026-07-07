"""
测试共享 fixtures。

注意：logs.py 在导入时会调用 init_db() 并使用默认的 settings.db_path。
为了让测试不污染开发数据库，我们在 fixture 中动态切换 settings.db_path 到临时目录，
并重新调用 init_db() 创建测试用数据库。
"""
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.config import settings
from app.mcp.core import logs


@pytest.fixture
def temp_db_path():
    """提供一个临时数据库文件路径，并确保其目录存在。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "test_ai_debug_mcp.sqlite3"
        yield str(db_file)


@pytest.fixture
def fresh_db(temp_db_path):
    """切换 settings.db_path 到临时文件并初始化数据库。"""
    original_db_path = settings.db_path
    settings.db_path = temp_db_path
    logs.init_db()
    try:
        yield temp_db_path
    finally:
        settings.db_path = original_db_path


@pytest.fixture
def temp_project(tmp_path):
    """创建一个带规范文件的临时项目目录。"""
    api_spec = tmp_path / "API_SPEC.md"
    api_spec.write_text(
        "# API 规范\n\n"
        "## 错误响应\n\n"
        "所有接口错误必须返回 `{code, message, data}` 格式。\n\n"
        "## 状态码\n\n"
        "HTTP 状态码统一使用 200，业务错误通过 code 区分。\n",
        encoding="utf-8",
    )

    component_spec = tmp_path / "COMPONENT_SPEC.md"
    component_spec.write_text(
        "# 组件规范\n\n"
        "## Vue 组件\n\n"
        "Vue 组件必须使用 Composition API 和 script setup。\n",
        encoding="utf-8",
    )

    readme = tmp_path / "README.md"
    readme.write_text(
        "# 项目说明\n\n"
        "这是一个示例项目。\n",
        encoding="utf-8",
    )

    cursor_rules = tmp_path / ".cursorrules"
    cursor_rules.write_text(
        "# Cursor 规则\n\n"
        "优先使用 TypeScript。\n",
        encoding="utf-8",
    )

    # 创建一个项目根标记，让 _find_project_root 能把 temp_project 识别为项目根
    (tmp_path / "package.json").write_text('{"name": "test-project"}', encoding="utf-8")

    yield tmp_path
