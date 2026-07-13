"""
轻量 MCP stdio 入口 —— 只加载 MCP 工具，不启动 FastAPI。

供 Qoder / Claude Desktop 等 MCP 客户端连接。
"""
import sys
import json
import logging

# 最小化导入：只注册 MCP 工具，不加载 FastAPI
from app.mcp.tools import register_all_tools
from app.mcp.protocol.server import dispatch
from app.mcp.protocol.jsonrpc import parse_request, make_error, INVALID_REQUEST

register_all_tools()

# 日志全走 stderr
logging.basicConfig(level=logging.INFO, stream=sys.stderr, force=True)
logger = logging.getLogger("ai-debug-mcp.stdio")


def main():
    logger.info("MCP stdio server 启动，等待 stdin 输入")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = parse_request(line)
        except Exception as e:
            sys.stdout.write(
                json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": INVALID_REQUEST, "message": str(e)}}) + "\n"
            )
            sys.stdout.flush()
            continue

        result = dispatch(req)
        if req.id is None:
            continue
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    logger.info("MCP stdio server 关闭（stdin 关闭）")


if __name__ == "__main__":
    main()
