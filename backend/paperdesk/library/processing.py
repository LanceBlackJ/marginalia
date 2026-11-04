"""论文导入后的自动处理进度：解析、整体分析、公式识别、表格还原、语义索引。

界面上说“已完成/已分析”之前必须看这里：公式和表格还没处理好的时候，论文不能被说成“分析好了”。
每一步给出状态 done / running / pending / failed / skipped（skipped 要写明原因，例如“未配置视觉模型，公式仍是截图”），
总状态 overall：
  processing  有步骤正在后台运行
  waiting     有步骤还没开始（比如还没配置自动处理，或打开论文才会触发）
  attention   全部跑完了，但有失败、或因为没配置而跳过的步骤，需要用户留意
  done        所有步骤都做完了
列表接口用只查计数的轻量版本（不做表格检测），单篇详情接口再做完整检测。
"""
from __future__ import annotations

from ..ai import embeddings, glossary
from ..ai import settings as ai_settings
from ..context import AppContext
from ..util import ApiError

STEP_ORDER = ("analysis", "formulas", "tables", "index", "glossary")
STEP_NAME = {"analysis": "整体分析", "formulas": "公式", "tables": "表格", "index": "语义索引", "glossary": "术语表"}


def _placeholders(ids: list[str]) -> str:
    return ",".join("?" * len(ids))


def facts_for(app: AppContext, ids: list[str]) -> dict[str, dict]:
    """一次查完这些论文要用的全部计数（每类一条 SQL，不是每篇论文各查一遍）：后台任务、公式、表格、语义索引。"""
    facts = {i: {"jobs": set(), "eq_total": 0, "eq_ok": 0, "eq_bad": 0, "tb_ready": 0, "tb_failed": 0, "tb_running": 0, "ix_total": 0, "ix_done": 0,
                 "gl_total": 0, "gl_done": 0, "gl_failed": False}
             for i in ids}
    if not ids:
        return facts
    ph = _placeholders(ids)
    kinds = ",".join("?" * len(embeddings.EMBED_KINDS))
    model = ai_settings.load_settings(app).get("embedding_model") or ""
    with app.db.session() as conn:
        for r in conn.execute(f"SELECT DISTINCT paper_id, type FROM jobs WHERE paper_id IN ({ph}) AND status IN ('pending','running')", ids):
            facts[r["paper_id"]]["jobs"].add(r["type"])
        for r in conn.execute(f"SELECT paper_id, COUNT(*) AS n FROM equations WHERE paper_id IN ({ph}) GROUP BY paper_id", ids):
            facts[r["paper_id"]]["eq_total"] = r["n"]
        for r in conn.execute(
            f"SELECT paper_id, SUM(CASE WHEN status = 'ok' AND latex IS NOT NULL THEN 1 ELSE 0 END) AS ok, "
            f"SUM(CASE WHEN status = 'ok' AND latex IS NOT NULL THEN 0 ELSE 1 END) AS bad FROM equation_latex WHERE paper_id IN ({ph}) GROUP BY paper_id", ids):
            facts[r["paper_id"]]["eq_ok"], facts[r["paper_id"]]["eq_bad"] = r["ok"] or 0, r["bad"] or 0
        for r in conn.execute(f"SELECT paper_id, status, COUNT(*) AS n FROM table_analysis WHERE paper_id IN ({ph}) GROUP BY paper_id, status", ids):
            f = facts[r["paper_id"]]
            if r["status"] == "ready":
                f["tb_ready"] = r["n"]
            elif r["status"] == "failed":
                f["tb_failed"] = r["n"]
            else:
                f["tb_running"] += r["n"]
        for r in conn.execute(
            f"SELECT paper_id, COUNT(*) AS n FROM passages WHERE paper_id IN ({ph}) AND length(trim(text)) >= {embeddings.MIN_CHARS} "
            f"AND kind IN ({kinds}) GROUP BY paper_id", [*ids, *embeddings.EMBED_KINDS]):
            facts[r["paper_id"]]["ix_total"] = r["n"]
        for r in conn.execute(
            f"SELECT paper_id, COUNT(*) AS n FROM passages WHERE paper_id IN ({ph}) AND kind = 'paragraph' AND length(trim(text)) >= {glossary.MIN_CHARS} GROUP BY paper_id", ids):
            facts[r["paper_id"]]["gl_total"] = 1 if r["n"] >= glossary.MIN_PARAS else 0  # 正文够多才做术语表
        for r in conn.execute(f"SELECT paper_id FROM paper_glossary WHERE paper_id IN ({ph})", ids):
            facts[r["paper_id"]]["gl_done"] = 1
        for r in conn.execute(f"SELECT paper_id, status FROM jobs WHERE type = ? AND paper_id IN ({ph}) ORDER BY created_at", [glossary.JOB_TYPE, *ids]):
            facts[r["paper_id"]]["gl_failed"] = r["status"] == "failed"
        if model:
            for r in conn.execute(f"SELECT paper_id, COUNT(*) AS n FROM passage_embeddings WHERE paper_id IN ({ph}) AND model = ? GROUP BY paper_id", [*ids, model]):
                facts[r["paper_id"]]["ix_done"] = r["n"]
    return facts


def _vision_ready(cfg: dict) -> bool:
    return bool(cfg.get("has_api_key") and cfg.get("vision_model"))


def _step(state: str, done: int = 0, total: int = 0, reason: str = "", attention: bool = False) -> dict:
    return {"state": state, "done": done, "total": total, "reason": reason, "attention": attention}


def _formulas(f: dict, cfg: dict) -> dict:
    total, ok, bad = f["eq_total"], f["eq_ok"], f["eq_bad"]
    if total == 0:
        return _step("done")
    if "equations_latex" in f["jobs"]:
        return _step("running", ok, total)
    if ok + bad >= total:
        return _step("failed", ok, total, f"{bad} 个公式没能识别，仍显示为截图", True) if bad else _step("done", ok, total)
    if not _vision_ready(cfg):
        return _step("skipped", ok, total, "未配置视觉模型，公式仍是截图", True)
    return _step("pending", ok, total)


def _tables(app: AppContext, paper_id: str, f: dict, cfg: dict, detailed: bool) -> dict:
    if detailed:
        from ..vision import table_batch

        st = table_batch.status(app, paper_id)
        total, ready, failed, running = st["total"], st["ready"], st["failed"], st["running"]
    else:
        ready, failed, running = f["tb_ready"], f["tb_failed"], f["tb_running"]
        total = ready + failed + running
    if total == 0:
        return _step("done")
    if "tables_analysis" in f["jobs"] or running:
        return _step("running", ready, total)
    if ready + failed >= total:
        return _step("failed", ready, total, f"{failed} 张表没能还原，仍显示为行文字", True) if failed else _step("done", ready, total)
    if not _vision_ready(cfg):
        return _step("skipped", ready, total, "未配置视觉模型，部分表格没能还原", True)
    return _step("pending", ready, total)


def _analysis(status: str, cfg: dict, jobs: set) -> dict:
    if status == "ready":
        return _step("done")
    if status in ("running", "pending") or "paper_analysis" in jobs:
        return _step("running")
    if status == "failed":
        return _step("failed", reason="整体分析失败，可在概览里重试", attention=True)
    if not cfg.get("configured"):
        return _step("skipped", reason="未配置 API Key 或模型，没有做整体分析", attention=True)
    if not cfg.get("auto_analyze"):
        return _step("skipped", reason="没有开启自动分析，可在概览里手动生成")
    return _step("pending")


def _index(f: dict, cfg: dict) -> dict:
    if not (cfg.get("configured") and cfg.get("semantic_retrieval") and cfg.get("embedding_model")):
        return _step("skipped", reason="没有配置嵌入模型，问答只用关键词检索")
    total, done = f["ix_total"], f["ix_done"]
    if total == 0 or done >= total:
        return _step("done", done, total)
    if "embed_paper" in f["jobs"]:
        return _step("running", done, total)
    return _step("pending", done, total)


def _glossary(f: dict, cfg: dict) -> dict:
    total, done = f["gl_total"], min(f["gl_done"], f["gl_total"])
    if total == 0 or done >= total:
        return _step("done", done, total)
    if glossary.JOB_TYPE in f["jobs"]:
        return _step("running", done, total)
    if not cfg.get("configured"):
        return _step("skipped", done, total, "未配置 API Key 或模型，没有生成术语表", True)
    if f["gl_failed"]:
        return _step("failed", done, total, "术语表没生成成功（模型出错），打开术语标签会重试", True)
    return _step("pending", done, total)


def _overall(steps: dict[str, dict]) -> str:
    states = [s["state"] for s in steps.values()]
    if "running" in states:
        return "processing"
    if "pending" in states:
        return "waiting"
    if any(s["attention"] for s in steps.values()):
        return "attention"
    return "done"


def _label(overall: str, steps: dict[str, dict]) -> str:
    def part(k: str) -> str:
        s = steps[k]
        return f"{STEP_NAME[k]} {s['done']}/{s['total']}" if s["total"] else STEP_NAME[k]

    if overall == "done":
        return "已完成"
    if overall == "processing":
        return "处理中（" + "、".join(part(k) for k in STEP_ORDER if steps[k]["state"] == "running") + "）"
    if overall == "waiting":
        return "待处理（" + "、".join(part(k) for k in STEP_ORDER if steps[k]["state"] == "pending") + "）"
    return "需要注意"


def _build(app: AppContext, paper_id: str, f: dict, cfg: dict, analysis_status: str, detailed: bool) -> dict:
    steps = {
        "analysis": _analysis(analysis_status, cfg, f["jobs"]),
        "formulas": _formulas(f, cfg),
        "tables": _tables(app, paper_id, f, cfg, detailed),
        "index": _index(f, cfg),
        "glossary": _glossary(f, cfg),
    }
    overall = _overall(steps)
    reasons = [f"{STEP_NAME[k]}：{s['reason']}" for k, s in steps.items() if s["reason"] and s["attention"]]
    label = _label(overall, steps)
    reparsing = "reparse_paper" in f["jobs"]
    if reparsing:  # 重新解析期间：段落、公式、表格都在被替换，不能说“已完成”
        overall, label = "processing", "重新解析中"
    return {"overall": overall, "label": label, "steps": steps, "reasons": reasons, "reparsing": reparsing}


def summary(app: AppContext, paper_id: str, *, analysis_status: str | None = None, detailed: bool = False) -> dict:
    cfg = ai_settings.public_settings(app)
    if analysis_status is None:
        with app.db.session() as conn:
            row = conn.execute("SELECT status FROM paper_analysis WHERE paper_id = ?", (paper_id,)).fetchone()
        analysis_status = row["status"] if row else "none"
    return _build(app, paper_id, facts_for(app, [paper_id])[paper_id], cfg, analysis_status, detailed)


def attach_summaries(app: AppContext, items: list[dict]) -> list[dict]:
    """给文库列表里的每篇论文加上 `processing`（轻量：设置读一次、计数每类一条 SQL，不做表格检测）。"""
    ids = [it["id"] for it in items]
    cfg = ai_settings.public_settings(app)
    facts = facts_for(app, ids)
    for it in items:
        it["processing"] = _build(app, it["id"], facts[it["id"]], cfg, it.get("analysis_status") or "none", False)
    return items


def start_auto(app: AppContext, paper_id: str) -> dict:
    """导入后/打开论文时：启动还没做的自动步骤（公式识别、表格还原、语义索引）。幂等；没配置的步骤不启动，状态里如实写明。"""
    cfg = ai_settings.public_settings(app)
    started: dict[str, str | None] = {}
    if _vision_ready(cfg):
        from ..vision import formula_batch, table_batch

        for name, fn in (("formulas", lambda: formula_batch.enqueue(app, paper_id)), ("tables", lambda: table_batch.start(app, paper_id))):
            try:
                started[name] = fn()
            except ApiError:
                started[name] = None
    if cfg.get("configured"):
        try:
            started["index"] = embeddings.enqueue_embedding(app, paper_id)
        except ApiError:
            started["index"] = None
        try:  # 术语表：读论文前先扫清概念障碍，导入后一次做好
            started["glossary"] = glossary.enqueue(app, paper_id)
        except ApiError:
            started["glossary"] = None
    return started
