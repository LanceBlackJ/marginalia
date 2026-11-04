"""图内数据块的归类：落在图版面里的“数字堆砌”“标签堆砌”“子图标题”段落不属于正文阅读流。

解析阶段只检测到图的各个子图区域（矢量图/位图），子图之间的数据网格（如 Fig.5 里每个网络的 Avg score 表）、
坐标轴刻度、方法名图例都是普通文字，会被当成正文段落。这里用图注推出“图的版面区”，再把区内不像句子的段落改成 figure_text。
纯函数，便于测试：输入是已经排好序的段落（dict 或带属性的对象均可，通过 getter 访问）与图实体。
"""
from __future__ import annotations

import re
from typing import Callable, Iterable

CAPTION_FIG_RE = re.compile(r"^\s*(?:(?:Supplementary |Extended Data )?)(?:Fig\.|Figure|FIGURE|图)\s*[A-Z]?\d+[A-Za-z]?\s*[:.|：]")
PANEL_TITLE_RE = re.compile(r"^(?:[a-z]\s+)?[A-Z][A-Za-z0-9\-]*\s*[–—-]\s*\S")  # “d CN – crime – node-unweighted …”
NUM_TOKEN_RE = re.compile(r"^[-+−–]?\d+(?:[.,]\d+)*(?:[%×x]\d*)?$|^NA$|^N/A$|^n\.?a\.?$", re.IGNORECASE)
SENTENCE_END_RE = re.compile(r"[.!?。！？][\"”’)\]]?\s*$")

MIN_TOKENS = 6


def tokens(text: str) -> list[str]:
    return [t for t in re.split(r"\s+", text.strip()) if t]


def numeric_ratio(text: str) -> float:
    toks = tokens(text)
    if not toks:
        return 0.0
    return sum(1 for t in toks if NUM_TOKEN_RE.match(t)) / len(toks)


def is_numeric_dense(text: str) -> bool:
    """大部分词是数字（数据网格、坐标轴刻度）。"""
    toks = tokens(text)
    return len(toks) >= MIN_TOKENS and numeric_ratio(text) >= 0.6


def is_label_soup(text: str) -> bool:
    """同一组很短的词反复出现（图例：HDA CI RatioCut FINDER HDA CI RatioCut FINDER …），没有句子成分。"""
    toks = tokens(text)
    if len(toks) < MIN_TOKENS or SENTENCE_END_RE.search(text):
        return False
    distinct = len({t.lower() for t in toks})
    avg_len = sum(len(t) for t in toks) / len(toks)
    return distinct / len(toks) <= 0.55 and avg_len <= 10


ROW_RE = re.compile(r"[A-Za-z][\w\-]*(?:\s[A-Za-z][\w\-]*){0,2}\s+(?:[-+−–]?\d[\d.,]*\s+){2,}[-+−–]?\d[\d.,]*")


def tabular_row_count(text: str) -> int:
    """文字里有几处“行标签 + 至少 3 个数字”（数据网格的行，如 “Crime 11.62 12.43 13.83 …”）。"""
    return len(ROW_RE.findall(text))


def is_tabular_rows(text: str) -> bool:
    """这段文字是数据网格的行（而不是曲线图的刻度或图例）：有行标签加数字的写法，且数字占多数。"""
    return tabular_row_count(text) >= 1 and numeric_ratio(text) >= 0.5


def looks_like_sentence(text: str) -> bool:
    """较长的、以句号结尾、含较多小写单词的才算正文句子；图里的短标签不算。"""
    t = text.strip()
    words = [w for w in tokens(t) if re.search(r"[a-z]{3,}", w)]
    return len(t) >= 140 and bool(SENTENCE_END_RE.search(t)) and len(words) >= 18


def figure_zones(passages: list, figures: Iterable, get: Callable[[object, str], object]) -> list[dict]:
    """推出每张图的版面区：[{page, caption_ord, x0, y0, x1, y1}]。

    图注（“Fig. N | …”）在图的下方：同页上、位于图注上方且没有被另一个图注隔开的所有图区域，合起来就是这张图的版面。
    """
    zones: list[dict] = []
    caps = [p for p in passages if get(p, "kind") == "caption" and CAPTION_FIG_RE.match(str(get(p, "text")))]
    by_page: dict[int, list] = {}
    for c in caps:
        by_page.setdefault(get(c, "page"), []).append(c)
    regs = [f for f in figures if get(f, "kind") == "figure"]
    for page, page_caps in by_page.items():
        page_caps.sort(key=lambda p: get(p, "bbox")[1])
        page_regs = [f for f in regs if get(f, "page") == page]
        prev_bottom = 0.0
        for cap in page_caps:
            cy0 = get(cap, "bbox")[1]
            mine = [f for f in page_regs if prev_bottom - 2 <= get(f, "bbox")[1] and get(f, "bbox")[3] <= cy0 + 8]
            if mine:
                x0 = min(get(f, "bbox")[0] for f in mine)
                y0 = min(get(f, "bbox")[1] for f in mine)
                x1 = max(get(f, "bbox")[2] for f in mine)
                # 通栏的大图常常有一两个子图没被识别成图区域（比如最左边一列），版面区会缩在一侧：
                # 图已经很宽并且贴着一侧页边时，把另一侧扩到页边，免得左边的坐标和图例落在区外
                xs = [get(p, "bbox") for p in passages if get(p, "page") == page]
                if xs:
                    px0, px1 = min(b[0] for b in xs), max(b[2] for b in xs)
                    if x1 - x0 >= 0.55 * (px1 - px0):
                        if x1 >= px1 - 24:
                            x0 = min(x0, px0)
                        if x0 <= px0 + 24:
                            x1 = max(x1, px1)
                zones.append({"page": page, "caption_ord": get(cap, "ord"), "x0": x0 - 4, "y0": y0 - 4, "x1": x1 + 4, "y1": cy0})
            prev_bottom = get(cap, "bbox")[3]
    return zones


def _inside(bbox, z: dict) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return z["x0"] <= cx <= z["x1"] and z["y0"] <= cy <= z["y1"]


PANEL_LETTERS_RE = re.compile(r"^(?:[A-Za-z]\s+){0,7}[A-Za-z]$")
PANEL_LETTER_BAND = 30.0  # 版面区上边缘往上多远算“紧贴”


def _panel_letters_above(text: str, bbox, z: dict) -> bool:
    """文字只由单个字母组成（子图标号），并且紧贴在这张图版面区的上方、水平上落在版面区内。"""
    if not PANEL_LETTERS_RE.match(text.strip()):
        return False
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return z["x0"] <= cx <= z["x1"] and z["y0"] - PANEL_LETTER_BAND <= cy < z["y0"]


def data_reason(text: str, in_zone: bool) -> str | None:
    """这段文字为什么应当归入“图内文字”；None 表示保持正文。"""
    if looks_like_sentence(text):
        return None
    if is_numeric_dense(text):
        return "数字密集"
    if is_label_soup(text):
        return "图例或标签堆砌"
    if in_zone and (PANEL_TITLE_RE.match(text.strip()) or len(text.strip()) < 160):
        return "位于图的版面内"
    return None


def reclassify(passages: list, figures: Iterable, get: Callable[[object, str], object], set_kind: Callable[[object, str], None]) -> dict[int, str]:
    """把图版面内不像句子的 paragraph 改成 figure_text；返回 {ord: 原因}。

    只在图的版面区内处理（数字密集或标签堆砌的段落在版面区外保持原样，避免误伤正文里的数据表行）。
    """
    zones = figure_zones(passages, figures, get)
    changed: dict[int, str] = {}
    for p in passages:
        if get(p, "kind") != "paragraph":
            continue
        z = next((z for z in zones if z["page"] == get(p, "page") and _inside(get(p, "bbox"), z)), None)
        if z is None:
            # 子图字母（“b a”“a b c”）常常在图的上边缘外面一点点：只有单个字母组成的短文字、紧贴在图上方才算图内文字
            above = next((z for z in zones if z["page"] == get(p, "page") and _panel_letters_above(str(get(p, "text")), get(p, "bbox"), z)), None)
            if above is not None:
                set_kind(p, "figure_text")
                changed[get(p, "ord")] = "子图字母"
            continue
        why = data_reason(str(get(p, "text")), True)
        if why:
            set_kind(p, "figure_text")
            changed[get(p, "ord")] = why
    return changed
