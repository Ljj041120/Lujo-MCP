"""统一配置管理 —— 全局单例，替代散落的 os.getenv()"""

from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── LLM ──
    openai_api_key: str = ""
    llm_model: str = "gpt-4o"
    llm_timeout: int = 30
    llm_max_retries: int = 3
    llm_temperature: float = 0.3
    # 备用模型（主模型不可用时 fallback）
    llm_fallback_model: str = "gpt-4o-mini"

    # ── 上下文 ──
    max_context_tokens: int = 8000
    # 堆栈截断：只保留最近 N 帧
    max_stack_frames: int = 20
    # 局部变量截断：每个 frame 最多展示 N 个变量
    max_locals_per_frame: int = 8

    # ── 代码定位（FR11）──
    # 报错行上下各读取多少行源码片段
    code_context_lines: int = 5
    # 远程/容器路径 → 本地路径 映射，逗号分隔，如 "/app:/Users/me/project"
    source_path_map: str = ""
    # 生成的可点击链接协议：vscode | file
    ide_scheme: str = "vscode"
    # 允许生成 file:// 链接的路径白名单前缀（逗号分隔），为空=不限制
    whitelist_path_prefix: str = ""

    # ── 存储 ──
    storage_backend: str = "memory"  # "memory" | "postgresql"

    # ── 状态后端（限流/指标计数）──
    state_backend: str = "memory"  # "memory" | "redis"
    redis_url: str = "redis://localhost:6379/0"

    # ── PostgreSQL ──
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_database: str = "ai_debug_mcp"
    pg_user: str = "postgres"
    pg_password: str = ""

    # ── 过期清理 ──
    trace_ttl_seconds: int = 3600
    session_ttl_seconds: int = 3600

    # ── 安全 ──
    api_key: Optional[str] = None  # 不设置 = 不鉴权
    cors_origins: str = "*"
    rate_limit_per_minute: int = 60
    # 请求体最大字节数（防御超大请求体 OOM / DoS）
    max_body_size: int = 1_048_576

    # ── 脱敏（redaction）──
    # 默认开启：数据入库前统一掩码敏感字段（fail-safe，宁可多掩不泄露）
    redaction_enabled: bool = True
    # 额外脱敏正则，每行一条；无效正则静默跳过，不阻断主流程
    redaction_extra_patterns: str = ""

    # ── Git 归因（M5）──
    # git 命令超时秒数，超时返回 None，不阻断主流程
    git_timeout: int = 10
    # 允许执行 git 操作的路径白名单前缀（逗号分隔绝对路径）；为空=不限制（生产建议限定项目根）
    git_path_whitelist: str = ""

    # ── inbound 网络采集（M6）──
    # 默认关闭：开启后记录每个进入服务的请求为 network 记录（跳过 /health /metrics）
    network_capture_enabled: bool = False

    # ── 日志 ──
    log_level: str = "INFO"
    log_format: str = "json"  # "json" | "text"

    # ── 服务 ──
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    service_name: str = "ai-debug-mcp"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# 全局单例
settings = Settings()
