"""图的追问：把这张图的截图和问题一起发给视觉模型。

重点是防编造：图里看不出的信息必须回答“图中看不出”；数字只有在图的文字层/图注里找得到才算“图上印出的”，
其余一律标为读图估计；回答文字里出现的、既不在图里也不在问题里的具体数字会被指出来。
"""
from __future__ import annotations

import re

from ..ai.providers import LLMError
from ..ai.router import TaskType, choose_model
from ..ai.service import AIService
from ..context import AppContext
from ..jobs import JobContext
from ..util import ApiError
from . import figures
from . import validate as V
from .images import crop_png_fit

FIGURE_QA_SYSTEM = """你回答关于一张论文图的问题。严格遵守：
1. 只能依据图像里实际可见的内容和给出的图注。图里没有的信息（没画出的数据、没标注的数值、实验设置、运行时间、结论的原因等）必须明确回答“图中看不出”，不要根据常识或论文标题去猜。
2. 数字：只有图上直接印出的数字才能原样引用；靠坐标轴估读的数值必须写“约”并说明是读图估计；绝不推算、绝不补全。
3. 先检查问题的前提是否成立：热力图、表格没有坐标轴上的“数据点”；没有第三个子图就不能问第三个子图；图里没有的系列、没有的坐标轴也不能问。前提不成立时 premise_ok 写 false，answerable 写 false，并说明为什么不成立，不要硬凑一个答案。
4. 用中文回答，简短直接。
只输出一个 JSON 对象：{"premise_ok": true 或 false, "answerable": true 或 false, "answer": "回答", "seen": "你依据的是图里的什么（一句话）", "numbers": [{"value": "0.52", "how": "printed 或 estimated"}]}
answerable 为 false 时 answer 必须以“图中看不出”开头，并简要说明图里有什么、缺什么。"""


class _NoJob:
    """复用 _vlm_json 时不需要后台任务上下文。"""
    job_id = None

    def check_cancelled(self) -> None: ...
    def log(self, line: str) -> None: ...
    def progress(self, *a, **k) -> None: ...


_COORD_Q = re.compile(r"纵坐标|横坐标|坐标值|坐标|数据点|最右端|最左端|[xXyY]\s*轴")
_ORD = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_NO_POINT_TYPES = {"heatmap": "热力图"}


def _panel_index(question: str, panels: list[dict]) -> int | None:
    """问题里指的是第几个子图（按“第一个子图”“子图 a”“(b)”识别）；识别不出返回 None。"""
    m = re.search(r"第\s*([一二三四五六七八九十]|\d+)\s*个?\s*子图", question)
    if m:
        n = _ORD.get(m.group(1)) or int(m.group(1))
        return n - 1 if 1 <= n <= len(panels) else -1
    m = re.search(r"子图\s*[（(]?\s*([a-hA-H])\s*[)）]?|[（(]\s*([a-hA-H])\s*[)）]", question)
    if m:
        label = (m.group(1) or m.group(2)).lower()
        for i, p in enumerate(panels):
            if str(p.get("label", "")).strip("()（） ").lower() == label:
                return i
        return -1
    return None


def premise_problem(question: str, analysis: dict) -> str | None:
    """用已有的读图结果检查问题的前提：问热力图/表格“坐标轴上的数据点”、问不存在的子图，都不该硬答。返回说明；前提成立或无法判断返回 None。"""
    if not analysis or analysis.get("status") != "ready" or not _COORD_Q.search(question):
        return None
    kind, data = analysis.get("kind"), analysis.get("data") or {}
    if kind == "table":
        return "这是一张表格，没有坐标轴，也没有“数据点”；可以改问某个单元格里印出的数字。"
    panels = data.get("panels") or []
    if kind != "chart" or not panels:
        return None
    idx = _panel_index(question, panels)
    if idx == -1:
        return f"这张图只识别出 {len(panels)} 个子图，没有你问的那个子图。"
    targets = [panels[idx]] if idx is not None else []
    for p in targets:
        what = _NO_POINT_TYPES.get(str(p.get("chart_type", "")).lower())
        if what:
            label = p.get("label") or str(idx + 1)
            return f"子图 {label} 是{what}，没有坐标轴上的“数据点”，所以没有“最右端数据点的纵坐标”这类数值；可以改问它的颜色深浅规律，或格子里印出的数字。"
    return None


def mark_estimates(answer: str, numbers: list[dict]) -> str:
    """估读出来的数字必须带“约”和估计说明：回答里没写“约”的在数字前补上，没有任何估计说明的在末尾补一句。"""
    out = answer
    est = [n["value"] for n in numbers if n["how"] == "estimated" and n["value"] in out]
    for v in dict.fromkeys(est):
        pat = re.compile(r"(?<![\d.])" + re.escape(v) + r"(?![\d])")
        m = pat.search(out)
        if m and not re.search(r"(约|大约|大概|近|≈|~|估计)[为是在于等]{0,2}\s*$", out[: m.start()]) and not re.match(r"\s*(左右|上下)", out[m.end():]):
            out = out[: m.start()] + "约 " + out[m.start():]
    if numbers and any(n["how"] == "estimated" for n in numbers) and "估计" not in out:
        out = out.rstrip() + "（这是读图估计值，不是图上印出的精确数字。）"
    return out


async def ask_figure(app: AppContext, paper_id: str, figure_id: int, question: str) -> dict:
    question = question.strip()
    if not question:
        raise ApiError("bad_request", "请输入关于这张图的问题")
    fig = figures.load_figure(app, paper_id, figure_id)
    problem = premise_problem(question, figures.get_analysis(app, paper_id, figure_id))
    if problem:  # 问题的前提和这张图的类型对不上：不调用模型、不发截图，直接说明
        return {"answerable": False, "answer": "图中看不出。" + problem, "seen": "此前的读图结果里这张图的类型/子图设置与问题的前提不符",
                "numbers": [], "warnings": [], "model": "", "figure_id": fig["id"], "premise": "mismatch"}
    service = AIService(app)
    try:
        provider, cfg = service.provider()
        vmodel = choose_model(TaskType.VISION, cfg).model
        png = crop_png_fit(app, paper_id, fig["page"], fig["bbox"])
        raw = await figures._vlm_json(
            app, service, provider, vmodel, paper_id=paper_id, job=_NoJob(), system=FIGURE_QA_SYSTEM,
            text=f"{figures._context_text(fig)}\n\n【问题】{question}", png=png, task_type="vision_figure_qa",
            summary=f"发送了 1 张图截图（{fig['label'] or '第 ' + str(fig['page']) + ' 页'}）和你的问题", max_tokens=1200,
        )
    except LLMError as err:
        raise ApiError(err.code, err.message, err.status) from err
    answer = V.clean(raw.get("answer"))
    premise_ok = raw.get("premise_ok") is not False
    answerable = bool(raw.get("answerable")) and bool(answer) and premise_ok
    if not answerable and not answer.startswith("图中看不出"):
        answer = "图中看不出。" + answer
    src_nums = V.number_set(f"{fig['source_text']} {fig['caption']}")
    numbers = []
    for n in raw.get("numbers") or []:
        if not isinstance(n, dict) or not V.clean(n.get("value")):
            continue
        value = V.clean(n["value"])
        printed = V.clean(n.get("how")).lower() == "printed" and V.value_verified(value, src_nums)
        numbers.append({"value": value, "how": "printed" if printed else "estimated"})
    if answerable:
        answer = mark_estimates(answer, numbers)
    warnings: list[str] = []
    if any(n["how"] == "estimated" for n in numbers):
        warnings.append("其中的数值是读图估计，或在图的文字层/图注里没有找到，不要当作精确值引用。")
    bad = V.ungrounded_numbers([answer], f"{fig['source_text']} {fig['caption']} {question}") if answerable else []
    bad = [b for b in bad if b not in {n["value"] for n in numbers}]
    if bad:
        warnings.append("回答里出现了图的文字层/图注/问题里都找不到的数字：" + "、".join(bad[:6]) + "，请对照原图确认。")
    if not fig["source_text"].strip():
        warnings.append("这张图是位图，没有文字层，所有内容都只能是读图结果。")
    return {"answerable": answerable, "answer": answer, "seen": V.clean(raw.get("seen")), "numbers": numbers,
            "warnings": warnings, "model": vmodel, "figure_id": fig["id"], **({} if premise_ok else {"premise": "mismatch"})}
