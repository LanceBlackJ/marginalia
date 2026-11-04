"""公式转写：公式区域截图 → 视觉模型 → LaTeX（缓存、清洗、结构校验）。

数学字体的文字层往往是乱码（如 `G ¼ ðV; EÞ`），所以公式必须靠截图识别。
"""
from __future__ import annotations

import re

from ..ai import usage as usage_log
from ..ai.providers import LLMError, image_part
from ..ai.router import TaskType, choose_model
from ..ai.service import AIService
from ..context import AppContext
from ..llmjson import extract_json
from ..util import ApiError, dumps, loads, utcnow
from .images import crop_png

TRANSCRIBE_SYSTEM = (
    "你是严谨的数学公式转写器。把图片里的数学公式转写成 LaTeX。\n"
    "要求：\n"
    "1. 只转写图中的公式本身，不要解释，不要补充图里没有的内容。\n"
    "2. 用一个 ```latex 代码块输出公式；多行公式用 aligned 环境；公式编号如 (1) 写成 \\tag{1}。\n"
    "3. 看不清的符号不要猜，用 ? 占位，并把置信度降低。\n"
    "4. 代码块之后单独一行写 CONFIDENCE: high、medium 或 low（图像模糊、符号不确定时不要写 high）。"
)

_CONF_RE = re.compile(r"CONFIDENCE\s*[:：]\s*(high|medium|low)", re.I)
_FENCE_RE = re.compile(r"```(?:latex|tex)?\s*(.*?)```", re.S | re.I)


def _balanced(latex: str) -> list[str]:
    """基本的结构校验，返回问题列表。"""
    issues: list[str] = []
    depth = 0
    i = 0
    while i < len(latex):
        ch = latex[i]
        if ch == "\\":
            i += 2  # 跳过转义（\{ \} \\ 等）
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                break
        i += 1
    if depth != 0:
        issues.append("花括号不配对")
    if len(re.findall(r"\\left\b", latex)) != len(re.findall(r"\\right\b", latex)):
        issues.append("\\left 与 \\right 数量不一致")
    begins = re.findall(r"\\begin\{(\w+\*?)\}", latex)
    ends = re.findall(r"\\end\{(\w+\*?)\}", latex)
    if sorted(begins) != sorted(ends):
        issues.append("\\begin 与 \\end 不配对")
    if "?" in latex.replace("\\?", ""):
        issues.append("含有模型无法辨认的占位符 ?")
    return issues


_KEY_SUBS = (
    (r"\\tag\{[^}]*\}", ""),
    (r"\\(?:left|right)(?![A-Za-z])", ""),
    (r"\\(?:ldots|cdots|dots|dotsc|dotsb)(?![A-Za-z])", "..."),
    (r"\\(?:backslash|setminus|smallsetminus)(?![A-Za-z])", "SETMINUS"),
    (r"\\(?:mathrm|mathit|textrm|text|operatorname)(?![A-Za-z])", ""),
    (r"\\[,;:!]|\\(?:quad|qquad)(?![A-Za-z])|~", ""),
)


def tex_key(latex: str) -> str:
    """两份 LaTeX 的比较键：忽略空白、花括号、间距命令、\\left/\\right、\\dots 的各种写法与编号，其余必须一致。"""
    s = latex
    for pat, rep in _KEY_SUBS:
        s = re.sub(pat, rep, s)
    return re.sub(r"[{}\s]", "", s)


def clean_latex(raw: str) -> tuple[str, str, list[str]]:
    """从模型输出里取出 LaTeX、置信度与告警。返回 (latex, confidence, warnings)；latex 为空表示失败。"""
    text = raw.strip()
    m = _CONF_RE.search(text)
    confidence = m.group(1).lower() if m else "medium"
    text = _CONF_RE.sub("", text).strip()
    fence = _FENCE_RE.search(text)
    if fence:
        latex = fence.group(1)
    else:
        latex = text
        # 没有代码块、又不像 LaTeX 的自然语言回复（“抱歉，我看不清”）不能当作公式
        if re.search(r"[一-鿿]", latex) or (not re.search(r"[\\^_={}]", latex) and len(latex.split()) >= 6):
            return "", "low", ["模型没有返回 LaTeX 公式"]
    latex = latex.strip()
    for pat in (r"^\$\$(.*)\$\$$", r"^\\\[(.*)\\\]$", r"^\$(.*)\$$", r"^\\\((.*)\\\)$"):
        mm = re.match(pat, latex, re.S)
        if mm:
            latex = mm.group(1).strip()
            break
    latex = re.sub(r"\\begin\{equation\*?\}|\\end\{equation\*?\}", "", latex).strip()
    latex = re.sub(r"\\label\{[^}]*\}", "", latex).strip()
    if len(latex) > 4000:
        return "", "low", ["识别结果异常地长，已丢弃"]
    warnings = _balanced(latex) if latex else ["模型没有返回公式"]
    if warnings and confidence == "high":
        confidence = "medium"
    if any("花括号" in w or "不配对" in w for w in warnings):
        confidence = "low"
    return latex, confidence, warnings


def get_cached(app: AppContext, equation_id: int) -> dict | None:
    with app.db.session() as conn:
        r = conn.execute("SELECT * FROM equation_latex WHERE equation_id = ?", (equation_id,)).fetchone()
    if r is None:
        return None
    return {
        "equation_id": r["equation_id"], "latex": r["latex"], "confidence": r["confidence"], "status": r["status"],
        "warnings": loads(r["warnings_json"], []), "error": r["error"], "model": r["model"], "created_at": r["created_at"],
    }


def load_equation(app: AppContext, paper_id: str, equation_id: int) -> dict:
    with app.db.session() as conn:
        r = conn.execute("SELECT * FROM equations WHERE id = ? AND paper_id = ?", (equation_id, paper_id)).fetchone()
    if r is None:
        raise ApiError("not_found", "公式不存在", 404)
    return {"id": r["id"], "page": r["page"], "bbox": (r["x0"], r["y0"], r["x1"], r["y1"]), "number": r["number"],
            "text": r["text"], "passage_ord": r["passage_ord"]}


ARBITER_SYSTEM = (
    "你是严谨的公式校对员。图片里有一个数学公式，下面给出两份 LaTeX 转写 A 和 B。"
    "请逐个符号对照图片（运算符、上下标、括号、求和与分式结构都要看），判断哪一份更忠实：不漏符号、也不多出图里没有的符号。\n"
    "只输出 JSON：{\"better\":\"A\"|\"B\"|\"neither\",\"differences\":[\"一句话说明两份的差异，例如 A 少了集合差运算符\"]}。看不准就写 neither。"
)


async def _arbitrate(service: AIService, provider, model: str, paper_id: str, png: bytes, a: str, b: str) -> tuple[str | None, str]:
    """返回 (A|B|None, 差异说明)。调用失败或输出看不懂都当作“没能判定”。"""
    messages = [
        {"role": "system", "content": ARBITER_SYSTEM},
        {"role": "user", "content": [{"type": "text", "text": f"A：\n{a}\n\nB：\n{b}"}, image_part(png)]},
    ]
    try:
        res = await provider.complete(model, messages, temperature=0, max_tokens=500)
        service._log_usage(TaskType.VISION, model, res.usage, messages, res.text, paper_id)
        data = extract_json(res.text)
    except (LLMError, ValueError):
        return None, ""
    if not isinstance(data, dict):
        return None, ""
    better = str(data.get("better", "")).strip().strip("\"'").upper()
    diffs = data.get("differences")
    diff = "；".join(str(x).strip() for x in diffs if str(x).strip())[:160] if isinstance(diffs, list) else ""
    return (better if better in ("A", "B") else None), diff


async def transcribe_equation(app: AppContext, paper_id: str, equation_id: int, *, force: bool = False) -> dict:
    """返回 {equation_id, latex, confidence, status(ok|failed), warnings, model, cached}。"""
    eq = load_equation(app, paper_id, equation_id)
    if not force:
        hit = get_cached(app, equation_id)
        if hit and hit["status"] == "ok":
            return {**hit, "cached": True}
    service = AIService(app)
    provider, cfg = service.provider()
    choice = choose_model(TaskType.VISION, cfg)
    hint = f"（该公式的编号很可能是 ({eq['number']})）" if eq["number"] else ""

    def build(png: bytes) -> list[dict]:
        return [
            {"role": "system", "content": TRANSCRIBE_SYSTEM},
            {"role": "user", "content": [{"type": "text", "text": f"请转写图中的数学公式。{hint}"}, image_part(png)]},
        ]

    usage_log.record_request(
        app, task_type="vision_formula", model=choice.model, paper_id=paper_id, passage_ords=[], chars_sent=0,
        includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": False, "images": True},
        summary=f"发送了最多 3 张公式截图（第 {eq['page']} 页：两种分辨率各 1 张，两次结果不一致时再发 1 张对照）用于转写 LaTeX 并交叉核对",
    )
    messages = build(crop_png(app, paper_id, eq["page"], eq["bbox"], 200))
    res = await provider.complete(choice.model, messages, temperature=0, max_tokens=1500)
    service._log_usage(TaskType.VISION, choice.model, res.usage, messages, res.text, paper_id)
    latex, confidence, warnings = clean_latex(res.text)
    if latex:
        # 视觉模型自报的置信度不可靠（真实网关上出现过漏掉 \setminus 却自报 high）：换一个分辨率独立再转写一次，
        # 两次结果不一致就如实提示，并把置信度压到 low。第二次失败不影响第一次的结果。
        png2 = crop_png(app, paper_id, eq["page"], eq["bbox"], 300)
        messages2 = build(png2)
        try:
            res2 = await provider.complete(choice.model, messages2, temperature=0, max_tokens=1500)
            service._log_usage(TaskType.VISION, choice.model, res2.usage, messages2, res2.text, paper_id)
            latex2, _conf2, _w2 = clean_latex(res2.text)
        except LLMError:
            latex2 = ""
        if latex2 and tex_key(latex2) != tex_key(latex):
            # 不一致：再让模型拿着原图逐个符号对照这两份，裁决哪份更忠实；裁决也可能出错，所以无论结果如何都保留告警、置信度为 low
            better, diff = await _arbitrate(service, provider, choice.model, paper_id, png2, latex, latex2)
            note = ""
            if better == "B":
                latex, note = latex2, "第三次对照原图后，采用了第二次转写。"
            elif better == "A":
                note = "第三次对照原图后，认为第一次转写更准确。"
            else:
                note = "第三次对照原图也没能判定哪份更准确，显示的是第一次的转写。"
            warnings = [*warnings, "两次独立转写的结果不一致，模型可能漏掉或认错了符号。" + note
                        + (f"差异：{diff}" if diff else "") + "请对照原图确认。"]
            confidence = "low"
    status = "ok" if latex else "failed"
    error = None if latex else "模型没有返回可用的 LaTeX，可以重试或手动查看原文。"
    with app.db.session() as conn:
        conn.execute(
            "INSERT INTO equation_latex(equation_id, paper_id, latex, confidence, status, warnings_json, error, model, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(equation_id) DO UPDATE SET latex=excluded.latex, "
            "confidence=excluded.confidence, status=excluded.status, warnings_json=excluded.warnings_json, "
            "error=excluded.error, model=excluded.model, created_at=excluded.created_at",
            (equation_id, paper_id, latex or None, confidence, status, dumps(warnings), error, choice.model, utcnow()),
        )
    return {"equation_id": equation_id, "latex": latex or None, "confidence": confidence, "status": status,
            "warnings": warnings, "error": error, "model": choice.model, "cached": False}


def locate_equation(app: AppContext, paper_id: str, *, page: int | None, text: str | None, bbox: list[float] | None) -> dict:
    """把阅读器里选中的区域/文字映射到最近的公式实体。优先看区域重叠，其次看文字相近。"""
    from .. import reader

    with app.db.session() as conn:
        rows = conn.execute("SELECT * FROM equations WHERE paper_id = ?" + (" AND page = ?" if page else ""),
                            (paper_id, page) if page else (paper_id,)).fetchall()
    cands: list[tuple[float, dict]] = []
    for r in rows:
        score = 0.0
        if bbox and len(bbox) == 4:
            ix = min(bbox[2], r["x1"]) - max(bbox[0], r["x0"])
            iy = min(bbox[3], r["y1"]) - max(bbox[1], r["y0"])
            if ix > 0 and iy > 0:
                area = (r["x1"] - r["x0"]) * (r["y1"] - r["y0"])
                score = max(score, (ix * iy) / max(1.0, min(area, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))))
        if text and text.strip():
            score = max(score, reader.score_match(text, r["text"]) * 0.9)
        if score > 0.15:
            cands.append((score, {"id": r["id"], "page": r["page"], "bbox": [r["x0"], r["y0"], r["x1"], r["y1"]],
                                  "number": r["number"], "passage_ord": r["passage_ord"], "score": round(score, 3)}))
    cands.sort(key=lambda c: -c[0])
    return {"equation": cands[0][1] if cands else None, "candidates": [c[1] for c in cands[:5]]}
