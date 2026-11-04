"""阅读器所需的查询：大纲、按页段落、选中文字定位、论文内全文检索。"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from typing import Iterable

from .context import AppContext
from .materials.labels import supp_label_fts
from .parsing.pdf_parser import LIGATURES
from .util import ApiError, loads

PASSAGE_SQL = """
SELECT p.ord, p.page, p.kind, p.text, p.x0, p.y0, p.x1, p.y1, p.source, p.bold_json, p.math_json, s.title AS section_title
FROM passages p LEFT JOIN sections s ON s.id = p.section_id
"""


def passage_dict(row: sqlite3.Row, *, with_text: bool = True) -> dict:
    d = {
        "ord": row["ord"],
        "page": row["page"],
        "kind": row["kind"],
        "section_title": row["section_title"],
        "source": row["source"] if "source" in row.keys() else "text",
        "bbox": [row["x0"], row["y0"], row["x1"], row["y1"]],
    }
    if with_text:
        d["text"] = row["text"]
        d["bold"] = _bold_of(row)
        d["math"] = _math_of(row)
    return d


def _math_of(row: sqlite3.Row) -> list[dict]:
    raw = row["math_json"] if "math_json" in row.keys() else None
    return loads(raw, []) if raw else []


def _bold_of(row: sqlite3.Row) -> list[str]:
    raw = row["bold_json"] if "bold_json" in row.keys() else None
    return loads(raw, []) if raw else []


def _ensure_paper(conn: sqlite3.Connection, paper_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT id, has_pdf, page_count FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if row is None:
        raise ApiError("not_found", "论文不存在", 404)
    return row


def get_outline(app: AppContext, paper_id: str) -> dict:
    with app.db.session() as conn:
        _ensure_paper(conn, paper_id)
        rows = conn.execute(
            "SELECT id, title, level, page, source FROM sections WHERE paper_id = ? ORDER BY ord", (paper_id,)
        ).fetchall()
    source = rows[0]["source"] if rows else "none"
    return {
        "source": source,
        "items": [{"id": str(r["id"]), "title": r["title"], "level": r["level"], "page": r["page"]} for r in rows],
    }


def page_passages(app: AppContext, paper_id: str, page: int) -> dict:
    with app.db.session() as conn:
        _ensure_paper(conn, paper_id)
        info = conn.execute(
            "SELECT width, height FROM page_info WHERE paper_id = ? AND page = ?", (paper_id, page)
        ).fetchone()
        if info is None:
            raise ApiError("not_found", "页码超出范围", 404)
        rows = conn.execute(
            PASSAGE_SQL + " WHERE p.paper_id = ? AND p.page = ? ORDER BY p.ord", (paper_id, page)
        ).fetchall()
    return {"page": page, "width": info["width"], "height": info["height"], "items": [passage_dict(r) for r in rows]}


def full_text(app: AppContext, paper_id: str) -> dict:
    """整篇论文解析出的文字，按阅读顺序（页、段落序号）排列；不含坐标，供“文本”视图展示与复制。"""
    with app.db.session() as conn:
        _ensure_paper(conn, paper_id)
        rows = conn.execute(PASSAGE_SQL + " WHERE p.paper_id = ? ORDER BY p.ord", (paper_id,)).fetchall()
    items = [
        {"ord": r["ord"], "page": r["page"], "kind": r["kind"], "section_title": r["section_title"], "source": r["source"], "text": r["text"],
         "bbox": [r["x0"], r["y0"], r["x1"], r["y1"]], "bold": _bold_of(r), "math": _math_of(r)}
        for r in rows
    ]
    return {"count": len(items), "chars": sum(len(i["text"]) for i in items), "items": items}


def get_passage(app: AppContext, paper_id: str, ord_: int) -> dict:
    with app.db.session() as conn:
        _ensure_paper(conn, paper_id)
        row = conn.execute(PASSAGE_SQL + " WHERE p.paper_id = ? AND p.ord = ?", (paper_id, ord_)).fetchone()
    if row is None:
        raise ApiError("not_found", "段落不存在", 404)
    return passage_dict(row)


# ---- 文本归一化与匹配 ----
_LIG = re.compile("|".join(map(re.escape, LIGATURES)))
_WORD = re.compile(r"[a-z0-9]{2,}|[一-鿿]")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _LIG.sub(lambda m: LIGATURES[m.group(0)], text)
    text = re.sub(r"(\w)-\s+(\w)", r"\1\2", text)  # 行末连字符
    return re.sub(r"\s+", " ", text).strip().lower()


def words(text: str) -> set[str]:
    return set(_WORD.findall(normalize(text)))


def score_match(selection: str, passage_text: str) -> float:
    ns, npas = normalize(selection), normalize(passage_text)
    if not ns or not npas:
        return 0.0
    if ns in npas:
        return 1.0
    if len(npas) > 20 and npas in ns:
        return min(0.95, len(npas) / len(ns) + 0.3)
    sw = words(ns)
    if not sw:
        return 0.0
    return len(sw & words(npas)) / len(sw) * 0.9


def locate(app: AppContext, paper_id: str, text: str, page: int | None) -> list[dict]:
    if not text or not text.strip():
        raise ApiError("bad_request", "选中文字为空")
    with app.db.session() as conn:
        _ensure_paper(conn, paper_id)

        def scan(where: str, args: Iterable) -> list[dict]:
            rows = conn.execute(PASSAGE_SQL + where + " ORDER BY p.ord", tuple(args)).fetchall()
            out = []
            for r in rows:
                if r["kind"] == "reference":
                    continue
                sc = score_match(text, r["text"])
                if sc >= 0.4:
                    d = passage_dict(r, with_text=False)
                    d["score"] = round(sc, 3)
                    out.append(d)
            return sorted(out, key=lambda d: -d["score"])[:5]

        found: list[dict] = []
        if page:
            found = scan(" WHERE p.paper_id = ? AND p.page BETWEEN ? AND ?", (paper_id, page - 1, page + 1))
            # 优先本页：相邻页只在本页没有好结果时采用
            same = [d for d in found if d["page"] == page]
            if same and same[0]["score"] >= 0.6:
                found = same
        if not found or found[0]["score"] < 0.6:
            wider = scan(" WHERE p.paper_id = ?", (paper_id,))
            if wider and (not found or wider[0]["score"] > found[0]["score"]):
                found = wider
    return found


# ---- 论文内全文检索 ----
def fts_query(terms: Iterable[str], *, prefix: bool = False, operator: str = "AND") -> str:
    parts = []
    for t in terms:
        t = t.replace('"', " ").strip()
        if t:
            parts.append(f'"{t}"' + ("*" if prefix else ""))
    return f" {operator} ".join(parts)


def query_terms(q: str) -> list[str]:
    return [t for t in re.split(r"[\s,;，；]+", normalize(q)) if t]


def make_snippet(text: str, terms: list[str], width: int = 160) -> tuple[str, list[list[int]]]:
    low = text.lower()
    first = min((i for i in (low.find(t) for t in terms) if i >= 0), default=0)
    start = max(0, first - width // 3)
    end = min(len(text), start + width)
    snippet = text[start:end]
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    sl = snippet.lower()
    ranges: list[list[int]] = []
    for t in terms:
        pos = 0
        while t and (i := sl.find(t, pos)) >= 0:
            ranges.append([i + len(prefix), i + len(prefix) + len(t)])
            pos = i + len(t)
    ranges.sort()
    return prefix + snippet + suffix, ranges


def search(app: AppContext, paper_id: str, q: str, limit: int = 30) -> list[dict]:
    terms = query_terms(q)
    if not terms:
        return []
    with app.db.session() as conn:
        _ensure_paper(conn, paper_id)
        rows: list = []
        match = fts_query(terms, prefix=True)
        alt = supp_label_fts(q)
        if alt:  # “Supplementary Table 5” 在补充材料 PDF 里常写成 “Table S5”：两种写法都要能搜到
            match = f"({match}) OR {alt}"
        try:
            rows = conn.execute(
                "SELECT p.ord, p.page, p.kind, p.text, p.x0, p.y0, p.x1, p.y1, s.title AS section_title, "
                "bm25(passages_fts) AS rank FROM passages_fts f JOIN passages p ON p.id = f.rowid "
                "LEFT JOIN sections s ON s.id = p.section_id "
                "WHERE passages_fts MATCH ? AND p.paper_id = ? ORDER BY rank LIMIT ?",
                (match, paper_id, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if not rows:  # 中文等无分词语言，或前缀不匹配：退化为子串检索
            like = "%" + "%".join(t.replace("%", "\\%") for t in terms) + "%"
            rows = conn.execute(
                PASSAGE_SQL.replace("s.title AS section_title", "s.title AS section_title, 0.0 AS rank")
                + " WHERE p.paper_id = ? AND lower(p.text) LIKE ? ESCAPE '\\' ORDER BY p.ord LIMIT ?",
                (paper_id, like, limit),
            ).fetchall()
    out = []
    for r in rows:
        snippet, ranges = make_snippet(r["text"], terms)
        d = passage_dict(r, with_text=False)
        d.update({"snippet": snippet, "highlights": ranges, "score": round(-float(r["rank"]), 4)})
        out.append(d)
    return out
