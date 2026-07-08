"""
MCP 工具：verify_ui —— 按 UI 规范启动 Playwright 自动遍历并验证交互结果。

输入 spec 规范的 kind 必须为 "ui"，含 target(页面 URL) + interactions 列表。
输出 {matched, diffs, silent_failure, interactions: [{action, matched, diffs}]}。

可选依赖：playwright（未安装时返回错误提示，不影响其他功能）。
"""
from app.mcp.verifier import ui_runner

VERIFY_UI_DEF = {
    "name": "verify_ui",
    "description": (
        "按 UI 规范启动浏览器自动遍历页面交互并验证结果。"
        "spec.kind 须为 'ui'，含 target(页面URL) 和 expect.interactions 列表。"
        "交互类型: click / type / navigate / hover / select。"
        "返回 {matched, diffs, silent_failure, interactions[]}。"
        "需要 Playwright（pip install playwright && playwright install chromium）。"
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "spec": {
                "type": "object",
                "description": "UI 规范 {kind:'ui', target, expect}",
            },
            "spec_id": {
                "type": "string",
                "description": "已存储规范的 ID（与 spec 二选一）",
            },
            "timeout_ms": {
                "type": "integer",
                "description": "单个操作超时毫秒（默认 30000）",
            },
        },
        "required": [],
    },
}


def verify_ui_handler(arguments: dict) -> dict:
    """verify_ui 工具处理函数。"""
    spec = arguments.get("spec")
    spec_id = arguments.get("spec_id")
    timeout_ms = arguments.get("timeout_ms", 30000)

    # spec 优先；spec_id 从存储取
    if spec is None and spec_id:
        from app.mcp.verifier import spec_store
        spec = spec_store.get(spec_id)

    if spec is None:
        return {
            "matched": False,
            "diffs": [],
            "silent_failure": False,
            "error": "must provide spec or spec_id",
        }

    if spec.get("kind") != "ui":
        return {
            "matched": False,
            "diffs": [{"field": "kind", "expected": "ui", "actual": spec.get("kind")}],
            "silent_failure": False,
            "error": "spec.kind must be 'ui'",
        }

    return ui_runner.run_ui_verification(spec, timeout_ms=timeout_ms)
