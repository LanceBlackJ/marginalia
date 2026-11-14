"""重新解析已导入的论文：用当前版本的解析器重新读 PDF，原子替换派生数据，并把用户数据重新映射到新段落。

为什么需要：解析器会改进（图内数据归类、表格还原、扫描页 OCR……），但只对新导入的论文生效。
库里的旧论文要受益，就得能“重新解析”，而且不能丢用户的东西。

做法：
1. 派生数据（段落、章节、页面尺寸、图、公式）和各种缓存（公式 LaTeX、图理解、表格识别）在**一个事务里**整体替换；
   解析/OCR 在事务之外做，失败或取消时库里的旧数据完全不变；
2. 事务内先把旧数据存成快照（reparse_backup，保留最近一次，7 天或下次重新解析前），之后可以一键撤销；
3. 引用旧段落序号（ord）的用户数据按“页码 + 位置框重叠 + 文本相似度”（笔记另按引用原文）映射到新序号：
   笔记、研究卡片的证据、整体分析、思维导图、研究提示、知识条目/候选、复现评估与目标、论文↔代码关联、阅读位置……
   映射不上的**不删除**：保留原文引用和页码，标为“位置已失效”；
4. 公式/图/表格的识别缓存按页码与位置框重新关联到新的公式/图编号，对不上的丢弃并让它们重新识别；
5. 语义索引按新段落重建；翻译缓存按文本哈希，天然保留。
"""
from __future__ import annotations

import gzip
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from ..context import AppContext
from ..db import register_column, register_schema
from ..jobs import JobCancelled, JobContext, JobManager
from ..parsing import ocr
from ..parsing.pdf_parser import PARSER_VERSION, ParsedDoc, PdfError, parse_pdf
from ..util import ApiError, utcnow
from .importer import _store_parsed

JOB_TYPE = "reparse_paper"
BACKUP_DAYS = 7

register_schema("reparse", """
CREATE TABLE IF NOT EXISTS reparse_backup (
  paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  from_version INTEGER NOT NULL,      -- 快照里旧数据的解析版本
  to_version INTEGER NOT NULL,
  payload BLOB NOT NULL,              -- gzip(JSON)：旧的派生数据与缓存 + 用户数据被改写之前的值
  stats_json TEXT NOT NULL DEFAULT '{}'
);
""")
# 笔记的引用位置在重新解析后对不上时标为失效（保留原文引用与页码，不删除）
register_column("reparse", "notes", "stale", "INTEGER NOT NULL DEFAULT 0")
# 书签可以精确到正文的某一段（右键添加）：记段落序号与这段开头的原文；重新解析后按原文找回，找不到就标为位置已失效
register_column("reparse", "bookmarks", "passage_ord", "INTEGER")
register_column("reparse", "bookmarks", "quote", "TEXT")
register_column("reparse", "bookmarks", "stale", "INTEGER NOT NULL DEFAULT 0")

DERIVED = ("sections", "passages", "page_info", "figures", "equations")
CACHES = ("equation_latex", "figure_analysis", "table_analysis")

# 引用段落序号的 JSON 列：(表, WHERE 条件（一个 paper_id 参数）, 列)
JSON_TARGETS = (
    ("insight_cards", "paper_id = ?", ("evidence_json", "selection_json")),
    ("paper_analysis", "paper_id = ?", ("content_json", "evidence_json")),
    ("mindmap_nodes", "paper_id = ?", ("evidence_json",)),
    ("paper_findings", "paper_id = ?", ("evidence_json",)),
    ("kb_items", "source_paper_id = ?", ("evidence_json",)),
    ("kb_candidates", "paper_id = ?", ("evidence_json",)),
    ("repro_assessments", "paper_id = ?", ("report_json",)),
    ("repro_steps", "plan_id IN (SELECT id FROM repro_plans WHERE paper_id = ?)", ("evidence_json",)),
    ("paper_code_links", "paper_id = ?", ("module_json", "basis_json")),
)


# 证据列本身就是一串段落序号（[3, 7]，不是带键的对象）的表
ROOT_ORD_LISTS = {("paper_findings", "evidence_json")}


def register(manager: JobManager) -> None:
    manager.register(JOB_TYPE, handle_reparse)


# ---------------------------------------------------------------- 映射（纯函数）
_NON_WORD = re.compile(r"[^\w一-鿿]+")


def _norm(s: str | None) -> str:
    return " ".join(_NON_WORD.sub(" ", (s or "").lower()).split())


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _bbox(row) -> tuple[float, float, float, float]:
    return (row["x0"], row["y0"], row["x1"], row["y1"])


def map_passages(old: list[dict], new: list[dict]) -> dict[int, int]:
    """旧段落序号 → 新段落序号。只在同一页内比较：位置框重叠（含包含关系）与文本相似度（词集合 Jaccard、整段包含）。
    一个旧段落被拆成几段时取重叠最大的那段；映射不上的不出现在结果里。"""
    by_page: dict[int, list] = defaultdict(list)
    for n in new:
        nn = _norm(n["text"])
        by_page[n["page"]].append((n, set(nn.split()), nn, _bbox(n)))
    out: dict[int, int] = {}
    for o in old:
        on = _norm(o["text"])
        ot = set(on.split())
        ob = _bbox(o)
        oa = _area(ob)
        best, best_score = None, 0.0
        for n, nt, nn, nb in by_page.get(o["page"], ()):
            na = _area(nb)
            inter = _inter(ob, nb)
            union = oa + na - inter
            iou = inter / union if union > 0 else 0.0
            geo = inter / min(oa, na) if min(oa, na) > 0 else 0.0
            jac = len(ot & nt) / len(ot | nt) if (ot and nt) else 0.0
            cont = 1.0 if (len(on) >= 20 and len(nn) >= 20 and (on in nn or nn in on)) else 0.0
            score = 0.45 * max(iou, 0.8 * geo) + 0.55 * max(jac, cont)
            ok = score >= 0.5 or jac >= 0.85 or (cont and len(on) >= 40)
            if ok and score > best_score:
                best, best_score = n, score
        if best is not None:
            out[o["ord"]] = best["ord"]
    return out


def match_regions(old: list[dict], new: list[dict], label_key: str) -> dict[int, int]:
    """旧图/公式 id → 新 id：同一页、位置框 IoU（或旧框被新框包含）最高者一对一配对，编号/标签相同加分。"""
    pairs = []
    for o in old:
        ob = _bbox(o)
        oa = _area(ob)
        for n in new:
            if n["page"] != o["page"]:
                continue
            nb = _bbox(n)
            inter = _inter(ob, nb)
            union = oa + _area(nb) - inter
            iou = inter / union if union > 0 else 0.0
            geo = inter / oa if oa > 0 else 0.0
            score = max(iou, 0.9 * geo if geo >= 0.6 else 0.0)
            if o.get(label_key) and o.get(label_key) == n.get(label_key):
                score += 0.2
            if score >= 0.3:
                pairs.append((score, o["id"], n["id"]))
    pairs.sort(reverse=True)
    used_o: set[int] = set()
    used_n: set[int] = set()
    out: dict[int, int] = {}
    for _s, oid, nid in pairs:
        if oid in used_o or nid in used_n:
            continue
        used_o.add(oid)
        used_n.add(nid)
        out[oid] = nid
    return out


def find_by_quote(quote: str | None, page: int | None, new: list[dict]) -> int | None:
    """按引用原文在新段落里找：先找同一页，再找相邻页（长引用可能跨页被重新切分）。"""
    q = _norm(quote)
    if len(q) < 8:
        return None
    q = q[:160]
    for dp in (0, -1, 1):
        for n in new:
            if page is not None and n["page"] != page + dp:
                continue
            if q in _norm(n["text"]):
                return n["ord"]
    if page is None:
        return None
    return None


def walk_json(obj, om: dict[int, int], fm: dict[int, int], em: dict[int, int], st: dict, pid: str):
    """把 JSON 里的段落序号（ord / passage_ord / ords / evidence_ords / passage_ords）与图、公式 id 映射到新值。
    映射不上的：dict 里的 ord 置为 None 并加 stale=True、记下旧值；列表里的从列表移除并记到 stale_<键>。"""
    if isinstance(obj, list):
        return [walk_json(x, om, fm, em, st, pid) for x in obj]
    if not isinstance(obj, dict):
        return obj
    foreign = "paper_id" in obj and obj["paper_id"] not in (None, pid)  # 引用的是别的论文：不动
    has_ctx = any(k in obj for k in ("page", "quote", "ref", "section_title", "paper_id"))
    out: dict = {}
    for k, v in obj.items():
        if foreign:
            out[k] = v
        elif k in ("ords", "evidence_ords", "passage_ords") and isinstance(v, list):
            kept, lost = [], []
            for o in v:
                if isinstance(o, int) and not isinstance(o, bool):
                    m = om.get(o)
                    (kept if m is not None else lost).append(m if m is not None else o)
                else:
                    kept.append(o)
            seen: set = set()
            out[k] = [x for x in kept if not (x in seen or seen.add(x))]
            st["mapped"] += len(out[k])
            if lost:
                out["stale_" + k] = lost
                st["stale"] += len(lost)
        elif k in ("figure_ids", "equation_ids") and isinstance(v, list):
            mp = fm if k == "figure_ids" else em
            kept = [mp[x] for x in v if isinstance(x, int) and x in mp]
            lost = [x for x in v if isinstance(x, int) and x not in mp]
            out[k] = kept
            if lost:
                out["stale_" + k] = lost
        elif k in ("ord", "passage_ord") and isinstance(v, int) and not isinstance(v, bool) and (k == "passage_ord" or has_ctx):
            m = om.get(v)
            if m is None:
                out[k] = None
                out["stale"] = True
                out["stale_" + k] = v
                st["stale"] += 1
            else:
                out[k] = m
                st["mapped"] += 1
        else:
            out[k] = walk_json(v, om, fm, em, st, pid)
    return out


# ---------------------------------------------------------------- 数据库辅助
def _rows(conn: sqlite3.Connection, sql: str, *params) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict]) -> None:
    for r in rows:
        cols = list(r.keys())
        conn.execute(f"INSERT INTO {table}({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [r[c] for c in cols])


def _has_cols(conn: sqlite3.Connection, table: str, cols) -> bool:
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return bool(have) and all(c in have for c in cols)


def _backup_row(conn: sqlite3.Connection, paper_id: str):
    return conn.execute("SELECT * FROM reparse_backup WHERE paper_id = ?", (paper_id,)).fetchone()


def _age_days(ts: str | None) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat((ts or "").replace("Z", "+00:00"))).total_seconds() / 86400
    except ValueError:
        return 0.0


def _purge_expired(conn: sqlite3.Connection, paper_id: str) -> None:
    b = _backup_row(conn, paper_id)
    if b is not None and _age_days(b["created_at"]) > BACKUP_DAYS:
        conn.execute("DELETE FROM reparse_backup WHERE paper_id = ?", (paper_id,))


# ---------------------------------------------------------------- 用户数据重映射
def _remap_ord_rows(conn, table: str, where: str, params: tuple, ord_col: str, quote_col: str | None, page_col: str | None,
                    stale_col: str | None, om: dict[int, int], new_passages: list[dict], journal: list, st: dict,
                    skip: set | None = None) -> None:
    """表里有一个“段落序号”标量列（笔记、复现目标）：先按引用原文找，再按序号映射；都对不上：序号置空并标失效（有失效列时）。"""
    cols = [ord_col] + [c for c in (quote_col, page_col, stale_col) if c]
    if not _has_cols(conn, table, cols):
        return
    sel = ", ".join(["rowid AS _rid", *cols])
    for r in conn.execute(f"SELECT {sel} FROM {table} WHERE {where}", params).fetchall():
        if skip and (table, r["_rid"]) in skip:
            continue
        old_ord = r[ord_col]
        quote = r[quote_col] if quote_col else None
        if old_ord is None and not (quote or "").strip():
            continue
        new_ord = find_by_quote(quote, r[page_col] if page_col else None, new_passages)
        if new_ord is None and old_ord is not None:
            new_ord = om.get(old_ord)
        st["total"] += 1
        if new_ord is not None:
            st["mapped"] += 1
            if new_ord != old_ord or (stale_col and r[stale_col]):
                journal.append([table, r["_rid"], ord_col, old_ord])
                sets, args = [f"{ord_col} = ?"], [new_ord]
                if stale_col:
                    journal.append([table, r["_rid"], stale_col, r[stale_col]])
                    sets.append(f"{stale_col} = 0")
                conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE rowid = ?", [*args, r["_rid"]])
        elif old_ord is not None:  # 以前有位置、现在对不上：保留引用原文与页码，标为位置已失效
            st["stale"] += 1
            journal.append([table, r["_rid"], ord_col, old_ord])
            sets, args = [f"{ord_col} = NULL"], []
            if stale_col:
                journal.append([table, r["_rid"], stale_col, r[stale_col]])
                sets.append(f"{stale_col} = 1")
            conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE rowid = ?", [*args, r["_rid"]])


def _remap_json_targets(conn, pid: str, om, fm, em, journal: list, stats: dict, skip: set | None = None) -> None:
    for table, where, cols in JSON_TARGETS:
        if not _has_cols(conn, table, cols):
            continue
        st = stats.setdefault(table, {"mapped": 0, "stale": 0})
        for r in conn.execute(f"SELECT rowid AS _rid, {', '.join(cols)} FROM {table} WHERE {where}", (pid,)).fetchall():
            if skip and (table, r["_rid"]) in skip:
                continue
            for col in cols:
                raw = r[col]
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                one = {"mapped": 0, "stale": 0}
                if (table, col) in ROOT_ORD_LISTS and isinstance(obj, list):
                    new = [om[x] for x in obj if isinstance(x, int) and x in om]
                    one["mapped"] = len(new)
                    one["stale"] = len(obj) - len(new)
                else:
                    new = walk_json(obj, om, fm, em, one, pid)
                if not (one["mapped"] or one["stale"]):
                    continue
                new_raw = json.dumps(new, ensure_ascii=False)
                st["mapped"] += one["mapped"]
                st["stale"] += one["stale"]
                if new_raw != raw:
                    journal.append([table, r["_rid"], col, raw])
                    conn.execute(f"UPDATE {table} SET {col} = ? WHERE rowid = ?", (new_raw, r["_rid"]))


def _remap_reading_state(conn, pid: str, om: dict[int, int], journal: list) -> None:
    row = conn.execute("SELECT rowid AS _rid, reading_state_json FROM papers WHERE id = ?", (pid,)).fetchone()
    if row is None or not row["reading_state_json"]:
        return
    try:
        state = json.loads(row["reading_state_json"])
    except ValueError:
        return
    anchor = state.get("textAnchor") if isinstance(state, dict) else None
    if not isinstance(anchor, int):
        return
    new = om.get(anchor)
    if new == anchor:
        return
    journal.append(["papers", row["_rid"], "reading_state_json", row["reading_state_json"]])
    if new is None:
        state.pop("textAnchor", None)  # 对不上就回到页码位置，不把位置乱指到别的段落
    else:
        state["textAnchor"] = new
    conn.execute("UPDATE papers SET reading_state_json = ? WHERE rowid = ?", (json.dumps(state, ensure_ascii=False), row["_rid"]))


def _restore_cache_rows(conn, pid: str, caches: dict, om: dict[int, int], fm: dict[int, int], em: dict[int, int], new_fig_ids: set,
                        new_eq_ids: set, stats: dict) -> None:
    """公式 LaTeX、图理解、表格识别的缓存：按映射重新关联到新的 id / 段落序号；对不上的丢弃（会重新识别）。"""
    st = {"mapped": 0, "stale": 0}
    eq = {"kept": 0, "dropped": 0}
    for r in caches.get("equation_latex", []):
        nid = em.get(r["equation_id"])
        if nid is None or nid not in new_eq_ids:
            eq["dropped"] += 1
            continue
        conn.execute(
            "INSERT OR REPLACE INTO equation_latex(equation_id, paper_id, latex, confidence, status, warnings_json, error, model, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (nid, pid, r["latex"], r["confidence"], r["status"], r["warnings_json"], r["error"], r["model"], r["created_at"]),
        )
        eq["kept"] += 1
    fg = {"kept": 0, "dropped": 0}
    for r in caches.get("figure_analysis", []):
        nid = fm.get(r["figure_id"])
        if nid is None or nid not in new_fig_ids:
            fg["dropped"] += 1
            continue
        row = dict(r)
        row["figure_id"] = nid
        for col in ("data_json", "mapping_json", "analysis_json", "evidence_json", "verification_json"):
            if row.get(col):
                try:
                    row[col] = json.dumps(walk_json(json.loads(row[col]), om, fm, em, st, pid), ensure_ascii=False)
                except ValueError:
                    pass
        if row.get("derived_ord") is not None:
            row["derived_ord"] = om.get(row["derived_ord"])
        cols = list(row.keys())
        conn.execute(f"INSERT OR REPLACE INTO figure_analysis({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [row[c] for c in cols])
        fg["kept"] += 1
    tb = {"kept": 0, "dropped": 0}
    rank = {"ready": 3, "failed": 2}
    chosen: dict[int, dict] = {}
    for r in caches.get("table_analysis", []):
        nid = om.get(r["caption_ord"])
        if nid is None or r["status"] in ("pending", "running"):
            tb["dropped"] += 1
            continue
        prev = chosen.get(nid)
        if prev is None or rank.get(r["status"], 0) > rank.get(prev["status"], 0):
            if prev is not None:
                tb["dropped"] += 1
            chosen[nid] = r
        else:
            tb["dropped"] += 1
    for nid, r in chosen.items():
        row = dict(r)
        row["caption_ord"] = nid
        if row.get("data_json"):
            try:
                row["data_json"] = json.dumps(walk_json(json.loads(row["data_json"]), om, fm, em, st, pid), ensure_ascii=False)
            except ValueError:
                pass
        cols = list(row.keys())
        conn.execute(f"INSERT OR REPLACE INTO table_analysis({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [row[c] for c in cols])
        tb["kept"] += 1
    stats["equation_latex"], stats["figure_analysis"], stats["table_analysis"] = eq, fg, tb


def _looks_like_filename(title: str) -> bool:
    t = title.strip()
    if not t:
        return True
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,40}", t):  # 有空格或汉字的是真标题（或用户自己起的名字）
        return False
    return t.lower() in {"paper", "main", "article", "untitled", "document", "download", "file"} or any(c.isdigit() for c in t)


def _refill_metadata(conn, paper_id: str, parsed: ParsedDoc) -> None:
    """重新解析时补全题录：只补空缺（或导入时拿文件名顶替的标题），来自 Crossref 或用户改过的值不动。"""
    row = conn.execute("SELECT title, authors_json, year, venue, doi, abstract, metadata_source FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if row is None or row["metadata_source"] != "pdf":
        return
    md = parsed.metadata
    sets: dict[str, object] = {}
    if md.title and _looks_like_filename(row["title"] or "") and md.title.strip() != (row["title"] or "").strip():
        sets["title"] = md.title.strip()
    if md.authors and not json.loads(row["authors_json"] or "[]"):
        sets["authors_json"] = json.dumps(md.authors, ensure_ascii=False)
    for col, val in (("year", md.year), ("venue", md.venue), ("doi", md.doi), ("abstract", md.abstract)):
        if val and not row[col]:
            sets[col] = val
    if md.venue and row["venue"] and row["venue"] != md.venue and row["venue"].startswith(md.venue):
        sets["venue"] = md.venue  # 旧版把“刊名, 年, 卷(期): 页码”整串当成来源，只留刊名
    if sets:
        conn.execute(f"UPDATE papers SET {', '.join(k + ' = ?' for k in sets)} WHERE id = ?", (*sets.values(), paper_id))


# ---------------------------------------------------------------- 应用一次重新解析（单篇，一个事务）
def _apply(app: AppContext, paper_id: str, parsed: ParsedDoc) -> dict:
    """在一个事务里整体替换派生数据并重映射用户数据；任何一步抛异常都整体回滚，旧数据完全不变。"""
    stats: dict = {}
    warning = "；".join(parsed.warnings) or None
    with app.db.session() as conn:
        _purge_expired(conn, paper_id)
        old = {t: _rows(conn, f"SELECT * FROM {t} WHERE paper_id = ?", paper_id) for t in DERIVED}
        caches = {t: _rows(conn, f"SELECT * FROM {t} WHERE paper_id = ?", paper_id) for t in CACHES if _has_cols(conn, t, ("paper_id",))}
        prow = conn.execute(
            "SELECT parse_error, language, page_count, parser_version, reading_state_json FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        if prow is None:
            raise ApiError("not_found", "论文不存在", 404)
        journal: list = []

        # 1) 替换派生数据（图/公式被删除时，它们的缓存会按外键级联删掉——上面已经取出了快照）
        for t in ("passages", "sections", "page_info", "figures", "equations"):
            conn.execute(f"DELETE FROM {t} WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM table_analysis WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM paper_glossary WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM passage_insights WHERE paper_id = ?", (paper_id,))  # 段落序号变了：按段落缓存的学习分析作废
        conn.execute("DELETE FROM passage_embeddings WHERE paper_id = ?", (paper_id,))  # 语义索引按新段落重建
        for t in ("paper_references", "reference_meta", "reference_resolutions"):  # 参考文献解析与匹配缓存：按新段落重新生成
            if _has_cols(conn, t, ("paper_id",)):
                conn.execute(f"DELETE FROM {t} WHERE paper_id = ?", (paper_id,))
        _store_parsed(conn, paper_id, parsed)
        conn.execute(
            "UPDATE papers SET language = ?, parse_error = ?, page_count = ? WHERE id = ?",
            (parsed.language, warning, parsed.page_count, paper_id),
        )

        _refill_metadata(conn, paper_id, parsed)

        # 2) 新旧对应
        new_passages = _rows(conn, "SELECT ord, page, kind, text, x0, y0, x1, y1 FROM passages WHERE paper_id = ?", paper_id)
        new_figs = _rows(conn, "SELECT id, page, x0, y0, x1, y1, label FROM figures WHERE paper_id = ?", paper_id)
        new_eqs = _rows(conn, "SELECT id, page, x0, y0, x1, y1, number FROM equations WHERE paper_id = ?", paper_id)
        om = map_passages(old["passages"], new_passages)
        fm = match_regions(old["figures"], new_figs, "label")
        em = match_regions(old["equations"], new_eqs, "number")

        # 3) 缓存重新关联
        _restore_cache_rows(conn, paper_id, caches, om, fm, em, {f["id"] for f in new_figs}, {e["id"] for e in new_eqs}, stats)

        # 4) 用户数据重映射
        notes = {"total": 0, "mapped": 0, "stale": 0}
        _remap_ord_rows(conn, "notes", "paper_id = ?", (paper_id,), "passage_ord", "quote", "page", "stale", om, new_passages, journal, notes)
        stats["notes"] = notes
        bms = {"total": 0, "mapped": 0, "stale": 0}
        _remap_ord_rows(conn, "bookmarks", "paper_id = ?", (paper_id,), "passage_ord", "quote", "page", "stale", om, new_passages, journal, bms)
        stats["bookmarks"] = bms
        targets ={"total": 0, "mapped": 0, "stale": 0}
        _remap_ord_rows(conn, "repro_targets", "paper_id = ?", (paper_id,), "ord", "quote", "page", None, om, new_passages, journal, targets)
        stats["repro_targets"] = targets
        _remap_json_targets(conn, paper_id, om, fm, em, journal, stats)
        _remap_reading_state(conn, paper_id, om, journal)

        # 5) 快照（和上面的改动在同一个事务里：要么都成功，要么都没发生）
        ok = sum(v for k, v in ((k, stats[k].get("mapped", 0)) for k in stats if isinstance(stats[k], dict)) if k not in CACHES)
        stats["passages"] = {"old": len(old["passages"]), "new": len(new_passages), "mapped": len(om),
                             "unmapped": len(old["passages"]) - len(om)}
        stats["figures"] = {"old": len(old["figures"]), "new": len(new_figs), "matched": len(fm)}
        stats["equations"] = {"old": len(old["equations"]), "new": len(new_eqs), "matched": len(em)}
        stats["refs_remapped"] = ok
        payload = {
            "paper": dict(prow), "tables": old, "caches": caches, "journal": journal,
        }
        blob = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        conn.execute(
            "INSERT OR REPLACE INTO reparse_backup(paper_id, created_at, from_version, to_version, payload, stats_json) VALUES (?,?,?,?,?,?)",
            (paper_id, utcnow(), prow["parser_version"] or 1, PARSER_VERSION, blob, json.dumps(stats, ensure_ascii=False)),
        )
    return stats


# ---------------------------------------------------------------- 撤销
def undo(app: AppContext, paper_id: str) -> dict:
    """回滚到最近一次重新解析之前：旧的派生数据与缓存原样恢复（保留原来的 id），用户数据恢复为改写前的值；
    重新解析之后新建的笔记、卡片等按“新→旧”的映射挪回旧段落。"""
    with app.db.session() as conn:
        b = _backup_row(conn, paper_id)
        if b is None or _age_days(b["created_at"]) > BACKUP_DAYS:
            if b is not None:
                conn.execute("DELETE FROM reparse_backup WHERE paper_id = ?", (paper_id,))
            raise ApiError("not_found", "没有可以撤销的重新解析记录", 404)
        payload = json.loads(gzip.decompress(b["payload"]).decode("utf-8"))
        old = payload["tables"]
        cur_passages = _rows(conn, "SELECT ord, page, kind, text, x0, y0, x1, y1 FROM passages WHERE paper_id = ?", paper_id)
        cur_figs = _rows(conn, "SELECT id, page, x0, y0, x1, y1, label FROM figures WHERE paper_id = ?", paper_id)
        cur_eqs = _rows(conn, "SELECT id, page, x0, y0, x1, y1, number FROM equations WHERE paper_id = ?", paper_id)
        rev_om = map_passages(cur_passages, old["passages"])
        rev_fm = match_regions(cur_figs, old["figures"], "label")
        rev_em = match_regions(cur_eqs, old["equations"], "number")

        # 1) 用户数据：被改写过的行恢复成原值；之后新增或改过的行按“新→旧”映射
        covered: set = set()
        for table, rid, col, oldv in payload["journal"]:
            covered.add((table, rid))
            conn.execute(f"UPDATE {table} SET {col} = ? WHERE rowid = ?", (oldv, rid))
        scratch: list = []
        _remap_ord_rows(conn, "notes", "paper_id = ?", (paper_id,), "passage_ord", "quote", "page", "stale", rev_om, old["passages"],
                        scratch, {"total": 0, "mapped": 0, "stale": 0}, skip=covered)
        _remap_ord_rows(conn, "bookmarks", "paper_id = ?", (paper_id,), "passage_ord", "quote", "page", "stale", rev_om, old["passages"],
                        scratch, {"total": 0, "mapped": 0, "stale": 0}, skip=covered)
        _remap_ord_rows(conn, "repro_targets", "paper_id = ?", (paper_id,), "ord", "quote", "page", None, rev_om, old["passages"],
                        scratch,{"total": 0, "mapped": 0, "stale": 0}, skip=covered)
        _remap_json_targets(conn, paper_id, rev_om, rev_fm, rev_em, scratch, {}, skip=covered)

        # 2) 派生数据与缓存：删掉现在的，按原 id 把快照里的放回去
        for t in ("passages", "sections", "page_info", "figures", "equations"):
            conn.execute(f"DELETE FROM {t} WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM table_analysis WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM paper_glossary WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM passage_insights WHERE paper_id = ?", (paper_id,))  # 段落序号变了：按段落缓存的学习分析作废
        conn.execute("DELETE FROM passage_embeddings WHERE paper_id = ?", (paper_id,))
        for t in ("paper_references", "reference_meta", "reference_resolutions"):
            if _has_cols(conn, t, ("paper_id",)):
                conn.execute(f"DELETE FROM {t} WHERE paper_id = ?", (paper_id,))
        for t in ("sections", "page_info", "passages", "figures", "equations"):
            _insert_rows(conn, t, old[t])
        for t in CACHES:
            _insert_rows(conn, t, payload["caches"].get(t, []))
        p = payload["paper"]
        conn.execute(
            "UPDATE papers SET language = ?, parse_error = ?, page_count = ?, parser_version = ? WHERE id = ?",
            (p["language"], p["parse_error"], p["page_count"], p["parser_version"] or 1, paper_id),
        )
        conn.execute("DELETE FROM reparse_backup WHERE paper_id = ?", (paper_id,))
    _after(app, [paper_id], reparsed=False)
    return {"ok": True, "restored": {"passages": len(old["passages"]), "figures": len(old["figures"]),
                                      "equations": len(old["equations"]), "journal_rows": len(payload["journal"])}}


# ---------------------------------------------------------------- 状态
def status(app: AppContext, paper_id: str) -> dict:
    with app.db.session() as conn:
        row = conn.execute("SELECT has_pdf, parser_version FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if row is None:
            raise ApiError("not_found", "论文不存在", 404)
        _purge_expired(conn, paper_id)
        b = _backup_row(conn, paper_id)
        job = conn.execute(
            "SELECT id FROM jobs WHERE paper_id = ? AND type = ? AND status IN ('pending','running') ORDER BY created_at DESC LIMIT 1",
            (paper_id, JOB_TYPE),
        ).fetchone()
    version = row["parser_version"] or 1
    backup = None
    if b is not None:
        created = datetime.fromisoformat(b["created_at"].replace("Z", "+00:00"))
        backup = {
            "created_at": b["created_at"], "from_version": b["from_version"], "to_version": b["to_version"],
            "expires_at": (created + timedelta(days=BACKUP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "stats": json.loads(b["stats_json"] or "{}"),
        }
    return {
        "can_reparse": bool(row["has_pdf"]), "parser_version": version, "current_version": PARSER_VERSION,
        "outdated": bool(row["has_pdf"]) and version < PARSER_VERSION, "active_job_id": job["id"] if job else None, "backup": backup,
    }


def enqueue(app: AppContext, paper_id: str, force: bool = False) -> str:
    st = status(app, paper_id)  # 论文不存在时抛 404
    if not st["can_reparse"]:
        raise ApiError("bad_request", "这篇论文没有 PDF 文件，无法重新解析")
    if st["active_job_id"]:
        return st["active_job_id"]
    with app.db.session() as conn:
        row = conn.execute("SELECT title, role FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if row["role"] == "supplement":
            raise ApiError("bad_request", "补充材料随主论文一起重新解析：请在主论文上操作")
    return app.jobs.submit(JOB_TYPE, f"重新解析：{(row['title'] or '')}", {"paper_id": paper_id, "force": force}, paper_id)


# ---------------------------------------------------------------- 任务
def _children(app: AppContext, paper_id: str) -> list[str]:
    with app.db.session() as conn:
        return [r["id"] for r in conn.execute(
            "SELECT id FROM papers WHERE parent_id = ? AND role = 'supplement' AND has_pdf = 1", (paper_id,)).fetchall()]


def _cancel_others(ctx: JobContext, ids: list[str]) -> None:
    """重新解析期间，别的后台任务（公式/表格识别、语义索引、翻译、分析……）还在往旧段落序号上写东西：先取消它们，等它们停下。"""
    import time

    app = ctx.app
    with app.db.session() as conn:
        active = [r["id"] for pid in ids for r in conn.execute(
            "SELECT id FROM jobs WHERE paper_id = ? AND id != ? AND status IN ('pending','running','waiting_approval','paused')",
            (pid, ctx.job_id)).fetchall()]
    for jid in active:
        try:
            app.jobs.cancel(jid)
        except Exception:  # noqa: BLE001
            pass
    deadline = time.time() + 8
    while active and time.time() < deadline:
        with app.db.session() as conn:
            active = [j for j in active if conn.execute(
                "SELECT 1 FROM jobs WHERE id = ? AND status IN ('pending','running')", (j,)).fetchone()]
        if active:
            time.sleep(0.3)


def _parse_for(ctx: JobContext, paper_id: str, base: float, span: float, force: bool) -> tuple[ParsedDoc, dict]:
    app = ctx.app
    path = app.paths.paper_dir(paper_id) / "original.pdf"
    if not path.exists():
        raise ApiError("not_found", "找不到这篇论文的 PDF 文件，无法重新解析")
    with app.db.session() as conn:
        old_n = conn.execute("SELECT COUNT(*) FROM passages WHERE paper_id = ?", (paper_id,)).fetchone()[0]
        old_ocr = conn.execute("SELECT COUNT(*) FROM passages WHERE paper_id = ? AND source = 'ocr'", (paper_id,)).fetchone()[0]
    ctx.progress(base + 0.05 * span, "解析 PDF …")
    from ..vision import layout_ai

    from . import layout_boxes

    from ..parsing.parse_best import parse_with_mineru

    hints = layout_ai.load_hints(app, paper_id)  # 视觉模型对每页的版面划分（没有就走规则）
    try:
        parsed = parse_with_mineru(path, lambda m: ctx.progress(base + 0.05 * span, m))  # MinerU 可用就用它，否则 None
    except PdfError as exc:
        raise ApiError("parse_failed", f"重新解析失败，原有数据保持不变：{exc}") from exc
    if parsed is None:
        model_boxes = layout_boxes.ensure(app, paper_id, path, lambda m: ctx.progress(base + 0.05 * span, m), ctx.check_cancelled)  # 本地版面模型：表格/图/公式的框
        try:
            parsed = parse_pdf(path, layout_hints=hints, model_boxes=model_boxes)
        except PdfError as exc:
            raise ApiError("parse_failed", f"重新解析失败，原有数据保持不变：{exc}") from exc
    if parsed.needs_ocr:
        if not ocr.available():
            if old_ocr:
                raise ApiError("not_configured", "这篇论文原来是用本地 OCR 识别的，但现在没有可用的 OCR 组件，无法重新解析；原有数据保持不变。")
        else:
            spans = {}
            for i, pno in enumerate(parsed.needs_ocr, 1):
                ctx.check_cancelled()
                spans[pno] = ocr.ocr_page(path, pno)
                ctx.progress(base + (0.05 + 0.75 * i / len(parsed.needs_ocr)) * span, f"识别扫描页 {i}/{len(parsed.needs_ocr)}")
            parsed = parse_pdf(path, ocr_pages=spans, layout_hints=hints, model_boxes=model_boxes)
    if old_n and not parsed.passages:
        raise ApiError("parse_failed", "新的解析没有得到任何文字，原有数据保持不变。")
    if not force and old_n >= 20 and len(parsed.passages) < 0.4 * old_n:
        raise ApiError(
            "parse_suspicious",
            f"新的解析只得到 {len(parsed.passages)} 个段落（原来 {old_n} 个），差别太大，原有数据保持不变。确认要替换请用 force 重试。",
        )
    return parsed, {"old": old_n, "new": len(parsed.passages), "layout_pages": len(hints)}


def _after(app: AppContext, ids: list[str], reparsed: bool = True) -> None:
    """和导入后一样的自动处理：已配置嵌入模型就重建语义索引；开了自动分析就启动公式识别与表格还原。不重做整体分析（它的证据已重映射）。"""
    from ..ai import settings as ai_settings
    from ..util import ApiError as _Err

    cfg = ai_settings.public_settings(app)
    if not cfg["configured"]:
        return
    from ..ai import embeddings

    for pid in ids:
        try:
            embeddings.enqueue_embedding(app, pid)
        except _Err:
            pass
        if cfg["auto_analyze"]:
            from . import processing

            try:
                processing.start_auto(app, pid)
            except _Err:
                pass


def handle_reparse(ctx: JobContext) -> dict:
    app = ctx.app
    paper_id = ctx.payload["paper_id"]
    force = bool(ctx.payload.get("force"))
    ids = [paper_id, *_children(app, paper_id)]
    _cancel_others(ctx, ids)
    items = []
    try:
        for i, pid in enumerate(ids):
            ctx.check_cancelled()
            base, span = i / len(ids), 1 / len(ids)
            parsed, counts = _parse_for(ctx, pid, base, span, force)
            ctx.check_cancelled()
            ctx.progress(base + 0.85 * span, "替换解析结果并重映射笔记、卡片等数据 …")
            stats = _apply(app, pid, parsed)
            with app.db.session() as conn:  # 记下这次解析用了视觉划分：打开论文时不会再重复触发
                conn.execute("UPDATE papers SET layout_ai = ? WHERE id = ?", (1 if counts.get("layout_pages") else 0, pid))
            with app.db.session() as conn:
                title = conn.execute("SELECT title FROM papers WHERE id = ?", (pid,)).fetchone()["title"]
            items.append({"paper_id": pid, "title": title, **counts, "stats": stats})
    except JobCancelled:
        raise
    _after(app, ids)
    ctx.progress(1.0, "重新解析完成")
    return {"paper_id": paper_id, "items": items, "undo_available": True}
