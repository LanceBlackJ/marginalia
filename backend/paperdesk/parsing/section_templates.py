"""按出版社的版式惯例修正章节层级。

PDF 里标题的层级本来就不可靠（MinerU 的 level 不稳，无编号的版式根本没有层级信息）。出版社的模板是有规律的：

- 数字编号（Springer、ACM、Elsevier、arXiv 与大部分会议模板）：“1 Introduction / 2.1 Method / 2.1.1 …”，层级看编号的点数，不用改；
- IEEE：“I. INTRODUCTION / A. Subsection / 1) …”，罗马数字是一级，字母是二级，解析时已按这个认；
- 无编号（Nature 系列、Science、Cell、PLOS 等）：一级标题是固定的那几个（Introduction、Results、Discussion、Methods、References，
  以及后面的 Data availability、Code availability、Acknowledgements、Author contributions、Competing interests……），
  Results 和 Methods 下面不在这个名单里的标题，就是它们的小节（二级）。

只处理“没有编号”的版式，并且只动明确认得的情况；不确定的保持原样。"""
from __future__ import annotations

import re
from typing import Any

# 无编号版式里的一级标题（统一小写、去掉编号和标点后比较）
_MAJOR = re.compile(
    r"^(?:abstract|summary|keywords?|introduction|background|related work|results?|results and discussion|discussion|discussion and conclusions?|"
    r"conclusions?|concluding remarks|outlook|methods?|materials and methods|online methods|methods and materials|experimental(?: section| procedures)?|"
    r"star methods|references?|bibliography|acknowledge?ments?|author contributions?|authors? contributions?|competing interests?|"
    r"conflicts? of interest|declaration of competing interests?|funding|data availability|data and code availability|code availability|"
    r"availability of data and materials|additional information|supplementary information|reporting summary|"
    r"reprints and permissions(?: information)?|peer review information|ethics(?: statement| declarations?)?|declarations|abbreviations|"
    r"publisher'?s note|extended data|source data|appendix|appendices|supplementary materials?|"
    r"ethics approval and consent to participate|consent for publication|author information|rights and permissions|about this article)$"
)
# 这些一级标题下面的无编号标题是它们的小节
_CONTAINER = re.compile(
    r"^(?:results?|results and discussion|methods?|materials and methods|online methods|methods and materials|experimental(?: section| procedures)?|star methods)$"
)
_NUMBERED = re.compile(r"^\s*(?:\d+(?:\.\d+)*[.)]?\s|[IVXLC]+\.\s|[A-Z][.)]\s)")
# 不是章节的版权行等：和一级标题放在一起，不当小节
_BOILERPLATE = re.compile(r"^\s*(?:©|copyright|open access|\(c\))", re.I)
_SYNTHETIC = {"首部信息", "正文"}


def _norm(title: str) -> str:
    t = re.sub(r"^\s*(?:\d+(?:\.\d+)*[.)]?|[IVXLC]+\.|[A-Z][.)])\s+", "", title.strip())
    return re.sub(r"[\s:：.]+$", "", t).lower()


def apply(sections: list[Any]) -> None:
    """就地修正 sections（有 .title / .level 的对象，按阅读顺序）里无编号版式的层级。"""
    real = [s for s in sections if s.title not in _SYNTHETIC]
    if len(real) < 3:
        return
    numbered = sum(1 for s in real if _NUMBERED.match(s.title))
    if numbered >= 2:
        return  # 有编号的版式：层级看编号，不在这里改
    in_container = False
    for s in sections:
        if s.title in _SYNTHETIC:
            in_container = False
            continue
        key = _norm(s.title)
        if _MAJOR.match(key) or _BOILERPLATE.match(s.title):
            s.level = 1
            in_container = bool(_CONTAINER.match(key))
        elif in_container:
            s.level = 2  # Results / Methods 下面的小标题
        # 其它（不在容器里、也不在名单里）保持原样
