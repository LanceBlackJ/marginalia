"""全文上下文检索：按任务从整篇论文里挑出相关段落，而不是把全文塞给模型。

组成：选中段落(+邻近段落) ＋ 全文 BM25 检索（含章节加权）＋ 图表/公式引用解析 ＋ 摘要与章节大纲（全局框架），
在字符预算内按优先级装配，并保留 [P序号]、页码、章节，供模型引用与后端核实。
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

from .. import reader
from ..context import AppContext
from ..util import ApiError
from .router import TaskType

STOPWORDS = set(
    "the a an and or of to in on for with by from at as is are was were be been being this that these those it its "
    "we our you your they their what which who whom how why when where does do did can could should would may might "
    "paper article work method methods using use used based than then also into over under between about not no yes "
    "please explain describe tell give show author authors proposed propose".split()
)
SECTION_BOOST = {
    TaskType.METHOD: r"method|approach|model|framework|algorithm|propos|architecture|design|方法|模型|框架|算法",
    TaskType.FORMULA: r"method|approach|model|framework|algorithm|propos|preliminar|notation|方法|模型|算法|符号",
    TaskType.FIGURE: r"method|approach|model|framework|architecture|overview|方法|模型|框架",
    TaskType.EXPERIMENT: r"experiment|result|evaluation|ablation|analysis|实验|结果|评估|分析",
    TaskType.MOTIVATION: r"introduction|background|related|motivation|preliminar|引言|背景|相关|动机",
    TaskType.ANALYSIS: r"",
}
REF_RE = re.compile(r"(fig(?:ure)?s?\.?|table|eq(?:uation)?\.?|algorithm|图|表|公式)\s*[（(]?\s*(\d{1,2})\s*[）)]?", re.I)
MAX_PASSAGE_CHARS = 1800


@dataclass
class CtxPassage:
    ord: int
    page: int
    kind: str
    section_title: str | None
    text: str
    bbox: list[float]
    priority: float = 0.0
    reasons: set[str] = field(default_factory=set)
    doc: int = 0  # 0 = 正文；n>0 = 第 n 个补充文档（引用写作 [S{n}P{ord}]）


@dataclass
class ContextPack:
    paper_title: str
    paper_year: int | None
    paper_authors: list[str]
    outline: list[str]
    passages: list[CtxPassage]
    selection: dict | None
    selection_ords: list[int]
    terms: list[str]
    supp_docs: list[dict] = field(default_factory=list)  # 本次上下文用到的补充文档（已授权）：{n, paper_id, filename, …}

    @property
    def ords(self) -> list[int]:
        """正文段落序号（补充材料段落另见 supp_refs）。"""
        return [p.ord for p in self.passages if p.doc == 0]

    @property
    def supp_refs(self) -> list[tuple[int, int]]:
        return [(p.doc, p.ord) for p in self.passages if p.doc]

    @property
    def chars_sent(self) -> int:
        return sum(len(p.text) for p in self.passages) + (len(self.selection["text"]) if self.selection else 0)

    def passage(self, ord_: int, doc: int = 0) -> CtxPassage | None:
        return next((p for p in self.passages if p.ord == ord_ and p.doc == doc), None)

    def doc_info(self, doc: int) -> dict | None:
        return next((d for d in self.supp_docs if d["n"] == doc), None)

    def render_outline(self) -> str:
        return "\n".join(self.outline) if self.outline else "（未能识别章节结构）"

    def render_supp_list(self) -> str:
        return "\n".join(
            f"S{d['n']}：{d['filename']}（类别：{d['category']}，{d.get('page_count') or '?'} 页）" for d in self.supp_docs
        )

    def render_excerpts(self) -> str:
        parts = []
        for p in self.passages:
            sec = f" · {p.section_title}" if p.section_title else ""
            tag = f"P{p.ord}" if p.doc == 0 else f"S{p.doc}P{p.ord}"
            where = f"第{p.page}页" if p.doc == 0 else f"补充材料{p.doc} 第{p.page}页"
            parts.append(f"[{tag}] ({where}{sec})\n{p.text}")
        return "\n\n".join(parts) if parts else "（没有检索到相关段落）"


def keyword_terms(text: str, limit: int = 14) -> list[str]:
    seen: list[str] = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", text or ""):
        lw = w.lower().strip("-")
        if lw in STOPWORDS or lw in seen:
            continue
        seen.append(lw)
    # 缩写与长词优先
    seen.sort(key=lambda t: -len(t))
    return seen[:limit]


def cjk_ngrams(text: str, limit: int = 12) -> list[str]:
    runs = re.findall(r"[一-鿿]+", text or "")
    grams: list[str] = []
    for run in runs:
        for i in range(len(run) - 1):
            g = run[i : i + 2]
            if g not in grams:
                grams.append(g)
    return grams[:limit]


def has_cjk(text: str) -> bool:
    return bool(re.search(r"[一-鿿]", text or ""))


def _row_to_passage(r: sqlite3.Row, priority: float, reason: str) -> CtxPassage:
    return CtxPassage(
        r["ord"], r["page"], r["kind"], r["section_title"], r["text"][:MAX_PASSAGE_CHARS],
        [r["x0"], r["y0"], r["x1"], r["y1"]], priority, {reason},
    )


def build_context(
    app: AppContext,
    paper_id: str,
    *,
    question: str,
    selection: dict | None,
    task: TaskType,
    budget_chars: int = 16000,
    extra_terms: list[str] | None = None,
    force_terms: list[str] | None = None,
    include_references: bool = False,
    supplements: list[dict] | None = None,
    query_vec: list[float] | None = None,
) -> ContextPack:
    """supplements：已获用户授权的补充文档列表（materials.retrieval.list_docs 的结果）；None/空表示不使用补充材料。"""
    with app.db.session() as conn:
        paper = conn.execute("SELECT title, year, authors_json, language FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if paper is None:
            raise ApiError("not_found", "论文不存在", 404)
        sections = conn.execute(
            "SELECT title, level, page FROM sections WHERE paper_id = ? ORDER BY ord", (paper_id,)
        ).fetchall()
        outline = [f"{'  ' * (max(1, s['level']) - 1)}- {s['title']}（第{s['page']}页）" for s in sections[:70]]

        cand: dict[int, CtxPassage] = {}

        def add(p: CtxPassage) -> None:
            if p.kind == "reference" and not include_references:
                return
            if p.ord in cand:
                cand[p.ord].priority = max(cand[p.ord].priority, p.priority)
                cand[p.ord].reasons |= p.reasons
            else:
                cand[p.ord] = p

        base_sql = (
            "SELECT p.ord, p.page, p.kind, p.text, p.x0, p.y0, p.x1, p.y1, s.title AS section_title"
            "{extra} FROM passages p LEFT JOIN sections s ON s.id = p.section_id "
        )

        # 1) 选中内容及邻近段落
        sel_ords: list[int] = []
        sel_text = (selection or {}).get("text", "") if selection else ""
        if selection:
            given = [o for o in (selection.get("passage_ords") or []) if isinstance(o, int)]
            if given:
                sel_ords = given[:4]
            elif sel_text.strip():
                sel_ords = [m["ord"] for m in reader.locate(app, paper_id, sel_text, selection.get("page")) if m["score"] >= 0.5][:3]
            for o in sel_ords:
                rows = conn.execute(
                    base_sql.format(extra="") + "WHERE p.paper_id = ? AND p.ord BETWEEN ? AND ?", (paper_id, o - 1, o + 1)
                ).fetchall()
                for r in rows:
                    if r["ord"] == o:
                        add(_row_to_passage(r, 1000.0, "selection"))
                    else:
                        add(_row_to_passage(r, 400.0, "neighbor"))

        # 2) 全文检索
        terms: list[str] = []
        for t in [*(force_terms or []), *keyword_terms(question), *keyword_terms(sel_text, 8), *(extra_terms or [])]:
            t = t.strip().lower()
            if t and t not in terms:
                terms.append(t)
        terms = terms[:24]
        boost_pat = re.compile(SECTION_BOOST.get(task, ""), re.I) if SECTION_BOOST.get(task) else None
        hits: list[tuple[sqlite3.Row, float]] = []
        if terms:
            match = reader.fts_query(terms, operator="OR")
            try:
                rows = conn.execute(
                    base_sql.format(extra=", bm25(passages_fts) AS rank")
                    .replace("FROM passages p", "FROM passages_fts f JOIN passages p ON p.id = f.rowid")
                    + "WHERE passages_fts MATCH ? AND p.paper_id = ? ORDER BY rank LIMIT 30",
                    (match, paper_id),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if rows:
                best = max(-r["rank"] for r in rows) or 1.0
                hits = [(r, max(0.0, -r["rank"]) / best) for r in rows]
        if has_cjk(question) and (paper["language"] == "zh" or not hits):
            grams = cjk_ngrams(question)
            if grams:
                like = " OR ".join("p.text LIKE ?" for _ in grams)
                rows = conn.execute(
                    base_sql.format(extra="") + f"WHERE p.paper_id = ? AND ({like}) LIMIT 300",
                    (paper_id, *[f"%{g}%" for g in grams]),
                ).fetchall()
                scored = sorted(rows, key=lambda r: -sum(g in r["text"] for g in grams))[:20]
                top = sum(g in scored[0]["text"] for g in grams) if scored else 1
                hits += [(r, sum(g in r["text"] for g in grams) / max(1, top)) for r in scored]
        for r, norm in hits:
            boost = 1.35 if boost_pat and r["section_title"] and boost_pat.search(r["section_title"]) else 1.0
            if r["kind"] == "caption":
                boost *= 1.1
            add(_row_to_passage(r, 100.0 * norm * boost, "search"))

        # 2b) 语义向量检索（多语言）：与 BM25 融合；关键词与语义都命中的段落加分
        if query_vec:
            from . import embeddings

            for vh in embeddings.vector_search(app, paper_id, query_vec, k=24):
                if vh.score < 0.30:
                    continue
                r = conn.execute(base_sql.format(extra="") + "WHERE p.paper_id = ? AND p.ord = ?", (paper_id, vh.ord)).fetchone()
                if r is None:
                    continue
                norm = min(1.0, max(0.0, (vh.score - 0.30) / 0.35))
                boost = 1.35 if boost_pat and r["section_title"] and boost_pat.search(r["section_title"]) else 1.0
                p = _row_to_passage(r, 100.0 * norm * boost, "semantic")
                if r["ord"] in cand and "search" in cand[r["ord"]].reasons:
                    p.priority += 25.0
                add(p)

        # 3) 图 / 表 / 公式 / 算法 引用
        for kind, num in REF_RE.findall(f"{question} {sel_text}"):
            k = kind.lower()
            word = ("figure" if k.startswith("fig") or k == "图" else "table" if k in ("table", "表")
                    else "algorithm" if k == "algorithm" else "eq")
            patterns = {
                "figure": [f"Figure {num}%", f"Fig. {num}%", f"Fig {num}%", f"图 {num}%", f"图{num}%"],
                "table": [f"Table {num}%", f"表 {num}%", f"表{num}%"],
                "algorithm": [f"Algorithm {num}%"],
                "eq": [],
            }[word]
            for pat in patterns:
                for r in conn.execute(
                    base_sql.format(extra="") + "WHERE p.paper_id = ? AND p.kind = 'caption' AND p.text LIKE ? LIMIT 2",
                    (paper_id, pat),
                ).fetchall():
                    add(_row_to_passage(r, 600.0, "reference"))
            mention = {"figure": f"%Figure {num}%", "table": f"%Table {num}%", "algorithm": f"%Algorithm {num}%",
                       "eq": f"%({num})%"}[word]
            for r in conn.execute(
                base_sql.format(extra="") + "WHERE p.paper_id = ? AND p.kind = 'paragraph' AND p.text LIKE ? LIMIT 2",
                (paper_id, mention),
            ).fetchall():
                add(_row_to_passage(r, 150.0, "reference"))

        # 4) 全局框架：摘要
        abs_rows = conn.execute(
            base_sql.format(extra="")
            + "WHERE p.paper_id = ? AND p.kind = 'paragraph' AND (lower(s.title) LIKE '%abstract%' OR s.title LIKE '%摘要%' "
              "OR lower(p.text) LIKE 'abstract%') ORDER BY p.ord LIMIT 2",
            (paper_id,),
        ).fetchall()
        for r in abs_rows:
            add(_row_to_passage(r, 50.0, "frame"))

    # 补充材料（仅在已授权时由调用方传入）：先取，占用约 35% 的预算
    supp_passages: list[CtxPassage] = []
    if supplements:
        from ..materials import retrieval as supp_retrieval

        supp_labels = supp_retrieval.extract_labels(f"{question}\n{sel_text}")
        supp_passages = supp_retrieval.retrieve(
            app, supplements, terms=terms, labels=supp_labels, budget_chars=int(budget_chars * 0.35)
        )
        budget_chars -= sum(len(p.text) + 40 for p in supp_passages)

    # 装配：按优先级贪心填充预算，最终按文档顺序输出
    chosen: list[CtxPassage] = []
    used = 0
    for p in sorted(cand.values(), key=lambda x: -x.priority):
        cost = len(p.text) + 40
        if used + cost > budget_chars and chosen:
            continue
        chosen.append(p)
        used += cost
    chosen.sort(key=lambda p: p.ord)
    return ContextPack(
        paper_title=paper["title"], paper_year=paper["year"],
        paper_authors=json.loads(paper["authors_json"] or "[]"),
        outline=outline, passages=[*chosen, *supp_passages],
        selection=({"text": sel_text, "page": (selection or {}).get("page")} if selection else None),
        selection_ords=sel_ords, terms=terms, supp_docs=list(supplements or []),
    )
