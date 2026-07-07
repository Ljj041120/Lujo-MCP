"""
全局配置。所有模块统一从这里读取配置，而不是各处 os.getenv()。
"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # LLM（可选功能）
    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = 30
    llm_max_retries: int = 2

    # 存储
    db_path: str = "./data/ai_debug_mcp.sqlite3"
    trace_ttl_seconds: int = 86400
    session_ttl_seconds: int = 3600

    # 上下文构建
    code_context_lines: int = 10
    max_context_tokens_hint: int = 8000

    # FastAPI（人用面板）
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_key: str = "change-me-to-a-real-secret"

    # 脱敏
    redaction_enabled: bool = True
    redaction_extra_patterns: list[str] = Field(default_factory=list)

    # 日志
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
