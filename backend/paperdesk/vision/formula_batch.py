"""整篇论文的公式批量识别：打开文字阅读器时在后台把所有公式转成 LaTeX，供界面直接渲染。

逐个调用 `formula.transcribe_equation`（每个公式最多发 3 张截图给视觉模型，结果缓存；发送内容进隐私日志、用量计入统计）。
单个公式失败只计入失败数，不影响其它公式；密钥无效这类整体性错误才中止任务。
"""
from __future__ import annotations

from ..context import AppContext
from ..jobs import JobContext, JobManager
from ..ai.providers import LLMError
from ..util import ApiError
from . import formula

JOB_TYPE = "equations_latex"


def _equation_ids(app: AppContext, paper_id: str) -> list[int]:
    with app.db.session() as conn:
        return [r["id"] for r in conn.execute("SELECT id FROM equations WHERE paper_id = ? ORDER BY page, id", (paper_id,))]


def status(app: AppContext, paper_id: str) -> dict:
    """{total, done, failed, items:[{equation_id, latex, confidence, status, warnings}]}（只读缓存，不调用模型）。"""
    ids = _equation_ids(app, paper_id)
    items, failed = [], 0
    for eid in ids:
        hit = formula.get_cached(app, eid)
        if hit is None:
            continue
        if hit["status"] == "ok" and hit["latex"]:
            items.append({"equation_id": eid, "latex": hit["latex"], "confidence": hit["confidence"], "status": "ok",
                          "warnings": hit["warnings"]})
        else:
            failed += 1
    return {"total": len(ids), "done": len(items), "failed": failed, "complete": bool(ids) and len(items) + failed >= len(ids),
            "items": items}


def enqueue(app: AppContext, paper_id: str) -> str | None:
    """还有没识别的公式时创建后台任务；没有公式或都识别过了返回 None。"""
    st = status(app, paper_id)
    if not st["total"] or st["complete"]:
        return None
    with app.db.session() as conn:
        title = conn.execute("SELECT title FROM papers WHERE id = ?", (paper_id,)).fetchone()["title"]
    return app.jobs.submit(JOB_TYPE, f"识别公式：{title[:40]}", {"paper_id": paper_id}, paper_id)


async def _run(app: AppContext, ctx: JobContext, paper_id: str) -> dict:
    todo = [eid for eid in _equation_ids(app, paper_id) if formula.get_cached(app, eid) is None]
    done = failed = 0
    for i, eid in enumerate(todo, 1):
        ctx.check_cancelled()
        try:
            res = await formula.transcribe_equation(app, paper_id, eid)
            if res.get("status") == "ok" and res.get("latex"):
                done += 1
            else:
                failed += 1
        except LLMError as err:
            if err.code in ("auth_failed", "no_api_key", "not_configured"):
                raise ApiError(err.code, err.message) from err
            failed += 1
        ctx.progress(i / len(todo), f"已识别 {done}/{len(todo)} 个公式" + (f"，{failed} 个失败" if failed else ""))
    return {"recognized": done, "failed": failed}


def handle(ctx: JobContext) -> dict:
    return ctx.run_async(_run(ctx.app, ctx, ctx.payload["paper_id"]))


def register(manager: JobManager) -> None:
    manager.register(JOB_TYPE, handle)
