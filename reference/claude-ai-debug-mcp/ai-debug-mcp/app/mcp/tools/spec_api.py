"""
MCP 工具：get_related_specs —— 查询与指定文件相关的项目规范片段。
"""
from app.mcp.collectors.spec import get_related_specs


def tool_get_related_specs(file_path: str) -> dict:
    """根据文件路径返回相关的项目规范片段，用于约束 AI 修复风格。"""
    specs = get_related_specs(file_path)
    return {
        "found": bool(specs),
        "count": len(specs),
        "specs": [s.model_dump() for s in specs],
    }
