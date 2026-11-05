"""AI 代码解释：只发送必要的代码片段，流式生成，并回查真实文件/行号核实每条证据。

输出沿用研究卡片的结构：直接回答 / 详细解释 / 依据 / AI 推断 / 后续问题。
证据写作 `[C1]`（整个片段）、`[C1:12-30]`（片段内的行范围）或 `[path:12-30]`（仓库内任意文件）；
后端逐条核实：文件存在、行号在真实范围内、片段内引用必须落在给出的片段里。编造的引用标“无法核实”。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import AsyncIterator

from ..ai import evidence as ev
from ..ai import usage as usage_log
from ..ai.providers import LLMError, Usage
from ..ai.router import TaskType, can_escalate, choose_model
from ..ai.service import AIService, Event, llm_error_event
from ..context import AppContext
from ..util import ApiError, dumps, loads, new_id, utcnow
from . import context as ctx
from . import service
from .context import Snippet

MAX_CHARS = 17_000
MAX_TARGET_LINES = 220
KIND_TASK = {
    "file": TaskType.EXPLAIN, "repo": TaskType.EXPLAIN, "module": TaskType.EXPLAIN, "function": TaskType.EXPLAIN, "class": TaskType.EXPLAIN, "calls": TaskType.EXPLAIN, "line_range": TaskType.EXPLAIN,
    "algorithm": TaskType.METHOD, "dataflow": TaskType.METHOD, "variable": TaskType.METHOD,
}
INSTRUCTIONS = {
    "repo": (
        "给出整个仓库的“读码地图”，让没读过这份代码的人知道该看哪里。按这个顺序写：\n"
        "1. 直接回答：两三句话，说明这个仓库和论文是什么关系、论文提出的方法的核心实现在哪个目录/文件。\n"
        "2. 详细解释：先分块说明（论文核心实现；实验、训练和评测脚本；第三方或拷贝来的代码；数据与文档），每块一句话并附依据；再给出建议的阅读顺序，用 1. 2. 3. 编号，3 到 5 步，每步写“先看哪个文件、看什么、为什么”。\n"
        "给出的代码片段是和论文最相关的几个文件的结构摘录（定义行与紧跟的几行，中间省略），目录概况和符号索引只是背景。没有代码片段的部分只能根据名称判断，并写明“只从名称判断”；某个目录看上去是第三方代码的拷贝时，直说，不要把它当成论文的实现。"
    ),
    "module": "概述这个目录（模块）的作用：它由哪些文件组成、各自负责什么、文件之间如何配合，以及从哪个文件开始读。给出的片段是最相关几个文件的结构摘录（定义行与紧跟的几行，中间省略）；没有片段的文件只能根据文件名说“只从名称判断”。",
    "file": "概述这个文件的作用：列出主要的类/函数及各自职责，说明它与其他文件的关系（谁调用它、它调用谁）。",
    "function": "解释这个函数做什么：输入输出、关键步骤、它调用了谁、被谁调用，以及值得注意的实现细节。",
    "class": "解释这个类的职责、关键属性与方法，以及它们如何协作。",
    "algorithm": "解释这里实现的算法/方法的关键思路：逐步说明代码如何实现，指出能从代码里看出的对应算法或数学概念，并明确区分“代码直接体现的”和“你的推断”。",
    "dataflow": "追踪数据流：输入从哪里来，经过哪些变换，输出到哪里；每个关键变量标注其来源行。",
    "variable": "说明所问变量的来源、取值如何变化、被谁使用、用途是什么。",
    "calls": "说明这段代码的调用关系：它调用了哪些函数、被哪些地方调用，以及这些调用如何构成执行路径。",
    "line_range": "解释这段代码在做什么，以及它在所在函数/文件里的作用。",
}
SYSTEM = """你是 Marginalia 的论文代码阅读助手，帮助用户读懂论文作者提供的科研代码。

严格遵守：
1. 只能依据下面【代码片段】回答；每个片段有编号 [C1]、[C2]… 和 `文件:起止行`，行首带行号。
2. 每个关于代码行为的陈述后面标注依据，写法三选一：[C1]（整个片段）、[C1:12-30]（该片段内的行范围，行号必须是片段里真实显示出来的）、[文件路径:12-30]。绝不引用没有给出的行号或文件。片段里用“⋮”表示中间省略了若干行，省略的部分你没有看到，不要描述它。
3. 区分：代码里直接能看到的（带依据）；你的推断（放进“## AI 推断”并说明依据）；片段里看不出来的（直接说“所给代码片段里看不出”，不要猜）。动态行为（反射、回调、运行时才确定的调用）无法静态确定时要明说。
4. 用中文回答，代码标识符、函数名保持原样。先结论后解释，简单问题只给“直接回答”。
5. 你看不到这些代码被运行的结果，不要声称代码“已经跑通”或“效果很好”。
6. 如果给出了【论文相关段落】，它们是论文原文，只作背景：可以用它说明这段代码对应论文里的什么思想或公式，引用写 [P12]（只能引用给出的编号）。只有代码和论文的描述能逐点对上时才说“对应”；对不上或拿不准，就说“可能对应”并放进“## AI 推断”。段落标着“只是用词相近”时，先判断它和这段代码到底有没有关系，没关系就不要提。论文里的实验结果不是代码的行为。
7. 写给没读过这份代码的读者：先说这段代码整体做什么、为什么需要它，再讲关键步骤；不要逐行翻译代码，也不要堆砌术语。

输出格式（严格使用这些二级标题，没有内容的小节整节省略）：
## 直接回答
## 详细解释
## 依据
- [C1:12-30] 该段代码做了什么（一句话）；对应论文的写 [P12]
## AI 推断
- 推断内容（依据：[C1:12-30]）
## 后续问题
- 一个值得继续研究的具体问题"""

CITE_RE = re.compile(r"\[(?:(C\d+)(?::(\d+)(?:\s*[-–]\s*(\d+))?)?|([\w\.\-/\\]+\.\w+):(\d+)(?:\s*[-–]\s*(\d+))?|P(\d+))\]")


def _numbered(lines: list[str], a: int, b: int) -> str:
    return ctx.numbered(lines, list(range(a, min(b, len(lines)) + 1)))


def _sym_row(app: AppContext, repo_id: str, sid: int):
    with app.db.session() as conn:
        return conn.execute("SELECT * FROM code_symbols WHERE id = ? AND repo_id = ?", (sid, repo_id)).fetchone()


MODULE_FILES = 6
REPO_FILES = 7
ROLE_RANK = {"entry": 0, "train": 1, "model": 2, "config": 3, "util": 4, "dependency": 5}


def _in_dir(path: str, d: str) -> bool:
    return "/" not in path if not d else path.startswith(d + "/")


def _paper_brief(app: AppContext, repo_id: str) -> tuple[dict, str]:
    repo = service.get_repo(app, repo_id)
    info = ctx.paper_info(app, repo["paper_id"])
    text = ""
    if info["title"]:
        text = f"【对应的论文（背景资料，不可作为依据引用）】\n标题：{info['title']}\n" + (f"摘要：{info['abstract']}\n" if info["abstract"] else "")
    return repo, text


def _module_snippets(app: AppContext, repo_id: str, directory: str) -> tuple[list[Snippet], str]:
    """解释一个目录：按和论文的相关度挑最重要的几个文件，给它们的结构摘录；文件清单和符号索引只作背景，不可当依据。"""
    d = directory.strip().strip("/")
    if d in (".", ""):
        d = ""
    like = "%" if not d else d.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "/%"
    with app.db.session() as conn:
        files = conn.execute(
            "SELECT path, lines, role, language FROM code_files WHERE repo_id = ? AND skipped IS NULL AND path LIKE ? ESCAPE '!' ORDER BY path", (repo_id, like)
        ).fetchall()
        syms = conn.execute(
            "SELECT path, qualname, kind, line_start, line_end, signature FROM code_symbols WHERE repo_id = ? AND path LIKE ? ESCAPE '!' AND kind IN ('function','class','main') ORDER BY path, line_start",
            (repo_id, like),
        ).fetchall()
    if not files:
        raise ApiError("not_found", "这个目录里没有可解释的文本文件。", 404)
    repo, brief = _paper_brief(app, repo_id)
    ranked = ctx.rank_files(app, repo_id, repo["paper_id"], list(files), under=d)
    picks = [r["path"] for r in ranked[:MODULE_FILES]]
    if len(picks) < MODULE_FILES:  # 相关度打不出分的小目录：按角色补
        rest = sorted((f for f in files if f["lines"] > 0 and f["path"] not in picks), key=lambda f: (ROLE_RANK.get(f["role"], 9), -f["lines"]))
        picks += [f["path"] for f in rest[: MODULE_FILES - len(picks)]]
    snippets: list[Snippet] = []
    for i, path in enumerate(sorted(picks, key=lambda p: (p not in picks[:2], p))):
        sn = ctx.outline_snippet(app, repo_id, path, f"C{len(snippets) + 1}", max_chars=5000 if i == 0 else 3000 if i == 1 else 1900)
        if sn:
            snippets.append(sn)
    where = d or "仓库根目录"
    files_txt = "\n".join(f"- {f['path']}（{f['lines']} 行，{f['role']}）" for f in files[:60])
    desc = brief + f"目录 {where}（{len(files)} 个文本文件）\n【目录内文件清单（背景信息，不可作为依据引用）】\n" + files_txt
    if syms:
        sym_txt = "\n".join(f"- {s['path']}:{s['line_start']}-{s['line_end']} {s['qualname']}（{s['kind']}）{s['signature'] or ''}" for s in syms[:80])
        desc += "\n【目录内符号索引（仅签名，非代码片段，不可作为依据引用）】\n" + sym_txt
    return snippets, desc


def _repo_snippets(app: AppContext, repo_id: str) -> tuple[list[Snippet], str]:
    """总讲解：README，加上和论文最相关的几个文件的结构摘录；目录统计与入口只作背景。"""
    with app.db.session() as conn:
        files = conn.execute("SELECT path, lines, role, language FROM code_files WHERE repo_id = ? AND skipped IS NULL ORDER BY path", (repo_id,)).fetchall()
        syms = conn.execute("SELECT path, qualname, kind, line_start, line_end FROM code_symbols WHERE repo_id = ? AND kind IN ('function','class','main') ORDER BY path, line_start", (repo_id,)).fetchall()
    if not files:
        raise ApiError("not_found", "仓库里没有可解释的文本文件。", 404)
    repo, brief = _paper_brief(app, repo_id)
    ranked = ctx.rank_files(app, repo_id, repo["paper_id"], list(files))
    snippets: list[Snippet] = []
    readme_path, _ = ctx._readme_text(app, repo_id, list(files))
    if readme_path:
        sn = ctx.readme_snippet(app, repo_id, readme_path, "C1")
        if sn:
            snippets.append(sn)
    picks: list[str] = []
    per_dir: dict[str, int] = {}
    for r in ranked:  # 同一个目录最多两个，别让一个目录占满
        parent = r["path"].rsplit("/", 1)[0] if "/" in r["path"] else ""
        if r["path"] == readme_path or r.get("vendored") or per_dir.get(parent, 0) >= 2 or r["score"] <= 0 and len(picks) >= 3:
            continue
        picks.append(r["path"])
        per_dir[parent] = per_dir.get(parent, 0) + 1
        if len(picks) >= REPO_FILES:
            break
    # 每个顶层分组至少有它自己最相关的一个文件，免得整个分组只能靠名字判断
    covered = {p.split("/")[0] if "/" in p else "" for p in picks + ([readme_path] if readme_path else [])}
    groups = sorted({f["path"].split("/")[0] if "/" in f["path"] else "" for f in files})
    for g in groups:
        if g in covered or len(picks) >= REPO_FILES + 3:
            continue
        cand = next((r["path"] for r in ranked if _in_dir(r["path"], g) and r["path"] != readme_path and not r.get("vendored")), None)
        if cand:
            picks.append(cand)
            covered.add(g)
    for i, path in enumerate(picks):
        sn = ctx.outline_snippet(app, repo_id, path, f"C{len(snippets) + 1}", max_chars=6500 if i == 0 else 3000 if i == 1 else 1700)
        if sn:
            snippets.append(sn)
    # 目录统计：顶层目录，大目录再展开第二层
    top = sorted({f["path"].split("/")[0] if "/" in f["path"] else "" for f in files}, key=lambda d: (d != "", d))
    dirs: list[str] = []
    for t in top:
        dirs.append(t)
        sub = [f for f in files if _in_dir(f["path"], t) and t and f["path"].count("/") >= 2]
        if len(sub) >= 40:
            dirs += sorted({"/".join(f["path"].split("/")[:2]) for f in sub})[:10]
    ov = repo.get("overview") or {}
    vend = ctx.vendored_dirs(list(files))
    entries = [e for e in (ov.get("entry_points") or []) if not any(e["path"].startswith(v + "/") for v in vend)][:6]
    NL = "\n"
    desc = brief + f"整个仓库（{len(files)} 个文本文件，{len(top)} 个顶层分组）" + NL + "【目录概况（统计信息，不是代码片段，不可作为依据引用）】" + NL + ctx.dir_summary(list(files), dirs, ranked)
    if entries:
        desc += NL + "【静态分析找到的入口（不是代码片段，不可作为依据引用）】" + NL + NL.join(f"- {e['path']}（{e.get('kind') or ''}）" for e in entries)
    if syms:
        desc += NL + "【符号索引（仅签名，非代码片段，不可作为依据引用）】" + NL + NL.join(
            f"- {s['path']}:{s['line_start']}-{s['line_end']} {s['qualname']}（{s['kind']}）" for s in syms if len(s["qualname"].rsplit(".", 1)[-1]) > 1 and not any(s["path"].startswith(v + "/") for v in vend)
        )[:3500]
    return snippets, desc


def build_snippets(app: AppContext, repo_id: str, target: dict) -> tuple[list[Snippet], str]:
    """返回 (片段, 目标描述)。只取必要部分，总量受 MAX_CHARS 限制。"""
    kind = target.get("kind") or "function"
    if kind == "repo":
        out = _repo_snippets(app, repo_id)
        while sum(s.chars for s in out[0]) > MAX_CHARS and len(out[0]) > 1:
            out[0].pop()
        return out
    if kind == "module":
        out = _module_snippets(app, repo_id, str(target.get("path") or "."))
        while sum(s.chars for s in out[0]) > MAX_CHARS and len(out[0]) > 1:
            out[0].pop()
        return out
    path = target.get("path")
    sid = target.get("symbol_id")
    snippets: list[Snippet] = []
    desc = ""
    sym = _sym_row(app, repo_id, sid) if sid else None
    if sym is None and target.get("symbol") and path:  # 只给了文件和函数名
        with app.db.session() as conn:
            sym = conn.execute(
                "SELECT * FROM code_symbols WHERE repo_id = ? AND path = ? AND (qualname = ? OR name = ?) ORDER BY line_start LIMIT 1",
                (repo_id, path, target["symbol"], target["symbol"]),
            ).fetchone()
    if sym is not None:
        path = sym["path"]
    if not path:
        raise ApiError("bad_request", "请指定要解释的文件或函数")
    lines = service.file_lines(app, repo_id, path)
    if lines is None:
        raise ApiError("not_found", "该文件不存在或是二进制文件，无法解释。", 404)
    n = len(lines)

    def add(role: str, a: int, b: int, p: str = path, src: list[str] | None = None) -> None:
        src = src if src is not None else lines
        a, b = max(1, a), min(b, len(src))
        if a > b:
            return
        text = _numbered(src, a, b)
        snippets.append(Snippet(f"C{len(snippets) + 1}", p, a, b, text, role, len(text)))

    if kind == "file" or (sym is None and kind not in ("line_range",)):
        desc = f"文件 {path}（共 {n} 行）"
        if n <= 300:
            add("target", 1, n)
        else:
            osn = ctx.outline_snippet(app, repo_id, path, f"C{len(snippets) + 1}", per_symbol=2, whole_if=0, max_chars=4500)
            if osn:
                snippets.append(osn)
            with app.db.session() as conn:
                syms = conn.execute("SELECT qualname, kind, line_start, line_end, signature FROM code_symbols WHERE repo_id = ? AND path = ? ORDER BY line_start", (repo_id, path)).fetchall()
            big = sorted((s for s in syms if s["kind"] in ("function", "method", "main")), key=lambda s: s["line_end"] - s["line_start"], reverse=True)[:3]
            for s in sorted(big, key=lambda s: s["line_start"]):
                add("target", s["line_start"], min(s["line_end"], s["line_start"] + 60))
        with app.db.session() as conn:
            syms = conn.execute("SELECT qualname, kind, line_start, line_end, signature FROM code_symbols WHERE repo_id = ? AND path = ? ORDER BY line_start", (repo_id, path)).fetchall()
        index = "\n".join(f"- {s['qualname']}（{s['kind']}，{s['line_start']}-{s['line_end']}）{s['signature'] or ''}" for s in syms[:80])
        if index:
            desc += "\n【文件内符号索引（仅签名，非代码片段，不可作为依据引用）】\n" + index
    elif kind == "line_range" and sym is None:
        a, b = int(target.get("line_start") or 1), int(target.get("line_end") or min(n, 60))
        desc = f"{path} 第 {a}-{b} 行"
        add("target", a, min(b, a + MAX_TARGET_LINES))
    else:
        assert sym is not None
        a, b = sym["line_start"], sym["line_end"]
        desc = f"{sym['kind']} {sym['qualname']}（{path}:{a}-{b}）" + (f"；关注变量：{target.get('variable')}" if kind == "variable" and target.get("variable") else "")
        add("target", a, min(b, a + MAX_TARGET_LINES - 1))
        g = service.symbol_graph(app, repo_id, sym["id"])
        for c in g["callees"]:
            if c["callee_id"] and c["certainty"] in ("resolved", "name") and not any(s.path == c["path"] and s.line_start == c["line_start"] for s in snippets):
                cl = service.file_lines(app, repo_id, c["path"])
                if cl and len([s for s in snippets if s.role == "callee"]) < 6:
                    add("callee", c["line_start"], min(c["line_end"], c["line_start"] + 14), c["path"], cl)
        for c in g["callers"][:4]:
            cl = service.file_lines(app, repo_id, c["path"])
            if cl:
                add("caller", c["line"] - 2, c["line"] + 2, c["path"], cl)
        if a > 40:
            add("header", 1, 25)
    # 总量限制：从后往前丢弃次要片段
    while sum(s.chars for s in snippets) > MAX_CHARS and len(snippets) > 1:
        snippets.pop()
    return snippets, desc


def build_messages(desc: str, snippets: list[Snippet], kind: str, question: str, papers: list[dict] | None = None) -> list[dict]:
    body = "\n\n".join(f"{s.header()}\n{s.text}" for s in snippets)
    task = INSTRUCTIONS.get(kind, INSTRUCTIONS["function"])
    pb = ctx.paper_block(papers or [])
    user = f"【解释对象】{desc}\n\n【代码片段】\n{body}\n\n" + (pb + "\n\n" if pb else "") + f"【任务】{task}\n【用户问题】{question.strip() or task}"
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


# ---------------- 证据核实 ----------------
MULTI_CITE_RE = re.compile(r"\[([^\[\]\n]{3,160})\]")
_ONE_CITE = re.compile(r"(?:C\d+(?::\d+(?:\s*[-–]\s*\d+)?)?|P\d+|[\w\.\-/\\]+\.\w+:\d+(?:\s*[-–]\s*\d+)?)")
_BARE_RANGE = re.compile(r"\d+(?:\s*[-–]\s*\d+)?")


def split_multi_cites(markdown: str) -> str:
    """模型有时把几个引用写进同一对括号，如 [C1:148,152] 或 [C1:149, C2:23-24]。拆成单个引用，才能逐个核实并改写成 [L#]。
    只有括号里每一项都是合法引用时才拆；其它方括号原样保留。"""

    def fix(m: re.Match) -> str:
        parts = [p.strip() for p in re.split(r"[,，;；]", m.group(1)) if p.strip()]
        if len(parts) < 2:
            return m.group(0)
        out, label = [], None
        for p in parts:
            if _ONE_CITE.fullmatch(p):
                head = re.match(r"(C\d+):", p)
                label = head.group(1) if head else (p if re.fullmatch(r"C\d+", p) else None)
                out.append(p)
            elif label and _BARE_RANGE.fullmatch(p):  # 逗号后只写了行号：沿用前一个片段编号
                out.append(f"{label}:{p}")
            else:
                return m.group(0)
        return "".join(f"[{x}]" for x in out)

    return MULTI_CITE_RE.sub(fix, markdown)


def verify_answer(markdown: str, snippets: list[Snippet], app: AppContext, repo_id: str, papers: list[dict] | None = None) -> tuple[str, dict, list[dict], dict]:
    """解析引用并回查真实文件。返回 (改写后的 markdown[引用统一为 [L#]], answer, evidence, verification)。"""
    by_label = {s.label: s for s in snippets}
    by_ord = {p["ord"]: p for p in (papers or [])}
    refs: list[dict] = []
    index: dict[tuple, int] = {}
    file_cache: dict[str, list[str] | None] = {}

    def lines_of(path: str) -> list[str] | None:
        if path not in file_cache:
            try:
                file_cache[path] = service.file_lines(app, repo_id, service.clean_rel_path(path))
            except ApiError:
                file_cache[path] = None
        return file_cache[path]

    def symbol_at(path: str, a: int) -> str | None:
        with app.db.session() as conn:
            r = conn.execute(
                "SELECT qualname FROM code_symbols WHERE repo_id = ? AND path = ? AND kind IN ('function','method','class') AND line_start <= ? AND line_end >= ? "
                "ORDER BY (line_end - line_start) LIMIT 1", (repo_id, path, a, a)).fetchone()
        return r["qualname"] if r else None

    def ref_for(m: re.Match) -> int:
        label, a, b, path, pa, pb, pord = m.groups()
        reason = None
        if pord:
            o = int(pord)
            p = by_ord.get(o)
            key = ("P", o, 0, p is not None)
            if key not in index:
                refs.append({
                    "ref": len(refs) + 1, "kind": "paper", "path": "", "line_start": 0, "line_end": 0, "symbol": None, "ord": o,
                    "page": p["page"] if p else None, "section": p.get("section") if p else None, "snippet": (p["text"][:220] if p else ""),
                    "verified": p is not None, "reason": None if p else "引用了没有给出的论文段落",
                })
                index[key] = len(refs)
            return index[key]
        if label:
            sn = by_label.get(label)
            if sn is None:
                path, start, end, verified, reason = label, 0, 0, False, "引用了不存在的代码片段编号"
            else:
                path, start, end = sn.path, sn.line_start, sn.line_end
                verified = True
                if a:
                    s, e = int(a), int(b or a)
                    if s > e or s < sn.line_start or e > sn.line_end:
                        verified, reason = False, f"行号 {s}-{e} 不在所给片段 {sn.line_start}-{sn.line_end} 内"
                    elif not sn.covers(s, e):
                        verified, reason = False, f"行号 {s}-{e} 落在片段里被省略的部分，没有发给模型"
                    else:
                        start, end = s, e
        else:
            start, end = int(pa), int(pb or pa)
            fl = lines_of(path)
            if fl is None:
                verified, reason = False, "仓库里没有这个文件，或它是二进制文件"
            elif start < 1 or start > end or end > len(fl):
                verified, reason = False, f"行号 {start}-{end} 超出文件范围（共 {len(fl)} 行）"
            elif (sent := [s for s in snippets if s.path == path]) and not any(s.covers(start, end) for s in sent):
                verified, reason = False, "这几行没有发给模型"
            else:
                verified = True
        key = (path, start, end, verified)
        if key not in index:
            fl = lines_of(path) if verified else None
            snippet = "\n".join(fl[start - 1:end][:12]) if fl and start else ""
            refs.append({
                "ref": len(refs) + 1, "path": path, "line_start": start, "line_end": end, "symbol": symbol_at(path, start) if verified and start else None,
                "snippet": snippet, "verified": verified, "reason": reason, "kind": "code",
            })
            index[key] = len(refs)
        return index[key]

    rewritten = CITE_RE.sub(lambda m: f"[L{ref_for(m)}]", split_multi_cites(markdown))
    sections = ev.split_sections(rewritten)
    direct, detail = sections.get("direct", ""), sections.get("detail") or None
    notes = []
    for b in ev._bullets(sections.get("evidence", "")):
        r = [int(x) for x in re.findall(r"\[L(\d+)\]", b)]
        if r:
            notes.append({"ref": r[0], "note": re.sub(r"\[L\d+\]", "", b).strip(" ：:-—")})
    inferences = []
    for b in ev._bullets(sections.get("inference", "")):
        basis = [int(x) for x in re.findall(r"\[L(\d+)\]", b)]
        text = re.sub(r"\s*[（(]\s*依据[:：]?\s*(?:\[L\d+\]\s*)+[）)]", "", b)
        text = re.sub(r"^\s*(?:推断内容|推断)[:：]\s*", "", text)
        text = re.sub(r"\s*(?:\[L\d+\])+(?=\s*[。.；;]?\s*$)", "", text).strip(" ；;")
        inferences.append({"text": text, "basis_refs": basis})
    answer = {"direct": direct, "detail": detail, "evidence_notes": notes, "inferences": inferences, "follow_ups": ev._bullets(sections.get("follow_ups", ""))[:3],
              "paper_context": [{"ord": p["ord"], "page": p["page"], "section": p.get("section"), "source": p["source"], "text": p["text"][:240]} for p in (papers or [])]}

    bad = [r["ref"] for r in refs if not r["verified"]]
    v_notes: list[str] = []
    if bad:
        status = "partial"
        v_notes.append("回答里有引用无法在真实代码中核实，已标为无法核实。")
    else:
        status = "verified"
    code_refs = [r for r in refs if r["kind"] == "code"]
    if direct.strip() and not code_refs:
        if ev.INSUFFICIENT_RE.search(direct) or "看不出" in direct:
            status = "unverified"
            v_notes.append("模型说明所给代码片段里看不出，没有作事实断言。")
        else:
            status = "no_evidence"
            v_notes.append("回答没有附带可核实的代码依据，请谨慎对待。")
    broken = ev.integrity_issues(f"{direct}\n{detail or ''}")
    if broken:
        v_notes.append("回答文字可能有缺失：" + "；".join(broken) + "，建议重新生成。")
        if status in ("verified", "unverified"):
            status = "partial"
    return rewritten, answer, refs, {"status": status, "invalid_citations": bad, "notes": v_notes}


# ---------------- 服务 ----------------
@dataclass
class ExplainRequest:
    repo_id: str
    target: dict
    question: str = ""
    paper_id: str | None = None


def _target_symbols(app: AppContext, repo_id: str, target: dict) -> list[dict]:
    """要解释的目标涉及哪些符号：用来找论文里对应的段落。"""
    sid = target.get("symbol_id")
    with app.db.session() as conn:
        if sid:
            r = conn.execute("SELECT id, path, name, qualname, doc, line_start, line_end FROM code_symbols WHERE id = ? AND repo_id = ?", (sid, repo_id)).fetchone()
            return [dict(r)] if r else []
        if target.get("symbol") and target.get("path"):
            r = conn.execute("SELECT id, path, name, qualname, doc, line_start, line_end FROM code_symbols WHERE repo_id = ? AND path = ? AND (qualname = ? OR name = ?) LIMIT 1", (repo_id, target["path"], target["symbol"], target["symbol"])).fetchone()
            if r:
                return [dict(r)]
        if target.get("path") and (target.get("kind") or "file") in ("file", "line_range"):
            rows = conn.execute(
                "SELECT id, path, name, qualname, doc, line_start, line_end FROM code_symbols WHERE repo_id = ? AND path = ? AND kind IN ('function','class') ORDER BY (line_end - line_start) DESC LIMIT 4", (repo_id, target["path"])
            ).fetchall()
            return [dict(r) for r in rows]
    return []


def card_to_dict(r) -> dict:
    return {
        "id": r["id"], "repo_id": r["repo_id"], "target": loads(r["target_json"], {}), "question": r["question"], "status": r["status"],
        "model": r["model"], "task_type": "code_explain", "raw_markdown": r["raw_markdown"],
        "answer": loads(r["answer_json"]) or {"direct": "", "detail": None, "evidence_notes": [], "inferences": [], "follow_ups": []},
        "evidence": loads(r["evidence_json"], []),
        "verification": loads(r["verification_json"]) or {"status": "unverified", "invalid_citations": [], "notes": []},
        "usage": loads(r["usage_json"]) or {"prompt_tokens": 0, "completion_tokens": 0}, "created_at": r["created_at"],
    }


def get_card(app: AppContext, card_id: str) -> dict:
    with app.db.session() as conn:
        r = conn.execute("SELECT * FROM code_cards WHERE id = ?", (card_id,)).fetchone()
    if r is None:
        raise ApiError("not_found", "卡片不存在", 404)
    return card_to_dict(r)


def list_cards(app: AppContext, repo_id: str, path: str | None = None, symbol_id: int | None = None) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute("SELECT * FROM code_cards WHERE repo_id = ? AND status = 'complete' ORDER BY created_at", (repo_id,)).fetchall()
    out = [card_to_dict(r) for r in rows]
    if path:
        out = [c for c in out if c["target"].get("path") == path]
    if symbol_id:
        out = [c for c in out if c["target"].get("symbol_id") == symbol_id]
    return out


def delete_card(app: AppContext, card_id: str) -> None:
    with app.db.session() as conn:
        cur = conn.execute("DELETE FROM code_cards WHERE id = ?", (card_id,))
    if cur.rowcount == 0:
        raise ApiError("not_found", "卡片不存在", 404)


class CodeExplainer:
    def __init__(self, app: AppContext):
        self.app = app
        self.ai = AIService(app)

    async def explain_events(self, req: ExplainRequest) -> AsyncIterator[Event]:
        card_id: str | None = None
        finalized = False
        acc = {"text": "", "usage": Usage(), "dropped": 0}
        try:
            repo = service.get_repo(self.app, req.repo_id)
            if repo["status"] != "ready":
                raise ApiError("conflict", "代码仓库还没有分析完成。", 409)
            provider, cfg = self.ai.provider()
            kind = req.target.get("kind") or "function"
            snippets, desc = build_snippets(self.app, req.repo_id, req.target)
            task = KIND_TASK.get(kind, TaskType.EXPLAIN)
            choice = choose_model(task, cfg)
            semantic = None if kind in ("repo", "module") else await self._semantic(provider, cfg, repo, snippets, desc)
            papers = [] if kind in ("repo", "module") else ctx.paper_passages(self.app, repo, req.target, _target_symbols(self.app, req.repo_id, req.target), req.question, semantic=semantic)
            messages = build_messages(desc, snippets, kind, req.question, papers)
            card_id = new_id()
            stored = {**req.target, "path": req.target.get("path") if kind in ("module", "repo") else (snippets[0].path if snippets else req.target.get("path"))}
            if req.target.get("symbol_id") and not stored.get("symbol"):  # 卡片里记下符号名，前端才能写出“解释了哪个函数”
                srow = _sym_row(self.app, req.repo_id, req.target["symbol_id"])
                if srow is not None:
                    stored["symbol"] = srow["qualname"]
            with self.app.db.session() as conn:
                conn.execute(
                    "INSERT INTO code_cards(id, repo_id, target_json, question, status, model, created_at) VALUES (?,?,?,?, 'streaming', ?, ?)",
                    (card_id, req.repo_id, dumps(stored), req.question, choice.model, utcnow()),
                )
            chars = sum(s.chars for s in snippets)
            paths = sorted({s.path for s in snippets})
            includes = {"question": bool(req.question.strip()), "selection": False, "notes": False, "supplements": False, "code": True, "paper_passages": bool(papers)}
            usage_log.record_request(
                self.app, task_type="code_explain", model=choice.model, paper_id=repo["paper_id"], passage_ords=[], chars_sent=chars,
                includes=includes, summary=f"发送了 {len(snippets)} 段代码片段（{', '.join(paths[:4])}{'…' if len(paths) > 4 else ''}，约 {chars:,} 字符）" + (f"和 {len(papers)} 段论文原文" if papers else "") + "，不含整个仓库",
            )
            yield "meta", {
                "card_id": card_id, "model": choice.model, "task_type": "code_explain", "escalated": False,
                "context": {"snippets": [{"label": s.label, "path": s.path, "line_start": s.line_start, "line_end": s.line_end, "role": s.role} for s in snippets],
                            "snippet_count": len(snippets), "chars_sent": chars, "includes": includes,
                            "papers": [{"ord": p["ord"], "page": p["page"], "section": p.get("section"), "source": p["source"]} for p in papers]},
            }
            used = choice.model
            fb_notes: list[str] = []
            acc.pop("fallback", None)
            async for e in self.ai._stream(provider, choice.model, messages, cfg, acc):
                yield e
            if acc.get("fallback"):  # 强模型暂时不可用：回答实际来自快速模型，如实记录
                used = acc["fallback"]["to"]
                fb_notes.append(acc["fallback"]["message"])
            usage1 = self.ai._log_usage("code_explain", used, acc["usage"], messages, acc["text"], repo["paper_id"])
            rewritten, answer, refs, ver = verify_answer(acc["text"], snippets, self.app, req.repo_id, papers)
            if can_escalate(cfg, choice) and (not answer["direct"].strip() or ver["status"] == "no_evidence"):
                strong = choose_model(task, cfg, escalate=True)
                yield "escalate", {"reason": "快速模型的回答缺少可核实的代码依据", "model": strong.model}
                acc.update(text="", usage=Usage(), dropped=0)
                async for e in self.ai._stream(provider, strong.model, messages, cfg, acc):
                    yield e
                used = strong.model
                u2 = self.ai._log_usage("code_explain", used, acc["usage"], messages, acc["text"], repo["paper_id"])
                usage1 = {k: usage1[k] + u2[k] for k in usage1}
                rewritten, answer, refs, ver = verify_answer(acc["text"], snippets, self.app, req.repo_id, papers)
            for note in dict.fromkeys(fb_notes):
                ver["notes"].append(note)
            if acc.get("dropped"):
                ver["notes"].append(f"流式输出中有 {acc['dropped']} 个数据块无法解析，回答内容可能不完整，建议重新生成。")
                if ver["status"] in ("verified", "unverified"):
                    ver["status"] = "partial"
            with self.app.db.session() as conn:
                conn.execute(
                    "UPDATE code_cards SET status='complete', model=?, raw_markdown=?, answer_json=?, evidence_json=?, verification_json=?, usage_json=? WHERE id=?",
                    (used, rewritten, dumps(answer), dumps(refs), dumps(ver), dumps(usage1), card_id),
                )
            finalized = True
            yield "done", {"card": get_card(self.app, card_id)}
        except LLMError as err:
            yield llm_error_event(err, card_id)
            self._abandon(card_id)
            finalized = True
        except ApiError as err:
            yield "error", {"code": err.code, "message": err.message, **({"card_id": card_id} if card_id else {})}
            self._abandon(card_id)
            finalized = True
        finally:
            if card_id and not finalized:
                self._abandon(card_id)

    async def _semantic(self, provider, cfg: dict, repo: dict, snippets: list[Snippet], desc: str) -> list[tuple[int, float]] | None:
        """目标代码的向量 → 论文里语义最相近的段落。没有向量索引、没开语义检索、调用失败都返回 None，退回词法线索。"""
        from ..ai import embeddings

        if not snippets or not (cfg.get("semantic_retrieval", True) and cfg.get("embedding_model")):
            return None
        if embeddings.index_status(self.app, repo["paper_id"])["indexed"] == 0:
            return None
        text = f"{desc.splitlines()[0]}\n{snippets[0].text}"[:2000]
        try:
            usage_log.record_request(
                self.app, task_type="embedding", model=cfg["embedding_model"], paper_id=repo["paper_id"], passage_ords=[], chars_sent=len(text),
                includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": True},
                summary=f"发送了一段代码（约 {len(text)} 字符）用于在论文里找语义相近的段落",
            )
            vec = (await embeddings.embed_batch(provider, cfg["embedding_model"], [text]))[0]
        except LLMError:
            return None
        return ctx.semantic_hits(self.app, repo["paper_id"], vec) or None

    def _abandon(self, card_id: str | None) -> None:
        if card_id:
            with self.app.db.session() as conn:
                conn.execute("DELETE FROM code_cards WHERE id = ? AND status = 'streaming'", (card_id,))
