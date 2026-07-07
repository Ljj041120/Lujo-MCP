"""
测试敏感信息脱敏：默认规则、额外正则、开关控制。
"""
import pytest

from app.config import settings
from app.mcp.core.redaction import redact


class TestRedactionDefaults:
    def test_redacts_password(self):
        assert "super_secret" not in redact("password = 'super_secret'")
        assert "***" in redact("password = 'super_secret'")

    def test_redacts_phone(self):
        assert redact("contact 13800138000") == "contact ***PHONE***"

    def test_redacts_authorization_bearer(self):
        text = "authorization: Bearer abc123"
        assert "abc123" not in redact(text)
        assert "***" in redact(text)


class TestRedactionExtraPatterns:
    def test_extra_pattern_redacts(self, monkeypatch):
        monkeypatch.setattr(settings, "redaction_extra_patterns", [r"\bcvv\s*=\s*\S+"])
        assert "cvv=123" not in redact("cvv=123 and more")
        assert "***" in redact("cvv=123 and more")

    def test_invalid_extra_pattern_is_skipped(self, monkeypatch):
        monkeypatch.setattr(settings, "redaction_extra_patterns", [r"(invalid"])
        # 不应抛异常，默认规则仍生效
        assert "super_secret" not in redact("password = 'super_secret'")


class TestRedactionToggle:
    def test_disabled_redaction_returns_original(self, monkeypatch):
        monkeypatch.setattr(settings, "redaction_enabled", False)
        assert redact("password = 'super_secret'") == "password = 'super_secret'"
