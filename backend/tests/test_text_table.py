from paperdesk.vision import layout_tables as L


def _line(y, cells, h=9.0):
    """cells: [(x0, text)]，每个词宽 6 × 字符数，词间距 3。"""
    out = []
    for x0, text in cells:
        x = x0
        for w in text.split():
            out.append((x, y, x + 5 * len(w), y + h, w))
            x += 5 * len(w) + 3
    return out


def test_text_table_with_merged_first_column_and_wrapped_last_cell():
    words = []
    words += _line(0, [(57, "Application"), (157, "Domain"), (303, "Work")])
    words += _line(13, [(57, "Problem Solving"), (157, "Software Development"), (303, "Dong et al. [32], ChatEDA [303]")])
    words += _line(26, [(157, "Industrial Engineering"), (303, "Mehta et al. [2]")])
    words += _line(39, [(57, "World Simulation"), (157, "Gaming"), (303, "Li et al. [12], MarioGPT [306]")])
    words += _line(52, [(157, "Recommender Systems"), (303, "Zhang et al. [309], TALLRec [310],")])
    words += _line(65, [(303, "RecSim [318], AgentCF [49]")])
    t = L.reconstruct_text_table(words)
    assert t and t["columns"] == ["Application", "Domain", "Work"]
    assert [r[1] for r in t["rows"]] == ["Software Development", "Industrial Engineering", "Gaming", "Recommender Systems"]
    assert [r[0] for r in t["rows"]] == ["Problem Solving", "Problem Solving", "World Simulation", "World Simulation"]
    assert t["rows"][3][2].endswith("RecSim [318], AgentCF [49]")  # 折行并回上一行最后一格
    assert any("合并单元格" in r for r in t["reasons"])


def test_ragged_text_is_not_a_table():
    words = []
    for i, x in enumerate((57, 80, 120, 71, 99)):
        words += _line(i * 13, [(x, "some ordinary words here"), (x + 150, "more text")])
    assert L.reconstruct_text_table(words) is None


def test_caption_without_punctuation_is_detected_only_for_caption_kind():
    from paperdesk.vision.table_batch import detect_tables

    def p(ord_, kind, text, y):
        return {"ord": ord_, "page": 1, "kind": kind, "text": text, "bbox": (50.0, y, 300.0, y + 9.0)}

    rows = [p(i, "paragraph", f"row {i} text", 100 + i * 12) for i in range(1, 5)]
    # Springer 式图注：编号后面没有冒号或句点
    cap = p(0, "caption", "Table 2  Representative applications of the system", 80)
    assert [t["label"] for t in detect_tables([cap, *rows])] == ["Table 2"]
    # 正文里的 “Table 2 shows ...” 不是图注
    para = p(0, "paragraph", "Table 2 shows representative applications of the system", 80)
    assert detect_tables([para, *rows]) == []


def test_figure_region_touching_its_caption_is_not_matched_to_a_farther_table(tmp_path):
    """图区域下沿和自己的图注顶边几乎重合（差零点几个点）时，仍要关联到这个图注，而不是更远的表题。"""
    import pymupdf

    from paperdesk.parsing.pdf_parser import parse_pdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=790)
    page.insert_text((57, 60), "A Paper", fontsize=20)
    for i in range(40):  # 大量矢量绘图指令，成为一个图区域
        page.draw_rect(pymupdf.Rect(80 + (i % 8) * 50, 100 + (i // 8) * 30, 128 + (i % 8) * 50, 158 + (i // 8) * 30), color=(0, 0, 0), fill=(0.8, 0.8, 0.8))
    page.insert_text((63, 285.2), "Fig. 7 Diverse application of the system", fontsize=8)
    page.insert_text((57, 330), "Table 2 Representative applications of the system", fontsize=9)
    for i, row in enumerate(["Application Domain Work", "Problem Solving Software Dong et al.", "Embodied Agents SayCan", "Science Debate Du et al."]):
        page.insert_text((57, 350 + i * 12), row, fontsize=9)
    pdf = tmp_path / "t.pdf"
    doc.save(pdf)
    parsed = parse_pdf(pdf)
    figs = [f for f in parsed.figures if f.kind == "figure"]
    assert [f.label for f in figs] == ["Fig. 7"]


def test_long_publisher_caption_with_en_space_is_a_caption():
    from paperdesk.parsing import layout

    assert layout.is_caption("Fig. 1  Overview of the general multi-agent system. " + "x " * 300)
    assert not layout.is_caption("Table 2 shows the results of our long experiment " + "y " * 200)  # 正文里的引用


def test_inset_abstract_is_one_paragraph_and_the_full_abstract(tmp_path):
    """arXiv 常见版式：摘要比正文缩进一块（左右各收进去一截），每一行都不能被当成新段落，摘要要是完整的一段。"""
    import pymupdf

    from paperdesk.parsing.pdf_parser import parse_pdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 80), "A Study of Inset Abstracts in Narrow Columns", fontsize=18)
    page.insert_text((72, 110), "Some Author, Another Author", fontsize=11)
    page.insert_text((270, 150), "Abstract", fontsize=11)
    sentences = [
        "The dominant sequence transduction models are based on complex recurrent or",
        "convolutional neural networks that include an encoder and a decoder. The best",
        "performing models also connect the encoder and decoder through an attention",
        "mechanism. We propose a new simple network architecture, the Transformer,",
        "based solely on attention mechanisms, dispensing with recurrence and convolutions",
        "entirely. Experiments on two machine translation tasks show these models to",
        "be superior in quality while being more parallelizable and requiring significantly",
        "less time to train.",
    ]
    for i, line in enumerate(sentences):  # 摘要比正文窄：左边缩进 36pt
        page.insert_text((108, 175 + i * 11), line, fontsize=10)
    page.insert_text((72, 300), "1 Introduction", fontsize=13)
    body = "Recurrent neural networks have been firmly established as state of the art approaches in sequence modeling and"
    for i in range(6):  # 正文占满整个版心：比摘要宽，页面最左边在 72
        page.insert_text((72, 325 + i * 12), body, fontsize=10)
    pdf = tmp_path / "inset.pdf"
    doc.save(pdf)
    parsed = parse_pdf(pdf)
    abstract = parsed.metadata.abstract or ""
    assert "dominant sequence transduction" in abstract and "less time to train" in abstract, abstract
    assert len(abstract) > 400
    # 摘要那几行不应被拆成好几个段落
    abs_paras = [p for p in parsed.passages if p.kind == "paragraph" and p.page == 1 and ("transduction" in p.text or "attention" in p.text and "Transformer" in p.text)]
    assert len(abs_paras) <= 2


def test_centered_header_falls_back_to_body_column_starts():
    words = _line(0, [(150, "Task Type"), (380, "Constituent Tasks")])
    for i in range(1, 7):
        words += _line(i * 13, [(60, f"group{i}"), (300, f"task_{i}_a, task_{i}_b")])
    t = L.reconstruct_text_table(words)
    assert t and t["n_cols"] == 2 and t["n_rows"] == 6
    assert t["rows"][0] == ["group1", "task_1_a, task_1_b"]


def test_continuation_page_rows_with_empty_first_column_are_kept():
    words = _line(0, [(300, "task_x_a, task_x_b, task_x_c")]) + _line(12, [(300, "task_x_d")]) + _line(36, [(300, "task_y_a, task_y_b")]) + _line(60, [(300, "task_z_a, task_z_b")])
    t = L.reconstruct_text_table(words, anchors=[60.0, 300.0])
    assert t and t["n_rows"] == 3 and t["rows"][0][0] == "" and t["rows"][0][1].endswith("task_x_d")


def test_multi_page_table_is_merged_into_one(monkeypatch):
    from paperdesk.vision import table_batch as TB

    head = _line(0, [(150, "Task Type"), (380, "Constituent Tasks")])
    p1 = head + sum((_line(i * 13, [(60, f"group{i}"), (300, f"item_{i}_a, item_{i}_b")]) for i in range(1, 6)), [])
    p2 = _line(0, [(300, "item_6_a, item_6_b, item_6_c")]) + _line(12, [(300, "item_6_d")]) + _line(36, [(300, "item_7_a, item_7_b")]) + _line(60, [(300, "item_8_a, item_8_b")])
    pages = {1: p1, 2: p2}
    monkeypatch.setattr(TB, "fetch_words", lambda app, pid, page, bbox, pad=0.0: pages[page])
    t = {"page": 2, "bbox": (50, 0, 500, 40), "caption_bbox": None, "parts": [{"page": 1, "bbox": (50, 0, 500, 70)}]}
    got = TB.layout_caption_table(None, "p", t)
    assert got and len(got["rows"]) == 8
    assert got["rows"][5][0] == "" and got["rows"][5][1].startswith("item_6_a")  # 第一页没有合并单元格：续页的空首格不乱填
    assert any("跨 2 页" in w for w in got["warnings"])


def test_right_column_of_a_full_width_text_table_joins_the_table_body():
    from paperdesk.vision import table_batch as TB

    def psg(ord_, kind, bbox, text, page=1):
        return {"ord": ord_, "page": page, "kind": kind, "text": text, "bbox": bbox}

    ps = [
        psg(0, "caption", (50, 100, 300, 112), "Table 2 Representative work by application"),
        psg(1, "paragraph", (50, 120, 200, 130), "Application"),
        psg(2, "paragraph", (50, 134, 200, 200), "Problem Solving Software Development Industrial Engineering"),
        psg(3, "paragraph", (50, 204, 200, 260), "World Simulation Gaming Recommender Systems"),
        psg(4, "heading", (50, 280, 300, 292), "5 Discussion"),  # 双栏版面里，右栏排在这个标题之后：序号上和表体不相邻
        psg(5, "paragraph", (300, 120, 540, 260), "Work Dong et al. [32], ChatEDA [303] Mehta et al. [2]"),  # 右栏：表的另一列
        psg(6, "paragraph", (300, 300, 540, 400), "Body text in the right column far below the table."),
    ]
    t = next(x for x in TB.detect_tables(ps) if x["label"] == "Table 2")
    assert 5 in t["body_ords"] and 6 not in t["body_ords"] and 4 not in t["body_ords"]
    assert t["bbox"][2] >= 540


def test_reference_fragment_is_not_an_appendix_heading():
    from paperdesk.parsing.pdf_parser import _appendix_head

    assert _appendix_head("A. MassiveText") and _appendix_head("B. Model Card")
    assert not _appendix_head("Y. Bengio. Maxout networks. In ICML, 2013. 5")
