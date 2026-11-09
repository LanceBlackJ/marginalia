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
NOT_METHOD = re.compile(r"related|background|experiment|empirical|result|evaluat|conclusion|discussion|limitation|ethic|acknowledg|appendix|reference|ablation|baseline|dataset|future work|引言|相关工作|实验|结果|结论|讨论|附录|参考文献", re.I)
SKIP_SECTION = re.compile(r"introduction|abstract|overview|首部信息|正文|引言|摘要", re.I)
GENERIC_TITLES = re.compile(r"^(?:\d+\.?\s*)?(methods?|model|results?|discussion|introduction|abstract|references?|conclusions?|online methods|首部信息)\s*$", re.I)
GENERIC_TITLE_WORDS = set("language languages model models large small learning neural network networks deep adaptation method methods approach analysis study towards using based".split())
STOP = set(
    "the a an of and or for to in on with by from at as is are we our this that these those it its be using use used based can "
    "self not only any all each other such which where when then also here there into over under than more most may will would "
    "one two pre different describe described given let thus hence since while both its their".split()
)
_SURFACE: dict[str, str] = {}  # 词干 → 最短的原词，用来把匹配词显示成人能读的样子（paramet → parameter）


def surface(term: str) -> str:
    return _SURFACE.get(term, term)


def split_identifier(text: str) -> list[str]:
    parts = re.split(r"[^A-Za-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text))
    return [p.lower() for p in parts if len(p) > 1 and not p.isdigit()]


def stem(w: str) -> str:
    for suf in ("ization", "ations", "ation", "ings", "ing", "ers", "er", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            w = w[: -len(suf)]
            break
    if len(w) > 4 and w.endswith("e"):  # scale / scaling、update / updating 要落在同一个词干上
        w = w[:-1]
    return w


def ident_tokens(text: str) -> list[str]:
    """把文字或标识符拆成小写词。驼峰要拆开（LoRALayer → lora、layer），但 LoRA 这种缩写本身也是一个词：
    相邻的短片段会再并回去（lo+ra → lora），否则论文里的 “LoRA” 和代码里的 lora_A / LoRALayer 对不上。"""
    out: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9]*", text or ""):
        spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", word))
        parts = spaced.split()
        low = [p.lower() for p in parts]
        if len(parts) > 1:
            for i in range(len(low) - 1):
                if len(low[i]) <= 3 and len(low[i] + low[i + 1]) <= 6:
                    out.append(low[i] + low[i + 1])
            low = [p for p in low if len(p) > 2]  # 驼峰碎片里的 lo、ra 这类两个字母的碎片没有意义
        else:
            low = [p for p in low if len(p) > 1]
        out += [p for p in low if not p.isdigit()]
    return out


def tokens(text: str) -> list[str]:
    out = []
    for t in ident_tokens(text):
        if t in STOP:
            continue
        s = stem(t)
        if s not in _SURFACE or len(t) < len(_SURFACE[s]):
            _SURFACE[s] = t
        out.append(s)
    return out


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
    # 祖先章节：方法章节（如 “4 OUR METHOD”）下面的小节（4.1、4.2）标题里不一定带“方法”之类的词，但它们讲的就是方法；
    # 反过来，相关工作、实验这些章节下面的小节，即使标题里有 learning、model 之类的词，也不是方法。
    stack: list[tuple[int, bool, bool]] = []  # (level, 是方法章节, 是非方法章节)
    for s in secs:
        title = re.sub(r"^\s*\d+(\.\d+)*\.?\s*", "", s["title"]).strip(" .")
        while stack and stack[-1][0] >= s["level"]:
            stack.pop()
        in_method = any(m for _, m, _ in stack)
        in_other = any(o for _, _, o in stack)
        is_other = bool(NOT_METHOD.search(title))
        is_method = bool(METHOD_SECTION.search(title)) and not is_other
        stack.append((s["level"], is_method, is_other))
        if not title or GENERIC_TITLES.match(s["title"].strip()) and s["id"] in has_child:
            continue
        if in_other or is_other:
            continue
        if SKIP_SECTION.search(title) and s["id"] in has_child | {s["id"]}:
            continue
        ps = by_sec.get(s["id"], [])
        if not ps:
            continue
        text = " ".join(p["text"] for p in ps[:3])
        out.append({
            "name": title[:60], "description": text[:360], "passage_ords": [p["ord"] for p in ps[:6]], "pages": sorted({p["page"] for p in ps[:6]}),
            "section": s["title"], "keywords": keyword_terms(f"{title} {text}", 10), "source": "section",
        })
    return out[:60]


async def ai_modules(app: AppContext, paper_id: str, cfg: dict, provider, base: list[dict]) -> list[dict] | None:
    with app.db.session() as conn:
        paper = conn.execute("SELECT title FROM papers WHERE id = ?", (paper_id,)).fetchone()
        rows = conn.execute(
            "SELECT p.ord, p.page, s.title AS sec, p.text FROM passages p LEFT JOIN sections s ON s.id = p.section_id "
            "WHERE p.paper_id = ? AND p.kind = 'paragraph' ORDER BY p.ord", (paper_id,)).fetchall()
    chosen = [r for r in rows if r["sec"] and not NOT_METHOD.search(r["sec"]) and not SKIP_SECTION.search(r["sec"])] or rows[:60]
    body, used, valid = [], 0, set()
    for r in chosen:
        t = r["text"][:700]
        if used + len(t) > 22000:
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
    return mods[:30] or None


# ---------------- 代码侧：BM25 ----------------
class SymbolIndex:
    def __init__(self, app: AppContext, repo_id: str):
        from . import context as cx  # 延迟导入：context 依赖本模块的 split_identifier
        repo = service.get_repo(app, repo_id)
        with app.db.session() as conn:
            files = conn.execute("SELECT path, lines, role, language FROM code_files WHERE repo_id = ? AND skipped IS NULL", (repo_id,)).fetchall()
        self.vendored = cx.vendored_dirs(list(files))
        self.test_files = {f["path"] for f in files if f["role"] == "test"}
        self.title_terms = set(cx.paper_terms({"title": cx.paper_info(app, repo["paper_id"])["title"], "abstract": ""}))
        with app.db.session() as conn:
            syms = conn.execute(
                "SELECT id, path, name, qualname, kind, line_start, line_end, signature, doc FROM code_symbols WHERE repo_id = ? AND kind IN ('function','method','class','main')", (repo_id,)).fetchall()
            calls = conn.execute("SELECT caller_id, callee_name FROM code_calls WHERE repo_id = ?", (repo_id,)).fetchall()
        callnames: dict[int, list[str]] = {}
        for c in calls:
            callnames.setdefault(c["caller_id"], []).append(c["callee_name"])
        self.syms = {s["id"]: dict(s) for s in syms}
        bodies = self._body_tokens(app, repo_id, syms)
        self.docs: dict[int, Counter] = {}
        for s in syms:
            toks: list[str] = []
            toks += tokens(s["qualname"]) * 3 + tokens(s["name"]) * 2
            toks += tokens(s["path"])
            toks += tokens(s["doc"] or "") * 2 + tokens(s["signature"] or "")
            for cn in callnames.get(s["id"], [])[:40]:
                toks += tokens(cn)
            toks += bodies.get(s["id"], [])
            self.docs[s["id"]] = Counter(toks)
        self.n = max(1, len(self.docs))
        self.avg = sum(sum(c.values()) for c in self.docs.values()) / self.n or 1.0
        self.df: Counter = Counter()
        for c in self.docs.values():
            self.df.update(c.keys())

    def _body_tokens(self, app: AppContext, repo_id: str, syms) -> dict[int, list[str]]:
        """函数/类体里出现的标识符（lora_A、merged、scaling 这类名字不在函数名和注释里，但论文正文会提到）。
        只读作者自己的非测试代码——拷贝来的第三方目录太大，而且本来就不该成为候选。每个符号最多取 80 个不同的词、每个词算一次。"""
        by_file: dict[str, list] = {}
        for s in syms:
            p = s["path"]
            if p in self.test_files or any(p.startswith(d + "/") for d in self.vendored):
                continue
            by_file.setdefault(p, []).append(s)
        out: dict[int, list[str]] = {}
        for path, items in by_file.items():
            try:
                lines = service.file_lines(app, repo_id, path) or []
            except ApiError:
                continue
            for s in items:
                text = "\n".join(lines[s["line_start"] - 1: min(s["line_end"], s["line_start"] + 400)])
                out[s["id"]] = list(dict.fromkeys(tokens(text)))[:80]
        return out

    def query_terms(self, text: str, limit: int = 30) -> list[str]:
        """论文一节的正文 → 去代码里找的词：只留代码里真有的词，按 出现次数 × 在代码里的稀有程度 取前几个。
        论文里的泛词（model、network）在代码里到处都是，稀有度低，自然排后面。"""
        tf = Counter(t for t in tokens(text) if t in self.df)
        scored = {t: c * math.log(1 + self.n / self.df[t]) for t, c in tf.items() if self.df[t] < max(0.2 * self.n, 8)}  # 几乎每个符号里都有的词（self、model……）没有区分力；小仓库不按比例砍
        return sorted(scored, key=lambda t: -scored[t])[:limit]

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
                hit.append(surface(term))
            if sc > 0:
                res.append((sid, sc, hit))
        res = [(sid, sc * self.weight(sid), hit) for sid, sc, hit in res]
        res.sort(key=lambda x: -x[1])
        return res[:top]

    def weight(self, sid: int) -> float:
        """论文关联的候选不该是测试代码或拷贝来的第三方代码；名称里带论文标题里的专有词（LoRA 论文里的 lora）的，更可能是论文自己的实现。"""
        sym = self.syms[sid]
        p = sym["path"]
        w = 1.0
        if p in self.test_files or "/test" in p.lower() or "test_" in p.rsplit("/", 1)[-1].lower():
            w *= 0.15
        if any(p.startswith(d + "/") for d in self.vendored):
            w *= 0.12
        name = f"{p} {sym['qualname']}".lower()
        if any(t in name for t in self.title_terms if len(t) >= 4 and t not in GENERIC_TITLE_WORDS):
            w *= 1.8
        return w


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


def module_query(m: dict, idx: "SymbolIndex | None" = None, body: str = "") -> list[str]:
    q = tokens(m["name"]) * 3
    for k in m.get("keywords", []):
        q += tokens(k) * 2
    q += tokens(m.get("description", ""))
    if idx is not None and body:
        q += idx.query_terms(body)  # 整节正文里和代码词汇重合的词（只看描述的前 360 字符会漏掉 scaling、merge 这类实现细节）
    return q


# ---------------- 代码优先：每个函数/类 → 论文里的对应段落 ----------------
THIRD_PARTY_DIR = re.compile(r"(?:^|/)(?:third_?party|3rdparty|external|extern|vendor|deps|thirdparty|libs?)/", re.I)
MIN_SYMBOL_LINES = 4
MAX_AI_SYMBOLS = 240


class PaperIndex:
    """论文正文的 BM25 索引（段落为单位）：给定一个函数/类，找论文里最像在讲它的段落。"""

    def __init__(self, app: AppContext, paper_id: str):
        with app.db.session() as conn:
            rows = conn.execute(
                "SELECT p.ord, p.page, p.text, p.kind, p.section_id, s.title AS sec FROM passages p LEFT JOIN sections s ON s.id = p.section_id "
                "WHERE p.paper_id = ? AND p.kind IN ('paragraph', 'caption', 'formula') ORDER BY p.ord", (paper_id,)).fetchall()
        rows = [r for r in rows if not (r["sec"] and re.search(r"related work|reference|acknowledg|相关工作|参考文献|致谢", r["sec"], re.I))]
        self.rows = {r["ord"]: dict(r) for r in rows}
        self.docs: dict[int, Counter] = {}
        for r in rows:
            self.docs[r["ord"]] = Counter(tokens(r["text"]) + tokens(r["sec"] or "") * 2)
        self.n = max(1, len(self.docs))
        self.avg = sum(sum(c.values()) for c in self.docs.values()) / self.n or 1.0
        self.df: Counter = Counter()
        for c in self.docs.values():
            self.df.update(c.keys())

    def score(self, query: Counter, top: int = 4) -> list[tuple[int, float, list[str]]]:
        k1, b = 1.5, 0.75
        res = []
        for ord_, tf in self.docs.items():
            dl = sum(tf.values()) or 1
            sc, hit = 0.0, []
            for term, qw in query.items():
                f = tf.get(term, 0)
                if not f:
                    continue
                idf = math.log(1 + (self.n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                sc += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / self.avg)) * min(qw, 3)
                hit.append(surface(term))
            if len(hit) >= 2 or (hit and sc >= 4.0):
                res.append((ord_, sc, hit))
        res.sort(key=lambda x: -x[1])
        return res[:top]


def eligible_symbols(idx: "SymbolIndex") -> list[dict]:
    """要拿去和论文对照的函数/类：作者自己的、非测试、不是拷来的第三方库（整个目录、或一个文件里几百个符号的单头文件库）、不是几行的小函数。"""
    per_file = Counter(s["path"] for s in idx.syms.values())
    out = []
    for s in idx.syms.values():
        p = s["path"]
        if s["kind"] not in ("function", "method", "class"):
            continue
        if p in idx.test_files or any(p.startswith(d + "/") for d in idx.vendored) or THIRD_PARTY_DIR.search("/" + p) or per_file[p] > 250:
            continue
        if s["line_end"] - s["line_start"] + 1 < MIN_SYMBOL_LINES:
            continue
        out.append(s)
    out.sort(key=lambda s: (s["path"], s["line_start"]))
    return out


# 代码里到处都有、和论文内容无关的词（语言关键字、通用动词）：不拿来当匹配依据
CODE_NOISE = set("if else elif continue break return for while void int const auto true false null none nullptr max min set get size first last "
                 "follow time control index idx count tmp temp val value data ptr list vector string name type result res init update add remove "
                 "check make create compute calc free total each next prev current frame".split())
CODE_NOISE = CODE_NOISE | {stem(w) for w in CODE_NOISE}  # 索引里的词是词干（continue → continu）


def symbol_query(idx: "SymbolIndex", pidx: PaperIndex, s: dict) -> Counter:
    q: Counter = Counter()
    for t in tokens(s["qualname"]):
        q[t] += 3
    for t in tokens(s["name"]):
        q[t] += 2
    for t in tokens(s["doc"] or ""):
        q[t] += 2
    for t, c in idx.docs.get(s["id"], Counter()).most_common(60):
        q[t] += 1
    # 论文里几乎每段都有的词（method、approach）没有区分力
    return Counter({t: w for t, w in q.items() if t in pidx.df and t not in CODE_NOISE and pidx.df[t] <= max(0.2 * pidx.n, 6)})


CODEFIRST_SYSTEM = ("你是论文与代码对应关系的审阅者。下面有若干个代码符号，每个附有几段候选的论文段落（编号 P序号）。"
                    "对每个符号判断：它是否实现了/对应某一段候选论文内容。输出 JSON："
                    "{\"links\": [{\"symbol_id\": 整数, \"passage_ord\": 候选里的段落编号整数或 null, \"confidence\": \"likely\"|\"possible\"|\"no\", "
                    "\"basis\": \"一句话依据，必须点出代码里的具体标识符或步骤，以及论文段落里对应的说法\"}]}。"
                    "只有代码里有明确证据时才写 likely；只是名字或词语相似写 possible；与论文无关（工具函数、日志、解析等）写 no 且 passage_ord 为 null。"
                    "passage_ord 只能用候选里给出的编号，不要编造；每个符号只输出一项。")


async def judge_batch(app: AppContext, repo: dict, batch: list[tuple[dict, list[tuple[int, float, list[str]]]]], pidx: PaperIndex, cfg, provider) -> dict[int, dict] | None:
    blocks = []
    for s, cands in batch:
        cp = "\n".join(f"  [P{o}]（{pidx.rows[o]['sec'] or ''}）{pidx.rows[o]['text'][:420]}" for o, _sc, _hit in cands)
        blocks.append(f"symbol_id={s['id']} {s['qualname']} ({s['path']}:{s['line_start']}-{s['line_end']})\n签名：{s['signature']}\n文档：{s['doc'] or '无'}\n{_code_snip(app, repo['id'], s, 14)}\n候选论文段落：\n{cp}")
    user = "\n\n=====\n".join(blocks)
    messages = [{"role": "system", "content": CODEFIRST_SYSTEM}, {"role": "user", "content": user}]
    choice = choose_model(TaskType.METHOD, {**cfg, "mode": "fast"})  # 要逐个函数判断、批次很多：用快速模型，强模型太慢
    usage_log.record_request(app, task_type="code_associate", model=choice.model, paper_id=repo["paper_id"], passage_ords=sorted({o for _s, c in batch for o, _a, _b in c}), chars_sent=len(user),
                             includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": True},
                             summary=f"发送了 {len(batch)} 个函数的签名与开头代码，以及各自的候选论文段落（约 {len(user):,} 字符），用于判断它们和论文哪一段对应")
    try:
        res = await provider.complete(choice.model, messages, temperature=0.1, max_tokens=2500, json_mode=True)
    except LLMError:
        return None
    AIService(app)._log_usage("code_associate", choice.model, res.usage, messages, res.text, repo["paper_id"])
    m = re.search(r"\{.*\}", res.text, re.S)
    try:
        items = json.loads(m.group(0)).get("links", []) if m else None
    except ValueError:
        return None
    if items is None:
        return None
    return {v["symbol_id"]: v for v in items if isinstance(v, dict) and isinstance(v.get("symbol_id"), int)}


def _section_module(pidx: PaperIndex, cache: dict, ord_: int) -> dict:
    """论文段落所在的章节 → 模块（同一章节的所有关联归到同一个模块名下）。"""
    r = pidx.rows[ord_]
    key = r["section_id"]
    if key not in cache:
        sec = [x for x in pidx.rows.values() if x["section_id"] == key and x["kind"] == "paragraph"][:6]
        title = re.sub(r"^\s*\d+(\.\d+)*\.?\s*", "", r["sec"] or "").strip(" .") or f"第 {r['page']} 页"
        cache[key] = {"name": title[:60], "description": " ".join(x["text"] for x in sec[:3])[:360], "passage_ords": [x["ord"] for x in sec],
                      "pages": sorted({x["page"] for x in sec}) or [r["page"]], "section": r["sec"], "keywords": [], "source": "code"}
    return cache[key]


async def code_first(app: AppContext, repo: dict, idx: "SymbolIndex", provider, cfg, ctx: JobContext | None = None) -> dict:
    """读遍仓库里作者自己的函数/类，每个都去论文里找对应的段落。返回 {read, matched, links: [(module, symbol, conf, method, basis, hit, passage_ord)]}。"""
    pidx = PaperIndex(app, repo["paper_id"])
    syms = eligible_symbols(idx)
    cands: dict[int, list] = {}
    for s in syms:
        q = symbol_query(idx, pidx, s)
        if q:
            got = pidx.score(q, 4)
            if got:
                cands[s["id"]] = got
    by_id = {s["id"]: s for s in syms}
    links: list[tuple] = []
    sec_cache: dict = {}
    verdicts: dict[int, dict] = {}
    ai_ok = False
    if provider is not None:
        # 候选得分高的先送审（上限内），其余只给词法结果
        order = sorted(cands, key=lambda sid: -cands[sid][0][1])[:MAX_AI_SYMBOLS]
        batches = [[(by_id[sid], cands[sid]) for sid in order[i:i + 6]] for i in range(0, len(order), 6)]
        sem = asyncio.Semaphore(3)
        done = 0

        async def run(b):
            async with sem:
                return await judge_batch(app, repo, b, pidx, cfg, provider)

        for coro in asyncio.as_completed([run(b) for b in batches]):
            got = await coro
            done += 1
            if got is not None:
                ai_ok = True
                verdicts.update(got)
            if ctx:
                ctx.check_cancelled()
                ctx.progress(0.55 + 0.4 * done / max(1, len(batches)), f"已对照 {min(len(order), done * 6)}/{len(order)} 个函数和论文")
    matched: set[int] = set()
    for sid, cl in cands.items():
        s = by_id[sid]
        v = verdicts.get(sid)
        if v is not None:
            o = v.get("passage_ord")
            conf = v.get("confidence")
            if conf in ("likely", "possible") and isinstance(o, int) and o in {c[0] for c in cl}:
                hit = next(c[2] for c in cl if c[0] == o)
                basis = str(v.get("basis", ""))[:240]
                if conf == "likely" and not any(t.lower() in basis.lower() for t in {s["name"], *split_identifier(s["name"])} if len(t) > 2):
                    conf = "possible"  # 依据里没有点出这个函数自己的标识符：不允许认定
                links.append((_section_module(pidx, sec_cache, o), s, conf, "llm", basis, hit, o))
                matched.add(sid)
            continue  # 模型看过并判了 no / 不匹配：不再用词法结果硬凑
        o, _sc, hit = cl[0]
        if len(hit) >= 3:  # 没有模型时，只留词重合较多的
            links.append((_section_module(pidx, sec_cache, o), s, "possible", "lexical", f"名称/注释/调用名与论文这一节的词重合：{', '.join(hit[:6])}（仅词法匹配，需人工确认）", hit, o))
            matched.add(sid)
    return {"read": len(syms), "matched": len(matched), "links": links, "ai": ai_ok}


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


def _module_text(app: AppContext, paper_id: str, mod: dict) -> str:
    """论文模块对应的原文（它的 passage_ords 所指的段落）。"""
    ords = mod.get("passage_ords") or []
    if not ords:
        return ""
    with app.db.session() as conn:
        rows = conn.execute(f"SELECT text FROM passages WHERE paper_id = ? AND ord IN ({','.join('?' * len(ords))}) ORDER BY ord", (paper_id, *ords)).fetchall()
    return " ".join(r["text"] for r in rows)


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
            seen = {re.sub(r"\W+", "", m["name"].lower()) for m in mods}
            modules, used_ai = mods + [m for m in modules if re.sub(r"\W+", "", m["name"].lower()) not in seen], True  # 模型整理的模块 + 按章节切出的模块
    if not modules:
        raise ApiError("bad_request", "没有在论文里找到可对应的方法章节。")
    idx = SymbolIndex(app, repo_id)
    if not idx.syms:
        raise ApiError("bad_request", "仓库里没有可关联的函数/类。")
    created = 0
    sem = asyncio.Semaphore(3)
    n_modules = len(modules)  # 各模块的重排互相独立，并行调用以缩短等待

    async def judge(mod: dict):
        ranked = idx.score(module_query(mod, idx, _module_text(app, paper_id, mod)), 10)
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
    try:
        cf = await code_first(app, repo, idx, provider, cfg, ctx)
    except ApiError:
        raise
    except Exception:  # noqa: BLE001  代码优先只是补充：出错不影响按模块找到的关联
        cf = {"read": 0, "matched": 0, "links": [], "ai": False}
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
    with app.db.session() as conn:
        have = {(loads(r["module_json"], {}).get("name"), r["symbol_id"]) for r in conn.execute("SELECT module_json, symbol_id FROM paper_code_links WHERE repo_id = ?", (repo_id,)).fetchall()}
        for mod, s, conf, method, basis, hit, _o in cf["links"]:
            if (mod["name"], s["id"]) in have:
                continue
            have.add((mod["name"], s["id"]))
            conn.execute(
                "INSERT INTO paper_code_links(id, paper_id, repo_id, module_json, symbol_id, path, line_start, line_end, confidence, method, basis_json, status, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?, 'suggested', ?)",
                (new_id(), paper_id, repo_id, dumps({k: mod[k] for k in ("name", "description", "passage_ords", "pages", "section")}), s["id"], s["path"], s["line_start"], s["line_end"],
                 conf, method, dumps({"text": basis, "matched_terms": hit[:10], "also_in": []}), utcnow()),
            )
            created += 1
    return {"modules": n_modules, "links": created, "ai_used": used_ai or cf["ai"], "symbols_read": cf["read"], "symbols_matched": cf["matched"]}


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
