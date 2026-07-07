"""调试上下文构建器 —— 安全地解析追踪日志"""

import logging

logger = logging.getLogger("ai-debug-mcp.context")


def build_context(request_id: str, logs: list) -> dict:
    """
    将原始 logs 转换成 AI 可理解的 debug context。
    对格式异常的日志记录做保护性处理。
    """
    flow = []
    input_data = None
    output_data = None
    errors = []

    for item in logs:
        try:
            step = item.get("step", "unknown")
            data = item.get("data")
            flow.append(step)

            if step == "request_start":
                input_data = data
            elif step == "response_ready":
                output_data = data
            elif step == "error":
                errors.append(data)
        except Exception:
            # 单条日志格式异常不应阻断整个上下文构建
            logger.warning(f"跳过格式异常日志: {item}", exc_info=True)
            flow.append("<malformed>")

    return {
        "request_id": request_id,
        "flow": flow,
        "input": input_data,
        "output": output_data,
        "errors": errors,
    }
