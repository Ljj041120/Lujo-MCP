"""
敏感信息脱敏。

在异常消息、源码片段进入数据库或返回给 AI/LLM 前，对常见密钥类字段做掩码。
"""
import re

from app.config import settings

_DEFAULT_PATTERNS = [
    # password = "secret", pwd='x', passwd: xxx
    (
        re.compile(r"(?i)\b(password|pwd|passwd)\s*[:=]\s*(?:'[^']*'|\"[^\"]*\"|\S+)"),
        r'\1="***"',
    ),
    # api_key = ..., api-key=..., token=..., secret=..., private_key=...
    (
        re.compile(r"(?i)\b(api[_-]?key|apikey|secret|token|access[_-]?token|auth[_-]?token|private[_-]?key)\s*[:=]\s*(?:'[^']*'|\"[^\"]*\"|\S+)"),
        r'\1="***"',
    ),
    # Authorization: Bearer xxx 或 authorization=Bearer xxx
    (
        re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+))(?:'[^']*'|\"[^\"]*\"|\S+)"),
        r"\1***",
    ),
    # 11 位手机号（中国大陆常见号段）
    (re.compile(r"\b1[3-9]\d{9}\b"), "***PHONE***"),
]


def _compile_extra_patterns(patterns: list[str]) -> list[tuple[re.Pattern, str]]:
    """将用户配置的额外正则编译为 (pattern, replacement) 列表，无效正则跳过。"""
    compiled: list[tuple[re.Pattern, str]] = []
    for p in patterns:
        try:
            compiled.append((re.compile(p), "***"))
        except re.error:
            # 无效正则 graceful degradation，不阻断主流程
            continue
    return compiled


def redact(text: str | None) -> str | None:
    if not text:
        return text
    if not settings.redaction_enabled:
        return text
    for pattern, repl in _DEFAULT_PATTERNS:
        text = pattern.sub(repl, text)
    for pattern, repl in _compile_extra_patterns(settings.redaction_extra_patterns):
        text = pattern.sub(repl, text)
    return text
