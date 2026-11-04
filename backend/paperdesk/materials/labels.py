"""补充材料“标签”：Supplementary Table 5 / Fig. 3 / Section II.D.3 / Note 2 …

论文正文里写 “Supplementary Table 5”，补充材料 PDF 里的图注却常写成 “Table S5”（Nature 系、IEEE 系都如此）。
这里统一抽取标签，并给出两种匹配方式：LIKE 模式（找 caption/章节）与 FTS 查询（论文内搜索）。
不依赖 reader / ai，避免循环导入。
"""
from __future__ import annotations

import re

# “Supplementary Fig. 14”、“Supplementary Table 5”、“Supplementary Section II.D.3”、“Supplementary Note 2”、
# “Supplementary equation (27)”、“Supplementary algorithm 2”；以及中文“补充图 3”“补充表 2”
SUPP_REF_RE = re.compile(
    r"(?:Supplementary|Supporting|Extended\s+Data)\s+(Fig(?:ure)?s?\.?|Tables?|Sections?|Notes?|Algorithms?|Eq(?:uations?|s?\.)?|Methods?)\s*"
    r"\(?\s*([IVX]+(?:\.[A-Za-z0-9]+)*|[A-Z]?\d+(?:\.\d+)*[a-z]?)\s*\)?",
    re.I,
)
ZH_REF_RE = re.compile(r"补充(图|表|材料|说明|方法|算法)\s*([一二三四五六七八九十\d]+)?")
_KIND = {"fig": "Fig.", "tab": "Table", "sec": "Section", "not": "Note", "alg": "Algorithm", "equ": "Equation", "eq.": "Equation", "eqs": "Equation", "met": "Methods"}


def extract_labels(text: str) -> list[str]:
    """从文本里抽出“补充材料标签”，规范化为 'Supplementary Table 5' 这种形式（保序去重）。"""
    labels: list[str] = []
    for m in SUPP_REF_RE.finditer(text or ""):
        kind = _KIND.get(m.group(1).lower()[:3], m.group(1))
        num = m.group(2)
        if num[:1] in ("S", "s") and num[1:2].isdigit():
            num = num[1:]  # “Supplementary Table S5” → 5
        lab = f"Supplementary {kind} {num}"
        if lab not in labels:
            labels.append(lab)
    for m in ZH_REF_RE.finditer(text or ""):
        lab = f"补充{m.group(1)}{m.group(2) or ''}".strip()
        if lab not in labels:
            labels.append(lab)
    return labels


def label_patterns(label: str) -> list[str]:
    """标签 → 在补充文档里找 caption/章节的 LIKE 模式（既找 “Table 5”，也找 “Table S5”）。"""
    m = re.match(r"Supplementary\s+(\S+)\s+(.+)", label)
    if not m:
        return [f"%{label}%"]
    kind, num = m.group(1), m.group(2)
    base = [f"{label}%", f"%{label} |%", f"%{label}.%", f"%{label}:%"]
    short = {
        "Fig.": ["Fig. {n}%", "Figure {n}%", "Fig {n}%", "Fig. S{n}%", "Figure S{n}%", "Fig S{n}%"],
        "Table": ["Table {n}%", "Table S{n}%"],
        "Algorithm": ["Algorithm {n}%", "Algorithm S{n}%"],
        "Note": ["Note {n}%", "Supplementary Note {n}%", "Note S{n}%"],
        "Equation": ["%({n})%", "%(S{n})%"],
        "Section": ["%{n} %", "Section {n}%", "Section S{n}%"],
    }
    return base + [p.format(n=num) for p in short.get(kind, [])]


def supp_label_fts(q: str) -> str | None:
    """论文内搜索：把查询里的 “Supplementary Table 5” 展开成也能命中 “Table S5” 的 FTS 子查询（没有标签时返回 None）。"""
    alts: list[str] = []
    for lab in extract_labels(q):
        m = re.match(r"Supplementary\s+(\S+)\s+(\d+[a-z]?)$", lab)
        if not m:
            continue
        kind, num = m.group(1), m.group(2)
        word = {"Fig.": '"fig"*', "Table": '"table"', "Algorithm": '"algorithm"', "Note": '"note"'}.get(kind)
        if word:
            alts.append(f'({word} AND "s{num.lower()}")')
    return " OR ".join(alts) if alts else None
