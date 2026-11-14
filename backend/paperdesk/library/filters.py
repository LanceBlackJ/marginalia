"""文库的多特征筛选：把查询条件翻译成 SQL（所有条件是 AND 关系），并取出笔记/批注/正文/卡片的命中片段。

- 笔记、批注、卡片用 LIKE（子串匹配，中文也可用；几千条笔记下是毫秒级）；
- 论文正文用已有的 `passages_fts`（FTS5，英文按词前缀匹配）；查询里含中文时 FTS 的分词帮不上忙，改用 LIKE；
- 阅读状态：未读 = 从没打开过；读完 = 进度 ≥ 95% 或读到最后一页；其余是在读。
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from ..util import ApiError, loads

CJK = re.compile(r"[一-鿿]")
LATIN_TOKEN = re.compile(r"[A-Za-z0-9À-ɏ]+")
WORDS = re.compile(r"\S+")
READ_STATES = ("unread", "reading", "done")
PARSE_STATES = ("ready", "parsing", "failed", "metadata_only", "ocr")

DONE_SQL = "(p.read_progress >= 0.95 OR (p.page_count > 1 AND p.reading_page >= p.page_count))"
UNREAD_SQL = "(p.last_opened_at IS NULL AND p.read_progress <= 0 AND p.reading_page <= 1)"

CARD_TEXT = "lower(COALESCE(c.raw_markdown,'') || ' ' || COALESCE(c.answer_json,''))"

# 文本类筛选条件的键（有这些条件时，列表项会附带命中片段）
TEXT_KEYS = ("note_q", "annotation_q", "text_q", "card_q")


def like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def words(text: str | None) -> list[str]:
    """用户输入拆成小写词（空白分隔，最多 8 个）；每个词都必须出现（AND）。"""
    return [w.lower() for w in WORDS.findall((text or "").strip())][:8]


def fts_query(text: str) -> str | None:
    toks = LATIN_TOKEN.findall(text)
    if not toks:
        return None
    return " ".join(f'"{t}"*' for t in toks[:8])


def _all_words_like(expr: str, ws: list[str]) -> tuple[str, list[str]]:
    return " AND ".join(f"{expr} LIKE ? ESCAPE '\\'" for _ in ws), [like(w) for w in ws]


def subtree_ids(conn: sqlite3.Connection, folder_id: str) -> list[str]:
    rows = conn.execute("SELECT id, parent_id FROM folders").fetchall()
    children: dict[str | None, list[str]] = {}
    for r in rows:
        children.setdefault(r["parent_id"], []).append(r["id"])
    out, stack = [], [folder_id]
    while stack:
        cur = stack.pop()
        out.append(cur)
        stack.extend(children.get(cur, []))
    return out


def normalize(f: dict[str, Any]) -> dict[str, Any]:
    """去掉空值与非法值；非法的枚举直接报错，让前端能给出明确提示。"""
    out: dict[str, Any] = {}
    for k, v in f.items():
        if v is None or v == "" or v == []:
            continue
        out[k] = v
    if "read" in out and out["read"] not in READ_STATES:
        raise ApiError("bad_request", f"不支持的阅读状态：{out['read']}")
    if "parse" in out and out["parse"] not in PARSE_STATES:
        raise ApiError("bad_request", f"不支持的解析状态：{out['parse']}")
    if out.get("tag_mode", "any") not in ("any", "all"):
        raise ApiError("bad_request", "标签匹配方式只能是 any 或 all")
    return out


def build_where(conn: sqlite3.Connection, f: dict[str, Any]) -> tuple[list[str], list[Any]]:
    f = normalize(f)
    where: list[str] = ["COALESCE(p.role, 'paper') = 'paper'"]  # 补充材料子论文不进入文献库列表
    args: list[Any] = []

    tag_ids = list(f.get("tag_ids") or [])
    if f.get("tag"):
        tag_ids.append(f["tag"])
    if tag_ids:
        sub = "SELECT paper_id FROM kb_paper_topics WHERE status = 'confirmed' AND topic_id IN (%s)" % ",".join("?" * len(tag_ids))
        if f.get("tag_mode", "any") == "all":
            where.append(f"p.id IN ({sub} GROUP BY paper_id HAVING COUNT(DISTINCT topic_id) = {len(set(tag_ids))})")
        else:
            where.append(f"p.id IN ({sub})")
        args.extend(tag_ids)

    if f.get("folder"):
        fid = f["folder"]
        if fid == "none":
            where.append("p.folder_id IS NULL")
        else:
            ids = subtree_ids(conn, fid) if f.get("subfolders", True) else [fid]
            where.append("p.folder_id IN (%s)" % ",".join("?" * len(ids)))
            args.extend(ids)

    ws = words(f.get("q"))
    if ws:
        cols = ["title", "alias", "authors_json", "doi", "venue", "abstract", "arxiv_id", "source_folder"]
        for w in ws:  # 每个词至少出现在一个字段里
            where.append("(" + " OR ".join(f"lower(p.{c}) LIKE ? ESCAPE '\\'" for c in cols) + ")")
            args.extend([like(w)] * len(cols))

    def hits(sql: str, a: list[str]) -> None:
        """子查询命中的论文 id 只算一次（计数和取列表共用），用 json_each 传进去。"""
        where.append("p.id IN (SELECT value FROM json_each(?))")
        args.append(json.dumps([r[0] for r in conn.execute(sql, a)]))

    ws = words(f.get("note_q"))
    if ws:
        cond, a = _all_words_like("lower(COALESCE(n.text,'') || ' ' || COALESCE(n.quote,''))", ws)
        hits(f"SELECT DISTINCT n.paper_id FROM notes n WHERE {cond}", a)

    ws = words(f.get("annotation_q"))
    if ws:
        cond, a = _all_words_like("lower(COALESCE(a.text,'') || ' ' || COALESCE(a.note,''))", ws)
        hits(f"SELECT DISTINCT a.paper_id FROM annotations a WHERE {cond}", a)

    ws = words(f.get("card_q"))
    if ws:
        cond, a = _all_words_like(CARD_TEXT, ws)
        hits(f"SELECT DISTINCT c.paper_id FROM insight_cards c WHERE c.saved = 1 AND {cond}", a)

    text_q = (f.get("text_q") or "").strip()
    if text_q:
        # 正文命中的论文 id 只算一次（计数和取列表共用），用 json_each 传进去，不受变量个数限制
        fts = None if CJK.search(text_q) else fts_query(text_q)
        if fts:
            rows = conn.execute(
                "SELECT DISTINCT ps.paper_id FROM passages_fts x JOIN passages ps ON ps.id = x.rowid WHERE passages_fts MATCH ?", (fts,)
            ).fetchall()
        else:  # 中文或没有可分词的内容：退回子串匹配
            cond, a = _all_words_like("lower(ps.text)", words(text_q))
            rows = conn.execute(f"SELECT DISTINCT ps.paper_id FROM passages ps WHERE {cond}", a).fetchall()
        where.append("p.id IN (SELECT value FROM json_each(?))")
        args.append(json.dumps([r[0] for r in rows]))

    if f.get("year_from") is not None:
        where.append("p.year >= ?")
        args.append(int(f["year_from"]))
    if f.get("year_to") is not None:
        where.append("p.year <= ?")
        args.append(int(f["year_to"]))
    if f.get("author"):
        where.append("lower(p.authors_json) LIKE ? ESCAPE '\\'")
        args.append(like(str(f["author"]).strip().lower()))
    if f.get("venue"):
        where.append("lower(p.venue) LIKE ? ESCAPE '\\'")
        args.append(like(str(f["venue"]).strip().lower()))
    if f.get("language"):
        where.append("p.language = ?")
        args.append(f["language"])

    read = f.get("read")
    if read == "done":
        where.append(DONE_SQL)
    elif read == "unread":
        where.append(f"(NOT {DONE_SQL} AND {UNREAD_SQL})")
    elif read == "reading":
        where.append(f"(NOT {DONE_SQL} AND NOT {UNREAD_SQL})")

    parse = f.get("parse")
    if parse == "ocr":
        where.append("EXISTS (SELECT 1 FROM passages ps WHERE ps.paper_id = p.id AND ps.source = 'ocr')")
    elif parse:
        where.append("p.status = ?")
        args.append(parse)

    def flag(key: str, exists_sql: str) -> None:
        if f.get(key) is None:
            return
        where.append(f"{'' if f[key] else 'NOT '}EXISTS ({exists_sql})")

    flag("has_notes", "SELECT 1 FROM notes n WHERE n.paper_id = p.id")
    flag("has_annotations", "SELECT 1 FROM annotations a WHERE a.paper_id = p.id")
    flag("has_code", "SELECT 1 FROM code_repos r WHERE r.paper_id = p.id")
    flag(
        "has_supplement",
        "SELECT 1 FROM papers c WHERE c.parent_id = p.id UNION ALL "
        "SELECT 1 FROM supplementary_assets s WHERE s.paper_id = p.id AND s.child_paper_id IS NULL",
    )
    if f.get("finished") is not None:
        where.append("p.finished_at IS NOT NULL" if f["finished"] else "p.finished_at IS NULL")
    if f.get("favorite") is not None:
        where.append("p.favorite = ?")
        args.append(1 if f["favorite"] else 0)
    if f.get("recent"):
        where.append("p.last_opened_at IS NOT NULL")
    if f.get("imported_from"):
        where.append("p.imported_at >= ?")
        args.append(str(f["imported_from"]))
    if f.get("imported_to"):
        where.append("p.imported_at < ?")  # 含当天：调用方传次日零点或带时间的上界
        args.append(str(f["imported_to"]))
    return where, args


def relevance_sql(f: dict[str, Any]) -> tuple[str, list[Any]]:
    """有关键词时的相关度：标题命中 > 作者 > 摘要。没有关键词时按导入时间。"""
    ws = words(f.get("q"))
    if not ws:
        return "p.imported_at DESC", []
    parts, args = [], []
    for w in ws:
        parts.append(
            "(CASE WHEN lower(p.title) LIKE ? ESCAPE '\\' OR lower(COALESCE(p.alias,'')) LIKE ? ESCAPE '\\' THEN 5 ELSE 0 END"
            " + CASE WHEN lower(p.authors_json) LIKE ? ESCAPE '\\' THEN 3 ELSE 0 END"
            " + CASE WHEN lower(COALESCE(p.abstract,'')) LIKE ? ESCAPE '\\' THEN 2 ELSE 0 END)"
        )
        args.extend([like(w)] * 4)
    return "(" + " + ".join(parts) + ") DESC, p.imported_at DESC", args


# ---------------------------------------------------------------- 命中片段
def snippet(text: str | None, needles: list[str], width: int = 48) -> dict | None:
    """围绕第一个命中词截取片段：{text, hit:[start,end]}（hit 是 text 里的下标，供界面高亮）。"""
    text = (text or "").replace("\n", " ")
    low = text.lower()
    best: tuple[int, str] | None = None
    for n in needles:
        i = low.find(n)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, n)
    if best is None:
        return None
    i, n = best
    start, end = max(0, i - width), min(len(text), i + len(n) + width)
    lead = 1 if start > 0 else 0
    out = ("…" if lead else "") + text[start:end] + ("…" if end < len(text) else "")
    return {"text": out, "hit": [lead + i - start, lead + i - start + len(n)]}


def collect_matches(conn: sqlite3.Connection, ids: list[str], f: dict[str, Any], per_paper: int = 3) -> dict[str, dict[str, list[dict]]]:
    """为当前页的论文取出命中片段：{paper_id: {notes:[…], annotations:[…], fulltext:[…], cards:[…]}}。"""
    f = normalize(f)
    out: dict[str, dict[str, list[dict]]] = {i: {} for i in ids}
    if not ids:
        return out
    marks = ",".join("?" * len(ids))

    def add(pid: str, kind: str, item: dict) -> None:
        lst = out[pid].setdefault(kind, [])
        if len(lst) < per_paper:
            lst.append(item)

    ws = words(f.get("note_q"))
    if ws:
        cond, a = _all_words_like("lower(COALESCE(n.text,'') || ' ' || COALESCE(n.quote,''))", ws)
        for r in conn.execute(
            f"SELECT n.id, n.paper_id, n.page, n.text, n.quote FROM notes n WHERE n.paper_id IN ({marks}) AND {cond} "
            "ORDER BY n.updated_at DESC", (*ids, *a)
        ):
            sn = snippet(r["text"], ws) or snippet(r["quote"], ws)
            if sn:
                add(r["paper_id"], "notes", {"id": r["id"], "page": r["page"], "from": "笔记" if snippet(r["text"], ws) else "笔记引用的原文", **sn})
    ws = words(f.get("annotation_q"))
    if ws:
        cond, a = _all_words_like("lower(COALESCE(a.text,'') || ' ' || COALESCE(a.note,''))", ws)
        for r in conn.execute(
            f"SELECT a.id, a.paper_id, a.page, a.text, a.note FROM annotations a WHERE a.paper_id IN ({marks}) AND {cond}", (*ids, *a)
        ):
            sn = snippet(r["note"], ws) or snippet(r["text"], ws)
            if sn:
                add(r["paper_id"], "annotations", {"id": r["id"], "page": r["page"], "from": "批注" if snippet(r["note"], ws) else "高亮的原文", **sn})
    ws = words(f.get("card_q"))
    if ws:
        cond, a = _all_words_like(CARD_TEXT, ws)
        for r in conn.execute(
            f"SELECT c.id, c.paper_id, c.raw_markdown, c.answer_json FROM insight_cards c "
            f"WHERE c.saved = 1 AND c.paper_id IN ({marks}) AND {cond}", (*ids, *a)
        ):
            sn = snippet(r["raw_markdown"], ws) or snippet(_flat_json(r["answer_json"]), ws)
            if sn:
                add(r["paper_id"], "cards", {"id": r["id"], "page": None, "from": "AI 卡片", **sn})
    text_q = (f.get("text_q") or "").strip()
    if text_q:
        # 只在当前页这几十篇论文的段落里找词（不再回头做整库的 FTS 排序，那样要几百毫秒）
        needles = [t.lower() for t in (words(text_q) if CJK.search(text_q) else LATIN_TOKEN.findall(text_q))][:8]
        if needles:
            for r in conn.execute(
                f"SELECT paper_id, ord, page, text FROM passages WHERE paper_id IN ({marks}) AND kind IN ('paragraph','caption','table') "
                "ORDER BY paper_id, ord", ids,
            ):
                if len(out[r["paper_id"]].get("fulltext", [])) >= per_paper:
                    continue
                low = r["text"].lower()
                if all(n in low for n in needles):
                    sn = snippet(r["text"], needles)
                    if sn:
                        add(r["paper_id"], "fulltext", {"ord": r["ord"], "page": r["page"], "from": "正文", **sn})
    return {k: v for k, v in out.items() if v}


def _flat_json(raw: str | None) -> str:
    """AI 卡片的 JSON 内容拍平成一段文字，便于截取片段。"""
    data = loads(raw, None)
    parts: list[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, str):
            parts.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(data)
    return " ".join(parts) if parts else (raw or "")


# ---------------------------------------------------------------- 筛选项可选值与计数
def facets(conn: sqlite3.Connection) -> dict[str, Any]:
    base = "COALESCE(p.role, 'paper') = 'paper'"
    years = [
        {"year": r["year"], "count": r["n"]}
        for r in conn.execute(f"SELECT p.year, COUNT(*) AS n FROM papers p WHERE {base} AND p.year IS NOT NULL GROUP BY p.year ORDER BY p.year DESC")
    ]
    authors: dict[str, int] = {}
    for r in conn.execute(f"SELECT p.authors_json FROM papers p WHERE {base}"):
        for a in set(loads(r["authors_json"], [])):
            authors[a] = authors.get(a, 0) + 1
    venues = [
        {"venue": r["venue"], "count": r["n"]}
        for r in conn.execute(
            f"SELECT p.venue, COUNT(*) AS n FROM papers p WHERE {base} AND p.venue IS NOT NULL AND p.venue != '' "
            "GROUP BY p.venue ORDER BY n DESC, p.venue LIMIT 60"
        )
    ]
    tags = [
        {"id": r["id"], "name": r["name"], "count": r["n"]}
        for r in conn.execute(
            "SELECT t.id, t.name, COUNT(*) AS n FROM kb_topics t JOIN kb_paper_topics x ON x.topic_id = t.id AND x.status = 'confirmed' "
            f"JOIN papers p ON p.id = x.paper_id WHERE {base} GROUP BY t.id ORDER BY n DESC, t.name"
        )
    ]
    langs = [
        {"language": r["language"], "count": r["n"]}
        for r in conn.execute(
            f"SELECT p.language, COUNT(*) AS n FROM papers p WHERE {base} AND p.language IS NOT NULL GROUP BY p.language ORDER BY n DESC"
        )
    ]
    status = {r["status"]: r["n"] for r in conn.execute(f"SELECT p.status, COUNT(*) AS n FROM papers p WHERE {base} GROUP BY p.status")}
    status["ocr"] = conn.execute(
        f"SELECT COUNT(DISTINCT ps.paper_id) FROM passages ps JOIN papers p ON p.id = ps.paper_id WHERE {base} AND ps.source = 'ocr'"
    ).fetchone()[0]

    def count(extra: str) -> int:
        return conn.execute(f"SELECT COUNT(*) FROM papers p WHERE {base} AND {extra}").fetchone()[0]

    return {
        "total": count("1=1"),
        "years": years,
        "authors": [{"author": a, "count": n} for a, n in sorted(authors.items(), key=lambda kv: (-kv[1], kv[0]))[:60]],
        "venues": venues,
        "tags": tags,
        "languages": langs,
        "parse": status,
        "read": {
            "unread": count(f"NOT {DONE_SQL} AND {UNREAD_SQL}"),
            "reading": count(f"NOT {DONE_SQL} AND NOT {UNREAD_SQL}"),
            "done": count(DONE_SQL),
        },
        "flags": {
            "has_notes": count("EXISTS (SELECT 1 FROM notes n WHERE n.paper_id = p.id)"),
            "has_annotations": count("EXISTS (SELECT 1 FROM annotations a WHERE a.paper_id = p.id)"),
            "has_code": count("EXISTS (SELECT 1 FROM code_repos r WHERE r.paper_id = p.id)"),
            "has_supplement": count(
                "EXISTS (SELECT 1 FROM papers c WHERE c.parent_id = p.id) OR "
                "EXISTS (SELECT 1 FROM supplementary_assets s WHERE s.paper_id = p.id AND s.child_paper_id IS NULL)"
            ),
            "favorite": count("p.favorite = 1"),
            "finished": count("p.finished_at IS NOT NULL"),
        },
    }
