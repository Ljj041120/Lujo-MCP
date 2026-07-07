"""
规范采集器 —— 自动扫描项目中的规范文件，并匹配与报错文件相关的规范片段。

目标：让 AI 在分析错误时自动看到项目约定，减少用户手动复制规范到 prompt。
"""
import os
import time
from pathlib import Path
from typing import Optional

from app.config import settings
from app.mcp.core.redaction import redact
from app.schemas.context import SpecSnippet

# 显式优先识别的规范文件名
SPEC_CANDIDATES = [
    "CONVENTION.md",
    "API_SPEC.md",
    "COMPONENT_SPEC.md",
    "STYLE_GUIDE.md",
    "README.md",
    ".cursorrules",
]

# 按扩展名和文件名后缀识别规范文件
SPEC_SUFFIXES = (".md", ".cursorrules", "_spec.json", "_spec.yaml", "_spec.yml")

# 跳过目录
_SKIP_DIRS = {
    "node_modules", ".git", ".venv", "venv", "__pycache__",
    "dist", "build", ".tox", ".pytest_cache", ".mypy_cache",
    ".next", ".nuxt", "coverage", "target", "vendor",
    ".trae", ".idea", ".vscode",
}

# 文件名/标题关键词 -> 标签 & 目标扩展名
_TAG_RULES = [
    # API / 后端接口
    ({"api", "rest", "http", "endpoint", "swagger", "openapi"},
     ["api", "backend"], [".py", ".ts", ".js", ".java", ".go", ".rs"]),
    # UI / 前端组件
    ({"component", "ui", "vue", "react", "frontend", "jsx", "tsx", "svelte"},
     ["ui", "frontend"], [".vue", ".tsx", ".jsx", ".svelte", ".html"]),
    # 样式
    ({"style", "css", "scss", "less", "tailwind", "styled"},
     ["style"], [".css", ".scss", ".less", ".styl"]),
    # Python 后端
    ({"python", "django", "flask", "fastapi", "backend"},
     ["backend", "python"], [".py"]),
    # 数据库
    ({"database", "db", "sql", "orm", "prisma", "migration"},
     ["db"], [".py", ".sql", ".prisma"]),
    # 通用规范
    ({"convention", "guide", "rule", "standard", "cursor"},
     ["general"], []),
]

_MAX_FILE_BYTES = 1024 * 1024  # 1MB
_CHUNK_MAX_CHARS = 800
_TOTAL_MAX_CHARS = 6000  # 约 2000 tokens，按 1 token ≈ 3 中文字符估算

_spec_cache: dict = {
    "project_root": None,
    "specs": [],
    "mtime": 0,
}


def _find_project_root(file_path: str | Path) -> Path:
    r"""从文件路径向上查找项目根目录，直到用户主目录或文件系统根。

    只返回同时满足以下条件的目录：
    1. 包含 .git / pyproject.toml / package.json 之一；
    2. 仍在原始文件所在目录的祖先链上，且不超过用户主目录。
    避免把 C:\Users\ASUS\package.json 这种误识别为项目根。
    """
    path = Path(file_path).resolve()
    # 即使文件不存在，只要路径带后缀就按文件处理，返回其父目录
    if path.is_file() or path.suffix:
        file_parent = path.parent
    else:
        file_parent = path

    home = Path.home()
    for parent in [file_parent, *file_parent.parents]:
        # 不要超过用户主目录，避免扫描整个用户目录
        if home != Path(".") and (parent == home or not str(parent).startswith(str(home))):
            break
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists() or (parent / "package.json").exists():
            return parent

    # 兜底：返回文件所在目录
    return file_parent


def _extract_tags(path: Path, first_lines: str) -> tuple[list[str], list[str]]:
    """根据文件名和文件开头提取标签与目标扩展名。"""
    lower_name = path.name.lower()
    lower_head = first_lines.lower()
    text = f"{lower_name}\n{lower_head}"

    tags: set[str] = set()
    extensions: set[str] = set()

    for keywords, rule_tags, rule_exts in _TAG_RULES:
        if any(kw in text for kw in keywords):
            tags.update(rule_tags)
            extensions.update(rule_exts)

    # .cursorrules 默认通用
    if path.name == ".cursorrules":
        tags.add("general")

    # README 默认通用，但如果标题命中具体领域则保留更具体标签
    if lower_name.startswith("readme") and not tags:
        tags.add("general")

    return sorted(tags), sorted(extensions)


def _slice_content(content: str) -> tuple[str, str]:
    """按二级标题切分，取前几个 chunk，返回 (summary, sliced_content)。"""
    lines = content.splitlines()
    summary = lines[0].strip() if lines else ""
    if summary.startswith("# "):
        summary = summary[2:].strip()
    elif summary.startswith("## "):
        summary = summary[3:].strip()

    # 按 ## 切分，保留标题
    chunks = []
    current = []
    for line in lines:
        if line.startswith("## "):
            if current:
                chunks.append("\n".join(current))
                current = []
        current.append(line)
    if current:
        chunks.append("\n".join(current))

    # 如果没有二级标题，整篇作为一个 chunk
    if not chunks:
        chunks = [content]

    # 取前 3 个 chunk，每个限制长度
    selected: list[str] = []
    total = 0
    for chunk in chunks[:3]:
        if len(chunk) > _CHUNK_MAX_CHARS:
            chunk = chunk[:_CHUNK_MAX_CHARS] + "\n...（已截断）"
        if total + len(chunk) > _TOTAL_MAX_CHARS:
            remaining = _TOTAL_MAX_CHARS - total
            if remaining > 100:
                selected.append(chunk[:remaining] + "\n...（已截断）")
            break
        selected.append(chunk)
        total += len(chunk)

    return summary, "\n\n".join(selected)


def parse_spec_file(path: Path) -> Optional[SpecSnippet]:
    """解析单个规范文件为 SpecSnippet。"""
    try:
        stat = path.stat()
        if stat.st_size > _MAX_FILE_BYTES:
            return None

        content = path.read_text(encoding="utf-8", errors="ignore")
        if not content.strip():
            return None

        # 取前 30 行用于标签提取
        first_lines = "\n".join(content.splitlines()[:30])
        tags, target_extensions = _extract_tags(path, first_lines)

        summary, sliced = _slice_content(content)
        # 脱敏后使用
        sliced = redact(sliced) or sliced
        summary = redact(summary) or summary

        return SpecSnippet(
            file=str(path.resolve()),
            summary=summary,
            content=sliced,
            tags=tags,
            target_extensions=target_extensions,
        )
    except Exception:
        return None


def discover_spec_files(project_root: str | Path) -> list[Path]:
    """扫描项目根目录下的规范文件。"""
    root = Path(project_root)
    if not root.exists():
        return []

    found: set[Path] = set()

    # 显式候选文件（根目录优先）
    for name in SPEC_CANDIDATES:
        candidate = root / name
        if candidate.exists() and candidate.is_file():
            found.add(candidate.resolve())

    # 递归扫描 md / cursorrules / spec.json / spec.yaml
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        # 跳过不需要的目录
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        # 跳过超大文件
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue

        name = path.name.lower()
        if name in {c.lower() for c in SPEC_CANDIDATES}:
            found.add(path.resolve())
            continue
        if name.endswith(SPEC_SUFFIXES):
            found.add(path.resolve())

    return sorted(found)


def _load_specs(project_root: str | Path) -> list[SpecSnippet]:
    """加载并解析所有规范文件。"""
    files = discover_spec_files(project_root)
    specs = []
    for f in files:
        spec = parse_spec_file(f)
        if spec:
            specs.append(spec)
    return specs


def _cache_needs_refresh(project_root: Path) -> bool:
    """检查缓存是否需要刷新。基于项目根目录下规范文件的最大 mtime。"""
    global _spec_cache
    if _spec_cache["project_root"] != str(project_root):
        return True

    try:
        files = discover_spec_files(project_root)
        if not files:
            return _spec_cache["specs"] != []
        max_mtime = max(os.path.getmtime(f) for f in files)
        return max_mtime > _spec_cache["mtime"]
    except Exception:
        return False


def get_project_specs(project_root: Optional[str | Path] = None) -> list[SpecSnippet]:
    """获取项目规范列表，带缓存。"""
    global _spec_cache

    if project_root is None:
        project_root = Path(settings.db_path).parent

    root = Path(project_root)
    if not root.exists():
        return []

    if _cache_needs_refresh(root):
        _spec_cache = {
            "project_root": str(root),
            "specs": _load_specs(root),
            "mtime": time.time(),
        }

    return _spec_cache["specs"]


def reload_specs(project_root: Optional[str | Path] = None) -> list[SpecSnippet]:
    """强制刷新规范缓存。"""
    global _spec_cache
    _spec_cache["mtime"] = 0
    return get_project_specs(project_root)


def match_specs(error_file: str, specs: list[SpecSnippet], max_chars: int = _TOTAL_MAX_CHARS) -> list[SpecSnippet]:
    """根据报错文件扩展名匹配相关规范片段，并按重要性排序。"""
    ext = Path(error_file).suffix.lower()
    matched = []

    # 优先匹配明确指定了目标扩展名的规范
    for spec in specs:
        if ext in [e.lower() for e in spec.target_extensions]:
            matched.append(spec)

    # 若命中不足，补充通用规范
    general_specs = [s for s in specs if "general" in s.tags and s not in matched]
    matched.extend(general_specs)

    # 裁剪总长度
    result = []
    total = 0
    for spec in matched:
        length = len(spec.content)
        if total + length > max_chars:
            remaining = max_chars - total
            if remaining > 100:
                # 不修改原始对象，返回截断副本
                trimmed = spec.content[:remaining] + "\n...（已截断）"
                result.append(spec.model_copy(update={"content": trimmed}))
            break
        result.append(spec)
        total += length

    return result


def get_related_specs(file_path: str, project_root: Optional[str | Path] = None) -> list[SpecSnippet]:
    """获取与指定文件相关的项目规范片段。"""
    if project_root is None:
        project_root = _find_project_root(file_path)

    specs = get_project_specs(project_root)
    if not specs:
        return []

    return match_specs(file_path, specs)
