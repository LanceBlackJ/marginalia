"""AI 辅助的知识整理：候选知识提取、主题建议、关系建议、画像建议。

AI 的产出一律先进入“候选/建议”状态，用户确认后才进入知识库或画像。证据始终从数据库真实段落重建。
"""
from __future__ import annotations

import re
from collections import defaultdict

from ..ai import analysis as analysis_mod
from ..ai import cards as cards_mod
from ..ai.providers import LLMError
from ..context import AppContext
from ..jobs import JobContext, JobManager
from ..llmjson import ask_json
from ..util import ApiError, dumps, loads, new_id, utcnow
from . import store

EXTRACT_SYSTEM = (
    "你是科研知识整理助手。下面是用户读一篇论文时产生的材料：论文整体分析（S 开头的来源编号）、用户保存的研究卡片、用户的笔记。\n"
    "请从中提炼 3-8 条“值得长期保存、可跨论文复用”的知识：方法的核心原理、关键结论、重要公式/符号的含义、已知局限。\n"
    "要求：只能依据给出的材料，不得加入材料之外的信息；每条知识必须说明来自哪些来源编号；"
    "不要把寒暄、过程性描述、没有信息量的句子当作知识；标题 30 字以内，内容 1-3 句且包含具体细节；"
    "如果材料里把某内容标为“推断”，要在内容里保留“（推断）”。\n"
    '只输出 JSON 对象：{"candidates": [{"title": "...", "content": "...", "kind": "method|finding|concept|formula|limitation|other", "from": ["S1","S3"]}]}'
)


def _sources(app: AppContext, paper_id: str) -> list[dict]:
    out: list[dict] = []
    a = analysis_mod.get_analysis(app, paper_id)
    labels = {"background": "研究背景", "motivation": "研究动机", "challenges": "核心挑战", "method": "方法设计", "modules": "模块关系",
              "experiment_design": "实验设计", "results": "实验结果", "conclusion": "研究结论", "limitations": "局限性"}
    if a["status"] == "ready" and a["content"]:
        for k, v in a["content"].items():
            if v.get("text") and "没有为该项给出有效内容" not in v["text"]:
                out.append({"text": f"论文分析·{labels.get(k, k)}：{v['text']}", "refs": [{"paper_id": paper_id, "ord": o} for o in v.get("evidence_ords", [])],
                            "origin_kind": "analysis", "origin_ref": None})
    for c in cards_mod.list_cards(app, paper_id, None, True):
        if c["status"] != "complete":
            continue
        text = f"研究卡片（问题：{c['question'] or c['object_label']}）：{c['answer']['direct']}"
        for inf in c["answer"].get("inferences", [])[:2]:
            text += f"\n（AI 推断）{inf['text']}"
        out.append({"text": text, "refs": [{"paper_id": paper_id, "ord": e["ord"]} for e in c["evidence"]],
                    "origin_kind": "card", "origin_ref": c["id"]})
    with app.db.session() as conn:
        notes = conn.execute("SELECT id, page, quote, passage_ord, text FROM notes WHERE paper_id = ? ORDER BY id", (paper_id,)).fetchall()
    for n in notes:
        if not (n["text"] or "").strip() and not (n["quote"] or "").strip():
            continue
        out.append({"text": f"用户笔记（第 {n['page'] or '?'} 页）：{(n['text'] or '').strip()}" + (f"\n摘录：{n['quote'][:300]}" if n["quote"] else ""),
                    "refs": [{"kind": "note", "note_id": n["id"]}], "origin_kind": "note", "origin_ref": str(n["id"])})
    for i, s in enumerate(out, 1):
        s["id"] = f"S{i}"
    return out


async def extract_candidates(app: AppContext, paper_id: str, job: JobContext | None = None) -> dict:
    with app.db.session() as conn:
        paper = conn.execute("SELECT title FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if paper is None:
        raise ApiError("not_found", "论文不存在", 404)
    sources = _sources(app, paper_id)
    if not sources:
        raise ApiError("bad_request", "这篇论文还没有可整理的材料。请先生成论文分析、保存研究卡片，或写几条笔记。")
    if job:
        job.progress(0.3, f"整理 {len(sources)} 份材料")
    user = f"论文：{paper['title']}\n\n" + "\n\n".join(f"[{s['id']}] {s['text'][:1200]}" for s in sources)
    model_notes: list[str] = []
    try:
        data = await ask_json(app, system=EXTRACT_SYSTEM, user=user, task_type="kb_extract", tier="strong", max_tokens=2500, notes=model_notes,
                              paper_id=paper_id, job_id=getattr(job, "job_id", None),
                              summary=f"发送了这篇论文的分析结果、{sum(1 for s in sources if s['origin_kind'] == 'card')} 张研究卡片和 "
                                      f"{sum(1 for s in sources if s['origin_kind'] == 'note')} 条笔记，用于提炼知识",
                              includes={"question": False, "selection": False, "notes": any(s["origin_kind"] == "note" for s in sources),
                                        "supplements": False, "code": False})
    except LLMError as err:
        raise ApiError(err.code, err.message) from err
    by_id = {s["id"]: s for s in sources}
    added = skipped = 0
    for c in (data.get("candidates") or []) if isinstance(data, dict) else []:
        if not isinstance(c, dict) or not str(c.get("title", "")).strip() or not str(c.get("content", "")).strip():
            continue
        refs: list[dict] = []
        origin = None
        for sid in c.get("from") or []:
            s = by_id.get(str(sid))
            if s:
                refs += s["refs"]
                origin = origin or s
        evidence = store.build_evidence(app, refs)[:4]
        if not evidence:
            skipped += 1  # 没有可核实的证据，不进入候选
            continue
        cid = store.add_candidate(app, paper_id=paper_id, kind=str(c.get("kind", "other")), title=str(c["title"]).strip(),
                                  content=str(c["content"]).strip(), evidence=evidence,
                                  origin_kind=origin["origin_kind"] if origin else "analysis",
                                  origin_ref=origin["origin_ref"] if origin else None)
        added += 1 if cid else 0
    return {"paper_id": paper_id, "added": added, "skipped_without_evidence": skipped, "sources": len(sources),
            "notes": model_notes}


# ---------------- 主题建议 ----------------
TOPIC_SYSTEM = (
    "你是科研文献整理助手。下面是用户文献库里的论文（带序号）。请把它们归纳成 2-8 个研究主题，每个主题包含属于它的论文序号。\n"
    "要求：主题名称具体（如“级联失效与网络恢复”而不是“网络”）；一篇论文可以属于多个主题；只能使用给出的序号；"
    "如果某个主题与“已有主题”相同或相近，沿用已有主题的名称。\n"
    '只输出 JSON 对象：{"topics": [{"name": "...", "description": "一句话说明范围", "papers": [1, 3]}]}'
)


async def suggest_topics(app: AppContext) -> dict:
    with app.db.session() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(papers)").fetchall()}
        where = "WHERE COALESCE(role,'paper')='paper'" if "role" in cols else ""
        papers = [dict(r) for r in conn.execute(f"SELECT id, title, year, abstract FROM papers {where} ORDER BY imported_at DESC LIMIT 60").fetchall()]
    if len(papers) < 2:
        raise ApiError("bad_request", "文献库里至少需要 2 篇论文才能归纳主题。")
    existing = [t["name"] for t in store.list_topics(app)]
    lines = [f"[{i}] {p['title']}（{p['year'] or '?'}）：{(p['abstract'] or '')[:160]}" for i, p in enumerate(papers, 1)]
    user = ("已有主题：" + ("、".join(existing) if existing else "（无）") + "\n\n论文：\n" + "\n".join(lines))
    try:
        data = await ask_json(app, system=TOPIC_SYSTEM, user=user, task_type="kb_topics", tier="fast", max_tokens=1800,
                              summary=f"发送了文献库中 {len(papers)} 篇论文的标题与摘要片段，用于归纳研究主题")
    except LLMError as err:
        raise ApiError(err.code, err.message) from err
    created: list[dict] = []
    for t in (data.get("topics") or []) if isinstance(data, dict) else []:
        name = str(t.get("name", "")).strip()
        idxs = [int(i) for i in t.get("papers") or [] if str(i).isdigit() and 1 <= int(i) <= len(papers)]
        if not name or not idxs:
            continue
        with app.db.session() as conn:
            ex = conn.execute("SELECT id, status FROM kb_topics WHERE lower(name) = lower(?)", (name,)).fetchone()
        if ex:
            tid, status = ex["id"], ex["status"]
        else:
            tid = store.create_topic(app, name, str(t.get("description", "")), source="ai", status="suggested")["id"]
            status = "suggested"
        for i in idxs:
            try:
                store.set_paper_topic(app, papers[i - 1]["id"], tid, source="auto", status="suggested" if status == "suggested" else "suggested")
            except ApiError:
                pass
        created.append({"topic_id": tid, "name": name, "papers": [papers[i - 1]["id"] for i in idxs], "existing": bool(ex and ex["status"] == "active")})
    return {"suggestions": created}


def list_topic_suggestions(app: AppContext) -> list[dict]:
    out = []
    for t in store.list_topics(app, "suggested"):
        out.append({**t, "papers": store.topic_papers(app, t["id"], "suggested")})
    # 指向已有主题的论文归类建议
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT kp.topic_id, t.name, p.id AS pid, p.title FROM kb_paper_topics kp JOIN kb_topics t ON t.id = kp.topic_id "
            "JOIN papers p ON p.id = kp.paper_id WHERE kp.status = 'suggested' AND t.status = 'active'").fetchall()
    grouped: dict[str, dict] = {}
    for r in rows:
        g = grouped.setdefault(r["topic_id"], {"id": r["topic_id"], "name": r["name"], "status": "active", "papers": [], "existing": True})
        g["papers"].append({"id": r["pid"], "title": r["title"]})
    return out + list(grouped.values())


def accept_topic_suggestion(app: AppContext, topic_id: str) -> dict:
    with app.db.session() as conn:
        r = conn.execute("SELECT * FROM kb_topics WHERE id = ?", (topic_id,)).fetchone()
        if r is None:
            raise ApiError("not_found", "主题不存在", 404)
        conn.execute("UPDATE kb_topics SET status = 'active', updated_at = ? WHERE id = ?", (utcnow(), topic_id))
        conn.execute("UPDATE kb_paper_topics SET status = 'confirmed' WHERE topic_id = ?", (topic_id,))
    return next(t for t in store.list_topics(app) if t["id"] == topic_id)


def reject_topic_suggestion(app: AppContext, topic_id: str) -> None:
    with app.db.session() as conn:
        r = conn.execute("SELECT status FROM kb_topics WHERE id = ?", (topic_id,)).fetchone()
        if r is None:
            raise ApiError("not_found", "主题不存在", 404)
        if r["status"] == "suggested":
            conn.execute("DELETE FROM kb_topics WHERE id = ?", (topic_id,))
        else:
            conn.execute("DELETE FROM kb_paper_topics WHERE topic_id = ? AND status = 'suggested'", (topic_id,))


# ---------------- 关系建议 ----------------
REL_SYSTEM = (
    "你是科研文献分析助手。下面是同一个主题下、来自不同论文的知识条目（带序号），每条附有来自论文原文的证据摘录。\n"
    "请找出条目之间**有明确依据**的学术关系：uses_method（采用了同一方法）、differs_from（做法/结论的区别）、improves（改进了）、"
    "inherits（继承自）、contradicts（结论相反）、supports（互相印证）。\n"
    "要求：只能依据给出的条目内容和证据；两端必须来自不同论文；不确定就不要写；description 用一句话说明关系的具体内容。\n"
    '只输出 JSON 对象：{"relations": [{"from": 1, "to": 2, "relation": "...", "description": "..."}]}'
)


async def suggest_relations(app: AppContext, topic_id: str) -> dict:
    items = store.list_items(app, topic_id=topic_id)[:14]
    papers = {i["source_paper"]["id"] for i in items if i["source_paper"]}
    if len(items) < 2 or len(papers) < 2:
        raise ApiError("bad_request", "这个主题下至少需要来自 2 篇不同论文的 2 条知识，才能建议关系。")
    lines = []
    for n, it in enumerate(items, 1):
        ev = "；".join(e["quote"][:120] for e in it["evidence"][:2])
        lines.append(f"[{n}]《{(it['source_paper'] or {}).get('title', '?')[:40]}》{it['title']}：{it['content'][:300]}\n   证据：{ev}")
    try:
        data = await ask_json(app, system=REL_SYSTEM, user="\n".join(lines), task_type="kb_relations", tier="strong", max_tokens=1800,
                              summary=f"发送了 {len(items)} 条知识条目及其证据摘录，用于建议条目之间的关系")
    except LLMError as err:
        raise ApiError(err.code, err.message) from err
    existing = {(r["from_id"], r["to_id"], r["relation"]) for r in store.list_relations(app, topic_id)}
    added = []
    for r in (data.get("relations") or []) if isinstance(data, dict) else []:
        try:
            a, b = items[int(r["from"]) - 1], items[int(r["to"]) - 1]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        if a["id"] == b["id"] or (a["source_paper"] or {}).get("id") == (b["source_paper"] or {}).get("id"):
            continue
        rel = str(r.get("relation", ""))
        if rel not in store.RELATIONS or (a["id"], b["id"], rel) in existing:
            continue
        refs = [{"paper_id": e.get("paper_id"), "ord": e.get("ord")} if e.get("kind") != "note" else {"kind": "note", "note_id": e.get("note_id")}
                for e in a["evidence"] + b["evidence"]]
        try:
            added.append(store.create_relation(app, from_kind="item", from_id=a["id"], to_kind="item", to_id=b["id"], relation=rel,
                                               description=str(r.get("description", "")), evidence_refs=refs, ai_inferred=True, status="suggested"))
        except ApiError:
            continue  # 没有可核实的证据 → 不建立
    return {"suggested": added}


# ---------------- 画像建议（只提“候选”，绝不自动认定已掌握） ----------------
def suggest_profile(app: AppContext) -> list[dict]:
    """确定性规则：用户多次让 AI 解释的术语 → 建议标为“可能不熟悉”。基于真实的使用记录，不调用模型。"""
    with app.db.session() as conn:
        rows = conn.execute("SELECT id, paper_id, selection_json, intent FROM insight_cards WHERE intent IN ('term','explain') AND status='complete'").fetchall()
        known = {r["name"].lower() for r in conn.execute("SELECT name FROM profile_concepts").fetchall()}
        # 待处理、已接受、已忽略的都不再重复建议（“忽略”就是别再问我）
        pending = {r["name"].lower() for r in conn.execute("SELECT name FROM profile_suggestions").fetchall()}
    counts: dict[str, dict] = defaultdict(lambda: {"cards": [], "papers": set(), "name": ""})
    for r in rows:
        sel = loads(r["selection_json"], {}) or {}
        text = re.sub(r"\s+", " ", (sel.get("text") or "")).strip()
        if not text or len(text) > 50 or len(text.split()) > 5:
            continue  # 只统计“术语”，不统计整句选中
        key = text.lower()
        g = counts[key]
        g["name"] = g["name"] or text
        g["cards"].append(r["id"])
        g["papers"].add(r["paper_id"])
    created = []
    now = utcnow()
    with app.db.session() as conn:
        for key, g in counts.items():
            if key in known or key in pending:
                continue
            n = len(g["cards"])
            basis = f"你曾 {n} 次让 AI 解释这个词/短语，涉及 {len(g['papers'])} 篇论文。" + ("多次出现，建议标为“正在学习”。" if n >= 3 else "")
            state = "learning" if n >= 3 else "unsure"
            sid = new_id()
            conn.execute("INSERT INTO profile_suggestions(id, name, state, basis, evidence_json, created_at) VALUES (?,?,?,?,?,?)",
                         (sid, g["name"][:120], state, basis, dumps([{"kind": "card", "card_id": c} for c in g["cards"][:6]]), now))
            created.append(sid)
    return store.list_suggestions(app)


def resolve_suggestion(app: AppContext, sid: str, accept: bool, state: str | None = None) -> dict | None:
    with app.db.session() as conn:
        r = conn.execute("SELECT * FROM profile_suggestions WHERE id = ?", (sid,)).fetchone()
        if r is None or r["status"] != "pending":
            raise ApiError("not_found", "建议不存在或已处理", 404)
        conn.execute("UPDATE profile_suggestions SET status = ? WHERE id = ?", ("accepted" if accept else "dismissed", sid))
    if accept:
        return store.upsert_concept(app, r["name"], state or r["state"], note=r["basis"], origin="suggestion")
    return None


# ---------------- 接入 Insight Flow 的提示词（默认关闭） ----------------
def prompt_hint(app: AppContext) -> str | None:
    meta = store.get_meta(app)
    if not meta["use_in_prompts"]:
        return None
    parts: list[str] = []
    depth = {"brief": "简明扼要", "standard": "标准详略", "deep": "深入细致"}.get(meta["explanation_depth"] or "")
    if depth:
        parts.append(f"用户偏好的解释深度：{depth}")
    by_state: dict[str, list[str]] = defaultdict(list)
    for c in store.list_concepts(app):
        by_state[c["state"]].append(c["name"])
    if by_state["mastered"]:
        parts.append("用户已确认掌握（解释时无需展开）：" + "、".join(by_state["mastered"][:10]))
    weak = by_state["learning"][:6] + by_state["unsure"][:6]
    if weak:
        parts.append("用户正在学习或不熟悉（遇到时请解释清楚）：" + "、".join(weak))
    return "；".join(parts) if parts else None


def register_jobs(manager: JobManager) -> None:
    import asyncio

    def handle_extract(ctx: JobContext) -> dict:
        return ctx.run_async(extract_candidates(ctx.app, ctx.payload["paper_id"], ctx))

    manager.register("kb_extract", handle_extract)
