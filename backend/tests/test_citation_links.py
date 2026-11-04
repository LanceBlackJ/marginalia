"""用 PDF 自带的内部超链接识别正文引用：链接目的地落在哪条参考文献上，就引用哪条；对不上标记文字的链接不要；没有链接的引用仍由正则补充。"""
from __future__ import annotations

import pymupdf

from paperdesk.references.citations import detect_all
from paperdesk.references.links import link_markers, merge_markers


def _build(tmp_path, wrong_link: bool = False):
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    p1, p2 = doc[0], doc[1]
    body = "Residual nets [1] work well, as do transformers [2] and old baselines [3] too."
    p1.insert_text((72, 100), body, fontsize=10)
    refs = [
        "[1] K. He, X. Zhang. Deep residual learning. CVPR (2016).",
        "[2] A. Vaswani, N. Shazeer. Attention is all you need. NeurIPS (2017).",
        "[3] S. Hochreiter. Long short-term memory. Neural Computation (1997).",
    ]
    ys = [100, 140, 180]
    for t, y in zip(refs, ys):
        p2.insert_text((72, y), t, fontsize=10)
    rects = {label: p1.search_for(label)[0] for label in ("[1]", "[2]")}  # 先把框都找出来，再加链接
    for label, y in (("[1]", ys[0]), ("[2]", ys[1])):
        rect = rects[label]
        target = ys[2] if (wrong_link and label == "[2]") else y
        # 目的地锚点放在条目首行上方 10pt（LaTeX 的做法），不在条目文字框里
        p1.insert_link({"kind": pymupdf.LINK_GOTO, "from": rect, "page": 1, "to": pymupdf.Point(72, target - 20)})
    path = tmp_path / "cites.pdf"
    doc.save(path)
    doc.close()
    passages = [{"ord": 0, "page": 1, "kind": "paragraph", "text": body, "bbox": [72, 90, 520, 104]}]
    entries = []
    for i, (t, y) in enumerate(zip(refs, ys), start=1):
        passages.append({"ord": i, "page": 2, "kind": "reference", "text": t, "bbox": [72, y - 9, 520, y + 3]})
        entries.append({"index": i, "ords": [i], "authors": [], "year": None})
    return path, passages, entries


def test_links_map_each_citation_to_the_entry_its_destination_points_at(tmp_path):
    path, passages, entries = _build(tmp_path)
    out = link_markers(path, passages, entries, "numeric")
    marks = out[0]["markers"]
    assert [(m["text"], m["refs"]) for m in marks] == [("[1]", [1]), ("[2]", [2])]  # 锚点在条目上方：取它下方最近的一条
    assert all(m["pre"] for m in marks)  # 带前文，前端靠它定位


def test_links_that_disagree_with_the_visible_marker_text_are_dropped(tmp_path):
    path, passages, entries = _build(tmp_path, wrong_link=True)
    out = link_markers(path, passages, entries, "numeric")
    assert [(m["text"], m["refs"]) for m in out[0]["markers"]] == [("[1]", [1])]  # “[2]”的链接指向了第 3 条：和文字对不上，不采用


def test_regex_fills_in_citations_that_have_no_link(tmp_path):
    path, passages, entries = _build(tmp_path)
    regex = detect_all(passages, entries, "numeric")
    merged = merge_markers(regex, link_markers(path, passages, entries, "numeric"))
    marks = merged[0]["markers"]
    assert [(m["text"], m["refs"]) for m in marks] == [("[1]", [1]), ("[2]", [2]), ("[3]", [3])]  # [3] 没有链接，由正则补上
    assert [m["kind"] for m in marks][:2] == ["link", "link"]


def test_no_links_or_no_pdf_is_harmless(tmp_path):
    assert link_markers(tmp_path / "missing.pdf", [], [{"index": 1, "ords": [1]}], "numeric") == []
    assert merge_markers([{"ord": 1, "markers": [{"kind": "bracket", "text": "[1]", "start": 0, "end": 3, "refs": [1]}]}], []) == [
        {"ord": 1, "markers": [{"kind": "bracket", "text": "[1]", "start": 0, "end": 3, "refs": [1]}]}]


def _mk(kind, text, full, refs, pre=""):
    s = full.index(text)
    return {"kind": kind, "text": text, "start": s, "end": s + len(text), "refs": refs, "pre": pre}


def test_author_year_links_covering_only_the_year_are_widened_to_the_full_citation():
    """APA：PDF 常只把年份做成链接。悬停范围要是完整的“Devlin et al. (2019)”，不能只有“2019”。"""
    t = "BERT (Devlin et al., 2019) and Smith (2020a) and (Brown & Lee; 2018) all agree."
    links = [{"ord": 0, "markers": [_mk("link", "2019", t, [2]), _mk("link", "2020a", t, [8]), _mk("link", "2018", t, [5])]}]
    out = merge_markers([], links, {0: t})[0]["markers"]
    assert [m["text"] for m in out] == ["Devlin et al., 2019", "Smith (2020a)", "Brown & Lee; 2018"]  # 括号边界由年份链接决定，向左补到作者名


def test_regex_and_link_for_the_same_reference_merge_into_the_longer_span():
    t = "As in Devlin et al. (2019) the model works."
    regex = [{"ord": 0, "markers": [_mk("authoryear", "Devlin et al. (2019)", t, [2])]}]
    links = [{"ord": 0, "markers": [_mk("link", "2019", t, [2])]}]
    out = merge_markers(regex, links, {0: t})[0]["markers"]
    assert [(m["text"], m["refs"]) for m in out] == [("Devlin et al. (2019)", [2])]
    # 重叠但引用的不是同一条：以链接为准，正则的丢掉
    regex2 = [{"ord": 0, "markers": [_mk("authoryear", "Devlin et al. (2019)", t, [9])]}]
    out2 = merge_markers(regex2, links, {0: t})[0]["markers"]
    assert [m["refs"] for m in out2] == [[2]]
