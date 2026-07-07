"""
最基础的 API Key 鉴权,防止 /api/debug/* 和 /mcp/* 被裸调用
（尤其是会触发LLM调用的 /analyze，被刷会直接产生费用）。

用法：请求头带 X-API-Key: <你在.env里配置的API_KEY>
生产环境建议换成更完整的方案（OAuth / JWT / 网关层鉴权），这里只是最低限度的防护。
"""
from fastapi import Header, HTTPException

from app.config import settings


def verify_api_key(x_api_key: str = Header(default="")):
    if not settings.api_key or settings.api_key == "change-me-to-a-real-secret":
        # 开发阶段没配置真实key时不强制拦截，但会在日志里提醒
        return
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="无效的API Key")
