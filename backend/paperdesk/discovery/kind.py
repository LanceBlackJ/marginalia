"""论文类型（综述、研究论文、数据集与基准……）：从数据源给出的类型和标题/摘要的措辞里判断，并写明依据。

和“发表状态”（预印本/正式发表）是两个维度：一篇综述可以是预印本，也可以是正式发表。
判断不了就归为“研究论文”（期刊/会议里最常见的一类），依据里如实写“没有看到其它类型的信号”，不假装确定。
"""
from __future__ import annotations

import re

KIND_LABELS = {
    "review": "综述",
    "research": "研究论文",
    "dataset": "数据集与基准",
    "perspective": "观点与评论",
    "tutorial": "教程与手册",
    "thesis": "学位论文",
    "book": "书籍与章节",
}

# 标题里的强信号
_REVIEW_TITLE = re.compile(
    r"\b(survey|review|meta[- ]?analysis|systematic (?:review|mapping)|scoping review|literature (?:review|survey)|"
    r"state[- ]of[- ]the[- ]art|overview|roadmap|taxonomy|a (?:comprehensive|brief|critical) (?:study|look)|recent advances|progress and challenges|"
    r"challenges and opportunities|research (?:directions|agenda)|bibliometric)\b", re.I)
_REVIEW_TITLE_ZH = re.compile(r"综述|述评|研究进展|研究现状|进展与展望|文献计量")
_DATASET_TITLE = re.compile(r"\b(dataset|data set|benchmark|corpus|testbed|leaderboard|a (?:large[- ]scale )?collection of|database of)\b", re.I)
_PERSPECTIVE_TITLE = re.compile(
    r"\b(position paper|perspective|viewpoint|opinion|commentary|editorial|comment on|reply to|rebuttal|manifesto|call for|essay|why we need|we need to|"
    r"considered harmful|rethinking|reflections? on)\b", re.I)
_TUTORIAL_TITLE = re.compile(r"\b(tutorial|primer|a gentle introduction|introduction to|lecture notes|handbook|textbook|cheat ?sheet|guide to|practical guide)\b", re.I)
# 摘要里的措辞（比标题弱，只在标题没有信号时用）
_REVIEW_ABS = re.compile(r"\b(this (?:survey|review|article reviews|paper reviews|paper surveys)|we (?:review|survey)|we systematically (?:review|searched)|"
                         r"literature search|prisma|included studies|pooled (?:effect|analysis))\b", re.I)
_DATASET_ABS = re.compile(r"\b(we (?:introduce|present|release|construct|collect|curate) (?:a|an|the) (?:(?:new|large[- ]scale|novel|first|high[- ]quality|comprehensive) )*(?:dataset|benchmark|corpus))\b", re.I)

_OPENALEX_TYPES = {
    "review": ("review", "数据源类型为综述"),
    "dataset": ("dataset", "数据源类型为数据集"),
    "editorial": ("perspective", "数据源类型为社论"),
    "letter": ("perspective", "数据源类型为来信/评论"),
    "dissertation": ("thesis", "数据源类型为学位论文"),
    "book": ("book", "数据源类型为书籍"),
    "book-chapter": ("book", "数据源类型为书籍章节"),
    "reference-entry": ("book", "数据源类型为参考书条目"),
}


def classify(title: str | None, abstract: str | None, work_type: str | None = None, venue: str | None = None) -> dict:
    """返回 {kind, label, evidence}。"""
    t, a = title or "", abstract or ""
    wt = (work_type or "").lower()
    if wt in _OPENALEX_TYPES:
        kind, why = _OPENALEX_TYPES[wt]
        return _out(kind, why)
    if wt == "review":
        return _out("review", "数据源类型为综述")
    if _REVIEW_TITLE.search(t) or _REVIEW_TITLE_ZH.search(t):
        m = _REVIEW_TITLE.search(t) or _REVIEW_TITLE_ZH.search(t)
        return _out("review", f"标题含“{m.group(0)}”")
    if _PERSPECTIVE_TITLE.search(t):
        return _out("perspective", f"标题含“{_PERSPECTIVE_TITLE.search(t).group(0)}”")
    if _TUTORIAL_TITLE.search(t):
        return _out("tutorial", f"标题含“{_TUTORIAL_TITLE.search(t).group(0)}”")
    if _DATASET_TITLE.search(t):
        return _out("dataset", f"标题含“{_DATASET_TITLE.search(t).group(0)}”")
    if _REVIEW_ABS.search(a):
        return _out("review", f"摘要写到“{_REVIEW_ABS.search(a).group(0)}”")
    if _DATASET_ABS.search(a):
        return _out("dataset", "摘要说明提出了新的数据集/基准")
    if re.search(r"(?i)\b(thesis|dissertation)\b", venue or ""):
        return _out("thesis", "来源是学位论文库")
    return _out("research", "没有看到其它类型的信号，按研究论文处理")


def _out(kind: str, why: str) -> dict:
    return {"kind": kind, "label": KIND_LABELS[kind], "evidence": why}
