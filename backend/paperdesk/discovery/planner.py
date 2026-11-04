"""检索规划：把自然语言研究问题转成检索词/同义概念/来源选择。优先用快速模型，失败回退到规则。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..ai.providers import LLMError
from ..context import AppContext
from ..llmjson import ask_json, llm_available
from .ranking import STOP, query_terms

PLAN_SYSTEM = (
    "你是学术检索规划器。用户用自然语言（中文或英文）描述想研究的问题、方法或领域。请只输出一个 JSON 对象：\n"
    '{"english_query": "适合 OpenAlex/Crossref 的英文检索串，6-12 个词，保留关键术语",'
    ' "alt_queries": ["最多 3 条，用同义概念或相邻说法的英文检索串"],'
    ' "keywords": ["4-8 个英文核心术语"],'
    ' "domains": ["所属学科，如 computer science / medicine / physics / mathematics / social science"],'
    ' "is_cs": true/false, "is_biomedical": true/false,'
    ' "analogies": ["仅当要求跨学科时填写：2-3 条“其它学科中类似概念或方法”的英文检索串；否则空数组"]}\n'
    "不要输出任何其它文字。"
)


@dataclass
class Plan:
    query: str
    english_query: str
    alt_queries: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    is_cs: bool = False
    is_bio: bool = False
    analogies: list[str] = field(default_factory=list)
    ai_used: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"english_query": self.english_query, "alt_queries": self.alt_queries, "keywords": self.keywords,
                "domains": self.domains, "ai_used": self.ai_used, "notes": self.notes, "analogies": self.analogies}


_CS_HINT = re.compile(r"neural|learning|network|graph|algorithm|deep|reinforcement|transformer|computing|software|robot|vision", re.I)
_BIO_HINT = re.compile(r"cancer|disease|patient|clinical|gene|protein|cell|drug|therapy|epidem|vaccine|neuro|diagnos|medical", re.I)


def rule_plan(query: str, mode: str) -> Plan:
    terms = query_terms(query)
    latin = [t for t in terms if re.match(r"[a-z]", t)]
    plan = Plan(query=query, english_query=" ".join(latin) if latin else query, keywords=latin[:8])
    plan.is_cs = bool(_CS_HINT.search(query))
    plan.is_bio = bool(_BIO_HINT.search(query))
    if not latin:
        plan.notes.append("检索词不含英文，且没有可用的模型来翻译；英文文献的命中可能较少。可在设置中配置模型后重试。")
    return plan


async def make_plan(app: AppContext, query: str, mode: str, use_ai: bool = True) -> Plan:
    query = query.strip()
    plan = rule_plan(query, mode)
    if not use_ai or not llm_available(app):
        if use_ai:
            plan.notes.append("未配置 AI 模型，使用规则生成检索词。")
        return plan
    try:
        data = await ask_json(
            app, system=PLAN_SYSTEM, user=f"检索模式：{mode}\n用户的描述：{query}", task_type="discovery_plan", tier="fast",
            max_tokens=600, summary="发送了检索描述用于生成检索词（不含论文内容）",
        )
    except LLMError as exc:
        plan.notes.append("AI 检索规划失败，已回退为规则检索词。")
        return plan
    if not isinstance(data, dict):
        return plan
    eq = str(data.get("english_query") or "").strip()
    if eq:
        plan.english_query = eq
    plan.alt_queries = [str(x).strip() for x in (data.get("alt_queries") or []) if str(x).strip()][:3]
    plan.keywords = [str(x).strip() for x in (data.get("keywords") or []) if str(x).strip()][:8] or plan.keywords
    plan.domains = [str(x).strip() for x in (data.get("domains") or []) if str(x).strip()][:3]
    plan.is_cs = bool(data.get("is_cs", plan.is_cs))
    plan.is_bio = bool(data.get("is_biomedical", plan.is_bio))
    plan.analogies = [str(x).strip() for x in (data.get("analogies") or []) if str(x).strip()][:3]
    plan.ai_used = True
    plan.notes = []  # AI 规划成功，规则回退时留下的提示不再适用
    return plan


def choose_sources(plan: Plan, mode: str) -> list[str]:
    """按学科与模式选择来源。OpenAlex 与 Crossref 始终参与；其它按学科加入。"""
    names = ["openalex", "crossref"]
    if plan.is_bio:
        names += ["europepmc", "s2"]
    elif plan.is_cs:
        names += ["s2", "arxiv"]
    else:
        names += ["s2", "arxiv"]
    if mode == "impact" and "arxiv" in names:
        names.remove("arxiv")  # 预印本影响力数据弱
    return names
