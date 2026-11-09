"""行内公式的视觉模型兜底。

解析时（parsing.inline_math）按字体、字号、基线能完整还原的行内公式已经直接转成 LaTeX；还原不了、但看起来是真公式的
（带分式、根号、集合这类规则做不了的结构）只记下页码和位置，tex 为空。这里把这些少量候选裁成小图，
一次几张地发给视觉模型转写，再核对：转写出的字母和数字必须和 PDF 文字层提取出的原文对得上、不能凭空多出内容，
对不上就丢掉，正文仍显示原文字——模型自报的把握不能当依据。

结果缓存在数据库里（重新解析后位置和原文没变的候选直接沿用，不再调用模型）；
发送的截图进隐私日志（includes.images=true）并计入用量。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter

from ..ai import usage as usage_log
from ..ai.providers import LLMError, image_part
from ..ai.router import TaskType, choose_model
from ..ai.service import AIService
from ..context import AppContext
from ..db import register_schema
from ..jobs import JobContext, JobManager
from ..llmjson import extract_json
from ..util import ApiError, dumps, loads, utcnow
from . import formula
from .images import crop_png

JOB_TYPE = "inline_math_ai"
BATCH = 6
DPI = 220

register_schema("inline_math_ai", """
CREATE TABLE IF NOT EXISTS inline_math_ai (
  paper_id TEXT NOT NULL,
  key TEXT NOT NULL,
  tex TEXT,
  status TEXT NOT NULL,
  model TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (paper_id, key)
);
""")

SYSTEM = (
    "你是严谨的数学公式转写器。下面有若干张编号的小图，每张是论文正文行内的一小段公式（可能带上下标、重音点、范数、集合、分式、根号）。\n"
    "要求：\n"
    "1. 只转写图里的内容，不要补充图里没有的符号，不要解释。\n"
    "2. 每张图输出一项；latex 里不要加 $ 符号。\n"
    "3. 看不清、或图里只有半截公式时，不要猜：该项的 latex 写成空字符串。\n"
    "4. 只输出 JSON：{\"items\":[{\"n\":1,\"latex\":\"...\"}]}。"
)


def cand_key(page: int, bbox: list[float], src: str) -> str:
    raw = f"{page}|{','.join(str(round(float(v))) for v in bbox)}|{src}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


_CMD = re.compile(r"\\[A-Za-z]+")
_ASCII_ALNUM = re.compile(r"[A-Za-z0-9]")


def plausible(src: str, tex: str) -> tuple[bool, str]:
    """转写结果和 PDF 文字层提取出的原文对得上吗：原文里的字母数字要都出现（至多缺 1 个），也不能多出一堆原文里没有的。"""
    if not tex.strip():
        return False, "模型没有给出公式"
    if len(tex) > 300:
        return False, "结果异常地长"
    issues = formula._balanced(tex)
    if issues:
        return False, "；".join(issues)
    skeleton = _ASCII_ALNUM.findall(_CMD.sub("", tex))
    want = Counter(_ASCII_ALNUM.findall(src))
    have = Counter(skeleton)
    missing = sum((want - have).values())
    extra = sum((have - want).values())
    n = sum(want.values())
    if missing > (1 if n >= 4 else 0):
        return False, f"原文里的 {missing} 个字母数字在转写结果里找不到"
    if extra > 3:
        return False, f"转写结果里多出 {extra} 个原文里没有的字母数字"
    return True, ""


def tidy(tex: str, src: str) -> str:
    """模型常把句末的逗号、句号也抄进公式；正文里它们在公式之外，留着会重复显示。原文自己以标点结尾（“η(α,”）时保留。"""
    t = tex.strip()
    while t and t[-1] in ".,;" and not src.rstrip().endswith(t[-1]):
        t = t[:-1].rstrip()
    return t


def _candidates(app: AppContext, paper_id: str) -> list[dict]:
    """[{ord, idx, key, page, bbox, src}]：所有 tex 为空的候选。"""
    out: list[dict] = []
    with app.db.session() as conn:
        rows = conn.execute("SELECT ord, math_json FROM passages WHERE paper_id = ? AND math_json IS NOT NULL ORDER BY ord", (paper_id,)).fetchall()
    for r in rows:
        for i, m in enumerate(loads(r["math_json"], [])):
            if m.get("tex") or not m.get("bbox") or not m.get("page"):
                continue
            out.append({"ord": r["ord"], "idx": i, "key": cand_key(m["page"], m["bbox"], m["src"]), "page": m["page"], "bbox": m["bbox"], "src": m["src"]})
    return out


def _cache(app: AppContext, paper_id: str) -> dict[str, dict]:
    with app.db.session() as conn:
        return {r["key"]: {"tex": r["tex"], "status": r["status"]} for r in conn.execute("SELECT key, tex, status FROM inline_math_ai WHERE paper_id = ?", (paper_id,))}


def _apply(app: AppContext, paper_id: str, cands: list[dict], cache: dict[str, dict]) -> int:
    """把缓存里成功的结果写回各段落的 math_json；返回写回的条数。"""
    by_ord: dict[int, list[dict]] = {}
    for c in cands:
        hit = cache.get(c["key"])
        if hit and hit["status"] == "ok" and hit["tex"]:
            by_ord.setdefault(c["ord"], []).append({**c, "tex": hit["tex"]})
    n = 0
    with app.db.session() as conn:
        for ord_, items in by_ord.items():
            row = conn.execute("SELECT math_json FROM passages WHERE paper_id = ? AND ord = ?", (paper_id, ord_)).fetchone()
            if row is None:
                continue
            arr = loads(row["math_json"], [])
            for it in items:
                if it["idx"] < len(arr) and not arr[it["idx"]].get("tex") and arr[it["idx"]].get("src") == it["src"]:
                    arr[it["idx"]]["tex"] = it["tex"]
                    arr[it["idx"]]["ai"] = True
                    n += 1
            conn.execute("UPDATE passages SET math_json = ? WHERE paper_id = ? AND ord = ?", (dumps(arr), paper_id, ord_))
    return n


def pending(app: AppContext, paper_id: str) -> int:
    """还需要问模型的候选数（缓存里已有结论的不算）。"""
    cache = _cache(app, paper_id)
    return sum(1 for c in _candidates(app, paper_id) if c["key"] not in cache)


def enqueue(app: AppContext, paper_id: str) -> str | None:
    """有候选就创建后台任务；缓存里已有结果的先直接写回（不调用模型）。没有要问模型的返回 None。"""
    cands = _candidates(app, paper_id)
    if not cands:
        return None
    cache = _cache(app, paper_id)
    _apply(app, paper_id, cands, cache)
    if all(c["key"] in cache for c in cands):
        return None
    with app.db.session() as conn:
        title = conn.execute("SELECT title FROM papers WHERE id = ?", (paper_id,)).fetchone()["title"]
    return app.jobs.submit(JOB_TYPE, f"识别行内公式：{title[:40]}", {"paper_id": paper_id}, paper_id)


_PAIR_RE = re.compile(r'"n"\s*:\s*(\d+)\s*,\s*"latex"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _parse_items(text: str) -> dict[int, str]:
    """{编号: LaTeX}。不能只靠 json.loads：模型常把几项写进同一个对象（真实网关上出现过 {"n":1,"latex":…,"n":2,"latex":…}，
    重复的键会让第一项悄悄丢掉），所以直接按“n + latex”成对地取。"""
    out: dict[int, str] = {}
    for m in _PAIR_RE.finditer(text):
        try:
            latex = json.loads('"' + m.group(2) + '"')
        except ValueError:
            latex = m.group(2).replace("\\\\", "\\")
        latex = latex.strip()
        out[int(m.group(1))] = formula.clean_latex(latex)[0] if latex else ""
    if out:
        return out
    try:
        data = extract_json(text)
    except ValueError:
        return {}
    items = data.get("items") if isinstance(data, dict) else data
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict):
                try:
                    n = int(it.get("n"))
                except (TypeError, ValueError):
                    continue
                latex = str(it.get("latex") or "").strip()
                out[n] = formula.clean_latex(latex)[0] if latex else ""
    return out


def _models(cfg: dict, primary: str) -> list[str]:
    """参与识别的视觉模型：主视觉模型 + 设置里的“第二视觉模型”（留空或与主模型相同就只用一个）。"""
    second = (cfg.get("vision_model_2") or "").strip()
    return [primary] + ([second] if second and second != primary else [])


async def _ask(service: AIService, provider, model: str, messages: list[dict], paper_id: str) -> dict[int, str] | None:
    """一个模型对一批小图的转写 {编号: LaTeX}；这个模型临时不可用返回 None（不影响另一个模型）。密钥类错误照常上抛。"""
    try:
        res = await provider.complete(model, messages, temperature=0, max_tokens=1500)
    except LLMError as err:
        if err.code in ("auth_failed", "no_api_key", "not_configured"):
            raise ApiError(err.code, err.message) from err
        return None
    service._log_usage(TaskType.VISION, model, res.usage, messages, res.text, paper_id)
    return _parse_items(res.text)


async def decide(service: AIService, provider, arbiter_model: str, paper_id: str, png: bytes, src: str,
                 answers: list[tuple[str, str]]) -> tuple[str | None, str]:
    """把几个模型对同一张图的转写合成一个结论。answers: [(模型名, 已 tidy 的 LaTeX)]（空字符串表示没答）。
    返回 (采用的 LaTeX 或 None, 说明)。
    - 只有一个模型有可用答案：核对通过就用它（没有对照，和只用一个模型时一样）；
    - 两个都可用且一致：直接采用，这是最可靠的一类；
    - 两个都可用但不一致：不能少数服从多数（真实例子里多数把 s_f 写成了 sf），让模型对着原图逐个符号裁决；裁决不出就不采用。"""
    good = [(m, t) for m, t in answers if t and plausible(src, t)[0]]
    if not good:
        return None, "没有模型给出能与原文对上的转写"
    if len(good) == 1:
        return good[0][1], ""
    (m1, a), (m2, b) = good[0], good[1]
    if formula.tex_key(a) == formula.tex_key(b):
        return a, ""
    better, diff = await formula._arbitrate(service, provider, arbiter_model, paper_id, png, a, b)
    if better == "A":
        return a, "两个模型不一致，裁决采用了前者"
    if better == "B":
        return b, "两个模型不一致，裁决采用了后者"
    return None, "两个模型的转写不一致，也没能裁决" + (f"（差异：{diff}）" if diff else "")


async def _recognize(app: AppContext, ctx: JobContext, paper_id: str) -> dict:
    service = AIService(app)
    provider, cfg = service.provider()
    choice = choose_model(TaskType.VISION, cfg)
    models = _models(cfg, choice.model)
    cache = _cache(app, paper_id)
    todo = [c for c in _candidates(app, paper_id) if c["key"] not in cache]
    ok = bad = disagreed = 0
    for start in range(0, len(todo), BATCH):
        ctx.check_cancelled()
        group = todo[start:start + BATCH]
        pngs = [crop_png(app, paper_id, c["page"], tuple(c["bbox"]), DPI, pad=3.0) for c in group]
        content: list[dict] = [{"type": "text", "text": f"共 {len(group)} 张图。每张图后面附有 PDF 文字层提取出的文字，仅供参考，可能错乱，以图为准。"}]
        for k, (c, png) in enumerate(zip(group, pngs), 1):
            content.append({"type": "text", "text": f"图 {k}（提取出的文字：{c['src']}）"})
            content.append(image_part(png))
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]
        for m in models:
            usage_log.record_request(
                app, task_type="vision_inline_math", model=m, paper_id=paper_id, passage_ords=[], chars_sent=0,
                includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": False, "images": True},
                summary=f"发送了 {len(group)} 张行内公式小截图（第 {group[0]['page']} 页起）用于转写 LaTeX；转写结果会与 PDF 文字层核对，"
                        f"{'并与另一个视觉模型的结果互相对照，' if len(models) > 1 else ''}对不上的不采用",
            )
        replies = await asyncio.gather(*(_ask(service, provider, m, messages, paper_id) for m in models))  # 几个模型并行
        if all(r is None for r in replies):
            got_any = False
        else:
            got_any = True
        rows = []
        for k, (c, png) in enumerate(zip(group, pngs), 1):
            answers = [(m, tidy(r.get(k, ""), c["src"])) for m, r in zip(models, replies) if r is not None]
            tex, why = await decide(service, provider, models[0], paper_id, png, c["src"], answers) if got_any else (None, "")
            disagreed += bool(why and "不一致" in why)
            rows.append((paper_id, c["key"], tex, "ok" if tex else "failed", "+".join(models), utcnow()))
            ok += bool(tex)
            bad += not tex
        with app.db.session() as conn:
            conn.executemany(
                "INSERT INTO inline_math_ai(paper_id, key, tex, status, model, created_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(paper_id, key) DO UPDATE SET tex=excluded.tex, status=excluded.status, model=excluded.model, created_at=excluded.created_at",
                rows,
            )
        ctx.progress(min(1.0, (start + len(group)) / max(1, len(todo))), f"已处理 {start + len(group)}/{len(todo)} 个行内公式")
    applied = _apply(app, paper_id, _candidates(app, paper_id), _cache(app, paper_id))
    return {"recognized": ok, "rejected": bad, "applied": applied, "models": models, "disagreed": disagreed}


def handle(ctx: JobContext) -> dict:
    return ctx.run_async(_recognize(ctx.app, ctx, ctx.payload["paper_id"]))


def register(manager: JobManager) -> None:
    manager.register(JOB_TYPE, handle)
