"""
上下文构建器：这是流程图里 "追踪日志 -> 调试上下文" 那一步。

把 collectors/ 下三个采集器的结果合并成一个 DebugContext，
一次性打包返回给宿主 AI（Trae/Codex 里的模型），减少它多次调用的成本。
"""
from app.mcp.core.logs import get_trace, get_network_records, get_ui_events
from app.mcp.core.git import get_blame_for_frame, get_recent_diff
from app.mcp.core.redaction import redact
from app.mcp.collectors.runtime import get_runtime_snapshot
from app.mcp.collectors.code_locator import get_snippets_for_frames
from app.mcp.collectors.network import network_records_from_extra
from app.mcp.collectors.spec import get_related_specs
from app.mcp.collectors.ui_event import ui_events_from_extra
from app.schemas.context import DebugContext, GitBlameInfo, GitDiffInfo, NetworkRecord, SpecSnippet, UIEvent


def _redact_snippet(snippet):
    snippet.snippet = redact(snippet.snippet) or snippet.snippet
    return snippet


def _collect_related_specs(frames: list) -> list[SpecSnippet]:
    """为前 3 帧收集相关规范片段，按 file 去重并限制总长度。"""
    seen_files = set()
    all_specs = []
    for frame in frames[:3]:
        file_path = frame.file
        if file_path in seen_files:
            continue
        seen_files.add(file_path)
        try:
            specs = get_related_specs(file_path)
            all_specs.extend(specs)
        except Exception:
            # 规范采集失败不应影响主调试流程
            continue

    # 按 file 去重（同一规范文件可能被多个 frame 命中）
    unique = {}
    for spec in all_specs:
        if spec.file not in unique:
            unique[spec.file] = spec

    # 限制总长度约 6000 字符（≈ 2000 tokens）
    result = []
    total = 0
    max_total = 6000
    for spec in unique.values():
        length = len(spec.content)
        if total + length > max_total:
            remaining = max_total - total
            if remaining > 100:
                trimmed = spec.content[:remaining] + "\n...（已截断）"
                result.append(spec.model_copy(update={"content": trimmed}))
            break
        result.append(spec)
        total += length

    return result


def _collect_ui_and_network(entry) -> tuple[list[UIEvent] | None, list[NetworkRecord] | None]:
    """为静默失败 trace 收集 UI 事件与网络请求，优先从独立表读取，失败则回退到 extra。"""
    has_silent_failure = entry.trace_kind == "silent_failure"
    has_ui_in_extra = bool(entry.extra.get("ui_events"))
    has_network_in_extra = bool(entry.extra.get("network_records"))

    if not (has_silent_failure or has_ui_in_extra or has_network_in_extra):
        return None, None

    try:
        ui_events = get_ui_events(entry.trace_id)
        if not ui_events and has_ui_in_extra:
            ui_events = ui_events_from_extra(entry.extra)
    except Exception:
        ui_events = ui_events_from_extra(entry.extra)

    try:
        network_trace = get_network_records(entry.trace_id)
        if not network_trace and has_network_in_extra:
            network_trace = network_records_from_extra(entry.extra)
    except Exception:
        network_trace = network_records_from_extra(entry.extra)

    return ui_events or None, network_trace or None


def build_debug_context(trace_id: str | None = None, include_runtime: bool = True) -> DebugContext | None:
    entry = get_trace(trace_id)
    if entry is None:
        return None

    runtime = get_runtime_snapshot() if include_runtime else None

    frames_as_dict = [f.model_dump() for f in entry.frames]
    code_snippets = [_redact_snippet(s) for s in get_snippets_for_frames(frames_as_dict)]

    git_blame = []
    recent_diffs = []
    for frame in entry.frames[:3]:
        blame = get_blame_for_frame(frame.file, frame.line)
        if blame:
            git_blame.append(GitBlameInfo(**blame))
        diff = get_recent_diff(frame.file, commits_back=3)
        if diff:
            recent_diffs.append(GitDiffInfo(**diff))

    related_specs = _collect_related_specs(entry.frames)
    ui_events, network_trace = _collect_ui_and_network(entry)

    return DebugContext(
        trace=entry,
        runtime=runtime,
        code_snippets=code_snippets,
        git_blame=git_blame or None,
        recent_diffs=recent_diffs or None,
        related_specs=related_specs or None,
        ui_events=ui_events,
        network_trace=network_trace,
    )
