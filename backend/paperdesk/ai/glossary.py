"""导入后的“术语表”：读这篇论文之前该先弄懂的概念，导入时在后台一次做好（一次模型调用），读的时候直接看。

每个词条：论文里的写法、中文名、用白话说它是什么、论文里定义它的那句原话（后端核对：必须真在那一段里，词也必须真出现在那一段里）。
论文默认读者已经懂、没有定义的“前置知识”也收，但标明解释来自模型自己的知识、不是论文内容；核对不上原文的词条一律按“前置知识”处理，不假装有出处。
"""
from __future__ import annotations

import json
import re

from ..context import AppContext
from ..db import register_schema
from ..jobs import JobContext, JobManager
from ..llmjson import ask_json
from ..util import ApiError, utcnow
from . import passage_insight as pi
from .providers import LLMError

register_schema("paper_glossary", """
CREATE TABLE IF NOT EXISTS paper_glossary (
  paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
""")

JOB_TYPE = "paper_glossary"
MIN_CHARS = 60
MIN_PARAS = 5  # 正文段落少于这个数的“论文”不做术语表
BUDGET = 30000
MAX_TERMS = 30
KINDS = ("术语", "缩写", "符号", "方法名", "前置知识")
FOCUS_RE = re.compile(r"abstract|introduction|background|preliminar|method|conclusion|摘要|引言|绪论|背景|预备|方法|结论", re.I)

SYSTEM = """你是帮读者在读论文之前先扫清概念障碍的导师。读者是研究生，读这篇论文时最怕：遇到不懂的术语、缩写、符号，或者论文默认读者已经懂的前置知识。
请根据给出的论文原文（每段以 [P编号] 开头），列出读者读这篇之前（或读到时）最需要弄懂的 15 到 30 个概念。只输出 JSON：
{"terms": [
  {"term": "论文里的写法（英文原词、缩写或符号，原样照抄，如 residual connection、BLEU、d_model）",
   "zh": "中文名（没有通用译名就留空）",
   "kind": "从这些里选一个：""" + "、".join(KINDS) + """",
   "plain": "用白话解释它是什么（不超过 90 字；要让没学过的人也能懂，不要用另一个没解释的术语来解释它）",
   "why": "在这篇论文里它起什么作用（不超过 50 字）",
   "in_paper": true,
   "ord": 12,
   "quote": "论文里定义它或第一次解释它的那句话，必须逐字照抄（不要改写、不要翻译），来自 P12 这一段"}]}
要求：
- 按读者该先懂的顺序排：基础概念在前，依赖它们的在后。只收读者可能不懂的，不要收“神经网络”“数据集”这类人人都懂的词。
- in_paper=true 表示论文自己定义或解释了它：必须给出 ord 和 quote，term 要真出现在那一段里。
- in_paper=false 表示论文默认读者已懂、没有定义（前置知识，或论文只用不讲的概念）：ord 和 quote 留空，plain 写你自己的解释（界面会标明“AI 补充，不是论文内容”）。
- 不要编造论文里没有的内容；不确定的概念宁可不收。用中文解释，term 保持论文里的写法。"""


def _paragraphs(app: AppContext, paper_id: str) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT p.ord, p.text, s.title AS section_title FROM passages p LEFT JOIN sections s ON s.id = p.section_id "
            "WHERE p.paper_id = ? AND p.kind = 'paragraph' AND length(trim(p.text)) >= ? ORDER BY p.ord", (paper_id, MIN_CHARS),
        ).fetchall()
    return [dict(r) for r in rows]


def eligible(app: AppContext, paper_id: str) -> bool:
    return len(_paragraphs(app, paper_id)) >= MIN_PARAS


def _context(app: AppContext, paper_id: str, paras: list[dict]) -> tuple[str, dict[int, str]]:
    """整篇原文按预算压缩：引言/背景/方法这些定义概念的章节给得多，其余章节每段只取开头。"""
    with app.db.session() as conn:
        paper = conn.execute("SELECT title, abstract FROM papers WHERE id = ?", (paper_id,)).fetchone()
    weights = [2.0 if FOCUS_RE.search(p["section_title"] or "") else 1.0 for p in paras]
    base = BUDGET / max(1.0, sum(weights))
    parts = [f"【论文】{paper['title']}"]
    if paper["abstract"]:
        parts.append(f"【摘要】{pi._clip(paper['abstract'], 1500)}")
    lines: list[str] = []
    texts: dict[int, str] = {}
    last = None
    for p, w in zip(paras, weights):
        if p["section_title"] != last:
            lines.append(f"## {p['section_title'] or '正文'}")
            last = p["section_title"]
        lines.append(f"[P{p['ord']}] {pi._clip(p['text'], int(max(220, min(1800, base * w))))}")
        texts[p["ord"]] = p["text"]
    parts.append("【正文】\n" + "\n".join(lines))
    return "\n\n".join(parts), texts


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.lower())


def _clean(data: object, texts: dict[int, str]) -> dict:
    d = data if isinstance(data, dict) else {}
    out: list[dict] = []
    seen: set[str] = set()
    for t in d.get("terms") or []:
        if not isinstance(t, dict):
            continue
        term = " ".join(str(t.get("term") or "").split())
        plain = pi._clip(t.get("plain"), 130)
        key = _norm(term)
        if not term or len(term) > 60 or not plain or not key or key in seen:
            continue
        seen.add(key)
        ord_, quote = None, None
        raw_ord = t.get("ord")
        if t.get("in_paper") and isinstance(raw_ord, int) and raw_ord in texts and key in _norm(texts[raw_ord]):  # 词必须真出现在那一段里
            q = pi._verbatim(str(t.get("quote") or ""), texts[raw_ord])  # 原话必须真在那一段里
            if q:
                ord_, quote = raw_ord, q
        kind = str(t.get("kind") or "").strip()
        out.append({
            "term": term, "zh": pi._clip(t.get("zh"), 30), "kind": kind if kind in KINDS else "术语", "plain": plain,
            "why": pi._clip(t.get("why"), 70), "in_paper": ord_ is not None, "ord": ord_, "quote": quote,
        })
    return {"terms": out[:MAX_TERMS]}


def _row(app: AppContext, paper_id: str) -> dict | None:
    with app.db.session() as conn:
        r = conn.execute("SELECT result_json FROM paper_glossary WHERE paper_id = ?", (paper_id,)).fetchone()
    return json.loads(r["result_json"]) if r else None


def enqueue(app: AppContext, paper_id: str) -> str | None:
    """没做过且正文够多才提交；已经有术语表或已有任务在跑就不再提交。"""
    with app.db.session() as conn:
        paper = conn.execute("SELECT title FROM papers WHERE id = ?", (paper_id,)).fetchone()
        running = conn.execute("SELECT id FROM jobs WHERE paper_id = ? AND type = ? AND status IN ('pending','running')", (paper_id, JOB_TYPE)).fetchone()
    if paper is None:
        raise ApiError("not_found", "论文不存在", 404)
    if running:
        return running["id"]
    if _row(app, paper_id) is not None or not eligible(app, paper_id):
        return None
    return app.jobs.submit(JOB_TYPE, f"术语表：{(paper['title'] or '')[:40]}", {"paper_id": paper_id}, paper_id)


async def _run(app: AppContext, ctx: JobContext, paper_id: str) -> None:
    paras = _paragraphs(app, paper_id)
    user, texts = _context(app, paper_id, paras)
    ctx.progress(0.2, "整理全文")
    ctx.log(f"取 {len(paras)} 段正文（约 {len(user):,} 字）交给模型整理术语表")
    try:
        data = await ask_json(
            app, system=SYSTEM, user=user, task_type="glossary", tier="strong", max_tokens=4096, paper_id=paper_id, job_id=ctx.job_id,
            includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": False},
            summary=f"发送了整篇论文的正文（压缩后约 {len(user):,} 字）和摘要，用来整理术语表",
        )
    except LLMError as err:
        raise ApiError(err.code, err.message) from err
    ctx.progress(0.85, "核对原文出处")
    result = _clean(data, texts)
    if len(result["terms"]) < 3:
        raise ApiError("provider_error", "模型没有给出可用的术语表（词条太少），请重试。")
    with app.db.session() as conn:
        conn.execute("INSERT OR REPLACE INTO paper_glossary(paper_id, result_json, created_at) VALUES (?,?,?)", (paper_id, json.dumps(result, ensure_ascii=False), utcnow()))
    n_src = sum(1 for t in result["terms"] if t["in_paper"])
    ctx.log(f"完成：{len(result['terms'])} 个词条，其中 {n_src} 个核对到了论文里的原话出处")


def handle(ctx: JobContext) -> dict:
    paper_id = ctx.payload["paper_id"]
    ctx.run_async(_run(ctx.app, ctx, paper_id))
    return {"paper_id": paper_id}


def register(manager: JobManager) -> None:
    manager.register(JOB_TYPE, handle)


def get_glossary(app: AppContext, paper_id: str) -> dict:
    with app.db.session() as conn:
        if conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
            raise ApiError("not_found", "论文不存在", 404)
        running = conn.execute("SELECT 1 FROM jobs WHERE paper_id = ? AND type = ? AND status IN ('pending','running')", (paper_id, JOB_TYPE)).fetchone()
        last = conn.execute("SELECT status, error FROM jobs WHERE paper_id = ? AND type = ? ORDER BY created_at DESC LIMIT 1", (paper_id, JOB_TYPE)).fetchone()
    g = _row(app, paper_id)
    state = "done" if g else "running" if running else "unavailable" if not eligible(app, paper_id) else "failed" if last and last["status"] == "failed" else "pending"
    return {"terms": g["terms"] if g else [], "status": {"state": state, "error": last["error"] if state == "failed" and last else None}}
