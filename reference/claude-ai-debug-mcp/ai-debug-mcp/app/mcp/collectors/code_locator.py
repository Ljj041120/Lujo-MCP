"""
代码定位器 —— 这是整个项目里对"节省排查时间"贡献最大的一个模块。

给定堆栈帧的 file + line，自动读取该行附近的源码，带高亮标记，
让宿主 AI 不需要再单独打开文件、翻到对应行号，一次调用就拿到
"哪里错了 + 错误代码长什么样"。
"""
import linecache

from app.config import settings
from app.schemas.context import CodeSnippet


def get_code_snippet(file_path: str, line_no: int, context_lines: int | None = None) -> CodeSnippet:
    context_lines = context_lines or settings.code_context_lines

    # linecache 对不存在的文件/行会返回空字符串，不会抛异常
    linecache.checkcache(file_path)
    first_line = linecache.getline(file_path, 1)
    if not first_line and line_no > 0:
        # 尝试确认文件是否真的读取不到（比如属于第三方库的 .pyc 或路径已变化）
        probe = linecache.getline(file_path, line_no)
        if not probe:
            return CodeSnippet(file=file_path, error_line=line_no, snippet="", found=False)

    start = max(1, line_no - context_lines)
    end = line_no + context_lines
    lines = []
    for i in range(start, end + 1):
        text = linecache.getline(file_path, i)
        if not text:
            continue
        marker = ">>> " if i == line_no else "    "
        lines.append(f"{marker}{i}: {text.rstrip()}")

    snippet = "\n".join(lines)
    return CodeSnippet(
        file=file_path,
        error_line=line_no,
        snippet=snippet if snippet else "(无法读取该文件源码，可能是内置模块或路径不存在)",
        found=bool(snippet),
    )


def get_snippets_for_frames(frames: list[dict], context_lines: int | None = None) -> list[CodeSnippet]:
    """批量处理堆栈里的每一帧,frames 结构对应 StackFrame.model_dump()"""
    return [get_code_snippet(f["file"], f["line"], context_lines) for f in frames]
