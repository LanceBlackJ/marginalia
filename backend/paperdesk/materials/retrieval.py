"""补充材料的检索：授权后把子论文（PDF 补充材料）的相关段落并入问答上下文；未授权时只判断“是否需要补充材料”。

引用格式：[S{n}P{ord}]——n 是本次上下文里补充文档的序号（1 起），ord 是该文档内的段落序号。
"""
from __future__ import annotations

import re
import sqlite3

from .. import reader
from ..ai import retrieval as main_retrieval
from ..context import AppContext
from . import grants
from .labels import SUPP_REF_RE, ZH_REF_RE, extract_labels, label_patterns  # noqa: F401  (re-export)

DETAIL_RE = re.compile(
    r"derive|derivation|proof|prove|complexity|hyper-?parameter|detail|dataset|configuration|implementation|ablation|"
    r"推导|证明|复杂度|超参数|参数|细节|详细|数据集|具体|实现|配置|消融",
    re.I,
)

def list_docs(app: AppContext, paper_id: str) -> list[dict]:
    """可用于检索的补充文档（已解析完成的 PDF 子论文），按添加顺序编号 S1、S2…"""
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT a.id AS asset_id, a.filename, a.category, a.child_paper_id, p.title, p.page_count "
            "FROM supplementary_assets a JOIN papers p ON p.id = a.child_paper_id "
            "WHERE a.paper_id = ? AND a.download_status = 'done' AND p.status = 'ready' ORDER BY a.created_at, a.rowid",
            (paper_id,),
        ).fetchall()
    return [
        {"n": i + 1, "asset_id": r["asset_id"], "paper_id": r["child_paper_id"], "filename": r["filename"],
         "category": r["category"], "title": r["title"], "page_count": r["page_count"],
         "label": f"补充材料 {i + 1} · {r['filename']}"}
        for i, r in enumerate(rows)
    ]


def has_linked_supplements(app: AppContext, paper_id: str) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT id, filename, category, size, child_paper_id FROM supplementary_assets "
            "WHERE paper_id = ? AND download_status = 'done' AND child_paper_id IS NOT NULL ORDER BY created_at", (paper_id,)
        ).fetchall()
    return [{"asset_id": r["id"], "filename": r["filename"], "category": r["category"], "size": r["size"],
             "paper_id": r["child_paper_id"]} for r in rows]


def needs_permission(app: AppContext, paper_id: str, question: str, sel_text: str, pack) -> dict | None:
    """未授权时：这次回答是否需要补充材料？需要则返回 needs_supplement_permission 事件内容，否则 None。

    触发条件（都要求确有已关联的补充 PDF）：
    (a) 问题/选中文字里明确提到 Supplementary Fig./Table/Section/Note…；
    (b) 问题是在问细节（推导/复杂度/参数/数据集…），而检索到的正文高相关段落里引用了补充材料。
    """
    if grants.is_granted(app, paper_id):
        return None
    avail = has_linked_supplements(app, paper_id)
    if not avail:
        return None
    asked = extract_labels(f"{question}\n{sel_text}")
    labels, reason = list(asked), ""
    if asked:
        reason = "你的问题提到了补充材料：" + "、".join(asked[:4])
    elif DETAIL_RE.search(f"{question} {sel_text}"):
        top = [p for p in pack.passages if p.doc == 0 and ({"search", "selection"} & p.reasons)]
        in_text: list[str] = []
        for p in top[:8]:
            for lab in extract_labels(p.text):
                if lab not in in_text:
                    in_text.append(lab)
        if in_text:
            labels = in_text
            reason = "相关正文引用了补充材料：" + "、".join(in_text[:4]) + "，回答细节可能需要它们"
    if not labels:
        return None
    return {"labels": labels[:8], "reason": reason, "supplements": avail}


# ---------------- 检索 ----------------
def _row_passage(r: sqlite3.Row, doc: int, priority: float, reason: str):
    return main_retrieval.CtxPassage(
        r["ord"], r["page"], r["kind"], r["section_title"], r["text"][: main_retrieval.MAX_PASSAGE_CHARS],
        [r["x0"], r["y0"], r["x1"], r["y1"]], priority, {reason, "supplement"}, doc=doc,
    )


_BASE = (
    "SELECT p.ord, p.page, p.kind, p.text, p.x0, p.y0, p.x1, p.y1, s.title AS section_title{extra} "
    "FROM {frm} LEFT JOIN sections s ON s.id = p.section_id "
)


def retrieve(app: AppContext, docs: list[dict], *, terms: list[str], labels: list[str], budget_chars: int,
             per_doc: int = 10) -> list:
    """从每个补充文档取相关段落：标签（caption/章节）精确命中优先，其次全文 BM25。返回 CtxPassage 列表（doc=n）。"""
    out = []
    used = 0
    for d in docs:
        pid, n = d["paper_id"], d["n"]
        cand: dict[int, main_retrieval.CtxPassage] = {}

        def add(p, _cand=cand):
            if p.kind == "reference":
                return
            if p.ord in _cand:
                _cand[p.ord].priority = max(_cand[p.ord].priority, p.priority)
                _cand[p.ord].reasons |= p.reasons
            else:
                _cand[p.ord] = p

        with app.db.session() as conn:
            for lab in labels:
                for pat in label_patterns(lab):
                    rows = conn.execute(
                        _BASE.format(extra="", frm="passages p") + "WHERE p.paper_id = ? AND p.text LIKE ? LIMIT 3", (pid, pat)
                    ).fetchall()
                    for r in rows:
                        add(_row_passage(r, n, 800.0 if r["kind"] == "caption" else 500.0, "label"))
                        for nb in conn.execute(
                            _BASE.format(extra="", frm="passages p") + "WHERE p.paper_id = ? AND p.ord BETWEEN ? AND ?",
                            (pid, r["ord"] - 1, r["ord"] + 1),
                        ).fetchall():
                            if nb["ord"] != r["ord"]:
                                add(_row_passage(nb, n, 300.0, "neighbor"))
                # 章节标题含标签尾部（如 II.D.3）
                tail = re.sub(r"^Supplementary\s+\S+\s+", "", lab)
                if re.fullmatch(r"[IVX]+(?:\.[A-Za-z0-9]+)+", tail):
                    for r in conn.execute(
                        _BASE.format(extra="", frm="passages p") + "WHERE p.paper_id = ? AND s.title LIKE ? ORDER BY p.ord LIMIT 5",
                        (pid, f"%{tail}%"),
                    ).fetchall():
                        add(_row_passage(r, n, 450.0, "label"))
            if terms:
                try:
                    rows = conn.execute(
                        _BASE.format(extra=", bm25(passages_fts) AS rank", frm="passages_fts f JOIN passages p ON p.id = f.rowid")
                        + "WHERE passages_fts MATCH ? AND p.paper_id = ? ORDER BY rank LIMIT 16",
                        (reader.fts_query(terms, operator="OR"), pid),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                if rows:
                    best = max(-r["rank"] for r in rows) or 1.0
                    for r in rows:
                        boost = 1.1 if r["kind"] == "caption" else 1.0
                        add(_row_passage(r, n, 100.0 * max(0.0, -r["rank"]) / best * boost, "search"))
        for p in sorted(cand.values(), key=lambda x: -x.priority)[:per_doc]:
            cost = len(p.text) + 40
            if used + cost > budget_chars and out:
                continue
            out.append(p)
            used += cost
    out.sort(key=lambda p: (p.doc, p.ord))
    return out
