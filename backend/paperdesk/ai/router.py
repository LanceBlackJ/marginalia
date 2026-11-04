"""意图识别与模型路由：简单任务走快速模型，复杂任务走强模型，必要时自动升级。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ..util import ApiError


class TaskType(str, Enum):
    TRANSLATE = "translate"
    EXPLAIN = "explain"
    SUMMARIZE = "summarize"
    TERM = "term"
    MOTIVATION = "design_motivation"
    METHOD = "method_analysis"
    FORMULA = "formula"
    FIGURE = "figure"
    EXPERIMENT = "experiment"
    FREE_QA = "free_qa"
    ANALYSIS = "paper_analysis"
    QUERY_PLAN = "query_plan"
    VISION = "vision"  # 带截图的识别任务（公式转写、框架图/图表/表格抽取）


FAST_TASKS = {TaskType.TRANSLATE, TaskType.EXPLAIN, TaskType.SUMMARIZE, TaskType.TERM, TaskType.QUERY_PLAN}

_RULES: list[tuple[TaskType, re.Pattern]] = [
    (TaskType.FORMULA, re.compile(r"公式|推导|符号|方程|equation|derive|derivation|\beq\.?\s*\(?\d|损失函数|loss function", re.I)),
    (TaskType.FIGURE, re.compile(r"框架图|流程图|示意图|架构图|图\s*\d|figure\s*\d|fig\.?\s*\d|architecture diagram", re.I)),
    (TaskType.EXPERIMENT, re.compile(r"实验|消融|指标|对比结果|基线|baseline|ablation|dataset|数据集|结果表|table\s*\d|表\s*\d|性能", re.I)),
    (TaskType.MOTIVATION, re.compile(r"为什么|为何|动机|出发点|设计思路|设计原因|why|motivation|rationale", re.I)),
    (TaskType.METHOD, re.compile(r"方法|模块|机制|如何工作|怎么实现|原理|算法|区别|比较|对比|与.{1,12}相比|method|module|mechanism|how does|algorithm|difference|compare", re.I)),
    (TaskType.SUMMARIZE, re.compile(r"总结|概括|归纳|摘要|summar", re.I)),
    (TaskType.TERM, re.compile(r"什么意思|是什么|指什么|术语|含义|what is|what does .* mean|define|meaning", re.I)),
]

INTENT_TO_TASK = {
    "explain": TaskType.EXPLAIN,
    "summarize": TaskType.SUMMARIZE,
    "term": TaskType.TERM,
    "motivation": TaskType.MOTIVATION,
    "formula": TaskType.FORMULA,
    "figure": TaskType.FIGURE,
    "experiment": TaskType.EXPERIMENT,
}


def classify(intent: str, question: str, selection_len: int = 0) -> TaskType:
    """意图识别：快捷操作直接映射；自由提问用规则判断，拿不准时倾向强模型。"""
    if intent in INTENT_TO_TASK:
        return INTENT_TO_TASK[intent]
    q = (question or "").strip()
    # “是什么/总结”等基础类规则只对短问题生效，避免把复杂的长问题误路由到快速模型
    # 长度按“中文字符计 2”折算，使中英文问题的阈值大致可比
    weighted = len(q) + len(re.findall(r"[一-鿿]", q))
    short_only = {TaskType.TERM: 40, TaskType.SUMMARIZE: 90}
    for task, pat in _RULES:
        if pat.search(q) and weighted <= short_only.get(task, 10_000):
            return task
    if len(q) <= 24 and selection_len and selection_len <= 400:
        return TaskType.EXPLAIN
    return TaskType.FREE_QA


@dataclass(frozen=True)
class ModelChoice:
    tier: str  # fast | strong
    model: str


def choose_model(task: TaskType, settings: dict, *, escalate: bool = False) -> ModelChoice:
    if task == TaskType.VISION:
        vision = (settings.get("vision_model") or "").strip()
        if not vision:
            raise ApiError("vision_not_configured", "需要在设置 → AI 模型里配置视觉模型，才能识别公式和图表。", 400)
        return ModelChoice("vision", vision)
    fast, strong = settings.get("fast_model") or "", settings.get("strong_model") or ""
    if not fast and not strong:
        raise ApiError("not_configured", "尚未配置 AI 模型，请先在设置中填写模型名称。", 400)
    mode = settings.get("mode", "auto")
    want_strong = escalate or mode == "strong" or (mode == "auto" and task not in FAST_TASKS)
    if mode == "fast" and not escalate:
        want_strong = False
    if want_strong:
        return ModelChoice("strong", strong or fast)
    return ModelChoice("fast", fast or strong)


def can_escalate(settings: dict, current: ModelChoice) -> bool:
    strong = settings.get("strong_model") or ""
    return current.tier == "fast" and bool(strong) and strong != current.model and settings.get("mode", "auto") == "auto"
