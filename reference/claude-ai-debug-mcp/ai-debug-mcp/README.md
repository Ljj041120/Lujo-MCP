# ai-debug-mcp

给 AI（Trae / Codex / Claude Desktop 等 agentic 工具）用的调试数据供给服务。

## 定位

**MCP Server 只负责"采集数据"，不负责"下结论"。**

- `get_debug_context` 一次性返回：异常堆栈 + 运行时快照 + 出错位置的源码片段
- 宿主 AI（Trae/Codex 里的模型）拿到这些结构化原始数据后自行推理根因
- 内置的 `analyze_with_llm` 仅作为可选工具保留，正常场景不需要用它（否则会重复推理、重复花钱）

## 两套入口，互不依赖

| 入口 | 用途 | 启动方式 |
|---|---|---|
| `app/mcp_server.py` | 给 Trae/Codex 等 MCP 客户端用，走 stdio + JSON-RPC 标准MCP协议 | 由 MCP 客户端自动启动，见下方配置 |
| `app/main.py` | 人用调试面板 / 手动测试用，普通 REST API | `uvicorn app.main:app --port 8000` |

两者共享同一套底层逻辑（`app/mcp/collectors`、`builders`、`core`），互不依赖，可以只跑一个。

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env   # 按需修改，纯采集功能不需要配置OPENAI_API_KEY

# 方式1：本地手动验证采集链路是否正常
python -m examples.error_demo

# 方式2：用官方 MCP Inspector 验证 stdio server 是否正确
pip install "mcp[cli]"
mcp dev app/mcp_server.py

# 方式3：起人用的Web面板
uvicorn app.main:app --reload --port 8000
# 然后 curl http://localhost:8000/api/debug/runtime -H "X-API-Key: xxx"
```

## 接入 Trae / Codex

把 `mcp_config_example.json` 里的内容（改成你的真实绝对路径）填进对应工具的 MCP 配置里即可。
之后在 Trae/Codex 里，AI 就能自主调用：

- `list_recent_traces` — 看看最近有哪些错误
- `get_debug_context` — 深入某一条，一次拿到堆栈+运行时+代码片段
- `search_logs` — 按关键字翻历史记录

## 让错误自动被记录

项目里已经装了全局异常钩子（`app/mcp/hooks/exception_hook.py`），
在你自己项目里引入这个包并调用一次 `install_global_hook()`，未处理异常会自动落库，
AI 随时能通过 `list_recent_traces` 看到最新错误，不需要你手动上报。

FastAPI 场景下 `app/main.py` 已经默认在启动时装好了，并额外用 middleware 兜底捕获请求处理中的异常。

## 目录结构

```
app/
├── mcp_server.py       # ★ stdio MCP入口,Trae/Codex真正启动这个
├── main.py              # FastAPI人用面板
├── config.py            # 统一配置管理
├── api/
│   ├── debug.py          # /api/debug/* 人用接口
│   ├── mcp_routes.py      # /mcp/* REST方式测试工具用（非标准MCP协议）
│   └── auth.py            # 简单API Key鉴权
├── llm/
│   └── analyzer.py        # 可选LLM分析器，带重试超时
├── schemas/               # Pydantic数据结构定义
└── mcp/
    ├── collectors/
    │   ├── runtime.py       # 运行时快照
    │   ├── stacktrace.py    # 堆栈捕获与格式化
    │   └── code_locator.py  # ★ 核心差异化能力：自动定位出错代码片段
    ├── builders/
    │   └── context.py        # 组装成完整DebugContext
    ├── core/
    │   ├── logs.py            # SQLite持久化追踪存储，带TTL清理
    │   └── session.py         # 会话管理
    ├── hooks/
    │   └── exception_hook.py  # 全局异常自动捕获
    └── tools/                 # 业务逻辑，被mcp_server.py和mcp_routes.py共同复用
```

## 后续可以补的方向

- 存储层从SQLite换成Postgres/Redis，支持多worker横向扩展
- code_locator 支持读取 git blame，让AI知道这段代码是谁最近改的
- 增加 `get_recent_diff` 工具，配合 git，让AI判断"是不是最近这次改动导致的"
