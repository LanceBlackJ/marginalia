"""比较结果的持久化与后台任务。"""
from __future__ import annotations

from ..context import AppContext
from ..db import register_schema
from ..jobs import JobCancelled, JobContext, JobManager
from ..ai.providers import LLMError
from ..util import ApiError, dumps, loads, new_id, utcnow
from .engine import MODE_LABELS, MODES, preflight, run_comparison

register_schema("compare", """
CREATE TABLE IF NOT EXISTS comparisons (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  mode TEXT NOT NULL,
  paper_ids_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  job_id TEXT,
  result_json TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
""")


def _row(r) -> dict:
    return {"id": r["id"], "title": r["title"], "mode": r["mode"], "mode_label": MODE_LABELS.get(r["mode"], r["mode"]),
            "paper_ids": loads(r["paper_ids_json"], []), "status": r["status"], "job_id": r["job_id"], "error": r["error"],
            "created_at": r["created_at"], "updated_at": r["updated_at"]}


def get_comparison(app: AppContext, cid: str) -> dict:
    with app.db.session() as conn:
        r = conn.execute("SELECT * FROM comparisons WHERE id = ?", (cid,)).fetchone()
    if r is None:
        raise ApiError("not_found", "比较记录不存在", 404)
    d = _row(r)
    d["result"] = loads(r["result_json"])
    return d


def list_comparisons(app: AppContext) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute("SELECT * FROM comparisons ORDER BY created_at DESC LIMIT 100").fetchall()
    return [_row(r) for r in rows]


def delete_comparison(app: AppContext, cid: str) -> None:
    with app.db.session() as conn:
        cur = conn.execute("DELETE FROM comparisons WHERE id = ?", (cid,))
    if cur.rowcount == 0:
        raise ApiError("not_found", "比较记录不存在", 404)


def create_comparison(app: AppContext, paper_ids: list[str], mode: str, title: str | None) -> dict:
    if mode not in MODES:
        raise ApiError("bad_request", f"不支持的比较模式：{mode}")
    ids = list(dict.fromkeys(paper_ids))
    pre = preflight(app, ids, mode)
    if pre["blockers"]:
        raise ApiError("bad_request", "；".join(pre["blockers"]))
    names = "、".join(p["title"][:18] for p in pre["papers"][:3]) + ("…" if len(ids) > 3 else "")
    cid = new_id()
    now = utcnow()
    with app.db.session() as conn:
        conn.execute("INSERT INTO comparisons(id, title, mode, paper_ids_json, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                     (cid, (title or f"{MODE_LABELS[mode]}：{names}")[:200], mode, dumps(ids), "pending", now, now))
    job_id = app.jobs.submit("paper_compare", f"{MODE_LABELS[mode]}（{len(ids)} 篇）", {"comparison_id": cid}, None)
    with app.db.session() as conn:
        conn.execute("UPDATE comparisons SET job_id = ? WHERE id = ?", (job_id, cid))
    return {"id": cid, "job_id": job_id}


def _set(app: AppContext, cid: str, status: str, **fields) -> None:
    cols = ["status = ?", "updated_at = ?"] + [f"{k} = ?" for k in fields]
    with app.db.session() as conn:
        conn.execute(f"UPDATE comparisons SET {', '.join(cols)} WHERE id = ?", (status, utcnow(), *fields.values(), cid))


def handle_compare(ctx: JobContext) -> dict:
    app, cid = ctx.app, ctx.payload["comparison_id"]
    row = get_comparison(app, cid)
    _set(app, cid, "running", error=None)
    try:
        result = ctx.run_async(run_comparison(app, row["paper_ids"], row["mode"], job=ctx))
    except JobCancelled:
        _set(app, cid, "cancelled")
        raise
    except (ApiError, LLMError) as exc:
        msg = exc.message
        _set(app, cid, "failed", error=msg)
        raise ApiError(getattr(exc, "code", "provider_error"), msg) from exc
    except Exception as exc:  # noqa: BLE001
        _set(app, cid, "failed", error=f"{type(exc).__name__}: {exc}"[:300])
        raise
    _set(app, cid, "ready", result_json=dumps(result), error=None)
    return {"comparison_id": cid}


def register_jobs(manager: JobManager) -> None:
    manager.register("paper_compare", handle_compare)
