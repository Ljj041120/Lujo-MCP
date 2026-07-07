"""单元测试：LLM analyzer（mock OpenAI 响应）"""
import pytest
from unittest.mock import patch, MagicMock


class TestAnalyzer:

    def test_truncate_context_basic(self):
        from app.llm.analyzer import truncate_context

        ctx = {
            "request_id": "001",
            "flow": ["start", "error"],
            "input": {"data": "x" * 10000},
            "output": None,
            "errors": ["test error"],
        }
        result = truncate_context(ctx, max_tokens=10)
        assert result["request_id"] == "001"
        assert result.get("_truncated") is True

    def test_truncate_context_short(self):
        from app.llm.analyzer import truncate_context

        ctx = {
            "request_id": "002",
            "flow": ["start", "end"],
        }
        result = truncate_context(ctx, max_tokens=10000)
        assert result.get("_truncated") is not True

    def test_build_analysis_prompt(self):
        from app.llm.analyzer import build_analysis_prompt

        ctx = {
            "request_id": "003",
            "flow": ["request_start", "error"],
            "input": {"operation": "test"},
            "errors": ["something went wrong"],
        }
        prompt = build_analysis_prompt(ctx)
        assert "请求 ID: 003" in prompt
        assert "request_start" in prompt
        assert "error" in prompt
        assert "something went wrong" in prompt

    @patch("app.llm.analyzer._get_client")
    def test_analyze_with_mock(self, mock_get_client):
        from app.llm.analyzer import analyze, truncate_context
        import json

        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "root_cause": "测试根因",
            "impact": "无影响",
            "fix": "无需修复",
            "confidence": "high",
        })
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model = "gpt-4o-mock"
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 50
        mock_response.usage.total_tokens = 150
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        ctx = {
            "request_id": "004",
            "flow": ["request_start", "error"],
            "errors": ["test error"],
        }

        result = analyze(ctx, model="gpt-4o-mock")
        assert "analysis" in result
        assert result["analysis"]["root_cause"] == "测试根因"
        assert result["model"] == "gpt-4o-mock"
        assert result["usage"]["total_tokens"] == 150
        assert result["attempts"] == 1
