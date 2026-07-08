# ai-debug-mcp

基于 MCP（Model Context Protocol）协议的 AI 智能调试服务。

## 功能

- **请求追踪** — 自动记录每个请求的完整执行链路（时间、步骤、数据）
- **调试上下文构建** — 将原始追踪日志转换为 AI 可理解的结构化上下文
- **异常堆栈捕获** — 捕获异常调用栈、局部变量、源码行号
- **运行时快照** — 采集系统/进程/解释器状态（CPU、内存、线程等）
- **LLM 智能分析** — 对接 OpenAI，自动分析错误根因并给出修复建议
- **MCP 工具集** — 提供标准化的 debug / context / trace / stacktrace 工具
- **规范驱动 + verify 自动断言** — 定义期望规范，系统自动比对实际结果，检测"返回正常但不符合规范"的静默失败

## 项目结构

```
ai-debug-mcp/
├── app/
│   ├── main.py               # FastAPI 应用入口
│   ├── api/
│   │   ├── debug.py          # 调试 API 路由
│   │   └── mcp_routes.py     # MCP Streamable HTTP 传输路由 (POST/GET SSE/DELETE)
│   ├── llm/
│   │   └── analyzer.py       # LLM 错误分析器
│   ├── mcp/
│   │   ├── builders/
│   │   │   └── context.py    # 调试上下文构建器
│   │   ├── collectors/
│   │   │   ├── runtime.py    # 运行时信息收集器
│   │   │   └── stacktrace.py # 堆栈追踪收集器
│   │   ├── core/
│   │   │   ├── logs.py       # 日志追踪存储
│   │   │   ├── storage/      # 存储抽象层（memory / postgresql）
│   │   │   └── session.py    # 会话管理
│   │   ├── protocol/         # JSON-RPC 2.0 解析 + MCP 服务端分发
│   │   ├── transports/       # stdio / Streamable HTTP 传输实现
│   │   │   ├── session.py    # MCP 会话注册表
│   │   │   ├── sse.py        # SSE 广播中心
│   │   │   └── stdio.py      # stdio 传输（Claude Desktop 子进程）
│   │   └── tools/
│   │       ├── debug_api.py      # MCP 调试工具
│   │       ├── context_api.py    # MCP 上下文工具
│   │       ├── trace_api.py      # MCP 追踪工具
│   │       └── stacktrace_api.py # MCP 堆栈追踪工具
│   ├── middleware.py          # 鉴权 / CORS / 限流 / 追踪中间件
│   ├── error_handlers.py      # 全局异常兜底
│   ├── observability.py       # /metrics Prometheus 指标
│   ├── config.py              # 统一配置（pydantic-settings）
│   ├── schemas/               # Pydantic 数据模型层
│   ├── services/
│   └── utils/
├── examples/
│   ├── error_demo.py         # 错误演示脚本
│   └── trace_demo.json       # 追踪数据示例
├── requirements.txt
├── .env
└── README.md
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

编辑 `.env` 文件，填入你的 OpenAI API Key：

```
OPENAI_API_KEY=sk-your-key-here
LLM_MODEL=gpt-4
```

### 3. 启动服务

```bash
python -m app.main
```

服务启动在 `http://localhost:8000`。

### 4. 使用 API

#### 健康检查

```bash
curl http://localhost:8000/
# → {"status":"ok","service":"ai-debug-mcp","version":"0.1.0"}
```

#### 快速调试

```bash
curl -X POST http://localhost:8000/debug \
  -H "Content-Type: application/json" \
  -d '{"operation": "test", "data": 123}'
```

#### 完整调试流程

```bash
curl -X POST http://localhost:8000/api/debug/run \
  -H "Content-Type: application/json" \
  -d '{"payload": {"operation": "test"}, "metadata": {"source": "curl"}}'
```

#### LLM 智能分析

```bash
curl -X POST http://localhost:8000/api/debug/analyze \
  -H "Content-Type: application/json" \
  -d '{"request_id": "your-request-id"}'
```

#### 运行时快照

```bash
curl http://localhost:8000/api/debug/runtime
```

#### verify 自动断言（静默失败检测）

```bash
curl -X POST http://localhost:8000/api/debug/verify \
  -H "Content-Type: application/json" \
  -d '{
    "actual": {"status_code": 200, "body": {"success": true}},
    "spec": {
      "kind": "api",
      "target": "POST /api/login",
      "expect": {"status": 200, "body_rules": {"success": false}}
    },
    "trace_id": "optional-trace-id"
  }'
# → {"matched": false, "diffs": [...], "silent_failure": true}
```

也可通过 MCP 工具 `verify` 调用（stdio / HTTP 传输均可），或用 `spec_id` 引用已存储规范。

#### 规范 CRUD

```bash
# 创建规范
curl -X POST http://localhost:8000/api/spec \
  -H "Content-Type: application/json" \
  -d '{"kind":"api","target":"POST /api/login","expect":{"status":200}}'

# 列出规范（支持 ?kind=api & ?target=login）
curl http://localhost:8000/api/spec

# 查看/更新/删除
curl http://localhost:8000/api/spec/{spec_id}
curl -X PATCH http://localhost:8000/api/spec/{spec_id} -d '{"target":"new"}'
curl -X DELETE http://localhost:8000/api/spec/{spec_id}
```

#### MCP 工具列表

```bash
curl -X POST http://localhost:8000/mcp/tools/list
```

## MCP 传输（两种模式）

本服务同时支持 **Streamable HTTP** 和 **stdio** 两种标准 MCP 传输，可被真实 MCP 客户端（Claude Desktop / Code）连接。

### 模式 A：Streamable HTTP（远程客户端）

```bash
# 1) 初始化握手（服务端返回 Mcp-Session-Id 响应头）
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'

# 2) 发送通知完成初始化
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Mcp-Session-Id: <上一步返回的session>" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# 3) 调用工具
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Mcp-Session-Id: <session>" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"debug","arguments":{"payload":{"x":1}}}}'

# 4) 打开 SSE 流（服务端→客户端推送通道）
curl -N -X GET http://localhost:8000/mcp \
  -H "Accept: text/event-stream" \
  -H "Mcp-Session-Id: <session>"

# 5) 结束会话
curl -X DELETE http://localhost:8000/mcp -H "Mcp-Session-Id: <session>"
```

### 模式 B：stdio（本地 Claude Desktop 子进程）

在 Claude Desktop 的 `claude_desktop_config.json` 中配置：

```json
{
  "mcpServers": {
    "ai-debug-mcp": {
      "command": "python",
      "args": ["-m", "app.mcp.transports.stdio"],
      "cwd": "/path/to/ai-debug-mcp"
    }
  }
}
```

启动服务时也可直接指定 stdio 模式：

```bash
python -m app.main --stdio
```

## 运行示例

```bash
python examples/error_demo.py
```

该脚本会触发一个除零错误，展示完整的调试追踪和堆栈捕获流程。

## 技术栈

- **Web 框架**: FastAPI + Uvicorn
- **AI**: OpenAI API (GPT-4)
- **系统监控**: psutil
- **协议**: MCP (Model Context Protocol)
