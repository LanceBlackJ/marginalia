"""论文参数 ↔ 代码参数的一致性检查（说明书 §15.6 的子集）。

论文侧：正则（免费、总是可用）+ 有 AI 时的补充抽取（引文必须真实存在于段落里才保留）。
代码侧：argparse 默认值、常量、调用里的关键字参数、配置文件。
比较结果只有 一致 / 不同 / 代码里没找到；对“不同”**不下结论说谁对**，只列出各自来源和可能的合理原因
（不同实验、不同代码版本、命令行默认值与论文实验设置不同、补充材料中另有设置）。
"""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from ..ai import usage as usage_log
from ..ai.providers import LLMError
from ..ai.router import TaskType, choose_model
from ..ai.service import AIService
from ..context import AppContext
from ..util import ApiError, dumps, loads, utcnow
from . import service

# 规范名 → 论文用语 / 代码里常见变量名（比较前统一小写并去掉非字母数字）
CANON: dict[str, list[str]] = {
    "learning_rate": ["lr", "learningrate", "learning rate", "alpha", "initlr", "baselr"],
    "batch_size": ["batchsize", "batch size", "minibatch", "bs", "nbatch"],
    "embedding_dim": ["embdim", "embeddingsize", "embeddingdimension", "embeddim", "embeddingdim", "embsize", "embedsize", "embedding dimension", "embedding size", "embeddingdimensions"],
    "hidden_dim": ["hiddendim", "hiddensize", "hiddenunits", "hidden", "nhid", "hid", "hidden size", "hidden units", "hidden dimension"],
    "epochs": ["epoch", "epochs", "numepochs", "nepochs", "maxepoch", "maxepochs"],
    "iterations": ["iteration", "iterations", "maxiteration", "numiterations", "niter", "maxiter", "numiter", "maxiterations"],
    "gamma": ["gamma", "discount", "discountfactor", "discount factor", "discountrate"],
    "dropout": ["dropout", "droprate", "dropoutrate", "dropprob"],
    "n_step": ["nstep", "nsteps", "numstep", "nstepreturn", "n-step"],
    "nodes_min": ["nodesmin", "nummin", "nmin", "minnodes", "minn", "numnodesmin", "min_nodes", "numminnodes"],
    "nodes_max": ["nodesmax", "nummax", "nmax", "maxnodes", "maxn", "numnodesmax", "max_nodes", "nummaxnodes"],
    "num_graphs": ["numgraphs", "ngraphs", "graphnum", "numtraingraphs", "traingraphs", "numtrain", "ntrain", "numsamples", "graphs"],
    "replay_size": ["memorysize", "replaysize", "buffersize", "memsize", "maxmemory", "replaybuffer", "replaymemory", "memorycapacity"],
    "weight_decay": ["weightdecay", "wd", "l2", "reg", "regularization"],
    "epsilon": ["epsilon", "eps", "epsstart", "epsilonstart"],
    "num_layers": ["numlayers", "nlayers", "layers", "depth", "layernum", "maxbpiter", "numhops", "nhops"],
    "optimizer": ["optimizer", "optim", "opt"],
}
LOOKUP = {re.sub(r"[^a-z0-9]", "", alias.lower()): canon for canon, aliases in CANON.items() for alias in [canon, *aliases]}
NUM = r"(\d[\d,]*(?:\.\d+)?(?:[eE]-?\d+)?)"
PATTERNS: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r"learning rate[^.\d]{0,30}" + NUM, re.I), ["learning_rate"]),
    (re.compile(r"batch size[^.\d]{0,30}" + NUM, re.I), ["batch_size"]),
    (re.compile(r"embedding (?:dimension|size|dimensionality)[^.\d]{0,30}" + NUM, re.I), ["embedding_dim"]),
    (re.compile(r"(?:hidden (?:size|dimension|units)|hidden layer size)[^.\d]{0,30}" + NUM, re.I), ["hidden_dim"]),
    (re.compile(NUM + r"[ -]?(?:training )?epochs", re.I), ["epochs"]),
    (re.compile(r"(?:discount factor|discount rate|\bgamma\b)[^.\d]{0,20}" + NUM, re.I), ["gamma"]),
    (re.compile(r"dropout(?: rate| probability)?[^.\d]{0,20}" + NUM, re.I), ["dropout"]),
    (re.compile(r"\b(\d+)-step", re.I), ["n_step"]),
    (re.compile(r"weight decay[^.\d]{0,20}" + NUM, re.I), ["weight_decay"]),
    (re.compile(r"(?:replay (?:buffer|memory)|memory) (?:size|capacity)?[^.\d]{0,30}" + NUM, re.I), ["replay_size"]),
    (re.compile(NUM + r"\s+(?:randomly generated |synthetic |small |training )*(?:synthetic )?(?:random )?graphs", re.I), ["num_graphs"]),
    (re.compile(r"(\d+)\s*(?:–|-|to)\s*(\d+)\s+nodes", re.I), ["nodes_min", "nodes_max"]),
    (re.compile(r"graphs? of (\d+)\s*(?:–|-|to)\s*(\d+)\s+nodes", re.I), ["nodes_min", "nodes_max"]),
]
REASONS = [
    "论文报告的可能是某个特定实验的设置，而代码里的是另一个实验或默认值",
    "代码仓库的版本可能与论文发表时不同",
    "代码里的值可能只是命令行/配置的默认值，实验时会被另行指定",
    "论文的补充材料里可能另有该参数的具体设置",
]


# AI 抽取项的“语义核对”：引文（及其所在句、上一句）里必须出现与该参数相符的词，否则只能标“待核查”。
# 例：模型把“30–50 个节点”归成 num_graphs，引文里虽有 graphs，但取值是区间 → 区间值不能对应“图的数量”。
CONTEXT: dict[str, tuple[str, ...]] = {
    "replay_size": ("replay", "memory", "buffer", "queue"),
    "num_graphs": ("graph",),
    "nodes_min": ("node",),
    "nodes_max": ("node",),
    "learning_rate": ("learning rate", "step size", "lr"),
    "batch_size": ("batch",),
    "embedding_dim": ("embedding", "dimension"),
    "hidden_dim": ("hidden",),
    "epochs": ("epoch",),
    "iterations": ("iteration", "iter"),
    "gamma": ("discount", "gamma", "γ"),
    "dropout": ("dropout",),
    "n_step": ("step",),
    "weight_decay": ("weight decay", "l2", "regulariz"),
    "num_layers": ("layer", "hop", "depth"),
}
RANGE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:-|–|—|~|to)\s*(\d+(?:\.\d+)?)\s*$")
SINGLE_ONLY = {"num_graphs", "replay_size", "batch_size", "epochs", "iterations", "n_step", "num_layers", "embedding_dim", "hidden_dim"}  # 计数/尺寸类：区间值不可能与之对应


def context_window(text: str, quote: str) -> str:
    """引文所在句 + 上一句（用于核对“这个数到底是什么的取值”）。找不到引文时返回引文本身。"""
    idx = [i for i, ch in enumerate(text) if not ch.isspace()]
    flat = "".join(text[i] for i in idx).lower()
    q = re.sub(r"\s+", "", quote).lower()
    pos = flat.find(q) if q else -1
    if pos < 0:
        return quote
    start, end = idx[pos], idx[pos + len(q) - 1] + 1
    p1 = text.rfind(". ", 0, start)
    a = p1 + 2 if p1 >= 0 else 0
    p2 = text.rfind(". ", 0, a - 2) if a >= 2 else -1
    a0 = p2 + 2 if p2 >= 0 else 0
    b = text.find(". ", end)
    return re.sub(r"\s+", " ", text[a0: b + 1 if b > 0 else len(text)]).strip()[-600:]


def check_ai_claim(name: str, value: str, quote: str, window: str) -> list[tuple[str, str, str, str | None]]:
    """核对一条 AI 抽取项，返回 [(规范名/原名, 值, check, 说明)]。check 为 ok 或 weak。

    - 区间值 + 上下文是“节点数” → 拆成 nodes_min / nodes_max（与规则抽取一致）；
    - 区间值却被归到计数/尺寸类参数 → weak；
    - 取值数字必须真的出现在引文里；
    - 参数相符的词必须出现在引文及其上下文里。
    """
    low = window.lower()
    rng = RANGE.match(value)
    cn = canon(name)
    if rng and cn in (None, "nodes_min", "nodes_max", "num_graphs") and "node" in low:
        return [("nodes_min", rng.group(1), "ok", None), ("nodes_max", rng.group(2), "ok", None)]
    if rng and cn in SINGLE_ONLY:
        return [(cn, value, "weak", "取值是一个区间，与该参数不对应，可能归类有误")]
    key = cn or name.lower()
    flat_quote = re.sub(r"[,\s]", "", quote.replace("−", "-"))
    digits = re.sub(r"[,\s]", "", value)
    if re.search(r"\d", digits) and digits not in flat_quote:
        return [(key, value, "weak", "引文里没有出现这个数值")]
    words = CONTEXT.get(cn or "")
    if words and not any(w in low for w in words):
        return [(key, value, "weak", "引文及其上下文里没有出现与参数相符的词，无法确认这个数指的是它")]
    return [(key, value, "ok", None)]


def canon(name: str) -> str | None:
    hit = LOOKUP.get(re.sub(r"[^a-z0-9]", "", name.lower().replace("-", "")))
    if hit:
        return hit
    # AI 给的名字五花八门（min_training_nodes / experience_replay_buffer_size …）：按词元兜底归一
    t = set(re.split(r"[^a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1_\2", name).lower()))
    if t & {"node", "nodes"}:
        if t & {"min", "minimum"}:
            return "nodes_min"
        if t & {"max", "maximum"}:
            return "nodes_max"
    if t & {"graph", "graphs"} and t & {"num", "number", "training", "train", "count", "total"}:
        return "num_graphs"
    if t & {"replay", "memory", "buffer"} and t & {"size", "capacity", "length"}:
        return "replay_size"
    if "batch" in t and t & {"size", "num"}:
        return "batch_size"
    if "learning" in t and "rate" in t:
        return "learning_rate"
    if "embedding" in t and t & {"size", "dim", "dimension", "dimensions"}:
        return "embedding_dim"
    if t & {"discount", "gamma"}:
        return "gamma"
    if t & {"epoch", "epochs"}:
        return "epochs"
    if t & {"iteration", "iterations", "iter", "iters"}:
        return "iterations"
    return None


def to_number(s: str) -> float | None:
    s = s.strip().strip("'\"").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def same_value(a: str, b: str) -> bool:
    x, y = to_number(a), to_number(b)
    if x is not None and y is not None:
        return abs(x - y) <= 1e-9 * max(1.0, abs(x), abs(y))
    return a.strip().strip("'\"").lower() == b.strip().strip("'\"").lower()


def _sentence(text: str, start: int, end: int) -> str:
    a = max(text.rfind(". ", 0, start) + 2, 0)
    b = text.find(". ", end)
    return re.sub(r"\s+", " ", text[a: b + 1 if b > 0 else len(text)]).strip()[:320]


# ---------------- 论文侧 ----------------
def paper_claims_regex(app: AppContext, paper_id: str) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute("SELECT ord, page, text FROM passages WHERE paper_id = ? AND kind = 'paragraph' ORDER BY ord", (paper_id,)).fetchall()
    out: list[dict] = []
    seen: set[tuple] = set()
    for r in rows:
        for rx, names in PATTERNS:
            for m in rx.finditer(r["text"]):
                vals = [g for g in m.groups() if g is not None]
                for name, val in zip(names, vals):
                    key = (name, val.replace(",", ""))
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append({"name": name, "value": val.replace(",", ""), "ord": r["ord"], "page": r["page"], "quote": _sentence(r["text"], m.start(), m.end()), "method": "regex"})
    return out


async def paper_claims_ai(app: AppContext, paper_id: str, cfg: dict, provider) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT p.ord, p.page, p.text, s.title AS sec FROM passages p LEFT JOIN sections s ON s.id = p.section_id WHERE p.paper_id = ? AND p.kind = 'paragraph' ORDER BY p.ord", (paper_id,)).fetchall()
    kw = re.compile(r"train|learning rate|batch|epoch|hyper|parameter|dimension|layer|optimi|implementation|experiment|setting|node|graph|iteration|step|dropout|embedding", re.I)
    chosen = [r for r in rows if kw.search(r["text"])]
    body, used = [], 0
    byord: dict[int, dict] = {}
    for r in chosen:
        t = r["text"][:1100]
        if used + len(t) > 14000:
            break
        body.append(f"[P{r['ord']}] {t}")
        byord[r["ord"]] = r
        used += len(t)
    if not body:
        return []
    system = ("你是严谨的论文信息抽取器。从【论文摘录】里抽取“可以和代码对照”的具体数值设置（学习率、批大小、嵌入维度、训练轮数/迭代数、网络层数、折扣因子、"
              "n-step、训练图的节点数范围与数量、随机种子等）。每条给出：name（英文小写下划线，如 learning_rate）、value（数字或名称，字符串）、"
              "ord（所在摘录编号整数）、quote（摘录里支撑它的**原文片段，必须逐字出自摘录**）。只抽取明确写出的数值，不要推算，不要编造。"
              '只输出 JSON：{"claims": [{"name": "", "value": "", "ord": 0, "quote": ""}]}')
    messages = [{"role": "system", "content": system}, {"role": "user", "content": "【论文摘录】\n" + "\n\n".join(body)}]
    choice = choose_model(TaskType.METHOD, cfg)
    usage_log.record_request(app, task_type="code_consistency", model=choice.model, paper_id=paper_id, passage_ords=sorted(byord), chars_sent=used,
                             includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": False},
                             summary=f"发送了 {len(body)} 段论文原文（约 {used:,} 字）用于抽取数值设置")
    try:
        res = await provider.complete(choice.model, messages, temperature=0, max_tokens=2500, json_mode=True)
    except LLMError:
        return []
    AIService(app)._log_usage("code_consistency", choice.model, res.usage, messages, res.text, paper_id)
    m = re.search(r"\{.*\}", res.text, re.S)
    try:
        data = json.loads(m.group(0)).get("claims", []) if m else []
    except ValueError:
        return []
    out = []
    norm = lambda s: re.sub(r"\s+", "", str(s)).lower()  # noqa: E731
    for c in data:
        if not isinstance(c, dict) or not isinstance(c.get("ord"), int) or c["ord"] not in byord:
            continue
        quote, name, val = str(c.get("quote", "")), str(c.get("name", "")).strip(), str(c.get("value", "")).strip()
        if not (quote and name and val) or norm(quote) not in norm(byord[c["ord"]]["text"]):
            continue  # 引文不在原段落里 → 丢弃（防止编造）
        window = context_window(byord[c["ord"]]["text"], quote)
        for key, value, check, why in check_ai_claim(name, val.replace(",", ""), quote, window):
            out.append({"name": key, "value": value, "ord": c["ord"], "page": byord[c["ord"]]["page"], "quote": quote[:320],
                        "context": window, "method": "ai", "check": check, "check_note": why})
    return out


# ---------------- 代码侧 ----------------
def config_params(app: AppContext, repo_id: str) -> list[dict]:
    with app.db.session() as conn:
        files = [r["path"] for r in conn.execute("SELECT path FROM code_files WHERE repo_id = ? AND role = 'config' AND skipped IS NULL LIMIT 40", (repo_id,)).fetchall()]
    out = []
    kv = re.compile(r"^\s*[\"']?([A-Za-z_][\w\-\.]*)[\"']?\s*[:=]\s*([\-+]?\d[\d\.eE\-+]*|true|false|[A-Za-z_]+)\s*,?\s*(?:[#;].*)?$")
    for path in files:
        lines = service.file_lines(app, repo_id, path) or []
        for i, line in enumerate(lines[:2000], 1):
            m = kv.match(line)
            if m and canon(m.group(1)):
                out.append({"path": path, "line": i, "name": m.group(1), "value": m.group(2), "origin": "config", "context": PurePosixPath(path).name})
    return out


def code_params(app: AppContext, repo_id: str) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute("SELECT path, line, name, value, origin, context FROM code_params WHERE repo_id = ?", (repo_id,)).fetchall()
    return [dict(r) for r in rows] + config_params(app, repo_id)


def compare(claims: list[dict], params: list[dict], repo: dict, paper_year: int | None) -> list[dict]:
    by_canon: dict[str, list[dict]] = {}
    for p in params:
        c = canon(p["name"])
        if c:
            by_canon.setdefault(c, []).append(p)
    # 研究仓库常有多份近似拷贝：相同（名字, 值, 来源）合并成一条，记录“另有 N 处”
    for k, ps in list(by_canon.items()):
        merged: dict[tuple, dict] = {}
        for p in ps:
            key = (p["name"].lower(), p["value"], p["origin"], p.get("context", ""))
            if key in merged:
                merged[key].setdefault("also_in", []).append(f"{p['path']}:{p['line']}")
            else:
                merged[key] = dict(p)
        by_canon[k] = list(merged.values())
    items = []
    stale = ""
    if repo.get("commit_date") and paper_year:
        try:
            if int(repo["commit_date"][:4]) - paper_year > 2:
                stale = f"代码最后提交于 {repo['commit_date'][:4]} 年，论文发表于 {paper_year} 年，相差较多，实现可能已变化"
        except ValueError:
            pass
    for cl in claims:
        found = by_canon.get(cl["name"], [])
        label = cl["name"]
        if cl.get("check") == "weak":
            # AI 抽取项没通过语义核对：不给“一致/不一致”的结论，只列出来让用户回原文确认
            relation, note = "to_check", f"这条设置是 AI 抽取的，但没能通过核对：{cl.get('check_note')}。请回到论文原文确认它是什么参数的取值，再与代码对照。"
        elif not found:
            relation, note = "not_found", "在代码的 argparse、常量、调用参数和配置文件里没有找到对应的参数。"
        elif any(same_value(cl["value"], p["value"]) for p in found):
            relation, note = "match", "论文中的取值与代码中的某处设置一致。"
        else:
            relation = "differs"
            vals = sorted({p["value"] for p in found})
            note = f"论文写的是 {cl['value']}，代码里出现的取值是 {', '.join(vals[:5])}。这不一定是冲突，请核对它们是否对应同一个实验设置。"
        caveats = list(REASONS) if relation == "differs" else []
        if relation == "differs":
            if any(p["origin"] == "argparse" for p in found):
                caveats.insert(0, "代码里的值是命令行参数的默认值，论文实验时可能通过命令行另行指定")
            if len({p["value"] for p in found}) > 1:
                caveats.insert(0, "代码里同一参数有多个不同取值，可能分别对应不同实验或数据集")
            if stale:
                caveats.insert(0, stale)
        items.append({"parameter": label, "relation": relation, "paper": {**{k: cl[k] for k in ("value", "page", "ord", "quote", "method")}, "context": cl.get("context") or cl["quote"],
                                "check": cl.get("check", "ok"), "check_note": cl.get("check_note")},
                      "code": [{**{k: p[k] for k in ("path", "line", "name", "value", "origin", "context")}, "also_in": p.get("also_in", [])} for p in found[:6]], "note": note, "caveats": caveats[:5]})
    order = {"differs": 0, "to_check": 1, "match": 2, "not_found": 3}
    items.sort(key=lambda i: (order[i["relation"]], i["parameter"]))
    return items


async def run_check(app: AppContext, repo_id: str, ctx=None) -> dict:
    repo = service.get_repo(app, repo_id)
    if repo["status"] != "ready":
        raise ApiError("conflict", "代码仓库还没有分析完成。", 409)
    with app.db.session() as conn:
        paper = conn.execute("SELECT year FROM papers WHERE id = ?", (repo["paper_id"],)).fetchone()
    claims = paper_claims_regex(app, repo["paper_id"])
    ai_used = False
    try:
        provider, cfg = AIService(app).provider()
        extra = await paper_claims_ai(app, repo["paper_id"], cfg, provider)
        keys = {(c["name"], c["value"]) for c in claims}
        for c in extra:
            if (c["name"], c["value"]) not in keys:
                claims.append(c)
        ai_used = bool(extra)
    except LLMError:
        pass
    items = compare(claims, code_params(app, repo_id), repo, paper["year"])
    report = {
        "items": items, "generated_at": utcnow(), "ai_used": ai_used,
        "summary": {k: sum(1 for i in items if i["relation"] == k) for k in ("match", "differs", "not_found", "to_check")},
        "note": "这是参数层面的对照，只列出各自来源，不判断哪一方正确。“不同”常常源于不同实验、不同代码版本或命令行默认值，请对照论文与补充材料确认。",
        "repo": {"commit_sha": repo["commit_sha"], "commit_date": repo["commit_date"], "authored_by": repo["authored_by"]},
    }
    with app.db.session() as conn:
        conn.execute(
            "INSERT INTO code_consistency(repo_id, status, report_json, error, updated_at) VALUES (?, 'ready', ?, NULL, ?) "
            "ON CONFLICT(repo_id) DO UPDATE SET status='ready', report_json=excluded.report_json, error=NULL, updated_at=excluded.updated_at",
            (repo_id, dumps(report), utcnow()),
        )
    return report


def get_report(app: AppContext, repo_id: str) -> dict:
    with app.db.session() as conn:
        r = conn.execute("SELECT * FROM code_consistency WHERE repo_id = ?", (repo_id,)).fetchone()
    if r is None:
        return {"status": "none", "report": None, "updated_at": None, "error": None}
    return {"status": r["status"], "report": loads(r["report_json"]), "updated_at": r["updated_at"], "error": r["error"]}
