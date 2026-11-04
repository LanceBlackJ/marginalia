"""论文方法模块 ↔ 代码函数的双向关联。

候选来自词法检索（BM25：符号名/注释/参数名/被调用名/路径与论文方法词的重合），有 AI 时再让模型重排并写出依据。
**不确定就标“可能关联（possible）”而不是认定**；只有模型给出落在候选代码行范围内的具体依据时才允许 likely。
所有关联默认是“待确认（suggested）”，用户可确认/拒绝，可被其它模块引用。
"""
from __future__ import annotations

import asyncio
import json
import math
import re
from collections import Counter

from ..ai import usage as usage_log
from ..ai.providers import LLMError
from ..ai.retrieval import keyword_terms
from ..ai.router import TaskType, choose_model
from ..ai.service import AIService
from ..context import AppContext
from ..jobs import JobContext
from ..util import ApiError, dumps, loads, new_id, utcnow
from . import service

METHOD_SECTION = re.compile(r"method|model|framework|approach|algorithm|encod|decod|train|architecture|implementation|network|learning|optimi|inference|"
                            r"方法|模型|算法|训练|编码|解码|框架|网络|实现", re.I)
GENERIC_TITLES = re.compile(r"^(?:\d+\.?\s*)?(methods?|model|results?|discussion|introduction|abstract|references?|conclusions?|online methods|首部信息)\s*$", re.I)
STOP = set("the a an of and or for to in on with by from at as is are we our this that these those it its be using use used based can".split())


def split_identifier(text: str) -> list[str]:
    parts = re.split(r"[^A-Za-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text))
    return [p.lower() for p in parts if len(p) > 1 and not p.isdigit()]


def stem(w: str) -> str:
    for suf in ("ization", "ations", "ation", "ings", "ing", "ers", "er", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def tokens(text: str) -> list[str]:
    return [stem(t) for t in split_identifier(text) if t not in STOP]


# ---------------- 论文侧：方法模块 ----------------
def deterministic_modules(app: AppContext, paper_id: str) -> list[dict]:
    with app.db.session() as conn:
        secs = conn.execute("SELECT id, title, level, page, ord FROM sections WHERE paper_id = ? ORDER BY ord", (paper_id,)).fetchall()
        rows = conn.execute(
            "SELECT p.ord, p.page, p.text, p.section_id, p.kind FROM passages p WHERE p.paper_id = ? AND p.kind IN ('paragraph', 'caption') ORDER BY p.ord", (paper_id,)
        ).fetchall()
    by_sec: dict[int, list] = {}
    for r in rows:
        by_sec.setdefault(r["section_id"], []).append(r)
    out: list[dict] = []
    # 哪些章节有子章节：优先取子章节，避免把总标题 “Methods” 当成一个模块
    has_child = {secs[i]["id"] for i in range(len(secs) - 1) if secs[i + 1]["level"] > secs[i]["level"]}
    for s in secs:
        title = re.sub(r"^\s*\d+(\.\d+)*\.?\s*", "", s["title"]).strip(" .")
        if not title or GENERIC_TITLES.match(s["title"].strip()) and s["id"] in has_child:
            continue
        if not METHOD_SECTION.search(title) or title.lower() in ("references", "related work", "related works"):
            continue
        ps = by_sec.get(s["id"], [])
        if not ps:
            continue
        text = " ".join(p["text"] for p in ps[:3])
        out.append({
            "name": title[:60], "description": text[:360], "passage_ords": [p["ord"] for p in ps[:6]], "pages": sorted({p["page"] for p in ps[:6]}),
            "section": s["title"], "keywords": keyword_terms(f"{title} {text}", 10), "source": "section",
        })
    return out[:24]


async def ai_modules(app: AppContext, paper_id: str, cfg: dict, provider, base: list[dict]) -> list[dict] | None:
    with app.db.session() as conn:
        paper = conn.execute("SELECT title FROM papers WHERE id = ?", (paper_id,)).fetchone()
        rows = conn.execute(
            "SELECT p.ord, p.page, s.title AS sec, p.text FROM passages p LEFT JOIN sections s ON s.id = p.section_id "
            "WHERE p.paper_id = ? AND p.kind = 'paragraph' ORDER BY p.ord", (paper_id,)).fetchall()
    chosen = [r for r in rows if r["sec"] and METHOD_SECTION.search(r["sec"])] or rows[:60]
    body, used, valid = [], 0, set()
    for r in chosen:
        t = r["text"][:900]
        if used + len(t) > 14000:
            break
        body.append(f"[P{r['ord']}] ({r['sec'] or ''}) {t}")
        used += len(t)
        valid.add(r["ord"])
    if not body:
        return None
    system = ("你是论文方法分析助手。请从下面的【论文摘录】中列出论文提出的方法的各个“模块/组成部分/步骤”（如 编码、解码、训练、在线应用…），"
              "每个模块给出：name（简短中文或英文名）、description（一句话，说明做什么）、passage_ords（支撑它的摘录编号整数列表，必须来自摘录）、"
              "keywords（3~8 个用于在代码里搜索的英文关键词，如函数名/概念词）。只依据摘录，不要编造论文里没有的模块。"
              '只输出 JSON：{"modules": [{"name": "", "description": "", "passage_ords": [], "keywords": []}]}')
    messages = [{"role": "system", "content": system}, {"role": "user", "content": f"【论文】{paper['title']}\n\n【论文摘录】\n" + "\n\n".join(body)}]
    choice = choose_model(TaskType.METHOD, cfg)
    usage_log.record_request(app, task_type="code_modules", model=choice.model, paper_id=paper_id, passage_ords=sorted(valid), chars_sent=used,
                             includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": False},
                             summary=f"发送了 {len(body)} 段论文方法原文（约 {used:,} 字）用于提取方法模块")
    try:
        res = await provider.complete(choice.model, messages, temperature=0.1, max_tokens=2500, json_mode=True)
    except LLMError:
        return None
    AIService(app)._log_usage("code_modules", choice.model, res.usage, messages, res.text, paper_id)
    m = re.search(r"\{.*\}", res.text, re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        return None
    pages = {r["ord"]: r["page"] for r in rows}
    mods = []
    for it in data.get("modules", []):
        if not isinstance(it, dict) or not str(it.get("name", "")).strip():
            continue
        ords = [o for o in it.get("passage_ords", []) if isinstance(o, int) and o in valid]
        mods.append({
            "name": str(it["name"]).strip()[:60], "description": str(it.get("description", "")).strip()[:300], "passage_ords": ords,
            "pages": sorted({pages[o] for o in ords}), "section": None,
            "keywords": [str(k).strip().lower() for k in it.get("keywords", []) if str(k).strip()][:10], "source": "ai",
        })
    return mods[:20] or None


# ---------------- 代码侧：BM25 ----------------
class SymbolIndex:
    def __init__(self, app: AppContext, repo_id: str):
        with app.db.session() as conn:
            syms = conn.execute(
                "SELECT id, path, name, qualname, kind, line_start, line_end, signature, doc FROM code_symbols WHERE repo_id = ? AND kind IN ('function','method','class','main')", (repo_id,)).fetchall()
            calls = conn.execute("SELECT caller_id, callee_name FROM code_calls WHERE repo_id = ?", (repo_id,)).fetchall()
        callnames: dict[int, list[str]] = {}
        for c in calls:
            callnames.setdefault(c["caller_id"], []).append(c["callee_name"])
        self.syms = {s["id"]: dict(s) for s in syms}
        self.docs: dict[int, Counter] = {}
        for s in syms:
            toks: list[str] = []
            toks += tokens(s["qualname"]) * 3 + tokens(s["name"]) * 2
            toks += tokens(s["path"])
            toks += tokens(s["doc"] or "") * 2 + tokens(s["signature"] or "")
            for cn in callnames.get(s["id"], [])[:40]:
                toks += tokens(cn)
            self.docs[s["id"]] = Counter(toks)
        self.n = max(1, len(self.docs))
        self.avg = sum(sum(c.values()) for c in self.docs.values()) / self.n or 1.0
        self.df: Counter = Counter()
        for c in self.docs.values():
            self.df.update(c.keys())

    def score(self, query: list[str], top: int = 8) -> list[tuple[int, float, list[str]]]:
        res = []
        k1, b = 1.5, 0.75
        q = Counter(query)
        for sid, tf in self.docs.items():
            dl = sum(tf.values()) or 1
            sc, hit = 0.0, []
            for term, qw in q.items():
                f = tf.get(term, 0)
                if not f:
                    continue
                idf = math.log(1 + (self.n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                sc += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / self.avg)) * min(qw, 2)
                hit.append(term)
            if sc > 0:
                res.append((sid, sc, hit))
        res.sort(key=lambda x: -x[1])
        return res[:top]


def dedupe_copies(app: AppContext, repo_id: str, ranked: list[tuple[int, float, list[str]]], idx: "SymbolIndex") -> list[tuple[dict, float, list[str], list[str]]]:
    """研究仓库常有多份拷贝或变体（A/Model.py 与 B/Model.py）：同文件名且同符号名的候选合并成一条（取得分最高的），其余记入 also_in。"""
    groups: dict[tuple, int] = {}
    out: list[tuple[dict, float, list[str], list[str]]] = []
    for sid, sc, hit in ranked:
        s = idx.syms[sid]
        key = (s["qualname"], s["path"].rsplit("/", 1)[-1])
        if key in groups:
            out[groups[key]][3].append(f"{s['path']}:{s['line_start']}-{s['line_end']}")
            continue
        groups[key] = len(out)
        out.append((s, sc, hit, []))
    return out


def module_query(m: dict) -> list[str]:
    q = tokens(m["name"]) * 3
    for k in m.get("keywords", []):
        q += tokens(k) * 2
    q += tokens(m.get("description", ""))
    return q


def _code_snip(app: AppContext, repo_id: str, s: dict, n: int = 10) -> str:
    lines = service.file_lines(app, repo_id, s["path"]) or []
    return "\n".join(f"{i}| {lines[i - 1]}" for i in range(s["line_start"], min(s["line_end"], s["line_start"] + n - 1) + 1) if i - 1 < len(lines))


async def rerank(app: AppContext, repo: dict, mod: dict, cands: list[tuple[dict, float, list[str]]], cfg: dict, provider) -> list[dict] | None:
    system = ("你是论文与代码对应关系的审阅者。给定论文中的一个方法模块和若干候选代码符号，判断每个候选是否实现了该模块。"
              "输出 JSON：{\"links\": [{\"symbol_id\": 整数, \"confidence\": \"likely\"|\"possible\"|\"no\", \"basis\": \"一句话依据，必须指出代码里的具体行或标识符\", \"lines\": [起始行, 结束行]}]}。"
              "只有代码里有明确证据（函数体里直接实现了该模块描述的步骤/公式）才写 likely；只是名字或注释相似写 possible；不相关写 no。"
              "lines 必须落在该候选的行号范围内。不要编造候选里没有的符号。")
    blocks = []
    for s, sc, hit in cands:
        blocks.append(f"symbol_id={s['id']} {s['qualname']} ({s['path']}:{s['line_start']}-{s['line_end']})\n签名：{s['signature']}\n文档：{s['doc'] or '无'}\n匹配词：{', '.join(hit[:8])}\n{_code_snip(app, repo['id'], s)}")
    user = f"【论文模块】{mod['name']}\n说明：{mod['description']}\n\n【候选代码】\n" + "\n\n---\n".join(blocks)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    choice = choose_model(TaskType.METHOD, cfg)
    usage_log.record_request(app, task_type="code_associate", model=choice.model, paper_id=repo["paper_id"], passage_ords=[], chars_sent=len(user),
                             includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": True},
                             summary=f"发送了论文模块“{mod['name']}”的描述和 {len(cands)} 个候选函数的签名与开头代码（约 {len(user):,} 字符）")
    try:
        res = await provider.complete(choice.model, messages, temperature=0.1, max_tokens=1500, json_mode=True)
    except LLMError:
        return None
    AIService(app)._log_usage("code_associate", choice.model, res.usage, messages, res.text, repo["paper_id"])
    m = re.search(r"\{.*\}", res.text, re.S)
    try:
        return json.loads(m.group(0)).get("links", []) if m else None
    except ValueError:
        return None


async def associate(app: AppContext, repo_id: str, ctx: JobContext | None = None, *, use_ai: bool = True) -> dict:
    repo = service.get_repo(app, repo_id)
    if repo["status"] != "ready":
        raise ApiError("conflict", "代码仓库还没有分析完成。", 409)
    paper_id = repo["paper_id"]
    provider = cfg = None
    try:
        provider, cfg = AIService(app).provider() if use_ai else (None, None)  # use_ai=False：只做本地词法匹配，不发送任何内容给模型
    except LLMError:
        provider = None
    modules = deterministic_modules(app, paper_id)
    used_ai = False
    if provider is not None:
        mods = await ai_modules(app, paper_id, cfg, provider, modules)
        if mods:
            modules, used_ai = mods, True
    if not modules:
        raise ApiError("bad_request", "没有在论文里找到可对应的方法章节。")
    idx = SymbolIndex(app, repo_id)
    if not idx.syms:
        raise ApiError("bad_request", "仓库里没有可关联的函数/类。")
    created = 0
    sem = asyncio.Semaphore(3)  # 各模块的重排互相独立，并行调用以缩短等待

    async def judge(mod: dict):
        ranked = idx.score(module_query(mod), 10)
        if not ranked:
            return mod, [], None
        cands4 = dedupe_copies(app, repo_id, ranked, idx)[:6]
        cands = [(s, sc, hit) for s, sc, hit, _also in cands4]
        async with sem:
            verdicts = await rerank(app, repo, mod, cands, cfg, provider) if provider is not None else None
        return mod, cands4, verdicts

    judged = []
    done = 0
    for coro in asyncio.as_completed([judge(m) for m in modules]):
        judged.append(await coro)
        done += 1
        if ctx:
            ctx.check_cancelled()
            ctx.progress(0.1 + 0.85 * done / len(modules), f"已关联 {done}/{len(modules)} 个论文模块")
    order = {m["name"]: i for i, m in enumerate(modules)}
    judged.sort(key=lambda t: order.get(t[0]["name"], 0))
    with app.db.session() as conn:  # 重新生成时清掉旧的“待确认”，保留用户确认/拒绝过的
        conn.execute("DELETE FROM paper_code_links WHERE repo_id = ? AND status = 'suggested'", (repo_id,))
        kept = {(loads(r["module_json"], {}).get("name"), r["symbol_id"]) for r in conn.execute("SELECT module_json, symbol_id FROM paper_code_links WHERE repo_id = ?", (repo_id,)).fetchall()}
    for mod, cands4, verdicts in judged:
        if not cands4:
            continue
        cands = [(s, sc, hit) for s, sc, hit, _also in cands4]
        also = {s["id"]: al for s, _sc, _hit, al in cands4}
        picked: list[tuple[dict, str, str, str, list[str], tuple[int, int]]] = []
        if verdicts is not None:
            byid = {s["id"]: (s, sc, hit) for s, sc, hit in cands}
            for v in verdicts:
                sid = v.get("symbol_id")
                if not isinstance(sid, int) or sid not in byid or v.get("confidence") not in ("likely", "possible"):
                    continue
                s, sc, hit = byid[sid]
                lines = v.get("lines")
                rng = (s["line_start"], s["line_end"])
                conf = v["confidence"]
                if isinstance(lines, list) and len(lines) == 2 and all(isinstance(x, int) for x in lines) and s["line_start"] <= lines[0] <= lines[1] <= s["line_end"]:
                    rng = (lines[0], lines[1])
                elif conf == "likely":
                    conf = "possible"  # 没给出落在候选范围内的具体行 → 不允许认定
                picked.append((s, conf, "llm", str(v.get("basis", ""))[:240], hit, rng))
        else:
            for s, sc, hit in cands[:3]:
                picked.append((s, "possible", "lexical", f"名称/注释/调用名与论文模块的词重合：{', '.join(hit[:6])}（仅词法匹配，需人工确认）", hit, (s["line_start"], s["line_end"])))
        with app.db.session() as conn:
            for s, conf, method, basis, hit, rng in picked[:4]:
                if (mod["name"], s["id"]) in kept:
                    continue
                conn.execute(
                    "INSERT INTO paper_code_links(id, paper_id, repo_id, module_json, symbol_id, path, line_start, line_end, confidence, method, basis_json, status, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?, 'suggested', ?)",
                    (new_id(), paper_id, repo_id, dumps({k: mod[k] for k in ("name", "description", "passage_ords", "pages", "section")}), s["id"], s["path"], rng[0], rng[1],
                     conf, method, dumps({"text": basis, "matched_terms": hit[:10], "also_in": also.get(s["id"], [])}), utcnow()),
                )
                created += 1
    return {"modules": len(modules), "links": created, "ai_used": used_ai}


async def handle(ctx: JobContext) -> dict:  # pragma: no cover - 由同步包装调用
    return await associate(ctx.app, ctx.payload["repo_id"], ctx)


def link_to_dict(r, sym=None) -> dict:
    return {
        "id": r["id"], "paper_id": r["paper_id"], "repo_id": r["repo_id"], "module": loads(r["module_json"], {}), "symbol_id": r["symbol_id"],
        "path": r["path"], "line_start": r["line_start"], "line_end": r["line_end"], "confidence": r["confidence"], "method": r["method"],
        "basis": loads(r["basis_json"], {}), "status": r["status"], "created_at": r["created_at"],
        "symbol": sym,
    }


def list_links(app: AppContext, repo_id: str, status: str | None = None) -> list[dict]:
    q = "SELECT l.*, s.qualname AS sym_qual, s.kind AS sym_kind, s.signature AS sym_sig FROM paper_code_links l LEFT JOIN code_symbols s ON s.id = l.symbol_id WHERE l.repo_id = ?"
    args: list = [repo_id]
    if status:
        q += " AND l.status = ?"
        args.append(status)
    q += " ORDER BY (l.status = 'rejected'), (l.confidence != 'likely'), l.created_at"
    with app.db.session() as conn:
        rows = conn.execute(q, args).fetchall()
    return [link_to_dict(r, {"qualname": r["sym_qual"], "kind": r["sym_kind"], "signature": r["sym_sig"]}) for r in rows]


def set_link_status(app: AppContext, link_id: str, status: str) -> dict:
    with app.db.session() as conn:
        cur = conn.execute("UPDATE paper_code_links SET status = ? WHERE id = ?", (status, link_id))
        if cur.rowcount == 0:
            raise ApiError("not_found", "关联不存在", 404)
        r = conn.execute("SELECT * FROM paper_code_links WHERE id = ?", (link_id,)).fetchone()
    return link_to_dict(r)


def add_manual_link(app: AppContext, repo_id: str, module_name: str, symbol_id: int, passage_ords: list[int] | None) -> dict:
    repo = service.get_repo(app, repo_id)
    with app.db.session() as conn:
        s = conn.execute("SELECT * FROM code_symbols WHERE id = ? AND repo_id = ?", (symbol_id, repo_id)).fetchone()
        if s is None:
            raise ApiError("not_found", "符号不存在", 404)
        pages: list[int] = []
        if passage_ords:
            pages = sorted({r["page"] for r in conn.execute(f"SELECT page FROM passages WHERE paper_id = ? AND ord IN ({','.join('?' * len(passage_ords))})", (repo["paper_id"], *passage_ords)).fetchall()})
        lid = new_id()
        conn.execute(
            "INSERT INTO paper_code_links(id, paper_id, repo_id, module_json, symbol_id, path, line_start, line_end, confidence, method, basis_json, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?, 'likely', 'manual', ?, 'confirmed', ?)",
            (lid, repo["paper_id"], repo_id, dumps({"name": module_name[:60], "description": "", "passage_ords": passage_ords or [], "pages": pages, "section": None}),
             symbol_id, s["path"], s["line_start"], s["line_end"], dumps({"text": "用户手动建立的关联", "matched_terms": []}), utcnow()),
        )
        r = conn.execute("SELECT * FROM paper_code_links WHERE id = ?", (lid,)).fetchone()
    return link_to_dict(r)


def paper_for_symbol(app: AppContext, symbol_id: int) -> dict:
    """反向：从代码符号出发，给出已建立的关联和论文里可能相关的段落（词法检索，标“可能”）。"""
    with app.db.session() as conn:
        s = conn.execute("SELECT * FROM code_symbols WHERE id = ?", (symbol_id,)).fetchone()
        if s is None:
            raise ApiError("not_found", "符号不存在", 404)
        repo = conn.execute("SELECT paper_id FROM code_repos WHERE id = ?", (s["repo_id"],)).fetchone()
        links = conn.execute("SELECT * FROM paper_code_links WHERE symbol_id = ? AND status != 'rejected' ORDER BY (status != 'confirmed')", (symbol_id,)).fetchall()
        paper_id = repo["paper_id"]
        terms = list(dict.fromkeys([t for t in split_identifier(s["name"]) + split_identifier(s["doc"] or "") if t not in STOP and len(t) > 2]))[:10]
        passages = []
        if terms:
            match = " OR ".join(f'"{t}"' for t in terms)
            try:
                rows = conn.execute(
                    "SELECT p.ord, p.page, p.kind, p.text, sec.title AS section_title, bm25(passages_fts) AS rank FROM passages_fts f JOIN passages p ON p.id = f.rowid "
                    "LEFT JOIN sections sec ON sec.id = p.section_id WHERE passages_fts MATCH ? AND p.paper_id = ? AND p.kind != 'reference' ORDER BY rank LIMIT 5",
                    (match, paper_id)).fetchall()
            except Exception:  # noqa: BLE001  FTS 语法问题不应让接口失败
                rows = []
            lower_terms = [t.lower() for t in terms]
            for r in rows:
                hit = [t for t in lower_terms if t in r["text"].lower()]
                i = min((r["text"].lower().find(t) for t in hit), default=0)
                passages.append({"ord": r["ord"], "page": r["page"], "kind": r["kind"], "section_title": r["section_title"],
                                 "snippet": r["text"][max(0, i - 60): i + 180].strip(), "matched_terms": hit[:6], "confidence": "possible", "method": "lexical"})
    return {
        "symbol": {"id": s["id"], "qualname": s["qualname"], "path": s["path"], "line_start": s["line_start"], "line_end": s["line_end"]},
        "paper_id": paper_id, "links": [link_to_dict(r) for r in links], "related_passages": passages,
        "note": "“相关段落”只是名称/注释词的检索结果，属于可能相关，不代表论文这一段就是对该函数的描述。",
    }
