"""正文里的引用标记识别：把 “[12]”“[12–15]”“systems1,2”“refs 3, 4”“(Smith et al., 2020)”映射到参考文献编号。

只处理明确的引用写法；映射不到参考文献列表的一律不当作引用（“Fig. 3”“Table 1”“S1”“log2”这类都不会被误加链接）。
"""
from __future__ import annotations

import re
from collections import Counter

from .parse import first_author_surname

_NUMS = r"\d{1,3}(?:\s*[,–‐\-]\s*\d{1,3}){0,6}"
_BRACKET = re.compile(rf"(?<![A-Za-z0-9])\[\s*({_NUMS})\s*\]")
_REF_WORD = re.compile(rf"\b[Rr]efs?\.?\s+({_NUMS})")
_SUP = re.compile(r"(?<![^\W\d_])([^\W\d_]{3,})(\d{1,3}(?:[,–‐-]\d{1,3}){0,4})(?=[\s.,;:)\]]|$)")
_PAREN = re.compile(r"\(([^()]{3,200})\)")
_NAME = r"[A-ZÀ-ÖØ-Þ][\w’'\-À-ÿ]+"
_AY_PART = re.compile(
    rf"({_NAME})(?:\s+et al\.?|\s+(?:and|&)\s+{_NAME})?,?\s+((?:19|20)\d{{2}})[a-z]?"
)
_AY_NARRATIVE = re.compile(
    rf"\b({_NAME})(?:\s+et al\.?|\s+(?:and|&)\s+{_NAME})?\s+\(\s*((?:19|20)\d{{2}})[a-z]?\s*\)"
)

# 数学/记号里常见的“词+数字”，不是引用（与前端 textClean.ts 保持一致）
NOT_CITATION = {
    "resnet", "vgg", "bert", "gpt", "yolo", "densenet", "efficientnet", "mobilenet", "inception", "alexnet", "roberta", "llama",
    "albert", "xlnet", "electra", "convnext", "unet", "python", "cuda", "log", "exp", "top", "sin", "cos", "tan", "max", "min",
    "arg", "dim", "det", "tr", "sup", "inf", "lim", "sinh", "tanh", "mod",
}


def expand_numbers(group: str, valid: set[int]) -> list[int]:
    """“1,2”“12–15”“3, 5-7” → [1, 2]、[12..15]、[3, 5, 6, 7]；只保留参考文献列表里确实存在的编号；区间最长 30。"""
    out: list[int] = []
    for part in re.split(r"\s*,\s*", group.strip()):
        m = re.fullmatch(r"(\d{1,3})\s*[–‐\-]\s*(\d{1,3})", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a < b and b - a <= 30:
                out.extend(range(a, b + 1))
            continue
        if part.isdigit():
            out.append(int(part))
    seen: set[int] = set()
    return [n for n in out if n in valid and not (n in seen or seen.add(n))]


def build_vocab(passages: list[dict]) -> Counter:
    """论文里出现过的“独立单词”次数：判断“词+数字”里的词本身是不是一个真实的词。"""
    vocab: Counter = Counter()
    for p in passages:
        if p["kind"] in ("paragraph", "caption"):
            for m in re.finditer(r"(?<![^\W\d_])[^\W\d_]{3,}(?![^\W_])", p["text"]):
                vocab[m.group(0)] += 1
    return vocab


def author_year_index(entries: list[dict]) -> dict[tuple[str, int], list[int]]:
    out: dict[tuple[str, int], list[int]] = {}
    for e in entries:
        sn = first_author_surname(e)
        if sn and e.get("year"):
            out.setdefault((sn, e["year"]), []).append(e["index"])
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^\w\-]", "", s).lower()


def detect_markers(text: str, valid: set[int], ay: dict[tuple[str, int], list[int]], vocab: Counter) -> list[dict]:
    """返回段落里的引用标记 [{kind, text, start, end, refs}]，按位置排序、互不重叠。"""
    found: list[dict] = []

    def add(kind: str, start: int, end: int, refs: list[int]) -> None:
        if refs:
            found.append({"kind": kind, "text": text[start:end], "start": start, "end": end, "refs": refs})

    for m in _BRACKET.finditer(text):
        add("bracket", m.start(), m.end(), expand_numbers(m.group(1), valid))
    for m in _REF_WORD.finditer(text):
        add("ref", m.start(1), m.end(1), expand_numbers(m.group(1), valid))
    for m in _SUP.finditer(text):
        word, digits = m.group(1), m.group(2)
        lower, upper = word == word.lower(), word == word.upper() and len(word) >= 2
        if not (lower or upper) or word.lower() in NOT_CITATION or re.match(r"0\d", digits):
            continue
        if vocab.get(word, 0) < (2 if upper else 1):
            continue
        add("sup", m.start(2), m.end(2), expand_numbers(digits, valid))
    if ay:
        for pm in _PAREN.finditer(text):
            inner_start = pm.start(1)
            for part in re.finditer(r"[^;]+", pm.group(1)):
                am = _AY_PART.search(part.group(0))
                if not am:
                    continue
                refs = ay.get((_norm(am.group(1)), int(am.group(2))), [])
                if len(refs) == 1:  # 姓氏+年份对应多条时不猜
                    s = inner_start + part.start() + am.start()
                    add("authoryear", s, inner_start + part.start() + am.end(), refs)
        for nm in _AY_NARRATIVE.finditer(text):
            refs = ay.get((_norm(nm.group(1)), int(nm.group(2))), [])
            if len(refs) == 1:
                add("authoryear", nm.start(), nm.end(), refs)
    found.sort(key=lambda d: (d["start"], -d["end"]))
    out: list[dict] = []
    last_end = -1
    for d in found:  # 去掉重叠（保留靠前、较长的）
        if d["start"] >= last_end:
            out.append(d)
            last_end = d["end"]
    return out


def detect_all(passages: list[dict], entries: list[dict], style: str) -> list[dict]:
    """passages：论文全部段落 [{ord, kind, text}]；entries：解析出的参考文献。返回 [{ord, markers}]。"""
    valid = {e["index"] for e in entries}
    if not entries:
        return []
    vocab = build_vocab(passages)
    ay = author_year_index(entries) if style != "numeric" or any(e.get("year") for e in entries) else {}
    out = []
    for p in passages:
        if p["kind"] not in ("paragraph", "caption"):
            continue
        marks = detect_markers(p["text"], valid if style in ("numeric", "unknown") else set(), ay, vocab)
        if marks:
            out.append({"ord": p["ord"], "markers": marks})
    return out
