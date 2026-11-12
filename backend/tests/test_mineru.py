"""MinerU 适配器与回退：用合成的 middle_json，不连真实服务。"""
from __future__ import annotations

from pathlib import Path

import pytest

import pdfgen  # noqa: F401  (保证 tests 路径已加入)
from paperdesk.parsing import mineru_adapter, mineru_client, parse_best
from paperdesk.parsing.pdf_parser import PdfError

SIZES = [(600.0, 800.0), (600.0, 800.0)]


def blk(t, text, bbox=(0.1, 0.1, 0.9, 0.2), **kw):
    return {"type": t, "index": 0, "bbox": list(bbox), "content": [{"type": "text", "content": text}], **kw}


def mid(pages):
    return {"pages": [{"page_idx": i, "blocks": b} for i, b in enumerate(pages)], "metadata": {"document": {"authors": ["A B"]}}}


def sample():
    p1 = [
        {**blk("doc_title", "A Study of Things"), "level": 1},
        blk("text", "Alice Smith, Bob Jones"),
        {**blk("paragraph_title", "1 Introduction"), "level": 2},
        {"type": "text", "bbox": [0.1, 0.3, 0.9, 0.4], "content": [
            {"type": "text", "content": "We study "}, {"type": "equation_inline", "content": "x_1"},
            {"type": "text", "content": " in depth."}]},
        {"type": "equation", "bbox": [0.1, 0.5, 0.9, 0.55], "content": "E = mc^2 \\tag{1}"},
        {"type": "image", "bbox": [0.1, 0.6, 0.9, 0.8], "content": [
            {"type": "image_body", "bbox": [0.1, 0.6, 0.9, 0.75], "content": ""},
            {"type": "image_caption", "bbox": [0.1, 0.76, 0.9, 0.8], "content": [{"type": "text", "content": "Fig. 1. A figure."}]}]},
        blk("page_number", "1"),
    ]
    p2 = [
        {**blk("paragraph_title", "1.1 Details"), "level": 2},
        blk("text", "More body text here about the details of the method."),
        {**blk("paragraph_title", "References"), "level": 2},
        blk("ref_text", "[1] X. Author, A title, 2020."),
        blk("ref_text", "[2] Y. Author, Another title, 2021."),
    ]
    return mid([p1, p2])


def test_adapter_structure():
    d = mineru_adapter.build(sample(), SIZES, {})
    assert d.metadata.title == "A Study of Things"
    assert [(s.level, s.title) for s in d.sections] == [(1, "首部信息"), (1, "1 Introduction"), (2, "1.1 Details"), (1, "References")]
    assert [p.ord for p in d.passages] == list(range(len(d.passages)))
    kinds = [p.kind for p in d.passages]
    assert kinds.count("reference") == 2 and kinds.count("formula") == 1 and kinds.count("caption") == 1
    body = next(p for p in d.passages if p.text.startswith("We study"))
    assert body.math == [{"src": "x_1", "tex": "x_1"}]
    eq = d.equations[0]
    assert eq.number == "1" and eq.latex == "E = mc^2" and d.passages[eq.passage_ord].kind == "formula"
    fig = d.figures[0]
    assert fig.kind == "figure" and fig.label == "Fig. 1" and d.passages[fig.caption_ord].kind == "caption"
    assert fig.bbox == (60.0, 480.0, 540.0, 600.0)  # 归一化坐标 × 页面尺寸
    assert all(p.section_ord is None or 0 <= p.section_ord < len(d.sections) for p in d.passages)


def test_adapter_rejects_empty():
    with pytest.raises(PdfError):
        mineru_adapter.build({"pages": []}, SIZES, {})


def test_table_gets_label_and_inner_text():
    t = {"type": "table", "bbox": [0.1, 0.1, 0.9, 0.4], "content": [
        {"type": "table_caption", "bbox": [0.1, 0.05, 0.9, 0.09], "content": [{"type": "text", "content": "Table 2. Results"}]},
        {"type": "table_body", "bbox": [0.1, 0.1, 0.9, 0.4], "content": "<table><tr><td>a</td><td>b</td></tr></table>"}]}
    m = mid([[{**blk("paragraph_title", "1 Intro"), "level": 2}, blk("text", "body " * 30), t]])
    d = mineru_adapter.build(m, SIZES, {})
    f = next(f for f in d.figures if f.kind == "table")
    assert f.label == "Table 2" and f.inner_ord is not None and "a | b" in d.passages[f.inner_ord].text


def test_fallback_when_unavailable(tmp_path):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.4")
    assert mineru_client.available() is False   # conftest 默认关闭
    assert parse_best.parse_with_mineru(p) is None


def test_failure_falls_back_to_none(tmp_path, monkeypatch):
    monkeypatch.setattr(mineru_client, "ENABLED", True)
    def boom(_p):
        raise mineru_client.MineruUnavailable("down")
    mineru_client.set_engine(boom)
    monkeypatch.setattr(parse_best, "_probe", lambda p: (SIZES, {}, 5000))
    assert parse_best.parse_with_mineru(Path("x.pdf")) is None


def test_unlabelled_intro_and_abstract_not_front():
    """Nature 系：摘要、引言前都没有标题；引言不能被当成首部信息，子图拆成多块要并回一张图。"""
    long = "word " * 70
    p1 = [
        {**blk("doc_title", "T"), "level": 1},
        blk("text", "Alice Smith1 and Bob Jones2", (0.1, 0.2, 0.9, 0.22)),
        blk("text", long, (0.1, 0.3, 0.9, 0.4)),
        blk("text", long, (0.1, 0.4, 0.9, 0.5)),
        blk("text", "1College of Engineering, Some University, City, Country", (0.1, 0.9, 0.9, 0.95)),
        {"type": "image", "bbox": [0.1, 0.5, 0.4, 0.6], "content": [
            {"type": "image_body", "bbox": [0.1, 0.5, 0.4, 0.6], "content": ""},
            {"type": "image_caption", "bbox": [0.1, 0.6, 0.2, 0.61], "content": [{"type": "text", "content": "b"}]}]},
        {"type": "image", "bbox": [0.5, 0.5, 0.9, 0.7], "content": [
            {"type": "image_body", "bbox": [0.5, 0.5, 0.9, 0.7], "content": ""},
            {"type": "image_caption", "bbox": [0.1, 0.72, 0.9, 0.75], "content": [{"type": "text", "content": "Fig. 1 | Whole figure."}]}]},
    ]
    p2 = [{**blk("paragraph_title", "Results"), "level": 2}, blk("text", long)]
    d = mineru_adapter.build(mid([p1, p2]), SIZES, {})
    assert [s.title for s in d.sections] == ["首部信息", "Abstract", "正文", "Results"]
    kinds = {p.text[:8]: p.kind for p in d.passages}
    assert kinds["1College"] == "front" and kinds["Alice Sm"] == "front"
    assert len([p for p in d.passages if p.kind == "paragraph"]) == 3
    assert len(d.figures) == 1 and d.figures[0].label == "Fig. 1"
    assert d.figures[0].bbox[0] == 60.0  # 子图并入后取并集
    assert d.metadata.abstract and d.metadata.abstract.startswith("word")
