"""
给 REST 版 MCP 路由（/mcp/*，供人工测试用）使用的结构。
真正的 stdio MCP server（app/mcp_server.py）走官方 SDK 的类型，不依赖这里。
"""
from typing import Any, Optional
from pydantic import BaseModel


class ToolCallRequest(BaseModel):
    arguments: dict[str, Any] = {}


class ToolDescriptor(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class ToolListResponse(BaseModel):
    tools: list[ToolDescriptor]


class ToolCallResponse(BaseModel):
    content: Any
    error: Optional[str] = None
