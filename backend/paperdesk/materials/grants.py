"""补充材料的会话级授权（说明书 §9.3）。

每次打开论文时询问一次是否允许 AI 使用关联补充材料；同意后在“当前阅读期间”有效。
授权只保存在后端内存里（重启即失效），并带 TTL 兜底；未授权时 AI 不得读取/发送任何补充材料内容。
前端在打开论文时先 reset（decision=None）再提示，因此“当前阅读期间”＝本次打开。
"""
from __future__ import annotations

import threading
import time

from ..context import AppContext
from ..util import utcnow

TTL_SECONDS = 8 * 3600
_LOCK = threading.Lock()


def _store(app: AppContext) -> dict[str, dict]:
    with _LOCK:
        return app.__dict__.setdefault("_supplement_grants", {})


def _live(entry: dict | None) -> dict | None:
    if entry and entry["expires"] > time.time():
        return entry
    return None


def set_decision(app: AppContext, paper_id: str, allow: bool | None, ttl_seconds: int | None = None) -> dict:
    """allow=True/False 记录决定；None 清除（相当于“尚未询问”）。ttl_seconds 是这次决定的有效期，默认 TTL_SECONDS。"""
    store = _store(app)
    ttl = ttl_seconds or TTL_SECONDS
    with _LOCK:
        if allow is None:
            store.pop(paper_id, None)
        else:
            store[paper_id] = {"decision": "allow" if allow else "deny", "expires": time.time() + ttl, "ttl": ttl, "at": utcnow()}
    return state(app, paper_id)


def is_granted(app: AppContext, paper_id: str) -> bool:
    entry = _live(_store(app).get(paper_id))
    return bool(entry and entry["decision"] == "allow")


def state(app: AppContext, paper_id: str) -> dict:
    entry = _live(_store(app).get(paper_id))
    return {
        "granted": bool(entry and entry["decision"] == "allow"),
        "decision": entry["decision"] if entry else None,  # null = 本次尚未询问
        "decided_at": entry["at"] if entry else None,
        "ttl_seconds": entry.get("ttl", TTL_SECONDS) if entry else TTL_SECONDS,
    }
