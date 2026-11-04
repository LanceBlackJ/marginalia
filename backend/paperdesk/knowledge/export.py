"""阅读成果导出为 Markdown（含来源与证据位置）。内容由前端保存为文件（Blob 下载，Electron 会弹出“另存为”）。"""
from __future__ import annotations

import re
from datetime import datetime

from ..ai import cards as cards_mod
from ..context import AppContext
from ..util import ApiError, utcnow
from . import store

KIND_LABELS = {"method": "方法", "finding": "结论", "concept": "概念", "formula": "公式", "limitation": "局限", "other": "其它"}


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")[:60] or "export"


def _cite(e: dict) -> str:
    where = f"第 {e['page']} 页" if e.get("page") else "元数据"
    sec = f" · {e['section_title']}" if e.get("section_title") else ""
    return f"《{(e.get('paper_title') or '')[:50]}》{where}{sec}"


def _evidence_md(evs: list[dict]) -> str:
    lines = []
    for e in evs:
        quote = re.sub(r"\s+", " ", e.get("quote") or "").strip()
        tag = "笔记" if e.get("kind") == "note" else "原文"
        lines.append(f"  - {tag}（{_cite(e)}）：“{quote[:300]}”")
    return "\n".join(lines)


def _paper_line(p: dict | None) -> str:
    if not p:
        return "来源：无"
    bits = [f"《{p['title']}》"] + ([f"{p['year']}"] if p.get("year") else []) + ([f"DOI {p['doi']}"] if p.get("doi") else [])
    return "来源：" + "，".join(bits)


def export_topic(app: AppContext, topic_id: str, include_evidence: bool = True) -> dict:
    topics = {t["id"]: t for t in store.list_topics(app)}
    if topic_id not in topics:
        raise ApiError("not_found", "主题不存在", 404)
    t = topics[topic_id]
    items = store.list_items(app, topic_id=topic_id)
    papers = store.topic_papers(app, topic_id)
    lines = [f"# {t['name']}", "", f"> 导出自 Marginalia · {datetime.now().strftime('%Y-%m-%d %H:%M')} · {len(items)} 条知识 · {len(papers)} 篇论文", ""]
    if t["description"]:
        lines += [t["description"], ""]
    if papers:
        lines += ["## 相关论文", ""] + [f"- 《{p['title']}》" + (f"（{p['year']}）" if p.get("year") else "") for p in papers] + [""]
    by_kind: dict[str, list[dict]] = {}
    for it in items:
        by_kind.setdefault(it["kind"], []).append(it)
    for kind in ("method", "finding", "concept", "formula", "limitation", "other"):
        if kind not in by_kind:
            continue
        lines += [f"## {KIND_LABELS[kind]}", ""]
        for it in by_kind[kind]:
            lines += [f"### {it['title']}", "", it["content"], "", _paper_line(it["source_paper"]), ""]
            if include_evidence and it["evidence"]:
                lines += ["证据：", _evidence_md(it["evidence"]), ""]
    rels = [r for r in store.list_relations(app, topic_id, "confirmed")]
    if rels:
        title_by_id = {it["id"]: it["title"] for it in items}
        lines += ["## 已确认的关系", ""]
        for r in rels:
            lines.append(f"- {title_by_id.get(r['from_id'], '?')} —[{r['relation_label']}]→ {title_by_id.get(r['to_id'], '?')}：{r['description']}")
            if include_evidence:
                lines.append(_evidence_md(r["evidence"][:2]))
        lines.append("")
    return {"filename": f"主题_{_safe_name(t['name'])}.md", "content": "\n".join(lines).rstrip() + "\n", "count": len(items)}


def export_viewpoints(app: AppContext, topic_id: str | None) -> dict:
    """文献观点汇总：按论文列出各自的结论/方法要点，便于写综述时对照。"""
    items = store.list_items(app, topic_id=topic_id)
    if not items:
        raise ApiError("bad_request", "没有可导出的知识条目")
    by_paper: dict[str, list[dict]] = {}
    for it in items:
        key = (it["source_paper"] or {}).get("id") or "none"
        by_paper.setdefault(key, []).append(it)
    title = "文献观点汇总"
    if topic_id:
        title += "：" + next((t["name"] for t in store.list_topics(app) if t["id"] == topic_id), "")
    lines = [f"# {title}", "", f"> 导出自 Marginalia · {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    for key, its in by_paper.items():
        p = its[0]["source_paper"]
        lines += [f"## {('《' + p['title'] + '》') if p else '无来源论文'}" + (f"（{p['year']}）" if p and p.get("year") else ""), ""]
        for it in its:
            ev = it["evidence"][0] if it["evidence"] else None
            loc = f"（{_cite(ev)}）" if ev else ""
            lines.append(f"- **{it['title']}**：{it['content']}{loc}")
        lines.append("")
    return {"filename": f"{_safe_name(title)}.md", "content": "\n".join(lines).rstrip() + "\n", "count": len(items)}


def export_notes(app: AppContext, paper_ids: list[str] | None) -> dict:
    with app.db.session() as conn:
        sql = ("SELECT n.*, p.title AS paper_title, p.year FROM notes n JOIN papers p ON p.id = n.paper_id")
        args: list = []
        if paper_ids:
            sql += f" WHERE n.paper_id IN ({','.join('?' * len(paper_ids))})"
            args = list(paper_ids)
        rows = conn.execute(sql + " ORDER BY p.title, n.page, n.id", args).fetchall()
    if not rows:
        raise ApiError("bad_request", "没有可导出的笔记")
    lines = ["# 阅读笔记", "", f"> 导出自 Marginalia · {datetime.now().strftime('%Y-%m-%d %H:%M')} · {len(rows)} 条", ""]
    cur = None
    for r in rows:
        if r["paper_id"] != cur:
            cur = r["paper_id"]
            lines += [f"## 《{r['paper_title']}》" + (f"（{r['year']}）" if r["year"] else ""), ""]
        page = f"第 {r['page']} 页" if r["page"] else "未标注页码"
        lines.append(f"- **{page}**：{(r['text'] or '').strip() or '（仅摘录）'}")
        if r["quote"]:
            lines.append(f"  > {re.sub(chr(10), ' ', r['quote'])[:400]}")
    return {"filename": "阅读笔记.md", "content": "\n".join(lines).rstrip() + "\n", "count": len(rows)}


def export_cards(app: AppContext, paper_ids: list[str] | None) -> dict:
    with app.db.session() as conn:
        ids = paper_ids or [r["paper_id"] for r in conn.execute("SELECT DISTINCT paper_id FROM insight_cards WHERE saved = 1").fetchall()]
        titles = {r["id"]: (r["title"], r["year"]) for r in conn.execute("SELECT id, title, year FROM papers").fetchall()}
    lines = ["# 研究卡片", "", f"> 导出自 Marginalia · {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    n = 0
    for pid in ids:
        cs = [c for c in cards_mod.list_cards(app, pid, None, True) if c["status"] == "complete"]
        if not cs:
            continue
        t, y = titles.get(pid, ("?", None))
        lines += [f"## 《{t}》" + (f"（{y}）" if y else ""), ""]
        for c in cs:
            n += 1
            lines += [f"### {c['question'] or c['object_label']}", "", c["answer"]["direct"], ""]
            if c["answer"].get("detail"):
                lines += [c["answer"]["detail"], ""]
            if c["answer"].get("inferences"):
                lines += ["**AI 推断**（非论文原文）：", ""] + [f"- {i['text']}" for i in c["answer"]["inferences"]] + [""]
            if c["evidence"]:
                lines += ["证据：", _evidence_md([{**e, "paper_title": t} for e in c["evidence"]]), ""]
            lines += [f"*模型：{c['model']} · 核实状态：{c['verification']['status']}*", ""]
    if n == 0:
        raise ApiError("bad_request", "没有已保存的研究卡片可导出")
    return {"filename": "研究卡片.md", "content": "\n".join(lines).rstrip() + "\n", "count": n}
