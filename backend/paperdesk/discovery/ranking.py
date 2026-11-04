"""综合排序：相关性 + 学术质量 + 影响力（按学科与年份归一）+ 时效 + 新颖性，权重随学科与检索模式变化。

不得单纯按引用数排序：影响力优先用 OpenAlex 的 FWCI/同领域同年份引用百分位；只有原始引用数时按“年均引用”
做保守折算并打折。每条结果带可解释的分项与说明文字。
"""
from __future__ import annotations

import math
import re
from datetime import date

from .models import Work, STATUS_LABELS

STOP = set("the a an and or of to in on for with by from at as is are was were be this that these those using use based "
           "via into over under between about how what which towards toward approach method methods study analysis".split())

# 学科组 → 时效半衰期（年）
HALF_LIFE = {"cs": 4.0, "bio": 6.0, "phys": 10.0, "social": 12.0, "default": 6.0}
# 权重（相关性, 质量, 影响力, 时效, 新颖性）
WEIGHTS = {
    "cs": (0.40, 0.15, 0.20, 0.15, 0.10),
    "bio": (0.35, 0.25, 0.20, 0.10, 0.10),
    "phys": (0.40, 0.20, 0.20, 0.08, 0.12),
    "social": (0.40, 0.20, 0.18, 0.07, 0.15),
    "default": (0.38, 0.20, 0.20, 0.12, 0.10),
}
MODE_OVERRIDES = {  # 模式对权重的倾斜（相加后归一）
    "comprehensive": (0, 0, 0, 0, 0),
    "recent": (-0.05, 0, -0.05, 0.25, 0.10),
    "impact": (-0.05, 0.05, 0.30, -0.10, -0.10),
    "related": (0.10, 0, 0, -0.05, -0.05),
    "cross": (0, 0.05, 0, 0, 0.05),
}


def domain_group(w: Work) -> str:
    text = f"{w.domain or ''} {w.field_name or ''}".lower()
    if any(k in text for k in ("computer", "engineering", "decision", "information")):
        return "cs"
    if any(k in text for k in ("medicine", "health", "biochem", "immunology", "neuro", "pharmac", "life", "nursing",
                               "veterin", "dentist", "agricultur", "environmental")):
        return "bio"
    if any(k in text for k in ("physics", "mathematic", "chemistry", "material", "earth", "energy", "astronomy")):
        return "phys"
    if any(k in text for k in ("social", "psycholog", "econom", "business", "art", "humanit")):
        return "social"
    return "default"


def query_terms(query: str) -> list[str]:
    toks = re.findall(r"[A-Za-z][A-Za-z0-9\-]{1,}|[一-鿿]{2,}", query or "")
    out: list[str] = []
    for t in toks:
        lt = t.lower()
        if lt not in STOP and lt not in out:
            out.append(lt)
    return out


def _stem(t: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(t) > 5 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def lexical_relevance(terms: list[str], w: Work) -> tuple[float, list[str]]:
    """标题命中权重 2、摘要命中权重 1；返回 (0..1, 命中的词)。"""
    if not terms:
        return 0.0, []
    title = (w.title or "").lower()
    abstract = (w.abstract or "").lower()
    got, hits = 0.0, []
    for t in terms:
        st = _stem(t)
        in_t, in_a = st in title, st in abstract
        if in_t or in_a:
            hits.append(t)
        got += (2.0 if in_t else 0.0) + (1.0 if in_a else 0.0)
    return min(1.0, got / (3.0 * len(terms))), hits


def quality_score(w: Work) -> float:
    if w.status == "published":
        base = 0.7 if w.venue_type in ("journal", "conference", "book series") else 0.55
        if w.core_venue:
            base += 0.2
        if w.peer_reviewed:
            base += 0.05
        return min(1.0, base)
    if w.status == "accepted":
        return 0.55
    if w.status == "preprint":
        return 0.35
    return 0.3


def impact_score(w: Work, now_year: int) -> tuple[float, str]:
    if w.percentile is not None:
        if w.percentile >= 0.99:
            note = "同领域同年份前 1%"
        elif w.percentile >= 0.9:
            note = "同领域同年份前 10%"
        else:
            note = f"同领域同年份引用百分位 {w.percentile * 100:.0f}"
        return max(0.0, min(1.0, w.percentile)), note
    if w.fwci is not None:
        return max(0.0, min(1.0, math.log1p(max(0.0, w.fwci)) / math.log1p(8))), f"FWCI {w.fwci:.1f}"
    if w.citations is not None and w.year:
        age = max(1, now_year - w.year + 1)
        return 0.6 * min(1.0, math.log1p(w.citations / age) / math.log1p(60)), f"引用 {w.citations}，已折算"
    return 0.3, "影响力数据缺失"


def recency_score(w: Work, now_year: int, group: str) -> float:
    if not w.year:
        return 0.3
    age = max(0, now_year - w.year)
    return math.exp(-math.log(2) * age / HALF_LIFE.get(group, 6.0))


def novelty_score(w: Work, relevance: float, now_year: int) -> float:
    if not w.year:
        return 0.0
    age = now_year - w.year
    k = 1.0 if age <= 1 else 0.6 if age == 2 else 0.3 if age == 3 else 0.0
    return relevance * k


def weights_for(group: str, mode: str) -> tuple[float, ...]:
    base = WEIGHTS.get(group, WEIGHTS["default"])
    adj = MODE_OVERRIDES.get(mode, MODE_OVERRIDES["comprehensive"])
    w = [max(0.02, b + a) for b, a in zip(base, adj)]
    s = sum(w)
    return tuple(x / s for x in w)


def source_prior(w: Work) -> float:
    if not w.source_rank:
        return 0.3
    best = min(w.source_rank.values())
    return 1.0 / (1.0 + best / 8.0)


def score_works(
    works: list[Work], *, query: str, semantic: dict[str, float] | None = None, mode: str = "comprehensive",
    now_year: int | None = None, llm_relevance: dict[str, float] | None = None,
) -> None:
    """就地计算 score / breakdown / rank_note。semantic[key] 为 0..1 的语义相似度（可缺省）。"""
    now_year = now_year or date.today().year
    terms = query_terms(query)
    for w in works:
        lex, hits = lexical_relevance(terms, w)
        prior = source_prior(w)
        sem = (semantic or {}).get(w.key)
        if sem is not None:
            rel = 0.5 * sem + 0.35 * lex + 0.15 * prior
        else:
            rel = 0.7 * lex + 0.3 * prior
        if llm_relevance and w.key in llm_relevance:
            rel = 0.5 * rel + 0.5 * llm_relevance[w.key]
        group = domain_group(w)
        wr, wq, wi, wt, wn = weights_for(group, mode)
        q = quality_score(w)
        imp, imp_note = impact_score(w, now_year)
        rec = recency_score(w, now_year, group)
        nov = novelty_score(w, rel, now_year)
        w.score = wr * rel + wq * q + wi * imp + wt * rec + wn * nov
        w.breakdown = {"relevance": rel, "quality": q, "impact": imp, "recency": rec, "novelty": nov, "lexical": lex}
        if sem is not None:
            w.breakdown["semantic"] = sem
        qual_note = {
            "published": "同行评审的正式发表" if w.peer_reviewed else "正式发表",
            "accepted": "已接收待出版", "preprint": "预印本", "other": "发表状态未知",
        }.get(w.status, STATUS_LABELS.get(w.status, ""))
        if w.core_venue:
            qual_note += "，核心来源"
        w.rank_note = (f"相关性 {rel:.2f} · 质量 {q:.2f} · "
                       f"影响力 {imp:.2f} · 时效 {rec:.2f}")
        w.relevance_note = "、".join(hits[:5])


def diversify(works: list[Work], limit: int, penalty: float = 0.85) -> list[Work]:
    """按学科做轻量 MMR：同一学科已选得越多，后续同学科的得分折扣越大（跨学科探索用）。"""
    remaining = sorted(works, key=lambda w: -w.score)
    chosen: list[Work] = []
    seen: dict[str, int] = {}
    while remaining and len(chosen) < limit:
        best = max(remaining, key=lambda w: w.score * (penalty ** seen.get(w.field_name or "?", 0)))
        remaining.remove(best)
        chosen.append(best)
        seen[best.field_name or "?"] = seen.get(best.field_name or "?", 0) + 1
    return chosen


def rule_reason(w: Work, query: str) -> str:
    """没有模型时的确定性理由：只陈述可核对的事实。"""
    parts = []
    if w.relevance_note:
        parts.append(f"标题/摘要涉及：{w.relevance_note}")
    if w.venue and w.status == "published":
        parts.append(f"发表于《{w.venue}》" + ("，核心来源" if w.core_venue else ""))
    elif w.status == "preprint":
        parts.append("预印本，尚未确认经同行评审")
    if w.percentile is not None and w.percentile >= 0.9:
        parts.append("引用位于同领域同年份前 10%")
    elif w.fwci is not None and w.fwci >= 2:
        parts.append(f"领域归一化引用 FWCI {w.fwci:.1f}")
    if w.year:
        parts.append(f"{w.year} 年")
    return "；".join(parts) or "与检索词存在匹配"
