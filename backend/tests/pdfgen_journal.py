"""模拟 Nature 系期刊版式的合成 PDF：左右交替的页眉横幅与页脚、通栏粗体摘要、首字下沉、
上方是矢量图的双栏页、小字号通栏图注、行内粗体小标题、带编号的显示公式、参考文献。"""
from __future__ import annotations

from pathlib import Path

import pymupdf



def _put(page, x: float, y: float, w: float, text: str, size: float, font: str = "helv", gap: float = 6) -> float:
    """放置文本框并按实际用掉的高度推进 y（insert_textbox 返回的是剩余高度）。"""
    rc = page.insert_textbox(pymupdf.Rect(x, y, x + w, y + 600), text, fontsize=size, fontname=font)
    assert rc >= 0, f"textbox overflow for: {text[:40]}"
    return y + (600 - rc) + gap


def build_journal_paper(path: Path) -> Path:
    doc = pymupdf.open()
    colw, lx, rx = 240, 50, 305
    for n in range(1, 6):
        page = doc.new_page(width=595, height=842)
        # 页眉横幅：左右标签随页码奇偶交替
        left, right = ("ARTICLES", "NATURE MACHINE INTELLIGENCE") if n % 2 else ("NATURE MACHINE INTELLIGENCE", "ARTICLES")
        page.insert_text((50, 36), left, fontsize=14, fontname="hebo")
        page.insert_text((545 - pymupdf.get_text_length(right, "hebo", 14), 36), right, fontsize=14, fontname="hebo")
        page.insert_text((50, 820), "NATURE MACHINE INTELLIGENCE | VOL 2 | JUNE 2020 | 317-324", fontsize=7, fontname="helv")
        page.insert_text((540, 820), str(316 + n), fontsize=8, fontname="helv")

        if n == 1:
            y = _put(page, 50, 80, 495, "Finding key players in complex networks through deep reinforcement learning", 24, "hebo", 10)
            page.insert_text((50, y + 8), "Changjun Fan", fontsize=12, fontname="hebo")
            page.insert_text((140, y + 3), "1,2", fontsize=7, fontname="hebo")
            page.insert_text((160, y + 8), ", Li Zeng", fontsize=12, fontname="hebo")
            page.insert_text((222, y + 3), "1", fontsize=7, fontname="hebo")
            page.insert_text((232, y + 8), " and Yang-Yu Liu", fontsize=12, fontname="hebo")
            y += 28
            y = _put(
                page, 50, y, 495,
                "Finding an optimal set of nodes, called key players, whose activation or removal would maximally "
                "enhance or degrade a certain network functionality, is a fundamental class of problems in network "
                "science. Here we introduce a deep reinforcement learning framework FINDER, which can be trained "
                "purely on small synthetic networks and then applied to a wide spectrum of application scenarios.",
                9.5, "hebo", 14,
            )
            # 首字下沉 + 左栏正文
            page.insert_text((50, y + 44), "T", fontsize=41, fontname="helv")
            yl = _put(
                page, 86, y, 200,
                "he network, or graph in discrete mathematics, is a common data structure to describe many "
                "types of interactive systems such as the Internet and power grids.", 9.3, "tiro", 4,
            )
            yl = _put(
                page, lx, max(yl, y + 52), colw,
                "Finding an optimal set of key players in complex networks has been a long-standing problem "
                "in network science, with many real-world applications.", 9.3, "tiro", 6,
            )
            _put(
                page, rx, y, colw,
                "Depending on the specific application scenario, we need to define the corresponding measure "
                "to quantify the network functionality appropriately.", 9.3, "tiro", 6,
            )
            page.insert_text(
                (50, 790), "1College of Systems Engineering, National University of Defense Technology, Changsha, China.",
                fontsize=6.5, fontname="helv",
            )
        elif n == 2:
            # 上方：矢量图（一组矩形）+ 子图标签 + 通栏小字号图注
            for i in range(6):
                for j in range(4):
                    page.draw_rect(pymupdf.Rect(120 + i * 60, 80 + j * 30, 170 + i * 60, 105 + j * 30), color=(0, 0, 0), width=0.5)
            for i, lab in enumerate(["HD: remove 16 nodes", "CI: remove 16 nodes", "FINDER: remove 14 nodes"]):
                page.insert_text((130 + i * 130, 125 + i * 5), lab, fontsize=6.5, fontname="helv")
            page.insert_text((122, 72), "a  Original network", fontsize=6.5, fontname="hebo")
            page.insert_text((300, 72), "b  Residual network", fontsize=6.5, fontname="hebo")
            page.insert_text((50, 232), "Fig. 1 | Finding key players in a network.", fontsize=8, fontname="hebo")
            _put(
                page, 50, 236, 495,
                "a, The 9/11 terrorist network. b, Removing 16 nodes with the highest degree. FINAL-CAPTION-WORD.", 8, "helv", 6,
            )
            # 左栏：段落 + 带编号的公式
            yl = _put(
                page, lx, 290, colw,
                "Formally, given a network G, our learning objective is to design a node removal strategy that "
                "minimizes the accumulated normalized connectivity:", 9.3, "tiro", 6,
            )
            ye = yl + 22
            page.insert_text((lx + 20, ye), "R(v1, v2, ..., vN) =", fontsize=9.3, fontname="tiit")
            page.insert_text((lx + 118, ye - 7), "1", fontsize=6.5, fontname="tiit")
            page.insert_text((lx + 118, ye + 7), "N", fontsize=6.5, fontname="tiit")
            page.insert_text((lx + 130, ye), "sum", fontsize=9.3, fontname="tiit")
            page.insert_text((lx + 160, ye - 8), "sigma(G-v)", fontsize=6.5, fontname="tiit")
            page.insert_text((lx + 170, ye + 8), "sigma(G)", fontsize=6.5, fontname="tiit")
            page.insert_text((lx + colw - 20, ye), "(1)", fontsize=9.3, fontname="tiro")
            _put(page, lx, ye + 24, colw, "Here, N is the total number of nodes and sigma is the connectivity measure.", 9.3, "tiro", 6)
            # 右栏：独立粗体小标题 + 行内粗体小标题
            page.insert_text((rx, 292), "Model", fontsize=10.5, fontname="hebo")
            page.insert_text((rx, 308), "Framework.", fontsize=9.3, fontname="hebo")
            page.insert_text((rx + 52, 308), "Fundamentally different from traditional methods,", fontsize=9.3, fontname="tiro")
            _put(
                page, rx, 312, colw,
                "FINDER takes a purely data-driven approach without any domain specific heuristic, "
                "and is trained offline on small synthetic graphs.", 9.3, "tiro", 6,
            )
        elif n in (3, 4):
            yl = yr = 80
            for k in range(3):
                yl = _put(page, lx, yl, colw, f"Body paragraph {k} on page {n}. " + "Cascading failures spread through networks. " * 4, 9.3, "tiro", 6)
                yr = _put(page, rx, yr, colw, f"Another paragraph {k} on page {n}. " + "Recovery needs efficient strategies. " * 4, 9.3, "tiro", 6)
        else:
            y = _put(page, 50, 80, 495, "References", 10.5, "hebo", 6)
            for k in range(1, 4):
                y = _put(page, 50, y, 495, f"{k}. Author {k}. A study on cascading failures. Journal of Networks, 20{10 + k}.", 8, "helv", 4)
    doc.save(str(path))
    doc.close()
    return path
