"""
规范存储 —— 期望规范的 CRUD。

复用 TraceStorage 抽象（add_log / get_logs / delete_logs，step="spec"）做持久化备份，
同时用模块级 dict 做主存以支持 list / update / delete。

Spec = { id, kind: "api"|"ui"|"rule", target, expect: {status?, body_rules?, state_change?} }
"""
import time
import uuid
import threading

from app.mcp.core.logs import add_log, get_logs, delete_logs

_STEP_SPEC = "spec"

# 主存：spec_id → spec dict
_specs: dict[str, dict] = {}
_lock = threading.Lock()


def _new_id() -> str:
    return "spec-" + uuid.uuid4().hex[:12]


def create(spec: dict) -> str:
    """创建一条规范，返回 spec_id。

    spec 可选传 id（不传则自动生成）。
    """
    spec_id = spec.get("id") or _new_id()
    now = time.time()
    record = {
        "id": spec_id,
        "kind": spec.get("kind", "api"),
        "target": spec.get("target", ""),
        "expect": spec.get("expect") or {},
        "created_at": spec.get("created_at") or now,
        "updated_at": now,
    }
    with _lock:
        _specs[spec_id] = record
    # 持久化备份
    add_log(spec_id, _STEP_SPEC, record)
    return spec_id


def get(spec_id: str) -> dict | None:
    """取一条规范。优先从内存取，fallback 从存储层恢复。"""
    with _lock:
        if spec_id in _specs:
            return dict(_specs[spec_id])

    # fallback：从存储层恢复到内存
    for entry in get_logs(spec_id):
        if entry.get("step") == _STEP_SPEC:
            data = entry.get("data")
            if data:
                with _lock:
                    _specs[spec_id] = data
                return dict(data)
    return None


def update(spec_id: str, patch: dict) -> dict | None:
    """部分更新规范，返回更新后的规范。不存在则返回 None。

    id 不可修改。
    """
    with _lock:
        existing = _specs.get(spec_id)
        if not existing:
            return None
        existing.update(patch)
        existing["id"] = spec_id  # id 不可改
        existing["updated_at"] = time.time()
        updated = dict(existing)

    # 重新持久化（delete + add）
    try:
        delete_logs(spec_id)
    except Exception:
        pass
    add_log(spec_id, _STEP_SPEC, updated)
    return updated


def delete(spec_id: str) -> bool:
    """删除一条规范，返回是否删除成功。"""
    with _lock:
        existed = spec_id in _specs
        _specs.pop(spec_id, None)

    if existed:
        try:
            delete_logs(spec_id)
        except Exception:
            pass
    return existed


def list_specs(kind: str | None = None, target: str | None = None) -> list[dict]:
    """列出所有规范，可按 kind / target 过滤。按 updated_at 倒序。"""
    with _lock:
        items = [dict(s) for s in _specs.values()]

    if kind:
        items = [s for s in items if s.get("kind") == kind]
    if target:
        items = [s for s in items if target in (s.get("target") or "")]
    items.sort(key=lambda s: s.get("updated_at", 0), reverse=True)
    return items


def clear() -> None:
    """清空所有规范（测试用）。"""
    with _lock:
        _specs.clear()
