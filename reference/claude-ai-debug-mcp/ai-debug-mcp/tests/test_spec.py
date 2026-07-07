"""
测试规范采集器：discover / parse / match / 脱敏。
"""
from pathlib import Path

import pytest

from app.mcp.collectors import spec
from app.mcp.collectors.spec import (
    discover_spec_files,
    parse_spec_file,
    match_specs,
    get_related_specs,
    reload_specs,
)


class TestDiscoverSpecFiles:
    def test_discovers_named_candidates(self, temp_project):
        files = discover_spec_files(temp_project)
        names = {p.name for p in files}
        assert "API_SPEC.md" in names
        assert "COMPONENT_SPEC.md" in names
        assert "README.md" in names
        assert ".cursorrules" in names

    def test_skips_node_modules(self, temp_project):
        node_modules = temp_project / "node_modules"
        node_modules.mkdir()
        (node_modules / "API_SPEC.md").write_text("# 不应被发现")

        files = discover_spec_files(temp_project)
        # 按目录名精确匹配，避免测试路径本身包含 node_modules 子串造成误判
        assert not any("node_modules" in p.parts for p in files)

    def test_skips_large_files(self, temp_project):
        big_spec = temp_project / "BIG_SPEC.md"
        big_spec.write_text("x" * (1024 * 1024 + 100))  # > 1MB

        files = discover_spec_files(temp_project)
        assert "BIG_SPEC.md" not in {p.name for p in files}


class TestParseSpecFile:
    def test_extracts_tags_and_extensions(self, temp_project):
        api_spec = temp_project / "API_SPEC.md"
        snippet = parse_spec_file(api_spec)
        assert snippet is not None
        assert "api" in snippet.tags
        assert ".py" in snippet.target_extensions

    def test_slices_content_by_second_heading(self, temp_project):
        api_spec = temp_project / "API_SPEC.md"
        snippet = parse_spec_file(api_spec)
        assert "错误响应" in snippet.content
        assert "状态码" in snippet.content
        # 内容应以一级标题开头，二级标题切分后合并展示
        assert snippet.content.startswith("# API 规范")

    def test_redacts_secrets(self, temp_project):
        spec_file = temp_project / "API_SPEC.md"
        spec_file.write_text(
            "# API 规范\n\n"
            "## 认证\n\n"
            "api_key = \"secret123\"\n"
            "password = 'my_password'\n",
            encoding="utf-8",
        )
        snippet = parse_spec_file(spec_file)
        assert "secret123" not in snippet.content
        assert "my_password" not in snippet.content
        assert "api_key=\"***\"" in snippet.content


class TestMatchSpecs:
    def test_matches_by_extension(self, temp_project):
        files = discover_spec_files(temp_project)
        specs = [parse_spec_file(f) for f in files]
        specs = [s for s in specs if s]

        matched = match_specs("/project/src/api/user.py", specs)
        assert any("API" in s.summary for s in matched)

    def test_includes_general_specs(self, temp_project):
        files = discover_spec_files(temp_project)
        specs = [parse_spec_file(f) for f in files]
        specs = [s for s in specs if s]

        matched = match_specs("/project/src/utils/helper.py", specs)
        # .cursorrules 是通用规范，应对所有文件生效
        assert any(".cursorrules" in s.file for s in matched)

    def test_respects_max_chars(self, temp_project):
        files = discover_spec_files(temp_project)
        specs = [parse_spec_file(f) for f in files]
        specs = [s for s in specs if s]

        matched = match_specs("/project/src/api/user.py", specs, max_chars=100)
        total = sum(len(s.content) for s in matched)
        assert total <= 100 + 50  # 允许截断提示占用少量空间


class TestGetRelatedSpecs:
    def test_returns_empty_for_no_specs(self, tmp_path):
        reload_specs(tmp_path)
        result = get_related_specs("/project/src/api/user.py", project_root=tmp_path)
        assert result == []

    def test_returns_related_specs(self, temp_project):
        reload_specs(temp_project)
        result = get_related_specs(
            str(temp_project / "src" / "api" / "user.py"),
            project_root=temp_project,
        )
        assert len(result) > 0
        assert any("API" in s.summary for s in result)
