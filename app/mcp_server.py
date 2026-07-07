"""
标准 MCP Server（stdio transport）。

这是 Trae / Codex / Claude Desktop 之类的 MCP 客户端真正会启动的入口，
通过 stdio 管道 + JSON-RPC 协议通信（由 mcp SDK 处理，不需要自己实现协议细节）。

注册方式（在 Trae/Codex 的 MCP 配置里）：
{
  "mcpServers": {
    "ai-debug-mcp": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/绝对路径/ai-debug-mcp"
    }
  }
}

设计原则：这里只暴露"采集数据"的工具（get_stacktrace / get_debug_context /
get_runtime_snapshot / search_logs / list_recent_traces），
不默认做LLM推理 —— 宿主AI（Trae/Codex里的模型）拿到原始数据后自己判断根因，
这样避免重复推理、重复花钱。analyze_with_llm 作为可选工具保留，
仅在宿主客户端本身不具备推理能力时才需要用它。
"""
import asyncio
import json
import logging

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from app.mcp.tools.stacktrace_api import get_stacktrace as tool_get_stacktrace
from app.mcp.tools.trace_api import list_recent_traces as tool_list_recent_traces, search_logs as tool_search_logs
from app.mcp.tools.debug_api import (
    get_debug_context as tool_get_debug_context,
    get_runtime_snapshot as tool_get_runtime_snapshot,
    analyze_with_llm as tool_analyze_with_llm,
)
from app.mcp.hooks.exception_hook import install_global_hook

logging.basicConfig(level=logging.INFO, stream=None)  # stdio模式下不要往stdout打日志，避免污染协议流
logger = logging.getLogger("ai-debug-mcp")

server = Server("ai-debug-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_stacktrace",
            description=(
                "获取最近一次捕获的异常堆栈，包含每一帧的文件路径、行号、函数名。"
                "不传 trace_id 则返回最新一条。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "trace_id": {"type": "string", "description": "可选，指定要查看的追踪ID"}
                },
            },
        ),
        Tool(
            name="get_runtime_snapshot",
            description="获取当前进程运行时快照：CPU占用、内存、线程数、Python版本，用于判断是否是资源类问题。",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="search_logs",
            description="按关键字和时间范围（最近N分钟）搜索历史追踪记录。",
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键字，匹配异常类型或异常消息"},
                    "since_minutes": {"type": "integer", "default": 30, "description": "只搜索最近N分钟内的记录"},
                },
                "required": ["keyword"],
            },
        ),
        Tool(
            name="get_debug_context",
            description=(
                "【核心工具】一次性获取某次错误的完整调试上下文：异常堆栈 + 运行时快照 + "
                "堆栈每一帧对应的源码片段（自动定位，含可点击的 IDE 链接，无需再单独读取文件）。"
                "推荐宿主AI拿到这份数据后自行分析根因，不需要额外调用 analyze_with_llm。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "trace_id": {"type": "string", "description": "可选，指定 error_id；不传则取最新一条捕获记录"}
                },
            },
        ),
        Tool(
            name="list_recent_traces",
            description="列出最近发生的错误追踪记录摘要列表（不含完整堆栈），供AI选择要深入查看哪一条。",
            inputSchema={
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 10}},
            },
        ),
        Tool(
            name="analyze_with_llm",
            description=(
                "【可选工具，一般不需要调用】调用内置LLM对指定追踪记录做根因分析。"
                "仅在当前MCP客户端本身不具备AI推理能力时才使用此工具；"
                "如果你（宿主AI）本身能推理，请直接用 get_debug_context 拿原始数据自行分析，"
                "调用这个会产生重复的LLM调用花费。"
            ),
            inputSchema={
                "type": "object",
                "properties": {"trace_id": {"type": "string"}},
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "get_stacktrace":
            result = tool_get_stacktrace(arguments.get("trace_id"))
        elif name == "get_runtime_snapshot":
            result = tool_get_runtime_snapshot()
        elif name == "search_logs":
            result = tool_search_logs(
                keyword=arguments["keyword"],
                since_minutes=arguments.get("since_minutes", 30),
            )
        elif name == "get_debug_context":
            result = tool_get_debug_context(arguments.get("trace_id"))
        elif name == "list_recent_traces":
            result = tool_list_recent_traces(arguments.get("limit", 10))
        elif name == "analyze_with_llm":
            result = tool_analyze_with_llm(arguments.get("trace_id"))
        else:
            result = {"error": f"未知工具: {name}"}
    except Exception as e:
        logger.exception("工具调用失败: %s", name)
        result = {"error": f"工具执行异常: {e}"}

    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


async def main():
    install_global_hook()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
