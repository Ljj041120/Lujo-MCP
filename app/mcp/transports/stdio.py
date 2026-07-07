"""MCP stdio 传输 —— 供 Claude Desktop 等本地客户端以子进程方式连接

协议：stdin 逐行读取 JSON-RPC 请求，stdout 逐行写回 JSON-RPC 响应。
通知（无 id）不写回响应体。
注意：stdin/stdout 是协议通道，日志必须走 stderr，否则会污染协议流。
"""
import sys
import json
import logging

from app.mcp.tools import register_all_tools
from app.mcp.protocol.server import dispatch
from app.mcp.protocol.jsonrpc import parse_request
from app.mcp.protocol.jsonrpc import make_error, INVALID_REQUEST

logger = logging.getLogger("ai-debug-mcp.mcp.stdio")


def _configure_stdio_logging():
    """日志重定向到 stderr，避免污染 stdout 协议通道"""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def run_stdio():
    _configure_stdio_logging()
    register_all_tools()
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

        # 通知类消息（无 id）不写回响应
        if req.id is None:
            continue

        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    logger.info("MCP stdio server 关闭（stdin 关闭）")


if __name__ == "__main__":
    run_stdio()
