from __future__ import annotations

import pytest

from paperdesk.parsing.pdf_parser import PdfError, parse_pdf


def test_parses_structure_and_reading_order(sample_pdf):
    doc = parse_pdf(sample_pdf)
    assert doc.page_count == 5
    assert doc.has_text_layer
    titles = [s.title for s in doc.sections]
    for expected in ("1 Introduction", "2 Method", "3 Experiments", "4 Conclusion", "References"):
        assert expected in titles, titles

    # 双栏阅读顺序：第 1 页左栏（引言）应先于右栏（方法）
    page1 = [p for p in doc.passages if p.page == 1]
    texts = [p.text for p in page1]
    i_intro = next(i for i, t in enumerate(texts) if "Cascading failures occur" in t)
    i_method = next(i for i, t in enumerate(texts) if "Dual Attention Routing (DAR), consists" in t)
    assert i_intro < i_method


def test_removes_headers_footers_and_assigns_sections(sample_pdf):
    doc = parse_pdf(sample_pdf)
    all_text = " ".join(p.text for p in doc.passages)
    assert "Journal of Cascade Recovery" not in all_text  # 重复页眉被剔除
    method = next(s for s in doc.sections if s.title == "2 Method")
    in_method = [p for p in doc.passages if p.section_ord == method.ord]
    assert any("topology attention" in p.text for p in in_method)
    # 参考文献段落被标记，检索时可降权
    refs = [p for p in doc.passages if p.kind == "reference"]
    assert any("Journal of Networks" in p.text for p in refs)
    captions = [p for p in doc.passages if p.kind == "caption"]
    assert [c.text[:8] for c in captions] == ["Figure 1"]
    # 正文里以 “Table 1 shows” 开头的句子不是图表标题
    body = next(p for p in doc.passages if p.text.startswith("Table 1 shows"))
    assert body.kind == "paragraph"


def test_is_caption_variants():
    from paperdesk.parsing.pdf_parser import is_caption

    assert is_caption("Figure 2: Overview of the model")
    assert is_caption("Table 3. Results on three datasets")
    assert is_caption("Fig. 4 Training curves of the baseline")
    assert is_caption("图 3 系统框架")
    assert not is_caption("Table 1 shows that our method wins")
    assert not is_caption("Figures of merit are defined below")


def test_metadata_extraction(sample_pdf):
    md = parse_pdf(sample_pdf).metadata
    assert md.title == "Dual Attention Routing for Robust Cascade Recovery"
    assert "Alice Zhang" in md.authors and "Carol Davies" in md.authors
    assert md.doi == "10.1234/dar.2023.001"
    assert md.year == 2023
    assert md.abstract and "Dual Attention Routing" in md.abstract


def test_bbox_inside_page(sample_pdf):
    doc = parse_pdf(sample_pdf)
    w, h = doc.pages[0]
    for p in doc.passages:
        x0, y0, x1, y1 = p.bbox
        assert 0 <= x0 < x1 <= w + 1 and 0 <= y0 < y1 <= h + 1


def test_scanned_pdf_reports_no_text_layer(scanned_pdf):
    doc = parse_pdf(scanned_pdf)
    assert not doc.has_text_layer
    assert doc.passages == []
    assert any("文字层" in w for w in doc.warnings)


def test_rotated_pages_bbox_matches_rotated_rect(rotated_pdf):
    doc = parse_pdf(rotated_pdf)
    w, h = doc.pages[0]
    assert (w, h) == (800.0, 600.0)  # 旋转后页面尺寸
    for p in doc.passages:
        assert p.bbox[2] <= w + 1 and p.bbox[3] <= h + 1


def test_corrupt_file_raises(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4\nnot really a pdf")
    with pytest.raises(PdfError):
        parse_pdf(bad)


def test_chinese_paper_metadata_and_structure(chinese_pdf):
    doc = parse_pdf(chinese_pdf)
    assert doc.language == "zh" and doc.has_text_layer
    assert doc.metadata.title == "基于双重注意力的级联失效恢复方法研究"
    assert doc.metadata.authors == ["张三", "李四", "王五"]
    assert doc.metadata.abstract and doc.metadata.abstract.startswith("级联失效是网络化基础设施")
    assert [s.title for s in doc.sections if s.title not in ("首部信息", "Abstract")] == ["1 引言", "2 方法"]
    method = next(p for p in doc.passages if "拓扑注意力模块" in p.text)
    assert doc.sections[method.section_ord].title == "2 方法"
