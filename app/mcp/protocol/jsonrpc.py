"""MCP 协议核心 —— JSON-RPC 2.0 消息解析与封装"""

import json
from typing import Any, Optional
from pydantic import BaseModel


class JSONRPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[int] = None
    method: str
    params: Optional[dict] = None


class JSONRPCResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[int] = None
    result: Optional[Any] = None
    error: Optional[dict] = None


# 标准 JSON-RPC 错误码
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def make_response(id: Optional[int], result: Any) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": id,
        "result": result,
    }


def make_error(id: Optional[int], code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": id,
        "error": {
            "code": code,
            "message": message,
        },
    }


def parse_request(raw: str | bytes) -> JSONRPCRequest:
    """解析原始 JSON-RPC 请求"""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON")

    return JSONRPCRequest(**data)
