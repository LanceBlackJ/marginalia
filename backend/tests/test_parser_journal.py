"""真实期刊（Nature 系）版式的解析回归测试：全部基于合成 PDF，不依赖任何用户文件。"""
from __future__ import annotations

from pathlib import Path

import pytest

import pdfgen_journal
from paperdesk.parsing.pdf_parser import parse_pdf


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    return parse_pdf(pdfgen_journal.build_journal_paper(tmp_path_factory.mktemp("journal") / "journal.pdf"))


def _titles(d):
    return [s.title for s in d.sections]


def test_front_matter_and_page_furniture(doc):
    all_text = " ".join(p.text for p in doc.passages)
    # 左右交替的页眉横幅、页脚、页码都被去除
    for junk in ("ARTICLES", "NATURE MACHINE INTELLIGENCE", "JUNE 2020", "317-324"):
        assert junk not in all_text, junk
    md = doc.metadata
    assert md.title == "Finding key players in complex networks through deep reinforcement learning"
    assert {"Changjun Fan", "Li Zeng"} <= set(md.authors)  # 带上标/间隙的作者行被整行识别
    assert md.abstract and md.abstract.startswith("Finding an optimal set of nodes")  # 无 “Abstract” 字样的粗体通栏摘要
    assert "Abstract" in _titles(doc)


def test_drop_cap_and_two_columns(doc):
    p1 = [p for p in doc.passages if p.page == 1]
    assert any(p.text.startswith("The network, or graph") for p in p1), [p.text[:30] for p in p1]  # 首字下沉并回
    texts = [p.text for p in p1]
    i_left = next(i for i, t in enumerate(texts) if t.startswith("Finding an optimal set of key players"))
    i_right = next(i for i, t in enumerate(texts) if t.startswith("Depending on the specific"))
    assert i_left < i_right  # 左栏先于右栏
    assert not any("Depending" in p.text and "Finding an optimal set of key" in p.text for p in p1)  # 没有跨栏拼行


def test_sections_run_in_headings_and_references(doc):
    titles = _titles(doc)
    assert "Model" in titles and "Framework" in titles and "References" in titles
    lvl = {s.title: s.level for s in doc.sections}
    assert lvl["Framework"] > lvl["Model"]  # 行内粗体小标题比独立小标题更深一层
    framework = next(s for s in doc.sections if s.title == "Framework")
    body = [p for p in doc.passages if p.section_ord == framework.ord]
    assert body and body[0].text.startswith("Fundamentally different")  # 小标题文字不混进正文
    assert any(p.kind == "reference" for p in doc.passages)


def test_figures_captions_and_numbered_equations(doc):
    figs = [f for f in doc.figures if f.page == 2]
    assert figs and any(f.label == "Fig. 1" and f.caption_ord is not None for f in figs)
    cap = next(p for p in doc.passages if p.kind == "caption")
    assert cap.text.startswith("Fig. 1") and "FINAL-CAPTION-WORD" in cap.text  # 图注是完整一段，没有被逐行切开
    labels = [p for p in doc.passages if p.page == 2 and "remove 16 nodes" in p.text]
    assert labels and all(p.kind != "paragraph" for p in labels)  # 图内文字不进正文段落
    assert doc.equations and doc.equations[0].number == "1"
    eq = next(p for p in doc.passages if p.kind == "formula")
    assert "R(v1" in eq.text and "sigma(G)" in eq.text  # 分式分子分母归并到同一个公式


REAL = Path(r"C:\Users\华为\Desktop\Recovery")
REAL_ARTICLE = REAL / "Fan 等 - 2020 - Finding key players in complex networks through de .pdf"
REAL_SUPP = REAL / "Fan 等 - 2020 - Finding key players in complex networks through de.pdf"


@pytest.mark.skipif(not REAL_ARTICLE.exists(), reason="本机没有用户的真实论文")
def test_real_nature_article_structure():
    d = parse_pdf(REAL_ARTICLE)
    titles = _titles(d)
    for expected in ("Abstract", "Model", "Framework", "Encoding", "Decoding", "Offline training", "Online application",
                     "Code availability", "References"):
        assert expected in titles, titles
    assert d.metadata.title.startswith("Finding key players")
    assert d.metadata.doi == "10.1038/s42256-020-0177-2" and d.metadata.year == 2020
    assert d.metadata.abstract and d.metadata.abstract.startswith("Finding an optimal set of nodes")
    p1 = [p.text for p in d.passages if p.page == 1]
    assert any(t.startswith("The network, or graph in discrete mathematics") for t in p1)  # 首字下沉
    all_text = " ".join(p.text for p in d.passages)
    assert "NaTuRE MacHInE" not in all_text and "ARTICLES" not in all_text
    assert sum(1 for p in d.passages if p.kind == "reference") > 30
    assert len([f for f in d.figures if f.label and f.label.startswith("Fig")]) >= 4


@pytest.mark.skipif(not REAL_SUPP.exists(), reason="本机没有用户的真实补充材料")
def test_real_supplement_parses_quickly_with_nested_outline():
    import time

    t0 = time.time()
    d = parse_pdf(REAL_SUPP)
    assert time.time() - t0 < 30 and d.page_count == 92 and len(d.passages) > 400
    lvl = {s.title: s.level for s in d.sections}
    assert lvl["D. FINDER"] < lvl["1. Model architecture"]  # 字母小节在数字小节之上
