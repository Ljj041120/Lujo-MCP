"""Dashboard API —— Web 控制台后端接口"""

import logging

from fastapi import APIRouter, HTTPException

from app.mcp.core import errors, trace_repo, logs

logger = logging.getLogger("ai-debug-mcp.dashboard")

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
def get_stats():
    """控制台概览统计"""
    recent = errors.list_recent(limit=100)

    total = len(recent)
    silent_count = sum(
        1 for e in recent if e.get("type") == "SilentFailure"
        or trace_repo.get_trace(e["error_id"]) is not None
        and trace_repo.get_trace(e["error_id"]).get("trace_kind") == "silent_failure"
    )

    # 检查 spec 数量
    from app.mcp.verifier import spec_store
    spec_count = len(spec_store.list_specs())

    return {
        "total_traces": total,
        "silent_failures": silent_count,
        "exceptions": total - silent_count,
        "spec_count": spec_count,
    }


@router.get("/traces")
def list_traces(limit: int = 20):
    """列出最近 traces（含 verify 结果摘要）"""
    items = errors.list_recent(limit=limit)
    result = []

    for e in items:
        tid = e["error_id"]
        trace = trace_repo.get_trace(tid) or {}
        trace_kind = trace.get("trace_kind", "exception")

        # 取 verify 结果
        spec_diffs = []
        try:
            spec_diffs = [
                entry["data"] for entry in logs.get_logs(tid)
                if entry.get("step") == "verify"
            ]
        except Exception:
            pass

        has_silent = trace_kind == "silent_failure" or any(
            d.get("silent_failure") for d in spec_diffs
        )

        result.append({
            "trace_id": tid,
            "timestamp": e.get("last_seen", e.get("timestamp", 0)),
            "type": e.get("type", ""),
            "message": (e.get("message") or "")[:200],
            "trace_kind": trace_kind,
            "occurrence_count": e.get("occurrence_count", 1),
            "has_silent_failure": has_silent,
            "verify_count": len(spec_diffs),
        })

    return {"traces": result, "total": len(result)}


@router.get("/trace/{trace_id}")
def get_trace_detail(trace_id: str):
    """获取 trace 详情（含 spec_diffs）"""
    from app.mcp.builders.context import build_debug_context

    ctx = build_debug_context(trace_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"找不到 trace {trace_id}")

    # 精简返回（去掉 runtime/network_trace 等大字段）
    return {
        "trace_id": ctx.get("trace_id"),
        "trace_kind": ctx.get("trace_kind"),
        "exception": ctx.get("exception"),
        "errors": ctx.get("errors"),
        "spec_diffs": ctx.get("spec_diffs"),
        "code_snippets": ctx.get("code_snippets"),
        "source": ctx.get("source"),
        "extra": ctx.get("extra"),
    }


@router.get("/specs")
def list_specs():
    """列出所有已存规范"""
    from app.mcp.verifier import spec_store
    return {"specs": spec_store.list_specs()}
