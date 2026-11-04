"""论文文件夹（纵向归档）、智能视图（保存下来的筛选条件）与“建议归类”。

文件夹与标签的分工：一篇论文只能放在一个文件夹里（像资料柜的一格），但可以同时有多个标签（像贴在上面的便利贴）。
- 删除文件夹不会删除论文：它和所有子文件夹一起被移除，里面的论文回到“未分类”；
- 补充材料子论文跟随主论文，不单独放进文件夹；
- 建议归类只给出建议（带相似依据），绝不自动移动论文。
"""
from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter

from ..context import AppContext
from ..db import register_column, register_schema
from ..util import ApiError, dumps, loads, new_id, utcnow
from . import filters

MAX_DEPTH = 6
MAX_NAME = 60

register_column("folders", "papers", "folder_id", "TEXT")
register_schema("folders", """
CREATE TABLE IF NOT EXISTS folders (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  parent_id TEXT,
  sort INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_folders_parent ON folders(parent_id, sort);
CREATE INDEX IF NOT EXISTS idx_papers_folder ON papers(folder_id);
CREATE TABLE IF NOT EXISTS smart_views (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  query_json TEXT NOT NULL,
  sort INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS folder_suggestion_ignored (
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  folder_id TEXT NOT NULL,
  PRIMARY KEY (paper_id, folder_id)
);
CREATE TABLE IF NOT EXISTS paper_vectors (
  paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  n INTEGER NOT NULL,
  vec BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_paper ON notes(paper_id);
CREATE INDEX IF NOT EXISTS idx_annotations_paper ON annotations(paper_id);
""")


# ---------------------------------------------------------------- 文件夹
def _clean_name(name: str | None) -> str:
    n = re.sub(r"\s+", " ", (name or "").strip())
    if not n:
        raise ApiError("bad_request", "文件夹名称不能为空")
    if len(n) > MAX_NAME:
        raise ApiError("bad_request", f"文件夹名称不能超过 {MAX_NAME} 个字符")
    return n


def _all(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, name, parent_id, sort, created_at FROM folders ORDER BY sort, lower(name)").fetchall()


def _depth(rows: dict[str, str | None], fid: str | None) -> int:
    d = 0
    while fid is not None and d < 50:
        fid = rows.get(fid)
        d += 1
    return d


def _height(children: dict[str | None, list[str]], fid: str) -> int:
    kids = children.get(fid, [])
    return 1 + (max(_height(children, k) for k in kids) if kids else 0)


def _row(conn: sqlite3.Connection, fid: str) -> sqlite3.Row:
    r = conn.execute("SELECT id, name, parent_id, sort, created_at FROM folders WHERE id = ?", (fid,)).fetchone()
    if r is None:
        raise ApiError("not_found", "文件夹不存在", 404)
    return r


def _assert_unique(conn: sqlite3.Connection, name: str, parent_id: str | None, exclude: str | None = None) -> None:
    for r in conn.execute("SELECT id, name FROM folders WHERE parent_id IS ?", (parent_id,)):
        if r["id"] != exclude and r["name"].lower() == name.lower():
            raise ApiError("conflict", f"这个位置已经有一个叫“{r['name']}”的文件夹", 409)


def list_folders(app: AppContext) -> dict:
    """文件夹平铺列表（前端按 parent_id 组树）与系统视图的论文数。"""
    with app.db.session() as conn:
        rows = _all(conn)
        direct = {
            r["folder_id"]: r["n"]
            for r in conn.execute(
                "SELECT folder_id, COUNT(*) AS n FROM papers WHERE COALESCE(role,'paper')='paper' AND folder_id IS NOT NULL GROUP BY folder_id"
            )
        }
        children: dict[str | None, list[str]] = {}
        for r in rows:
            children.setdefault(r["parent_id"], []).append(r["id"])

        def total(fid: str) -> int:
            return direct.get(fid, 0) + sum(total(k) for k in children.get(fid, []))

        items = [
            {"id": r["id"], "name": r["name"], "parent_id": r["parent_id"], "sort": r["sort"],
             "paper_count": direct.get(r["id"], 0), "total_count": total(r["id"])}
            for r in rows
        ]
        base = "COALESCE(role,'paper') = 'paper'"
        counts = {
            "all": conn.execute(f"SELECT COUNT(*) FROM papers WHERE {base}").fetchone()[0],
            "unfiled": conn.execute(f"SELECT COUNT(*) FROM papers WHERE {base} AND folder_id IS NULL").fetchone()[0],
            "favorite": conn.execute(f"SELECT COUNT(*) FROM papers WHERE {base} AND favorite = 1").fetchone()[0],
            "recent": conn.execute(f"SELECT COUNT(*) FROM papers WHERE {base} AND last_opened_at IS NOT NULL").fetchone()[0],
        }
    return {"items": items, "counts": counts}


def folder_paths(conn: sqlite3.Connection) -> dict[str, str]:
    """{folder_id: "上级 / 本级"}，列表里显示论文所在位置用。"""
    rows = {r["id"]: (r["name"], r["parent_id"]) for r in conn.execute("SELECT id, name, parent_id FROM folders")}
    out: dict[str, str] = {}
    for fid in rows:
        names, cur, guard = [], fid, 0
        while cur is not None and cur in rows and guard < 50:
            names.append(rows[cur][0])
            cur = rows[cur][1]
            guard += 1
        out[fid] = " / ".join(reversed(names))
    return out


def create_folder(app: AppContext, name: str, parent_id: str | None = None) -> dict:
    name = _clean_name(name)
    with app.db.session() as conn:
        if parent_id is not None:
            _row(conn, parent_id)
            parents = {r["id"]: r["parent_id"] for r in _all(conn)}
            if _depth(parents, parent_id) + 1 >= MAX_DEPTH:
                raise ApiError("bad_request", f"文件夹最多嵌套 {MAX_DEPTH} 层")
        _assert_unique(conn, name, parent_id)
        sort = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM folders WHERE parent_id IS ?", (parent_id,)).fetchone()[0]
        fid = new_id()
        conn.execute("INSERT INTO folders(id, name, parent_id, sort, created_at) VALUES (?,?,?,?,?)", (fid, name, parent_id, sort, utcnow()))
    return {"id": fid, "name": name, "parent_id": parent_id, "sort": sort, "paper_count": 0, "total_count": 0}


def update_folder(app: AppContext, fid: str, *, name: str | None = None, parent_id: str | None = None, move: bool = False,
                  sort: int | None = None) -> dict:
    """改名、移动（move=True 时按 parent_id 移动，None 表示移到最上层）、调整顺序。不能把文件夹移进它自己的子孙里。"""
    with app.db.session() as conn:
        cur = _row(conn, fid)
        new_name = _clean_name(name) if name is not None else cur["name"]
        new_parent = parent_id if move else cur["parent_id"]
        if move and new_parent is not None:
            _row(conn, new_parent)
            if new_parent in filters.subtree_ids(conn, fid):
                raise ApiError("bad_request", "不能把文件夹移到它自己或它的子文件夹里")
            parents = {r["id"]: r["parent_id"] for r in _all(conn)}
            children: dict[str | None, list[str]] = {}
            for k, v in parents.items():
                children.setdefault(v, []).append(k)
            if _depth(parents, new_parent) + _height(children, fid) >= MAX_DEPTH:
                raise ApiError("bad_request", f"文件夹最多嵌套 {MAX_DEPTH} 层")
        if new_name != cur["name"] or new_parent != cur["parent_id"]:
            _assert_unique(conn, new_name, new_parent, exclude=fid)
        new_sort = cur["sort"]
        if move and new_parent != cur["parent_id"] and sort is None:
            new_sort = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM folders WHERE parent_id IS ?", (new_parent,)).fetchone()[0]
        if sort is not None:
            new_sort = int(sort)
        conn.execute("UPDATE folders SET name = ?, parent_id = ?, sort = ? WHERE id = ?", (new_name, new_parent, new_sort, fid))
    return {"id": fid, "name": new_name, "parent_id": new_parent, "sort": new_sort}


def reorder(app: AppContext, parent_id: str | None, ids: list[str]) -> None:
    """把同一级文件夹按 ids 的顺序重新编号；ids 必须恰好是该级下的全部文件夹。"""
    with app.db.session() as conn:
        existing = [r["id"] for r in conn.execute("SELECT id FROM folders WHERE parent_id IS ?", (parent_id,))]
        if sorted(existing) != sorted(ids):
            raise ApiError("bad_request", "排序列表必须包含这一级的全部文件夹，且不能有重复")
        for i, fid in enumerate(ids):
            conn.execute("UPDATE folders SET sort = ? WHERE id = ?", (i, fid))


def delete_folder(app: AppContext, fid: str) -> dict:
    """删除文件夹及其全部子文件夹；里面的论文回到“未分类”，论文本身不受影响。"""
    with app.db.session() as conn:
        _row(conn, fid)
        ids = filters.subtree_ids(conn, fid)
        marks = ",".join("?" * len(ids))
        moved = conn.execute(f"UPDATE papers SET folder_id = NULL WHERE folder_id IN ({marks})", ids).rowcount
        conn.execute(f"DELETE FROM folder_suggestion_ignored WHERE folder_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM folders WHERE id IN ({marks})", ids)
    return {"ok": True, "deleted_folders": len(ids), "moved_papers": moved}


def move_papers(app: AppContext, paper_ids: list[str], folder_id: str | None) -> dict:
    """把论文移入文件夹（folder_id=None 表示移回“未分类”）。补充材料子论文不能单独移动，会被跳过并计数。"""
    if not paper_ids:
        raise ApiError("bad_request", "没有选择论文")
    ids = list(dict.fromkeys(paper_ids))
    with app.db.session() as conn:
        if folder_id is not None:
            _row(conn, folder_id)
        marks = ",".join("?" * len(ids))
        found = {r["id"]: r["role"] for r in conn.execute(f"SELECT id, COALESCE(role,'paper') AS role FROM papers WHERE id IN ({marks})", ids)}
        missing = [i for i in ids if i not in found]
        if missing:
            raise ApiError("not_found", f"有 {len(missing)} 篇论文不存在", 404)
        movable = [i for i in ids if found[i] == "paper"]
        if movable:
            m2 = ",".join("?" * len(movable))
            conn.execute(f"UPDATE papers SET folder_id = ? WHERE id IN ({m2})", (folder_id, *movable))
            if folder_id is not None:  # 已经归到这里的建议不再需要
                conn.execute(f"DELETE FROM folder_suggestion_ignored WHERE paper_id IN ({m2})", movable)
    return {"ok": True, "moved": len(movable), "skipped_supplements": len(ids) - len(movable)}


# ---------------------------------------------------------------- 智能视图
def list_views(app: AppContext) -> list[dict]:
    with app.db.session() as conn:
        return [
            {"id": r["id"], "name": r["name"], "query": loads(r["query_json"], {}), "sort": r["sort"]}
            for r in conn.execute("SELECT * FROM smart_views ORDER BY sort, created_at")
        ]


def create_view(app: AppContext, name: str, query: dict) -> dict:
    name = _clean_name(name)
    if not isinstance(query, dict) or not query:
        raise ApiError("bad_request", "没有可保存的筛选条件")
    clean = filters.normalize(query)
    with app.db.session() as conn:
        for r in conn.execute("SELECT name FROM smart_views"):
            if r["name"].lower() == name.lower():
                raise ApiError("conflict", f"已经有一个叫“{r['name']}”的智能视图", 409)
        sort = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM smart_views").fetchone()[0]
        vid = new_id()
        conn.execute("INSERT INTO smart_views(id, name, query_json, sort, created_at) VALUES (?,?,?,?,?)", (vid, name, dumps(clean), sort, utcnow()))
    return {"id": vid, "name": name, "query": clean, "sort": sort}


def delete_view(app: AppContext, vid: str) -> None:
    with app.db.session() as conn:
        if conn.execute("DELETE FROM smart_views WHERE id = ?", (vid,)).rowcount == 0:
            raise ApiError("not_found", "智能视图不存在", 404)


# ---------------------------------------------------------------- 建议归类
STOP = set(
    "the a an of and or for to in on with by from at as is are was were be been this that these those we our it its their which "
    "using use used based via into than then can may also such more most not but all any each both between over under about new "
    "paper study approach method methods results result show shows proposed propose present presents "
    "的 了 和 与 及 在 是 一个 我们 本文 通过 使用 基于 研究 方法 论文 提出".split()
)


def _terms(title: str | None, abstract: str | None) -> Counter:
    text = ((title or "") + " ") * 3 + (abstract or "")  # 标题的词权重更高
    low = text.lower()
    toks = [t for t in re.findall(r"[a-z][a-z0-9\-]{2,}", low) if t not in STOP]
    for run in re.findall(r"[一-鿿]{2,}", low):  # 中文没有空格分词：用相邻两个字作为词，中文论文之间才能比
        toks.extend(b for b in (run[i : i + 2] for i in range(len(run) - 1)) if b not in STOP)
    return Counter(toks)


def _cos_counter(a: Counter, b: Counter, idf: dict[str, float]) -> tuple[float, list[str]]:
    shared = [t for t in a if t in b]
    if not shared:
        return 0.0, []
    num = sum(a[t] * b[t] * idf.get(t, 1.0) ** 2 for t in shared)
    na = math.sqrt(sum((c * idf.get(t, 1.0)) ** 2 for t, c in a.items()))
    nb = math.sqrt(sum((c * idf.get(t, 1.0)) ** 2 for t, c in b.items()))
    score = num / (na * nb) if na and nb else 0.0
    top = sorted(shared, key=lambda t: -(a[t] * b[t] * idf.get(t, 1.0) ** 2))[:5]
    return score, top


def _paper_vectors(app: AppContext, conn: sqlite3.Connection, ids: list[str], model: str):
    """每篇论文的向量 = 前 60 个段落向量的均值（归一化），缓存在 paper_vectors；嵌入数量变化时重算。"""
    import numpy as np

    out: dict[str, "np.ndarray"] = {}
    for pid in ids:
        n = conn.execute("SELECT COUNT(*) FROM passage_embeddings WHERE paper_id = ? AND model = ?", (pid, model)).fetchone()[0]
        if n == 0:
            continue
        cached = conn.execute("SELECT dim, n, vec FROM paper_vectors WHERE paper_id = ? AND model = ?", (pid, model)).fetchone()
        if cached and cached["n"] == n:
            out[pid] = np.frombuffer(cached["vec"], dtype="<f4")
            continue
        rows = conn.execute(
            "SELECT dim, vec FROM passage_embeddings WHERE paper_id = ? AND model = ? ORDER BY ord LIMIT 60", (pid, model)
        ).fetchall()
        dim = rows[0]["dim"]
        mat = np.frombuffer(b"".join(r["vec"] for r in rows if r["dim"] == dim), dtype="<f4").reshape(-1, dim)
        v = mat.mean(axis=0)
        v = (v / (np.linalg.norm(v) or 1.0)).astype("<f4")
        conn.execute(
            "INSERT OR REPLACE INTO paper_vectors(paper_id, model, dim, n, vec) VALUES (?,?,?,?,?)", (pid, model, dim, n, v.tobytes())
        )
        out[pid] = v
    return out


def suggestions(app: AppContext, limit: int = 30) -> dict:
    """给“未分类”的论文建议可能适合的文件夹。

    有语义索引时用向量（每篇论文的段落向量均值，与文件夹里最相似的那篇论文比较）；没有就退到标题/摘要的关键词相似度。
    返回的 method 如实标注用了哪一种；每条建议带依据（最相似的那篇论文、共享的词）和置信度。
    """
    from ..ai.settings import load_settings

    cfg = load_settings(app)
    model = (cfg.get("embedding_model") or "") if cfg.get("semantic_retrieval", True) else ""
    with app.db.session() as conn:
        base = "COALESCE(role,'paper') = 'paper'"
        filed = conn.execute(f"SELECT id, title, abstract, folder_id FROM papers WHERE {base} AND folder_id IS NOT NULL").fetchall()
        unfiled = conn.execute(
            f"SELECT id, title, abstract FROM papers WHERE {base} AND folder_id IS NULL AND status = 'ready' "
            "AND id NOT IN (SELECT paper_id FROM folder_suggestion_ignored GROUP BY paper_id HAVING COUNT(*) >= 3)"
        ).fetchall()
        if not filed or not unfiled:
            return {"method": "none", "items": [], "reason": "需要至少一个已归类的文件夹和一篇未分类的论文才能给出建议。"}
        folders = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM folders")}
        paths = folder_paths(conn)
        ignored = {(r["paper_id"], r["folder_id"]) for r in conn.execute("SELECT paper_id, folder_id FROM folder_suggestion_ignored")}
        # 一个文件夹的成员 = 它和它所有子文件夹里的论文
        members: dict[str, list[sqlite3.Row]] = {}
        parents = {r["id"]: r["parent_id"] for r in conn.execute("SELECT id, parent_id FROM folders")}
        for p in filed:
            cur, guard = p["folder_id"], 0
            while cur is not None and guard < 50:
                members.setdefault(cur, []).append(p)
                cur, guard = parents.get(cur), guard + 1
        vec = _paper_vectors(app, conn, [p["id"] for p in filed] + [p["id"] for p in unfiled], model) if model else {}
        use_vec = bool(vec) and sum(1 for p in filed if p["id"] in vec) >= 1 and sum(1 for p in unfiled if p["id"] in vec) >= 1
        method = "embedding" if use_vec else "keywords"
        out: list[dict] = []
        if use_vec:
            import numpy as np

            for p in unfiled:
                if p["id"] not in vec:
                    continue
                best = None
                for fid, ms in members.items():
                    if (p["id"], fid) in ignored:
                        continue
                    cand = [(float(vec[m["id"]] @ vec[p["id"]]), m) for m in ms if m["id"] in vec]
                    if not cand:
                        continue
                    sims = sorted((c for c in cand), key=lambda c: -c[0])
                    score = 0.6 * sims[0][0] + 0.4 * (sum(s for s, _ in sims[:3]) / min(3, len(sims)))
                    if best is None or score > best[0]:
                        best = (score, fid, sims[0][1], sims[0][0])
                if best and best[0] >= 0.45:
                    score, fid, nearest, nsim = best
                    out.append({
                        "paper_id": p["id"], "paper_title": p["title"], "folder_id": fid, "folder_name": folders[fid],
                        "folder_path": paths.get(fid, folders[fid]), "score": round(score, 3),
                        "confidence": "high" if score >= 0.7 else "medium" if score >= 0.58 else "low",
                        "basis": f"与该文件夹里的《{nearest['title'][:50]}》最相似（语义相似度 {nsim:.2f}）。",
                    })
        else:
            terms = {p["id"]: _terms(p["title"], p["abstract"]) for p in [*filed, *unfiled]}
            df: Counter = Counter()
            for c in terms.values():
                df.update(set(c))
            n_docs = max(1, len(terms))
            idf = {t: math.log(1 + n_docs / (1 + d)) for t, d in df.items()}
            for p in unfiled:
                best = None
                for fid, ms in members.items():
                    if (p["id"], fid) in ignored:
                        continue
                    sims = sorted(((*_cos_counter(terms[p["id"]], terms[m["id"]], idf), m) for m in ms), key=lambda s: -s[0])
                    if not sims or len(sims[0][1]) < 2:
                        continue
                    score = sims[0][0]
                    if best is None or score > best[0]:
                        best = (score, fid, sims[0][2], sims[0][1])
                if best and best[0] >= 0.2:
                    score, fid, nearest, shared = best
                    out.append({
                        "paper_id": p["id"], "paper_title": p["title"], "folder_id": fid, "folder_name": folders[fid],
                        "folder_path": paths.get(fid, folders[fid]), "score": round(score, 3),
                        "confidence": "high" if score >= 0.45 else "medium" if score >= 0.3 else "low",
                        "basis": f"与该文件夹里的《{nearest['title'][:50]}》共享关键词：{'、'.join(shared)}；按关键词重合判断，较粗略。",
                    })
        out.sort(key=lambda s: -s["score"])
    return {"method": method, "items": out[:limit], "reason": None}


def ignore_suggestion(app: AppContext, paper_id: str, folder_id: str) -> None:
    with app.db.session() as conn:
        if conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
            raise ApiError("not_found", "论文不存在", 404)
        _row(conn, folder_id)
        conn.execute("INSERT OR IGNORE INTO folder_suggestion_ignored(paper_id, folder_id) VALUES (?,?)", (paper_id, folder_id))
