"""测试用 PDF 生成器：用 PyMuPDF 合成一篇双栏学术论文（含页眉页脚、章节、图注、参考文献）。"""
from __future__ import annotations

import math
from pathlib import Path

import pymupdf

TITLE = "Dual Attention Routing for Robust Cascade Recovery"
AUTHORS = "Alice Zhang, Bob Miller and Carol Davies"
ABSTRACT = (
    "Abstract. Cascading failures remain a central challenge for networked infrastructure. "
    "We propose Dual Attention Routing (DAR), a method that restores failed nodes by reallocating "
    "load through attention over both topology and temporal signals. Experiments on three real "
    "networks show that DAR reduces recovery time by 23 percent compared with the strongest baseline."
)

INTRO = [
    "Cascading failures occur when the failure of a few nodes overloads their neighbours and triggers "
    "further failures throughout the network. Existing recovery strategies rely on static priorities and "
    "ignore how load evolves over time, which limits their effectiveness in large networks.",
    "The key challenge is that recovery decisions must be made online with incomplete information. "
    "To address this challenge we introduce a learned routing policy that attends to both structure and "
    "dynamics. Our contributions are summarised as follows.",
]
METHOD = [
    "Our method, Dual Attention Routing (DAR), consists of two modules: a topology attention module "
    "and a temporal attention module. The topology module computes attention weights over neighbouring "
    "nodes, while the temporal module attends to the recent load history of each node.",
    "The routing score of node i is defined as s_i = softmax(W_t a_i + W_h h_i), where a_i is the "
    "topology attention output and h_i is the temporal attention output. We motivate the additive "
    "combination by the observation that the two signals are complementary.",
]
EXPERIMENT = [
    "We evaluate DAR on three real networks: a power grid, a road network and an internet topology. "
    "We compare against four baselines and report the recovery time and the fraction of restored load.",
    "Table 1 shows that DAR achieves the shortest recovery time on all datasets. The ablation study "
    "indicates that removing the temporal module increases recovery time by 15 percent.",
]
CONCLUSION = [
    "We presented DAR, a dual attention routing method for cascade recovery. A limitation of our work "
    "is that we assume accurate load measurements; future work will relax this assumption.",
]
REFERENCES = [
    "[1] A. Smith and B. Jones. Cascading failures in power grids. Journal of Networks, 2019.",
    "[2] C. Lee. Attention is useful for routing. Proceedings of the Routing Conference, 2021.",
]


def _lines(text: str, width: float, size: float, font: str = "helv") -> int:
    return max(1, math.ceil(pymupdf.get_text_length(text, font, size) / width * 1.1))


def _put(page, x: float, y: float, w: float, text: str, size: float, font: str = "helv", gap: float = 6) -> float:
    h = _lines(text, w, size, font) * size * 1.3 + 4
    rc = page.insert_textbox(pymupdf.Rect(x, y, x + w, y + h + 6), text, fontsize=size, fontname=font)
    assert rc >= 0, f"textbox overflow for: {text[:40]}"
    return y + h + gap


def _chrome(page, number: int) -> None:
    page.insert_text((50, 35), "Journal of Cascade Recovery  Vol. 12", fontsize=8, fontname="helv")
    page.insert_text((290, 815), str(number), fontsize=9, fontname="helv")


def build_paper(path: Path, *, pages: int = 5, doi: str | None = "10.1234/dar.2023.001") -> Path:
    doc = pymupdf.open()
    colw = 240
    lx, rx = 50, 305
    for n in range(1, pages + 1):
        page = doc.new_page(width=595, height=842)
        _chrome(page, n)
        if n == 1:
            y = _put(page, 50, 70, 495, TITLE, 18, "hebo", 8)
            y = _put(page, 50, y, 495, AUTHORS, 11, "helv", 10)
            y = _put(page, 50, y, 495, ABSTRACT, 10, "helv", 12)
            if doi:
                page.insert_text((50, 805), f"https://doi.org/{doi}   (c) 2023", fontsize=8, fontname="helv")
            yl = yr = y
            yl = _put(page, lx, yl, colw, "1 Introduction", 12, "hebo", 6)
            for p in INTRO:
                yl = _put(page, lx, yl, colw, p, 10, "helv", 6)
            yr = _put(page, rx, yr, colw, "2 Method", 12, "hebo", 6)
            for p in METHOD[:1]:
                yr = _put(page, rx, yr, colw, p, 10, "helv", 6)
        elif n == 2:
            yl = yr = 70
            for p in METHOD[1:]:
                yl = _put(page, lx, yl, colw, p, 10, "helv", 6)
            yl = _put(page, lx, yl, colw, "Figure 1: Overview of the proposed DAR framework.", 9, "helv", 6)
            yr = _put(page, rx, yr, colw, "3 Experiments", 12, "hebo", 6)
            for p in EXPERIMENT[:1]:
                yr = _put(page, rx, yr, colw, p, 10, "helv", 6)
        elif n == 3:
            yl = yr = 70
            for p in EXPERIMENT[1:]:
                yl = _put(page, lx, yl, colw, p, 10, "helv", 6)
            yr = _put(page, rx, yr, colw, "4 Conclusion", 12, "hebo", 6)
            for p in CONCLUSION:
                yr = _put(page, rx, yr, colw, p, 10, "helv", 6)
        elif n == 4:
            yl = yr = 70
            yl = _put(page, lx, yl, colw, "References", 12, "hebo", 6)
            for p in REFERENCES:
                yl = _put(page, lx, yl, colw, p, 9, "helv", 4)
        else:
            yl = 70
            _put(page, lx, yl, colw, "Additional page of filler text about cascading failures and recovery.", 10)
    doc.set_metadata({"title": "", "author": ""})
    doc.save(str(path))
    doc.close()
    return path


def build_scanned(path: Path) -> Path:
    """只有图片、没有文字层的 PDF。"""
    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), False)
        pix.clear_with(200)
        page.insert_image(pymupdf.Rect(50, 50, 300, 300), pixmap=pix)
    doc.save(str(path))
    doc.close()
    return path


def build_rotated(path: Path) -> Path:
    doc = pymupdf.open()
    for _ in range(4):
        page = doc.new_page(width=600, height=800)
        page.insert_textbox(
            pymupdf.Rect(72, 100, 520, 400),
            "This landscape page contains a long paragraph of text that should be recovered correctly "
            "even when the page rotation attribute is set to ninety degrees in the document. " * 3,
            fontsize=11,
        )
        page.set_rotation(90)
    doc.save(str(path))
    doc.close()
    return path


def build_chinese(path: Path) -> Path:
    """中文论文：标题、空格分隔的作者、行内“摘要”、编号章节。"""
    doc = pymupdf.open()
    f = "china-s"
    for n in range(1, 5):
        page = doc.new_page(width=595, height=842)
        page.insert_text((290, 815), str(n), fontsize=9)
        if n == 1:
            page.insert_textbox(pymupdf.Rect(50, 60, 545, 100), "基于双重注意力的级联失效恢复方法研究", fontsize=18, fontname=f)
            page.insert_textbox(pymupdf.Rect(50, 110, 545, 130), "张三 李四 王五", fontsize=11, fontname=f)
            page.insert_textbox(pymupdf.Rect(50, 150, 545, 200), "摘要 级联失效是网络化基础设施面临的核心问题。本文提出一种基于双重注意力的恢复方法，在三个真实网络上将恢复时间缩短了百分之二十三。", fontsize=10, fontname=f)
            page.insert_textbox(pymupdf.Rect(50, 220, 545, 245), "1 引言", fontsize=13, fontname=f)
            page.insert_textbox(pymupdf.Rect(50, 250, 545, 330), "级联失效发生在少数节点失效后，其邻居节点因负载过重而相继失效。现有恢复策略依赖静态优先级，忽略了负载随时间的变化。", fontsize=10, fontname=f)
        if n == 2:
            page.insert_textbox(pymupdf.Rect(50, 60, 545, 85), "2 方法", fontsize=13, fontname=f)
            page.insert_textbox(pymupdf.Rect(50, 90, 545, 180), "本文方法由拓扑注意力模块和时间注意力模块组成。拓扑模块计算邻居节点的注意力权重，时间模块关注节点近期的负载历史。", fontsize=10, fontname=f)
    doc.save(str(path))
    doc.close()
    return path


SCAN_TEXT = (
    "Cascading failures propagate through coupled infrastructure networks when a single overloaded "
    "node redistributes its load to neighbouring nodes. We study percolation thresholds and resilience "
    "of power grids under targeted attacks and propose an adaptive mitigation strategy."
)


def build_scanned_text(path: Path, pages: int = 2) -> Path:
    """整页都是一张图片、没有文字层的“扫描版”PDF：图片里是已知文字（先渲染成位图再嵌入）。"""
    out = pymupdf.open()
    for i in range(pages):
        src = pymupdf.open()
        sp = src.new_page(width=595, height=842)
        sp.insert_text((72, 100), f"{i + 1} Introduction", fontsize=18, fontname="hebo")
        sp.insert_textbox(pymupdf.Rect(72, 130, 523, 400), SCAN_TEXT * 2, fontsize=11, fontname="helv")
        pix = sp.get_pixmap(dpi=150)
        src.close()
        page = out.new_page(width=595, height=842)
        page.insert_image(page.rect, pixmap=pix)
    out.save(str(path))
    out.close()
    return path
