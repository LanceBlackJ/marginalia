"""用量统计与“发送给云端的内容”记录（隐私可审计）。"""
from __future__ import annotations

from ..context import AppContext
from ..util import dumps, loads, utcnow
from .settings import load_settings


def record_usage(
    app: AppContext, task_type: str, model: str, prompt_tokens: int, completion_tokens: int, *,
    estimated: bool = False, paper_id: str | None = None, job_id: str | None = None,
) -> None:
    now = utcnow()
    with app.db.session() as conn:
        conn.execute(
            "INSERT INTO ai_usage(ts, month, task_type, model, prompt_tokens, completion_tokens, estimated, paper_id, job_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (now, now[:7], task_type, model, prompt_tokens, completion_tokens, int(estimated), paper_id, job_id),
        )


def record_request(
    app: AppContext, *, task_type: str, model: str, paper_id: str | None, passage_ords: list[int],
    chars_sent: int, includes: dict, summary: str,
) -> None:
    with app.db.session() as conn:
        conn.execute(
            "INSERT INTO ai_requests(ts, task_type, model, paper_id, passage_ords_json, passage_count, chars_sent, "
            "includes_json, summary) VALUES (?,?,?,?,?,?,?,?,?)",
            (utcnow(), task_type, model, paper_id, dumps(passage_ords), len(passage_ords), chars_sent,
             dumps(includes), summary),
        )


def _cost(prices: dict, model: str, pt: int, ct: int) -> float | None:
    p = prices.get(model)
    if not p:
        return None
    return (pt * p.get("input", 0.0) + ct * p.get("output", 0.0)) / 1_000_000


def month_summary(app: AppContext, month: str | None = None) -> dict:
    month = month or utcnow()[:7]
    prices = load_settings(app).get("price_per_mtok") or {}
    budget = load_settings(app).get("monthly_budget")
    with app.db.session() as conn:
        by_model = conn.execute(
            "SELECT model, COUNT(*) AS calls, SUM(prompt_tokens) AS pt, SUM(completion_tokens) AS ct "
            "FROM ai_usage WHERE month = ? GROUP BY model ORDER BY calls DESC", (month,),
        ).fetchall()
        by_task = conn.execute(
            "SELECT task_type, COUNT(*) AS calls, SUM(prompt_tokens) AS pt, SUM(completion_tokens) AS ct "
            "FROM ai_usage WHERE month = ? GROUP BY task_type ORDER BY calls DESC", (month,),
        ).fetchall()
    models = []
    total_cost: float | None = None
    for r in by_model:
        c = _cost(prices, r["model"], r["pt"] or 0, r["ct"] or 0)
        if c is not None:
            total_cost = (total_cost or 0.0) + c
        models.append({"model": r["model"], "calls": r["calls"], "prompt_tokens": r["pt"] or 0,
                       "completion_tokens": r["ct"] or 0, "estimated_cost": c})
    return {
        "month": month,
        "calls": sum(m["calls"] for m in models),
        "prompt_tokens": sum(m["prompt_tokens"] for m in models),
        "completion_tokens": sum(m["completion_tokens"] for m in models),
        "estimated_cost": total_cost,
        "by_model": models,
        "by_task_type": [
            {"task_type": r["task_type"], "calls": r["calls"], "prompt_tokens": r["pt"] or 0,
             "completion_tokens": r["ct"] or 0} for r in by_task
        ],
        "budget": budget,
        "over_budget": bool(budget is not None and total_cost is not None and total_cost > budget),
    }


def recent_requests(app: AppContext, limit: int = 50, before_id: int | None = None, task_type: str | None = None) -> dict:
    """发送记录（新的在前），按游标分页：`before_id` 只取 id 更小的；`task_type` 按任务类型筛选。

    返回 {items, has_more, total, task_types}：`total` 是符合筛选的总条数，`task_types` 是各任务类型的条数（供筛选下拉）。
    """
    where, params = [], []
    if task_type:
        where.append("r.task_type = ?")
        params.append(task_type)
    scope = (" WHERE " + " AND ".join(where)) if where else ""
    cursor = params + ([before_id] if before_id is not None else [])
    cursor_sql = (scope + (" AND " if where else " WHERE ") + "r.id < ?") if before_id is not None else scope
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT r.*, p.title AS paper_title FROM ai_requests r LEFT JOIN papers p ON p.id = r.paper_id"
            f"{cursor_sql} ORDER BY r.id DESC LIMIT ?", (*cursor, limit + 1),
        ).fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM ai_requests r{scope}", params).fetchone()[0]
        types = conn.execute("SELECT task_type, COUNT(*) AS n FROM ai_requests GROUP BY task_type ORDER BY n DESC").fetchall()
    has_more = len(rows) > limit
    return {
        "items": [
            {
                "id": r["id"], "ts": r["ts"], "task_type": r["task_type"], "model": r["model"],
                "paper_id": r["paper_id"], "paper_title": r["paper_title"], "passage_count": r["passage_count"],
                "chars_sent": r["chars_sent"], "includes": loads(r["includes_json"], {}), "summary": r["summary"],
            }
            for r in rows[:limit]
        ],
        "has_more": has_more,
        "total": total,
        "task_types": [{"task_type": t["task_type"], "count": t["n"]} for t in types],
    }