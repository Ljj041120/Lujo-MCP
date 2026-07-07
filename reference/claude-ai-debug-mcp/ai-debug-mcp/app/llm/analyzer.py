"""
LLM 分析器 —— 定位为"可选工具"，不是主路径。

设计原则（详见项目讨论）：
- 接入 Trae/Codex 等 agentic 工具时，宿主 AI 本身就会消费 get_debug_context
  返回的原始数据自行推理，不需要这里再调一次模型，否则等于重复推理、重复花钱。
- 这个模块只在两种场景下使用：
  1. 独立使用 FastAPI 面板（POST /api/debug/analyze），没有宿主 AI 时
  2. 作为 MCP 的可选工具 analyze_with_llm，给不具备推理能力的轻量客户端用

带超时 + 重试，避免一次 OpenAI 调用卡住整个调试流程。
"""
import json

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import settings
from app.schemas.context import DebugContext
from app.schemas.debug import LLMAnalysisResult

_SYSTEM_PROMPT = (
    "你是一个资深的软件调试专家。你会收到一段结构化的调试上下文，"
    "包含异常堆栈、运行时快照和相关代码片段。"
    "请只输出 JSON，不要输出任何其他文字、不要用markdown代码块包裹，字段为："
    '{"root_cause": "根因分析（简洁，中文）", '
    '"fix_suggestion": "具体修复建议（可以包含代码片段）", '
    '"confidence": 0.0到1.0之间的置信度, '
    '"caveats": "如果信息不足以确诊，说明还需要什么额外信息，否则留空字符串"}'
)


def _build_user_prompt(context: DebugContext) -> str:
    # 上下文可能很大（比如很多帧、很长的代码片段），做一个简单的截断保护，
    # 避免超出模型上下文窗口。这里用字符数近似估算 token 数（粗略 1 token ≈ 2-4字符的中英混合场景）。
    payload = context.model_dump()
    text = json.dumps(payload, ensure_ascii=False)
    max_chars = settings.max_context_tokens_hint * 3
    if len(text) > max_chars:
        # 截断策略：优先保留 trace 和前几个 code_snippets，砍掉多余的帧和快照细节
        payload["code_snippets"] = payload["code_snippets"][:3]
        payload["trace"]["frames"] = payload["trace"]["frames"][:5]
        text = json.dumps(payload, ensure_ascii=False)
    return text


class AnalyzerUnavailableError(Exception):
    """OPENAI_API_KEY 未配置或调用彻底失败时抛出"""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _call_llm(user_prompt: str) -> str:
    client = OpenAI(api_key=settings.openai_api_key, timeout=settings.llm_timeout_seconds)
    resp = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )
    return resp.choices[0].message.content or ""


def analyze_with_llm(context: DebugContext) -> LLMAnalysisResult:
    if not settings.openai_api_key:
        raise AnalyzerUnavailableError(
            "OPENAI_API_KEY 未配置，无法使用内置LLM分析。"
            "建议：接入 Trae/Codex 时直接用 get_debug_context 让宿主AI自行分析，不依赖这个功能。"
        )

    user_prompt = _build_user_prompt(context)

    try:
        raw = _call_llm(user_prompt)
    except Exception as e:
        raise AnalyzerUnavailableError(f"LLM 调用失败（已重试）：{e}") from e

    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # 模型偶尔仍会输出非纯JSON，做一次兜底，不让整个请求失败
        return LLMAnalysisResult(
            root_cause="模型输出格式异常，无法解析",
            fix_suggestion=raw[:500],
            confidence=0.0,
            caveats="LLM返回内容不是合法JSON，已原样截断返回，请人工核实",
        )

    return LLMAnalysisResult(
        root_cause=data.get("root_cause", ""),
        fix_suggestion=data.get("fix_suggestion", ""),
        confidence=float(data.get("confidence", 0.0)),
        caveats=data.get("caveats") or None,
    )
