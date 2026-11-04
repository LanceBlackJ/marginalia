"""阅读成果合集导出：笔记、批注、AI 卡片、已确认的知识条目，按论文汇总，输出 Markdown 或 Word（.docx）。

两种格式共用同一个文档模型，保证内容一致。规则：
- 每一条都带出处（页码与原文片段）；
- AI 生成的内容（研究卡片、AI 推断）有明确的“AI 生成”标识，和论文原文、用户自己写的内容区分开；
- 只导出已经保存的内容，不重新调用任何模型。
"""
from __future__ import annotations

import io
import re
from datetime import datetime

from ..ai import cards as cards_mod
from ..context import AppContext
from ..library.citations import supplement_child_ids
from ..util import ApiError, loads
from . import store

KIND_LABELS = {"method": "方法", "finding": "结论", "concept": "概念", "formula": "公式", "limitation": "局限", "other": "其它"}
ANNOT_LABELS = {"highlight": "高亮", "underline": "下划线", "note": "批注"}


def _flat(s: str | None, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", s or "").strip()[:limit]


def build(app: AppContext, paper_ids: list[str] | None = None, *, notes: bool = True, annotations: bool = True,
          cards: bool = True, items: bool = True) -> dict:
    """文档模型：{title, generated_at, papers:[{id,title,year,doi,notes,annotations,cards,items}], counts}。"""
    with app.db.session() as conn:
        if paper_ids:
            rows = conn.execute(f"SELECT id, title, year, doi FROM papers WHERE id IN ({','.join('?' * len(paper_ids))})",
                                list(paper_ids)).fetchall()
            order = {pid: i for i, pid in enumerate(paper_ids)}
            rows = sorted(rows, key=lambda r: order[r["id"]])
        else:
            rows = conn.execute("SELECT id, title, year, doi FROM papers ORDER BY title").fetchall()
        sup = {r[0] for r in conn.execute("SELECT child_paper_id FROM paper_supplements").fetchall()} \
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'paper_supplements'").fetchone() else set()
        papers = []
        for r in rows:
            if r["id"] in sup:
                continue
            p = {"id": r["id"], "title": r["title"], "year": r["year"], "doi": r["doi"], "notes": [], "annotations": [], "cards": [], "items": []}
            if notes:
                p["notes"] = [{"page": n["page"], "quote": _flat(n["quote"]), "text": (n["text"] or "").strip()} for n in conn.execute(
                    "SELECT page, quote, text FROM notes WHERE paper_id = ? ORDER BY page, id", (r["id"],))]
            if annotations:
                p["annotations"] = [{"page": a["page"], "kind": a["kind"], "text": _flat(a["text"]), "note": (a["note"] or "").strip()}
                                    for a in conn.execute("SELECT page, kind, text, note FROM annotations WHERE paper_id = ? ORDER BY page, id", (r["id"],))
                                    if a["text"] or a["note"]]
            papers.append(p)
    for p in papers:
        if cards:
            for c in cards_mod.list_cards(app, p["id"], None, True):
                if c["status"] != "complete":
                    continue
                ans = c["answer"]
                p["cards"].append({
                    "question": c["question"] or c["object_label"], "direct": ans.get("direct", ""), "detail": ans.get("detail", ""),
                    "inferences": [i["text"] for i in ans.get("inferences", [])], "model": c["model"],
                    "verification": c["verification"]["status"],
                    "evidence": [{"page": e.get("page"), "quote": _flat(e.get("quote"), 300)} for e in c.get("evidence", [])[:6]],
                })
        if items:
            for it in store.list_items(app, paper_id=p["id"]):
                p["items"].append({"title": it["title"], "kind": KIND_LABELS.get(it["kind"], it["kind"]), "content": it["content"],
                                   "evidence": [{"page": e.get("page"), "quote": _flat(e.get("quote"), 300)} for e in it["evidence"][:4]]})
    papers = [p for p in papers if p["notes"] or p["annotations"] or p["cards"] or p["items"]]
    if not papers:
        raise ApiError("bad_request", "所选论文里没有可导出的笔记、批注、研究卡片或知识条目")
    counts = {k: sum(len(p[k]) for p in papers) for k in ("notes", "annotations", "cards", "items")}
    return {"title": "Marginalia 阅读成果", "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "papers": papers, "counts": counts}


def _where(page) -> str:
    return f"第 {page} 页" if page else "未标注页码"


def _head(p: dict) -> str:
    return f"《{p['title']}》" + (f"（{p['year']}）" if p["year"] else "") + (f" · DOI {p['doi']}" if p["doi"] else "")


def to_markdown(doc: dict) -> str:
    c = doc["counts"]
    out = [f"# {doc['title']}", "", f"> 导出自 Marginalia · {doc['generated_at']} · 笔记 {c['notes']} · 批注 {c['annotations']} · "
           f"研究卡片 {c['cards']}（AI 生成）· 知识条目 {c['items']}", "",
           "说明：标注“AI 生成”的内容由模型生成，其中“AI 推断”不是论文原文；“我的笔记”“我的批注”是你自己写的；“原文”是论文里的摘录。", ""]
    for p in doc["papers"]:
        out += [f"## {_head(p)}", ""]
        if p["notes"]:
            out += ["### 我的笔记", ""]
            for n in p["notes"]:
                out.append(f"- **{_where(n['page'])}**：{n['text'] or '（仅摘录）'}")
                if n["quote"]:
                    out.append(f"  > 原文：{n['quote']}")
            out.append("")
        if p["annotations"]:
            out += ["### 我的批注", ""]
            for a in p["annotations"]:
                line = f"- **{_where(a['page'])} · {ANNOT_LABELS.get(a['kind'], a['kind'])}**"
                if a["text"]:
                    line += f"：“{a['text']}”"
                out.append(line)
                if a["note"]:
                    out.append(f"  - 我的想法：{a['note']}")
            out.append("")
        if p["items"]:
            out += ["### 知识条目（我确认保留的）", ""]
            for it in p["items"]:
                out += [f"- **{it['title']}**（{it['kind']}）：{it['content']}"]
                for e in it["evidence"]:
                    out.append(f"  - 原文（{_where(e['page'])}）：“{e['quote']}”")
            out.append("")
        if p["cards"]:
            out += ["### 研究卡片（AI 生成）", ""]
            for cd in p["cards"]:
                out += [f"#### {cd['question']}", "", f"**AI 生成** · 模型 {cd['model']} · 核实状态 {cd['verification']}", "", cd["direct"], ""]
                if cd["detail"]:
                    out += [cd["detail"], ""]
                if cd["inferences"]:
                    out += ["**AI 推断**（非论文原文）：", ""] + [f"- {t}" for t in cd["inferences"]] + [""]
                for e in cd["evidence"]:
                    out.append(f"- 依据原文（{_where(e['page'])}）：“{e['quote']}”")
                out.append("")
    return "\n".join(out).rstrip() + "\n"


def to_docx(doc: dict) -> bytes:
    from docx import Document
    from docx.shared import Pt, RGBColor

    d = Document()
    st = d.styles["Normal"]
    st.font.name = "Microsoft YaHei"
    st.font.size = Pt(10.5)
    gray = RGBColor(0x55, 0x55, 0x55)

    def para(text: str = "", *, bold: bool = False, italic: bool = False, color=None, style: str | None = None, indent: float = 0):
        p = d.add_paragraph(style=style)
        if indent:
            p.paragraph_format.left_indent = Pt(indent)
        run = p.add_run(text)
        run.bold, run.italic = bold, italic
        if color is not None:
            run.font.color.rgb = color
        return p

    def labeled(label: str, text: str, indent: float = 0):
        p = d.add_paragraph(style="List Bullet")
        if indent:
            p.paragraph_format.left_indent = Pt(indent)
        p.add_run(label).bold = True
        p.add_run(text)

    c = doc["counts"]
    d.add_heading(doc["title"], level=0)
    para(f"导出自 Marginalia · {doc['generated_at']} · 笔记 {c['notes']} · 批注 {c['annotations']} · 研究卡片 {c['cards']}（AI 生成）· 知识条目 {c['items']}",
         color=gray)
    para("说明：标注“AI 生成”的内容由模型生成，其中“AI 推断”不是论文原文；“我的笔记”“我的批注”是你自己写的；“原文”是论文里的摘录。",
         italic=True, color=gray)
    for p in doc["papers"]:
        d.add_heading(_head(p), level=1)
        if p["notes"]:
            d.add_heading("我的笔记", level=2)
            for n in p["notes"]:
                labeled(f"{_where(n['page'])}：", n["text"] or "（仅摘录）")
                if n["quote"]:
                    para(f"原文：{n['quote']}", italic=True, color=gray, indent=28)
        if p["annotations"]:
            d.add_heading("我的批注", level=2)
            for a in p["annotations"]:
                labeled(f"{_where(a['page'])} · {ANNOT_LABELS.get(a['kind'], a['kind'])}", f"：“{a['text']}”" if a["text"] else "")
                if a["note"]:
                    para(f"我的想法：{a['note']}", indent=28)
        if p["items"]:
            d.add_heading("知识条目（我确认保留的）", level=2)
            for it in p["items"]:
                labeled(f"{it['title']}（{it['kind']}）：", it["content"])
                for e in it["evidence"]:
                    para(f"原文（{_where(e['page'])}）：“{e['quote']}”", italic=True, color=gray, indent=28)
        if p["cards"]:
            d.add_heading("研究卡片（AI 生成）", level=2)
            for cd in p["cards"]:
                d.add_heading(cd["question"], level=3)
                para(f"AI 生成 · 模型 {cd['model']} · 核实状态 {cd['verification']}", bold=True, color=gray)
                para(cd["direct"])
                if cd["detail"]:
                    para(cd["detail"])
                if cd["inferences"]:
                    para("AI 推断（非论文原文）：", bold=True)
                    for t in cd["inferences"]:
                        d.add_paragraph(t, style="List Bullet")
                for e in cd["evidence"]:
                    para(f"依据原文（{_where(e['page'])}）：“{e['quote']}”", italic=True, color=gray, indent=28)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()
