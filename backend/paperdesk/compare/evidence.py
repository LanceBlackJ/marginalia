"""多论文引用：`[A:P12]` 表示“论文 A 的第 12 号段落”，`[A:ABS]` 表示论文 A 的摘要（只有摘要的论文）。

与单篇的 `[P12]` 机制一致：模型只给编号，后端回查数据库里的真实段落生成 quote，编造的编号标记为无法核实。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..ai.evidence import best_excerpt

# 一个括号组：[A:P12] / 【A：P12，P13】 / (A-P12; B:P7) / [论文A:P12]
_GROUP = re.compile(r"[\[【(（]\s*([^\[\]【】()（）]{2,80}?)\s*[\]】)）]")
_TOKEN = re.compile(r"(?:论文\s*)?(?:(?P<label>[A-Z])\s*[:：\-]\s*)?(?:(?P<abs>ABS|摘要)|P?\s*(?P<ord>\d+))", re.I)
_HAS_LABELED = re.compile(r"(?:论文\s*)?[A-Z]\s*[:：\-]\s*(?:P?\s*\d+|ABS|摘要)", re.I)
CITE_RE = re.compile(r"\[([A-Z]):(P(\d+)|ABS)\]")


def normalize_cites(text: str) -> str:
    """把各种写法统一成 `[A:P12]`。只处理“组内至少有一个带字母标签的引用”的括号，避免误伤普通括号。"""

    def repl(m: re.Match) -> str:
        inner = m.group(1)
        if not _HAS_LABELED.search(inner):
            return m.group(0)
        out: list[str] = []
        last_label: str | None = None
        for part in re.split(r"[,，、;；]|和|与|及", inner):
            t = _TOKEN.fullmatch(part.strip())
            if not t:
                return m.group(0)  # 组内有无法识别的内容：保持原样
            label = (t.group("label") or last_label)
            if not label:
                return m.group(0)
            label = label.upper()
            last_label = label
            out.append(f"[{label}:ABS]" if t.group("abs") else f"[{label}:P{int(t.group('ord'))}]")
        return "".join(out)

    return _GROUP.sub(repl, text)


def cited_pairs(text: str) -> list[tuple[str, int | None]]:
    """按出现顺序返回去重的 (label, ord)；ord 为 None 表示摘要。"""
    seen: list[tuple[str, int | None]] = []
    for m in CITE_RE.finditer(normalize_cites(text)):
        pair = (m.group(1), None if m.group(2) == "ABS" else int(m.group(3)))
        if pair not in seen:
            seen.append(pair)
    return seen


@dataclass
class PaperPack:
    label: str
    paper_id: str
    title: str
    year: int | None
    passages: dict[int, dict] = field(default_factory=dict)  # ord -> {page, section_title, text, bbox}
    abstract: str | None = None
    basis: str = "fulltext"  # fulltext | abstract


def verify_text(text: str, packs: dict[str, PaperPack]) -> tuple[str, list[dict], list[str]]:
    """返回 (规范化文本, 有效证据列表, 无效引用字符串列表)。"""
    norm = normalize_cites(text)
    evidence: list[dict] = []
    invalid: list[str] = []
    for label, ord_ in cited_pairs(norm):
        pack = packs.get(label)
        if pack is None:
            invalid.append(f"{label}:{'ABS' if ord_ is None else 'P' + str(ord_)}")
            continue
        if ord_ is None:
            if pack.abstract:
                evidence.append({"label": label, "paper_id": pack.paper_id, "paper_title": pack.title, "ord": None, "page": None,
                                 "section_title": "摘要", "quote": best_excerpt(pack.abstract), "bbox": None, "kind": "abstract"})
            else:
                invalid.append(f"{label}:ABS")
            continue
        p = pack.passages.get(ord_)
        if p is None:
            invalid.append(f"{label}:P{ord_}")
            continue
        evidence.append({"label": label, "paper_id": pack.paper_id, "paper_title": pack.title, "ord": ord_, "page": p["page"],
                         "section_title": p.get("section_title"), "quote": best_excerpt(p["text"]), "bbox": p.get("bbox"),
                         "kind": "paper_text"})
    return norm, evidence, invalid


def merge_evidence(*lists: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for lst in lists:
        for e in lst:
            k = (e["label"], e["ord"])
            if k not in seen:
                seen.add(k)
                out.append(e)
    return out
