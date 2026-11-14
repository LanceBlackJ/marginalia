"""参考文献与查询结果的缓存表，以及对外的读取函数。表都带 ON DELETE CASCADE，随论文删除一起清理。"""
from __future__ import annotations

import hashlib
import re

from ..context import AppContext
from ..db import register_schema
from ..parsing.pdf_text_repair import repair_reference_passages
from ..util import ApiError, dumps, loads, utcnow
from . import citations as cit
from . import links, parse

register_schema("references", """
CREATE TABLE IF NOT EXISTS paper_references (
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  data_json TEXT NOT NULL,
  PRIMARY KEY (paper_id, idx)
);
CREATE TABLE IF NOT EXISTS reference_meta (
  paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
  style TEXT NOT NULL,
  sig TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reference_resolutions (
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  data_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (paper_id, idx)
);
""")


def _ensure_paper(app: AppContext, paper_id: str) -> None:
    with app.db.session() as conn:
        if conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
            raise ApiError("not_found", "论文不存在", 404)


def _reference_passages(app: AppContext, paper_id: str) -> tuple[list[dict], dict[int, float]]:
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT ord, page, text, x0, y0, x1, y1 FROM passages WHERE paper_id = ? AND kind = 'reference' ORDER BY ord",
            (paper_id,),
        ).fetchall()
        widths = {r["page"]: r["width"] for r in conn.execute("SELECT page, width FROM page_info WHERE paper_id = ?", (paper_id,))}
    ps = [{"ord": r["ord"], "page": r["page"], "text": r["text"], "bbox": [r["x0"], r["y0"], r["x1"], r["y1"]]} for r in rows]
    repair_reference_passages(app.paths.paper_dir(paper_id) / "original.pdf", ps)
    return ps, widths


def load_references(app: AppContext, paper_id: str) -> dict:
    """{style, count, items}：解析结果缓存在表里，参考文献段落变了（重新解析/OCR）会自动重建。"""
    _ensure_paper(app, paper_id)
    ps, widths = _reference_passages(app, paper_id)
    sig = hashlib.sha256("\x1f".join(f"{p['ord']}:{p['text']}" for p in ps).encode()).hexdigest()[:24]
    with app.db.session() as conn:
        meta = conn.execute("SELECT style, sig FROM reference_meta WHERE paper_id = ?", (paper_id,)).fetchone()
        if meta and meta["sig"] == sig:
            items = [loads(r["data_json"], {}) for r in conn.execute(
                "SELECT data_json FROM paper_references WHERE paper_id = ? ORDER BY idx", (paper_id,))]
            return {"style": meta["style"], "count": len(items), "items": items}
    style, entries = parse.split_entries(ps, widths)
    items = [parse.entry_to_dict(e) for e in entries]
    with app.db.session() as conn:
        conn.execute("DELETE FROM paper_references WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM reference_resolutions WHERE paper_id = ?", (paper_id,))  # 参考文献变了，旧的查询结果作废
        conn.executemany("INSERT INTO paper_references(paper_id, idx, data_json) VALUES (?,?,?)",
                         [(paper_id, it["index"], dumps(it)) for it in items])
        conn.execute("INSERT OR REPLACE INTO reference_meta(paper_id, style, sig, created_at) VALUES (?,?,?,?)",
                     (paper_id, style, sig, utcnow()))
    return {"style": style, "count": len(items), "items": items}


def get_reference(app: AppContext, paper_id: str, index: int) -> dict:
    for it in load_references(app, paper_id)["items"]:
        if it["index"] == index:
            return it
    raise ApiError("not_found", f"没有第 {index} 条参考文献", 404)


def _all_passages(app: AppContext, paper_id: str) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute("SELECT ord, page, kind, text, x0, y0, x1, y1 FROM passages WHERE paper_id = ? ORDER BY ord", (paper_id,)).fetchall()
    return [{"ord": r["ord"], "page": r["page"], "kind": r["kind"], "text": r["text"], "bbox": [r["x0"], r["y0"], r["x1"], r["y1"]]} for r in rows]


_LINK_CACHE: dict[tuple[str, str], list[dict]] = {}  # 读 PDF 的链接要几百毫秒：同一篇、同一份参考文献解析结果只算一次


def load_citations(app: AppContext, paper_id: str) -> dict:
    """正文里的引用标记：PDF 自带的内部超链接（指向参考文献条目的）优先，没有链接的引用再用正则补充。"""
    refs = load_references(app, paper_id)
    passages = _all_passages(app, paper_id)
    items = cit.detect_all(passages, refs["items"], refs["style"])
    pdf = app.paths.paper_dir(paper_id) / "original.pdf"
    if refs["items"] and pdf.is_file():
        key = (paper_id, hashlib.sha256(dumps([(e["index"], e["ords"]) for e in refs["items"]]).encode()).hexdigest()[:16] + str(pdf.stat().st_mtime_ns))
        if key not in _LINK_CACHE:
            try:
                _LINK_CACHE[key] = links.link_markers(pdf, passages, refs["items"], refs["style"])
            except Exception:  # noqa: BLE001  链接读不到只是退回正则识别
                _LINK_CACHE[key] = []
        if _LINK_CACHE[key]:
            items = links.merge_markers(items, _LINK_CACHE[key], {p["ord"]: p["text"] for p in passages})
    return {"style": refs["style"], "ref_count": refs["count"], "items": items}


_SENT_START = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“‘(\[])")


def citing_contexts(app: AppContext, paper_id: str, index: int, limit: int = 4) -> list[dict]:
    """本文里引用第 index 条参考文献的句子：[{ord, page, sentence}]（每个段落取包含标记的那一句，最多 limit 条）。"""
    data = load_citations(app, paper_id)
    by_ord = {p["ord"]: p for p in _all_passages(app, paper_id)}
    out: list[dict] = []
    for item in data["items"]:
        p = by_ord.get(item["ord"])
        if not p:
            continue
        for mk in item["markers"]:
            if index not in mk["refs"]:
                continue
            text = p["text"]
            starts = [0] + [m.end() for m in _SENT_START.finditer(text)]
            s = max(x for x in starts if x <= mk["start"])
            later = [m.start() for m in _SENT_START.finditer(text) if m.start() >= mk["end"]]
            e = later[0] if later else len(text)
            out.append({"ord": p["ord"], "page": p["page"], "sentence": re.sub(r"\s+", " ", text[s:e]).strip()[:600]})
            break
        if len(out) >= limit:
            break
    return out


def cached_resolution(app: AppContext, paper_id: str, index: int) -> dict | None:
    with app.db.session() as conn:
        r = conn.execute("SELECT data_json FROM reference_resolutions WHERE paper_id = ? AND idx = ?", (paper_id, index)).fetchone()
    return loads(r["data_json"], None) if r else None


def save_resolution(app: AppContext, paper_id: str, index: int, data: dict) -> None:
    with app.db.session() as conn:
        conn.execute("INSERT OR REPLACE INTO reference_resolutions(paper_id, idx, data_json, created_at) VALUES (?,?,?,?)",
                     (paper_id, index, dumps(data), utcnow()))
