"""
Git 信息集成。

为堆栈帧提供 blame 和最近 diff，帮助 AI 判断错误是不是近期改动引入的。
所有命令都带超时，失败时返回 None，不影响主调试流程。
"""
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _git_cmd(args: list[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout if result.returncode == 0 else None
    except Exception:
        return None


def _parse_blame_line(porcelain: str) -> dict | None:
    """解析 `git blame -L n,n --porcelain` 的单行输出。"""
    lines = porcelain.splitlines()
    if not lines:
        return None

    commit = lines[0].split()[0]
    author = ""
    author_time = ""
    summary = ""
    line_text = ""

    for line in lines:
        if line.startswith("author "):
            author = line[7:]
        elif line.startswith("author-time "):
            try:
                ts = int(line[12:])
                author_time = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            except ValueError:
                author_time = line[12:]
        elif line.startswith("summary "):
            summary = line[8:]
        elif line.startswith("\t"):
            line_text = line[1:]

    if not commit or commit.startswith("0000000"):
        # 未跟踪行（如文件未提交时）
        return None

    return {
        "commit": commit,
        "author": author,
        "date": author_time,
        "summary": summary,
        "line_text": line_text,
    }


def get_blame_for_frame(file_path: str, line_no: int) -> dict | None:
    """返回指定文件/行最后是谁在哪次 commit 改的。"""
    path = Path(file_path)
    cwd = path.parent
    out = _git_cmd(
        ["blame", "-L", f"{line_no},{line_no}", "--porcelain", path.name],
        cwd,
    )
    if not out:
        return None

    parsed = _parse_blame_line(out)
    if not parsed:
        return None

    return {
        "file": file_path,
        "line": line_no,
        **parsed,
    }


def get_recent_diff(file_path: str, commits_back: int = 3) -> dict | None:
    """返回指定文件最近 N 次 commit 的 diff。"""
    path = Path(file_path)
    cwd = path.parent
    out = _git_cmd(
        ["diff", f"HEAD~{commits_back}", "--", path.name],
        cwd,
    )
    if not out:
        return None

    return {
        "file": file_path,
        "commits_back": commits_back,
        "diff": out,
    }
