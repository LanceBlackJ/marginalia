"""论文整体分析（后台任务）：按方面检索全文 → 强模型综合 → 解析 JSON → 证据核实。"""
from __future__ import annotations

import json
import re

from ..context import AppContext
from ..jobs import JobCancelled, JobContext, JobManager
from ..util import ApiError, dumps, loads, utcnow
from . import evidence, prompts, retrieval
from .providers import LLMError
from .router import TaskType, choose_model
from .service import AIService
from .settings import context_scale, is_configured, load_settings

ASPECTS_EN = {
    "background": ["background", "introduction", "problem", "important", "recent", "widely"],
    "motivation": ["limitation", "drawback", "existing", "however", "fail", "motivation", "lack"],
    "challenges": ["challenge", "difficult", "problem", "issue", "hard"],
    "method": ["propose", "method", "approach", "framework", "architecture", "module", "algorithm", "novel"],
    "experiment": ["experiment", "dataset", "baseline", "implementation", "metric", "evaluate", "setup"],
    "results": ["results", "outperform", "improve", "achieve", "accuracy", "performance", "table"],
    "conclusion": ["conclusion", "future", "limitation", "limitations", "summarize", "presented"],
}
ASPECTS_ZH = "背景 问题 局限 不足 挑战 提出 方法 模块 框架 实验 数据集 结果 提升 结论 展望"
TOTAL_BUDGET = 28000


def _upsert_analysis(app: AppContext, paper_id: str, status: str, **fields) -> None:
    """原子 upsert：入队线程与任务线程可能同时写同一行，不能先查后插。"""
    now = utcnow()
    cols = ["paper_id", "status", "updated_at", *fields]
    vals = [paper_id, status, now, *fields.values()]
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols[1:])
    with app.db.session() as conn:
        conn.execute(
            f"INSERT INTO paper_analysis({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
            f"ON CONFLICT(paper_id) DO UPDATE SET {updates}",
            vals,
        )


def enqueue_analysis(app: AppContext, paper_id: str) -> str:
    if not is_configured(app):
        raise ApiError("not_configured", "尚未配置 AI 模型与 API Key，请先在设置 → AI 模型中完成配置。")
    with app.db.session() as conn:
        paper = conn.execute(
            "SELECT id, title, (SELECT COUNT(*) FROM passages WHERE paper_id = papers.id) AS n FROM papers WHERE id = ?",
            (paper_id,),
        ).fetchone()
        running = conn.execute(
            "SELECT job_id FROM paper_analysis WHERE paper_id = ? AND status IN ('pending','running')", (paper_id,)
        ).fetchone()
    if paper is None:
        raise ApiError("not_found", "论文不存在", 404)
    if not paper["n"]:
        raise ApiError("bad_request", "这篇论文没有可分析的文字内容。")
    if running and running["job_id"]:
        return running["job_id"]
    # 先写 pending 再提交任务，避免任务线程先把状态改成 running 又被覆盖回 pending
    _upsert_analysis(app, paper_id, "pending", job_id=None, error=None)
    job_id = app.jobs.submit("paper_analysis", f"论文分析：{paper['title']}", {"paper_id": paper_id}, paper_id)
    with app.db.session() as conn:
        conn.execute("UPDATE paper_analysis SET job_id = ? WHERE paper_id = ?", (job_id, paper_id))
    return job_id


def get_analysis(app: AppContext, paper_id: str) -> dict:
    with app.db.session() as conn:
        if conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
            raise ApiError("not_found", "论文不存在", 404)
        row = conn.execute("SELECT * FROM paper_analysis WHERE paper_id = ?", (paper_id,)).fetchone()
    if row is None:
        return {"status": "none", "job_id": None, "model": None, "updated_at": None, "error": None,
                "content": None, "evidence": [], "verification": None}
    ready = row["status"] == "ready"
    return {
        "status": row["status"], "job_id": row["job_id"], "model": row["model"], "updated_at": row["updated_at"],
        "error": row["error"], "content": loads(row["content_json"]) if ready else None,
        "evidence": loads(row["evidence_json"], []) if ready else [],
        "verification": loads(row["verification_json"]) if ready else None,
    }


def _merge_packs(packs: list[retrieval.ContextPack], budget: int) -> retrieval.ContextPack:
    merged: dict[int, retrieval.CtxPassage] = {}
    for pk in packs:
        for p in pk.passages:
            if p.ord in merged:
                merged[p.ord].priority = max(merged[p.ord].priority, p.priority)
            else:
                merged[p.ord] = p
    chosen, used = [], 0
    for p in sorted(merged.values(), key=lambda x: -x.priority):
        if used + len(p.text) + 40 > budget and chosen:
            continue
        chosen.append(p)
        used += len(p.text) + 40
    chosen.sort(key=lambda p: p.ord)
    base = packs[0]
    return retrieval.ContextPack(base.paper_title, base.paper_year, base.paper_authors, base.outline, chosen, None, [], [])


def _field_ok(field: str, item) -> bool:
    """字段内容是否真正是针对论文的分析：不能为空、过短，或照抄了字段说明。"""
    if not isinstance(item, dict):
        return False
    text = evidence.normalize_cites(str(item.get("text", ""))).strip()
    plain = re.sub(r"\[P\d+\]", "", text).strip()
    if evidence.INSUFFICIENT_RE.search(plain):
        return True  # 诚实地说“摘录里没有”是有效回答
    if len(plain) < 12:
        return False
    desc = prompts.ANALYSIS_DESCRIPTIONS[field]
    return not (plain.startswith(desc) or plain.startswith(desc.split("：")[0] + "：") and len(plain) < len(desc) + 12)


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no json object")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


async def _run(app: AppContext, ctx: JobContext, paper_id: str) -> None:
    service = AIService(app)
    try:
        provider, cfg = service.provider()
    except LLMError as err:
        raise ApiError(err.code, err.message) from err
    with app.db.session() as conn:
        paper = conn.execute("SELECT title, language FROM papers WHERE id = ?", (paper_id,)).fetchone()
    ctx.progress(0.1, "按主题检索全文")
    ctx.log("按背景/动机/方法/实验/结果/结论分别检索相关段落")
    packs = []
    for aspect, terms in ASPECTS_EN.items():
        ctx.check_cancelled()
        task = {"method": TaskType.METHOD, "experiment": TaskType.EXPERIMENT, "results": TaskType.EXPERIMENT,
                "motivation": TaskType.MOTIVATION, "background": TaskType.MOTIVATION}.get(aspect, TaskType.ANALYSIS)
        packs.append(retrieval.build_context(
            app, paper_id, question=ASPECTS_ZH if paper["language"] == "zh" else "", selection=None, task=task,
            budget_chars=5000, force_terms=terms,
        ))
    pack = _merge_packs(packs, int(TOTAL_BUDGET * context_scale(cfg)))
    ctx.log(f"装配上下文：{len(pack.passages)} 段，约 {pack.chars_sent:,} 字")
    if not pack.passages:
        raise ApiError("bad_request", "没有检索到可用于分析的正文内容。")

    choice = choose_model(TaskType.ANALYSIS, {**cfg, "mode": "strong" if cfg["mode"] != "fast" else "fast"})
    messages = prompts.build_analysis_messages(pack)
    from . import usage as usage_log

    usage_log.record_request(
        app, task_type=TaskType.ANALYSIS.value, model=choice.model, paper_id=paper_id, passage_ords=pack.ords,
        chars_sent=pack.chars_sent,
        includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": False},
        summary=f"发送了 {len(pack.passages)} 段原文（约 {pack.chars_sent:,} 字）及章节大纲，用于整体分析",
    )
    ctx.progress(0.3, f"调用模型 {choice.model} 生成分析")
    data: dict | None = None
    used_model = choice.model
    bad: list[str] = list(prompts.ANALYSIS_FIELDS)
    for attempt in range(2):
        ctx.check_cancelled()
        res = await provider.complete(choice.model, messages, temperature=0.1, max_tokens=cfg["max_output_tokens"],
                                      json_mode=True)
        used_model = res.model  # 强模型暂时不可用时，实际回答的是备用（快速）模型
        service._log_usage(TaskType.ANALYSIS, used_model, res.usage, messages, res.text, paper_id, ctx.job_id)
        try:
            data = _extract_json(res.text)
        except ValueError:
            ctx.log("模型输出不是合法 JSON，要求其重新输出")
            messages = [*messages, {"role": "assistant", "content": res.text[:4000]},
                        {"role": "user", "content": "上一次输出不是合法的 JSON。请只输出一个 JSON 对象，不要任何其它文字。"}]
            continue
        bad = [f for f in prompts.ANALYSIS_FIELDS if not _field_ok(f, data.get(f))]
        if not bad:
            break
        ctx.log(f"以下字段没有给出有效内容（照抄了字段说明或过短）：{', '.join(bad)}，要求其重写")
        messages = [*messages, {"role": "assistant", "content": res.text[:6000]},
                    {"role": "user", "content": "这些字段没有写出针对这篇论文的具体分析：" + "、".join(bad)
                     + "。请重新输出完整的 JSON，每个字段都要写 2~5 句具体的中文分析，不能复述字段说明。"}]
    if data is None:
        raise ApiError("provider_error", "模型多次返回了无法解析的分析结果，请重试或更换模型。")

    ctx.progress(0.85, "核实引用")
    content: dict = {}
    texts: list[str] = []
    listed_all: list[int] = []
    valid = {p.ord for p in pack.passages}
    for f in prompts.ANALYSIS_FIELDS:
        item = data.get(f)
        if f in bad or not isinstance(item, dict):
            content[f] = {"text": "模型没有为该项给出有效内容，可以点击“重新生成”再试一次。", "evidence_ords": []}
            continue
        text = evidence.normalize_cites(str(item.get("text", "")).strip())
        listed = [o for o in (item.get("evidence_ords") or []) if isinstance(o, int)]
        ords = [o for o in dict.fromkeys([*evidence.cited_ords(text), *listed]) if o in valid]
        content[f] = {"text": text, "evidence_ords": ords}
        texts.append(text)
        listed_all.extend(listed)
    ev, verification = evidence.verify_ords(texts, pack, listed_all)
    for fb in provider.fallback_events:  # 如实告知：这次分析不是强模型生成的
        if fb["message"] not in verification["notes"]:
            verification["notes"] = [*verification["notes"], fb["message"]]
            ctx.log(fb["message"])
    if bad:
        verification["notes"] = [*verification["notes"], f"有 {len(bad)} 项模型没有给出有效内容。"]
        if verification["status"] == "verified":
            verification["status"] = "partial"
    _upsert_analysis(
        app, paper_id, "ready", model=used_model, content_json=dumps(content), evidence_json=dumps(ev),
        verification_json=dumps(verification), error=None,
    )
    ctx.log("分析完成")


def handle_paper_analysis(ctx: JobContext) -> dict:
    app = ctx.app
    paper_id = ctx.payload["paper_id"]
    _upsert_analysis(app, paper_id, "running", job_id=ctx.job_id, error=None)
    try:
        ctx.run_async(_run(app, ctx, paper_id))
    except BaseException as exc:
        if isinstance(exc, JobCancelled):
            _upsert_analysis(app, paper_id, "none", error=None)
        else:
            msg = exc.message if isinstance(exc, (ApiError, LLMError)) else f"{type(exc).__name__}: {exc}"
            _upsert_analysis(app, paper_id, "failed", error=msg[:400])
            if isinstance(exc, LLMError):
                raise ApiError(exc.code, exc.message) from exc
        raise
    return {"paper_id": paper_id}


def register(manager: JobManager) -> None:
    manager.register("paper_analysis", handle_paper_analysis)
