import pymupdf

from paperdesk.parsing.pdf_parser import parse_pdf


def _pdf(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 100
    lines = [
        [("We study a new problem in this paper and we call the proposed method ", "helv"), ("Graph Rewiring Network", "hebo"),
         (" which is simple.", "helv")],
        [("The second line of the same paragraph keeps going with ordinary words and also has ", "helv"), ("key finding", "hebo"),
         (" inside it, so the paragraph is long enough.", "helv")],
        [("A third ordinary line follows here to make the paragraph look like real body text of a paper.", "helv")],
    ]
    for parts in lines:
        x = 72
        for text, font in parts:
            page.insert_text((x, y), text, fontname=font, fontsize=10)
            x += pymupdf.get_text_length(text, fontname=font, fontsize=10)
        y += 13
    p = tmp_path / "bold.pdf"
    doc.save(p)
    return p


def test_inline_bold_runs_are_recorded(tmp_path):
    parsed = parse_pdf(_pdf(tmp_path))
    paras = [p for p in parsed.passages if p.kind == "paragraph" and "Graph Rewiring" in p.text]
    assert paras, [p.text for p in parsed.passages]
    assert paras[0].bold == ["Graph Rewiring Network", "key finding"]
    assert all(r in paras[0].text for r in paras[0].bold)


def test_text_without_bold_has_no_runs(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    for i in range(4):
        page.insert_text((72, 100 + 13 * i), "Plain ordinary body text with no emphasis at all in this line number %d." % i, fontname="helv", fontsize=10)
    p = tmp_path / "plain.pdf"
    doc.save(p)
    assert all(not q.bold for q in parse_pdf(p).passages)


def test_text_api_returns_bold_after_import(api, tmp_path):
    item = api.import_pdf(_pdf(tmp_path))
    assert item["status"] == "imported", item
    items = api.get(f"/papers/{item['paper_id']}/text").json()["items"]
    para = next(i for i in items if "Graph Rewiring" in i["text"])
    assert para["bold"] == ["Graph Rewiring Network", "key finding"]
    assert all(i["bold"] == [] for i in items if i["kind"] in ("heading", "reference", "formula"))


def test_inline_math_is_rebuilt_from_fonts_and_baselines(tmp_path):
    """斜体变量 + 下标：vi 要还原成 v_i；不认识的字符整簇放弃。"""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 100
    rows = [
        [("Here every node ", "helv", 10, 0), ("v", "heit", 10, 0), ("i", "heit", 7, 2.5), (" is removed in order and we continue.", "helv", 10, 0)],
        [("The second line of this paragraph is ordinary running text that goes on for a while.", "helv", 10, 0)],
        [("A third ordinary line follows here to make the paragraph look like real body text.", "helv", 10, 0)],
    ]
    for parts in rows:
        x = 72
        for text, font, size, dy in parts:
            page.insert_text((x, y + dy), text, fontname=font, fontsize=size)
            x += pymupdf.get_text_length(text, fontname=font, fontsize=size)
        y += 13
    p = tmp_path / "math.pdf"
    doc.save(p)
    paras = [q for q in parse_pdf(p).passages if q.kind == "paragraph" and "removed in order" in q.text]
    assert paras, "没有解析出段落"
    maths = paras[0].math
    assert {"src": "vi", "tex": "v_{i}"} in maths, maths
    assert all(m["src"] in paras[0].text for m in maths)
