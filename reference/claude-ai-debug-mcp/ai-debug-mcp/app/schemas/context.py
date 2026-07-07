"""
运行时快照 & 调试上下文（打包给 AI 的最终结构）。
"""
from typing import Optional
from pydantic import BaseModel, Field

from app.schemas.trace import TraceEntry


class RuntimeSnapshot(BaseModel):
    pid: int
    cpu_percent: float
    memory_mb: float
    thread_count: int
    open_files: int
    python_version: str
    env_hint: dict = Field(default_factory=dict)  # 只挑几个非敏感的关键环境变量


class CodeSnippet(BaseModel):
    file: str
    error_line: int
    snippet: str
    found: bool = True


class GitBlameInfo(BaseModel):
    file: str
    line: int
    commit: str
    author: str
    date: str
    summary: str
    line_text: str


class GitDiffInfo(BaseModel):
    file: str
    commits_back: int
    diff: str


class SpecSnippet(BaseModel):
    """项目规范文件切片，注入 AI 上下文以约束修复风格"""
    file: str
    summary: str
    content: str
    tags: list[str] = Field(default_factory=list)
    target_extensions: list[str] = Field(default_factory=list)


class UIEvent(BaseModel):
    """前端用户行为事件，用于定位静默失败"""
    event_id: Optional[str] = None
    timestamp: float
    event_type: str          # click / submit / route_change
    target_selector: Optional[str] = None
    component_name: Optional[str] = None
    route_path: Optional[str] = None
    payload_json: Optional[str] = None


class NetworkRecord(BaseModel):
    """网络请求记录，关联前后端行为"""
    record_id: Optional[str] = None
    timestamp: float
    direction: str           # inbound / outbound
    method: Optional[str] = None
    url: Optional[str] = None
    status_code: Optional[int] = None
    request_body: Optional[str] = None
    response_body: Optional[str] = None
    duration_ms: Optional[float] = None


class DebugContext(BaseModel):
    """一次性打包给宿主 AI 的完整调试上下文"""
    trace: TraceEntry
    runtime: Optional[RuntimeSnapshot] = None
    code_snippets: list[CodeSnippet] = Field(default_factory=list)
    git_blame: Optional[list[GitBlameInfo]] = None
    recent_diffs: Optional[list[GitDiffInfo]] = None
    related_specs: Optional[list[SpecSnippet]] = None
    network_trace: Optional[list[NetworkRecord]] = None
    ui_events: Optional[list[UIEvent]] = None
    note: str = (
        "以上为原始堆栈、运行时状态、相关代码片段、项目规范、UI 事件与网络请求，未做 AI 分析。"
        "请结合项目代码库上下文自行判断根因与修复方案。"
    )
