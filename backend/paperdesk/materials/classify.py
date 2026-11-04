"""补充材料分类（规则）。分类：方法补充 / 公式推导 / 实验配置 / 额外实验 / 数据集 / 代码 / 其他。

规则只用文件名、扩展名和（本地可得的）内容样本，不联网；AI 分类是用户显式触发的另一条路径（见 analyze.py）。
"""
from __future__ import annotations

import re

from . import files as F

_KEYWORDS: list[tuple[str, re.Pattern, str]] = [
    ("公式推导", re.compile(r"proof|derivation|derive|lemma|theorem|corollary|appendix|supplementary (?:note|equation)|推导|证明|定理|引理", re.I), "出现推导/证明类关键词"),
    ("实验配置", re.compile(r"hyper-?parameter|configuration|implementation details|experimental (?:setup|settings)|training details|settings|超参数|实验设置|参数设置|实现细节", re.I), "出现超参数/实验设置类关键词"),
    ("额外实验", re.compile(r"additional (?:experiments|results)|extended data|supplementary (?:fig|table)|ablation|robustness|sensitivity|extra experiments|补充实验|额外实验|消融|鲁棒性", re.I), "出现额外实验/补充图表类关键词"),
    ("方法补充", re.compile(r"supplementary (?:method|section|information)|methods?|algorithm|model details|方法|算法", re.I), "出现方法/算法类关键词"),
]


def rule_category(filename: str, text_sample: str = "") -> tuple[str, str]:
    """返回 (类别, 依据说明)。"""
    kind = F.kind_of(filename)
    name = filename.lower()
    if kind == "code" or (kind == "archive" and re.search(r"code|source|src|repo|implementation|代码|源码", name)):
        return "代码", "代码文件或名称含 code/source"
    if kind == "table":
        return "数据集", f"表格数据文件 {F.ext_of(filename)}"
    if kind == "archive" and re.search(r"data|dataset|数据", name):
        return "数据集", "压缩包名称含 data"
    if kind == "archive":
        return "其他", "压缩包，需查看内容后再判断"
    haystack = f"{name}\n{text_sample[:6000]}"
    scores: dict[str, int] = {}
    for cat, pat, _why in _KEYWORDS:
        scores[cat] = len(pat.findall(haystack))
    best = max(scores, key=lambda c: scores[c])
    if scores[best] > 0:
        why = next(w for c, _p, w in _KEYWORDS if c == best)
        return best, f"{why}（命中 {scores[best]} 处）"
    return "其他", "没有命中任何分类关键词"


# 阅读顺序：先看对理解正文最有帮助、体量小的材料
_ORDER_WEIGHT = {"方法补充": 1, "公式推导": 2, "实验配置": 3, "额外实验": 4, "数据集": 5, "代码": 6, "其他": 7}


def rule_recommend(assets: list[dict], question: str = "") -> list[dict]:
    """没有模型或未授权时的阅读顺序：按类别权重，并让与问题关键词相关的材料靠前。"""
    q = set(re.findall(r"[a-z0-9]{3,}|[一-鿿]{2}", (question or "").lower()))
    scored = []
    for a in assets:
        if a["download_status"] != "done":
            continue
        text = f"{a['filename']} {a.get('summary') or ''}".lower()
        overlap = len(q & set(re.findall(r"[a-z0-9]{3,}|[一-鿿]{2}", text)))
        weight = _ORDER_WEIGHT.get(a["category"], 7) - overlap * 2
        reason = f"类别：{a['category']}"
        if overlap:
            reason += f"；与问题有 {overlap} 个相同关键词"
        scored.append((weight, a["created_at"], a["id"], reason))
    scored.sort()
    return [{"asset_id": aid, "order": i + 1, "reason": reason} for i, (_w, _c, aid, reason) in enumerate(scored)]
