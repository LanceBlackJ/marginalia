"""给代码讲解准备上下文：该读哪些代码、对应论文里的哪几段。

原来的做法是“每个目录取一个文件的开头”，开头几乎都是 import，模型拿到的基本只有文件名。
这里改成：
1. 用论文的标题和摘要给仓库里的文件打相关度分（稀有词权重高，第三方拷贝降权），先读最相关的；
2. 文件不读开头，读“结构摘录”：每个类/函数的定义行和紧跟的几行（含文档字符串），行号保持真实；
3. 解释函数/类/文件时，把论文里对应（已确认或可能相关）的原文段落一并给模型，标成 [P编号]，只作背景。
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from ..context import AppContext
from ..util import loads
from . import service
from .associate import METHOD_SECTION, STOP, split_identifier
from .scan import CODE_LANGS

ROLE_BONUS = {"entry": 1.0, "train": 0.8, "model": 1.2, "util": 0.3, "config": 0.0}
NOISE_ROLES = {"test", "doc", "data", "dependency", "other"}
VENDORED_FILES = 150           # 一个子目录里文件这么多、又自带依赖/构建文件，多半是拷贝来的第三方项目
README_NAMES = ("readme.md", "readme.rst", "readme.txt", "readme")
BADGE = re.compile(r"^\s*(?:\[!\[|<img|<p\s+align|<div\s+align|<a\s+href=.*<img|!\[)", re.I)
SYMBOL_KINDS = ("function", "method", "class", "main")
CODE_CLIP = 170
README_CLIP = 150


@dataclass
class Snippet:
    label: str
    path: str
    line_start: int
    line_end: int
    text: str                  # 带行号的渲染文本
    role: str                  # target | callee | caller | header | outline
    chars: int = 0
    shown: list[int] | None = None   # 真正给了模型的行号（摘录时不是连续的）；None 表示整段连续

    def header(self) -> str:
        role = {"target": "目标", "callee": "被调用者", "caller": "调用点", "header": "文件头", "outline": "结构摘录（只含定义行和紧跟的几行，中间省略）"}.get(self.role, self.role)
        return f"[{self.label}] {self.path}:{self.line_start}-{self.line_end}（{role}）"

    def covers(self, a: int, b: int) -> bool:
        """引用的行范围里，至少有一行是真的发给了模型的。"""
        if self.shown is None:
            return a >= self.line_start and b <= self.line_end
        return any(a <= n <= b for n in self.shown)


def numbered(lines: list[str], picked: list[int], clip: int | None = None) -> str:
    """只渲染挑出来的行；不连续的地方画一条省略线；过长的行（README 的长段落）截断并加“…”。"""
    out: list[str] = []
    prev = None
    for n in picked:
        if prev is not None and n != prev + 1:
            out.append("      ⋮")
        t = lines[n - 1]
        if clip and len(t) > clip:
            t = t[:clip].rstrip() + "…"
        out.append(f"{n:>5}| {t}")
        prev = n
    return "\n".join(out)


# ---------------- 论文侧 ----------------
def paper_info(app: AppContext, paper_id: str) -> dict:
    with app.db.session() as conn:
        p = conn.execute("SELECT title FROM papers WHERE id = ?", (paper_id,)).fetchone()
        sec = conn.execute("SELECT id FROM sections WHERE paper_id = ? AND lower(title) LIKE 'abstract%' ORDER BY ord LIMIT 1", (paper_id,)).fetchone()
        if sec:
            rows = conn.execute("SELECT text FROM passages WHERE paper_id = ? AND section_id = ? AND kind IN ('paragraph','front') ORDER BY ord LIMIT 3", (paper_id, sec["id"])).fetchall()
        else:
            rows = []
        if not rows:
            rows = conn.execute("SELECT text FROM passages WHERE paper_id = ? AND kind IN ('paragraph','front') ORDER BY ord LIMIT 3", (paper_id,)).fetchall()
    abstract = " ".join(r["text"] for r in rows)[:900]
    return {"title": (p["title"] if p else "") or "", "abstract": abstract}


def paper_terms(info: dict) -> dict[str, float]:
    """论文标题（权重 3）和摘要（权重 1）里的词，用来给代码打相关度分。"""
    weights: Counter[str] = Counter()
    for text, w in ((info["title"], 3.0), (info["abstract"], 1.0)):
        for t in split_identifier(text):
            if len(t) >= 4 and t not in STOP:
                weights[t] = max(weights[t], w)
    return dict(weights)


# ---------------- 文件相关度 ----------------
def _readme_text(app: AppContext, repo_id: str, files: list) -> tuple[str | None, str]:
    r = next((f for f in files if f["path"].lower() in README_NAMES), None)
    if r is None:
        return None, ""
    fl = service.file_lines(app, repo_id, r["path"])
    return r["path"], "\n".join(fl or []).lower()


def vendored_dirs(files: list) -> set[str]:
    """文件很多、又自带依赖/构建文件的子目录：多半是拷贝来的第三方项目，不当作论文的核心实现。"""
    counts: Counter[str] = Counter()
    own: set[str] = set()
    for f in files:
        parts = f["path"].split("/")
        for i in range(1, len(parts)):
            counts["/".join(parts[:i])] += 1
        if f["role"] == "dependency" and len(parts) > 1:
            own.add("/".join(parts[:-1]))
    return {d for d, n in counts.items() if n >= VENDORED_FILES and d in own}


def order_entry_points(entries: list[dict], files: list) -> list[dict]:
    """入口列表里，拷贝来的第三方目录里的入口排到后面（稳定排序，其它顺序不变）：研究仓库常把整个 transformers 之类拷进来，
    几百个入口会把作者自己的训练脚本淹没。"""
    vend = vendored_dirs(files)
    if not vend:
        return entries
    return sorted(entries, key=lambda e: any(e["path"].startswith(d + "/") for d in vend))


def rank_files(app: AppContext, repo_id: str, paper_id: str, files: list | None = None, under: str = "") -> list[dict]:
    """按和论文的相关度给文本文件排序：[{path, score, lines, role, reason}]。"""
    with app.db.session() as conn:
        allf = files if files is not None else conn.execute("SELECT path, lines, role, language FROM code_files WHERE repo_id = ? AND skipped IS NULL ORDER BY path", (repo_id,)).fetchall()
        syms = conn.execute("SELECT path, name, qualname, doc FROM code_symbols WHERE repo_id = ? AND kind IN ('function','method','class')", (repo_id,)).fetchall()
    terms = paper_terms(paper_info(app, paper_id))
    _, readme = _readme_text(app, repo_id, allf)
    hay: dict[str, list[str]] = {}
    for s in syms:
        hay.setdefault(s["path"], []).append(f"{s['qualname']} {(s['doc'] or '')[:200]}".lower())
    text_of = {f["path"]: (f["path"].lower() + " " + " ".join(hay.get(f["path"], []))) for f in allf}
    n = max(1, len(allf))
    idf: dict[str, float] = {}
    for t in terms:
        df = sum(1 for h in text_of.values() if t in h)
        idf[t] = math.log(1 + n / (1 + df)) if df else 0.0
    vend = vendored_dirs(allf)
    out: list[dict] = []
    for f in allf:
        p = f["path"]
        if under and not p.startswith(under + "/"):
            continue
        if f["lines"] <= 0 or f["role"] in ("test", "doc", "dependency") or f["language"] not in CODE_LANGS:  # 只把源码当重点（含 data 目录里的加载代码），测试、文档、配置、数据不算
            continue
        h = text_of[p]
        hits = [t for t in terms if t in h and idf[t] > 0]
        score = sum(terms[t] * idf[t] for t in hits)
        why = [f"和论文共有的词：{'、'.join(hits[:4])}"] if hits else []
        base = os_base(p)
        if base and base in readme:
            score += 2.0
            why.append("README 里提到")
        score += ROLE_BONUS.get(f["role"], 0.0)
        if f["language"] == "shell":
            score *= 0.3
        is_vend = any(p.startswith(d + "/") for d in vend)
        if is_vend:
            score *= 0.25
            why.append("在自带依赖文件的大型子目录里，可能是第三方拷贝")
        out.append({"path": p, "score": round(score, 3), "lines": f["lines"], "role": f["role"], "reason": "；".join(why), "vendored": is_vend})
    out.sort(key=lambda d: (-d["score"], d["path"]))
    return out


def os_base(path: str) -> str:
    """README 里可能写成 `loralib/` 或 `layers.py`：取文件名和所在目录名都试一下，返回其中更具体的一个。"""
    parts = path.lower().split("/")
    return parts[-2] + "/" if len(parts) > 1 and len(parts[-2]) >= 4 else ""


# ---------------- 摘录 ----------------
def _fit(fl: list[str], picked: list[int], max_chars: int) -> list[int]:
    """超出字数预算就从后面截掉；至少留一行。"""
    total, out = 0, []
    for n in picked:
        total += min(len(fl[n - 1]), README_CLIP) + 8
        if total > max_chars and out:
            break
        out.append(n)
    return out


def readme_snippet(app: AppContext, repo_id: str, path: str, label: str, max_lines: int = 90, max_chars: int = 4200) -> Snippet | None:
    fl = service.file_lines(app, repo_id, path)
    if not fl:
        return None
    picked = _fit(fl, [i for i in range(1, min(len(fl), 200) + 1) if not BADGE.match(fl[i - 1]) and (fl[i - 1].strip() or i > 1)][:max_lines], max_chars)
    while picked and not fl[picked[-1] - 1].strip():
        picked.pop()
    if not picked:
        return None
    text = numbered(fl, picked, README_CLIP)
    return Snippet(label, path, picked[0], picked[-1], text, "outline" if len(picked) < picked[-1] - picked[0] + 1 else "header", len(text), picked)


def outline_snippet(app: AppContext, repo_id: str, path: str, label: str, *, per_symbol: int = 2, max_lines: int = 140, whole_if: int = 90, max_chars: int = 3200) -> Snippet | None:
    """文件的结构摘录。文件很短就整个给；否则先给模块开头的说明和每个类/函数的定义，再一轮一轮地给每个函数多补几行，直到用完字数预算。
    这样每个函数都看得到开头，最重要的（通常较长）函数也能看到更多，而不是只有定义行。"""
    fl = service.file_lines(app, repo_id, path)
    if not fl:
        return None
    n = len(fl)
    if n <= whole_if and sum(len(x) + 8 for x in fl) <= max_chars:
        picked = list(range(1, n + 1))
        text = numbered(fl, picked, CODE_CLIP)
        return Snippet(label, path, 1, n, text, "target", len(text))
    with app.db.session() as conn:
        syms = conn.execute(
            "SELECT kind, line_start, line_end FROM code_symbols WHERE repo_id = ? AND path = ? AND kind IN ('function','method','class') ORDER BY line_start", (repo_id, path)
        ).fetchall()
    chosen: set[int] = set()
    budget = max_chars

    def take(k: int) -> bool:
        nonlocal budget
        if k in chosen or not 1 <= k <= n or not fl[k - 1].strip():
            return True
        cost = min(len(fl[k - 1]), CODE_CLIP) + 8
        if cost > budget or len(chosen) >= max_lines:
            return False
        chosen.add(k)
        budget -= cost
        return True

    comment_starts = ("#", '"""', "'''", "//", "/*", "*")
    i = 0
    while i < min(n, 40) and (not fl[i].strip() or fl[i].lstrip().startswith(comment_starts)) and len(chosen) < 4:  # 文件开头的说明
        take(i + 1)
        i += 1
    cursor: dict[int, int] = {}
    for idx, s in enumerate(syms):  # 第一轮：每个类/函数的定义
        last = min(s["line_end"], s["line_start"] + per_symbol - 1)
        for k in range(s["line_start"], last + 1):
            if not take(k):
                break
        cursor[idx] = last + 1
    # 之后只给函数/方法补正文（类的正文就是它的方法）；预算不够分时，较长的函数先补：核心逻辑通常在较长的函数里，
    # 而不是文件前面那些几行的小函数
    growing = sorted((idx for idx, s in enumerate(syms) if s["kind"] != "class"), key=lambda i: (-(syms[i]["line_end"] - syms[i]["line_start"]), i))
    while growing and budget > 0 and len(chosen) < max_lines:
        nxt = []
        stuck = True
        for idx in growing:
            s = syms[idx]
            for _ in range(2):
                while cursor[idx] <= s["line_end"] and not fl[cursor[idx] - 1].strip():
                    cursor[idx] += 1
                if cursor[idx] > s["line_end"]:
                    break
                if not take(cursor[idx]):
                    cursor[idx] = s["line_end"] + 1  # 预算不够放下这一行：这个函数到此为止
                    break
                stuck = False
                cursor[idx] += 1
            if cursor[idx] <= s["line_end"]:
                nxt.append(idx)
        if stuck:
            break
        growing = nxt
    picked = sorted(chosen)
    if not picked:
        picked = _fit(fl, [k for k in range(1, n + 1) if fl[k - 1].strip()][:max_lines], max_chars)
    text = numbered(fl, picked, CODE_CLIP)
    return Snippet(label, path, picked[0], picked[-1], text, "outline", len(text), picked)


# ---------------- 论文段落 ----------------
def _passage_rows(app: AppContext, paper_id: str, ords: list[int]) -> list[dict]:
    if not ords:
        return []
    with app.db.session() as conn:
        rows = conn.execute(
            f"SELECT p.ord, p.page, p.text, sec.title AS section FROM passages p LEFT JOIN sections sec ON sec.id = p.section_id WHERE p.paper_id = ? AND p.ord IN ({','.join('?' * len(ords))})",
            (paper_id, *ords),
        ).fetchall()
    by = {r["ord"]: r for r in rows}
    return [{"ord": o, "page": by[o]["page"], "section": by[o]["section"], "text": by[o]["text"]} for o in ords if o in by]


SECTION_LOW = re.compile(r"baseline|related|experiment|result|evaluat|appendix|reference|conclusion|introduction|acknowledg|limitation|ethic|dataset|implementation detail|hyper", re.I)
MATH_CHARS = re.compile(r"[=∈∑∏∆Δ×≤≥≈Θθαβγλμσ√∂∇]")


def lexical_passages(app: AppContext, paper_id: str, terms: list[str], limit: int = 4, title_terms: set[str] | None = None) -> list[int]:
    """先用全文检索取候选，再按几条通用线索重新打分：命中的词（标题里的词权重高）、所在章节（方法类加分，相关工作/实验/附录减分）、
    段落里有公式符号（论文里定义方法的段落通常带公式）。仍然只是词法线索，调用方要如实标“只是用词相近”。"""
    terms = [t for t in dict.fromkeys(terms) if len(t) > 2 and t not in STOP][:12]
    if not terms:
        return []
    match = " OR ".join(f'"{t}"' for t in terms)
    try:
        with app.db.session() as conn:
            rows = conn.execute(
                "SELECT p.ord, p.text, sec.title AS st FROM passages_fts f JOIN passages p ON p.id = f.rowid LEFT JOIN sections sec ON sec.id = p.section_id "
                "WHERE passages_fts MATCH ? AND p.paper_id = ? AND p.kind IN ('paragraph','caption','formula') ORDER BY bm25(passages_fts) LIMIT 40",
                (match, paper_id),
            ).fetchall()
    except Exception:  # noqa: BLE001  FTS 语法问题不应让解释失败
        return []
    title_terms = title_terms or set()
    scored = []
    for i, r in enumerate(rows):
        low = r["text"].lower()
        hit = [t for t in terms if t in low]
        s = sum(3.0 if t in title_terms else 1.0 for t in hit)
        sec = r["st"] or ""
        if METHOD_SECTION.search(sec):
            s += 1.5
        if SECTION_LOW.search(sec):
            s -= 2.0
        if len(MATH_CHARS.findall(r["text"])) >= 3:
            s += 1.5
        scored.append((s - i * 0.02, r["ord"]))  # 检索排名只用来在同分时定先后
    scored.sort(key=lambda t: -t[0])
    return [o for _, o in scored[:limit]]


CODE_NOISE = set(
    "self cls def class return import from none true false else elif for while with not and the len range isinstance super init forward print raise pass continue break "
    "try except finally yield lambda assert del global args kwargs str int float bool list dict tuple set torch numpy np nn mode result res out ret tmp data item items key value values "
    "path file name type size shape dim dims input output inputs outputs index idx num count batch config cfg opt options params param model models layer layers module modules function method "
    "bias transpose fan linear zeros ones init reset parameters view reshape cat concat sum mean max min abs sqrt exp log relu softmax device cuda cpu train eval training load save open close "
    "append extend update copy clone detach join split format get set add remove check test main run call build make create".split()
)


def code_terms(app: AppContext, repo_id: str, symbols: list[dict], limit: int = 10) -> list[str]:
    """从目标代码本身取最能代表它的词（变量名、属性名、注释里的词），用来去论文里找对应段落。比只用函数名准得多：
    比如 forward 这个名字在论文里到处都是，但 lora、scaling、merged、rank 只出现在讲这件事的段落里。"""
    freq: Counter[str] = Counter()
    for s in symbols[:3]:
        fl = service.file_lines(app, repo_id, s["path"]) if s.get("path") else None
        if not fl:
            continue
        a, b = s.get("line_start") or 1, min(s.get("line_end") or 1, (s.get("line_start") or 1) + 150)
        body = "\n".join(fl[a - 1:b])
        for ident in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", body):
            for t in split_identifier(ident):
                if len(t) > 2 and t not in STOP and t not in CODE_NOISE:
                    freq[t] += 1
        for t in split_identifier(s.get("qualname") or ""):  # 类名、函数名多给一点权重
            if len(t) > 2 and t not in CODE_NOISE:
                freq[t] += 2
    return [t for t, _ in freq.most_common(limit)]


SEMANTIC_MIN = 0.35


def semantic_hits(app: AppContext, paper_id: str, vec: list[float], k: int = 6) -> list[tuple[int, float]]:
    """用代码的向量去论文的向量索引里找最相近的正文段落（不含参考文献）。分数低于门槛的不要。"""
    from ..ai import embeddings

    hits = [h for h in embeddings.vector_search(app, paper_id, vec, k=24) if h.score >= SEMANTIC_MIN]
    if not hits:
        return []
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT p.ord, p.text, sec.title AS section FROM passages p LEFT JOIN sections sec ON sec.id = p.section_id "
            f"WHERE p.paper_id = ? AND p.kind IN ('paragraph','caption','formula') AND p.ord IN ({','.join('?' * len(hits))})", (paper_id, *[h.ord for h in hits])
        ).fetchall()
    info = {r["ord"]: r for r in rows}

    def adjusted(h) -> float:
        """代码实现的是论文的方法部分：实验/相关工作等小节里的段落即使向量相近，也往后排；含公式的段落略往前。"""
        r = info[h.ord]
        return h.score - (0.08 if SECTION_LOW.search(r["section"] or "") else 0) + (0.03 if MATH_CHARS.search(r["text"] or "") else 0)

    out, used = [], set()
    for h in sorted((h for h in hits if h.ord in info), key=adjusted, reverse=True):
        sec = info[h.ord]["section"] or h.ord
        if sec in used:  # 同一小节只取最相近的一段，免得几段都是同一个无关小节
            continue
        used.add(sec)
        out.append((h.ord, h.score))
    return out[:k]


def paper_passages(app: AppContext, repo: dict, target: dict, symbols: list[dict], question: str = "", limit: int = 3, max_chars: int = 700,
                   semantic: list[tuple[int, float]] | None = None) -> list[dict]:
    """目标代码对应的论文原文段落。优先用已建立（用户确认或系统建议）的关联，其次词法检索；每段标明来源。"""
    paper_id = repo["paper_id"]
    sids = [s["id"] for s in symbols if s.get("id")]
    found: list[tuple[int, str]] = []
    with app.db.session() as conn:
        conds, args = [], [repo["id"]]
        if sids:
            conds.append(f"symbol_id IN ({','.join('?' * len(sids))})")
            args += sids
        if target.get("path") and (target.get("kind") or "file") in ("file", "line_range"):
            conds.append("path = ?")
            args.append(target["path"])
        rows = conn.execute(
            f"SELECT module_json, status FROM paper_code_links WHERE repo_id = ? AND status != 'rejected' AND ({' OR '.join(conds)}) ORDER BY (status != 'confirmed')", args
        ).fetchall() if conds else []
    for r in rows:
        src = "confirmed" if r["status"] == "confirmed" else "suggested"
        for o in (loads(r["module_json"], {}) or {}).get("passage_ords", [])[:3]:
            found.append((o, src))
    if semantic:  # 有向量索引时，语义相近比词法线索可靠得多
        found += [(o, "semantic") for o, _ in semantic]
    elif len(found) < limit:
        terms: list[str] = code_terms(app, repo["id"], symbols)
        for s in symbols[:4]:
            terms += split_identifier((s.get("doc") or "")[:200])
        if not terms and target.get("path"):
            terms += split_identifier(target["path"].rsplit("/", 1)[-1])
        terms += split_identifier(question)
        title_terms = set(paper_terms(paper_info(app, paper_id))) if True else set()
        for o in lexical_passages(app, paper_id, terms, limit, title_terms):
            found.append((o, "lexical"))
    seen: set[int] = set()
    ords: list[int] = []
    src_of: dict[int, str] = {}
    for o, src in found:
        if o not in seen:
            seen.add(o)
            ords.append(o)
            src_of[o] = src
        if len(ords) >= limit:
            break
    out = []
    for r in _passage_rows(app, paper_id, ords):
        out.append({**r, "text": r["text"][:max_chars], "source": src_of[r["ord"]]})
    return out


def paper_block(papers: list[dict]) -> str:
    if not papers:
        return ""
    label = {"confirmed": "用户已确认与这段代码对应", "suggested": "系统根据名称推测的对应，未经确认", "semantic": "语义上相近，未必对应", "lexical": "只是用词相近，未必对应"}
    parts = ["【论文相关段落（背景资料：这是论文原文，不是代码；可用 [P编号] 引用）】"]
    for p in papers:
        parts.append(f"[P{p['ord']}] 第{p['page']}页{' · ' + p['section'] if p.get('section') else ''}（{label[p['source']]}）\n{p['text']}")
    return "\n\n".join(parts)


# ---------------- 仓库/目录的结构说明 ----------------
def dir_summary(files: list, depth_dirs: list[str], ranked: list[dict]) -> str:
    """每个目录一行：文件数、行数、主要角色、最相关的几个文件。只是事实统计，不是代码片段。"""
    vend = vendored_dirs(files)

    def inside(path: str, d: str) -> bool:
        return "/" not in path if not d else path.startswith(d + "/")

    lines = []
    for d in depth_dirs:
        sub = [f for f in files if inside(f["path"], d)]
        if not sub:
            continue
        roles = Counter(f["role"] for f in sub)
        main_roles = "、".join(f"{r} {c}" for r, c in roles.most_common(3))
        top = [r["path"].rsplit("/", 1)[-1] for r in ranked if inside(r["path"], d) and r["score"] > 0 and not r.get("vendored")][:3]
        tag = "；自带依赖/构建文件，规模较大，可能是拷贝来的第三方项目" if d in vend else ""
        lines.append(f"- {d or '(根目录)'}/ — {len(sub)} 个文件，{sum(f['lines'] for f in sub)} 行（{main_roles}）" + (f"；与论文最相关的文件：{'、'.join(top)}" if top else "") + tag)
    return "\n".join(lines)
