from __future__ import annotations

from types import SimpleNamespace as S

from paperdesk.parsing import section_templates as st


def _secs(titles):
    return [S(title=t, level=1) for t in titles]


def test_nature_style_results_and_methods_get_subsections():
    secs = _secs(["首部信息", "Abstract", "正文", "Results", "Method overview", "Towards network-level resilience", "Use case I",
                  "Discussion", "Methods", "Overview", "Search with DRL", "Data availability", "Code availability", "References",
                  "Acknowledgements", "Author contributions", "Reprints and permissions information", "© The Author(s) 2026"])
    st.apply(secs)
    lv = {s.title: s.level for s in secs}
    assert [lv[t] for t in ("Method overview", "Towards network-level resilience", "Use case I")] == [2, 2, 2]
    assert [lv[t] for t in ("Results", "Discussion", "Methods", "Data availability", "Code availability", "References", "Acknowledgements")] == [1] * 7
    assert lv["Overview"] == 2 and lv["Search with DRL"] == 2  # Methods 下面的小节
    assert lv["© The Author(s) 2026"] == 1 and lv["Reprints and permissions information"] == 1  # 版权行不当 Methods 的小节


def test_numbered_layouts_are_left_alone():
    secs = [S(title="1 Introduction", level=1), S(title="2 Methods", level=1), S(title="2.1 Setup", level=2), S(title="3 Results", level=1)]
    st.apply(secs)
    assert [s.level for s in secs] == [1, 1, 2, 1]


def test_unknown_headings_outside_containers_keep_their_level():
    secs = _secs(["Introduction", "Our Contributions", "Results", "Experiment A", "Discussion", "Methods"])
    st.apply(secs)
    assert [s.level for s in secs] == [1, 1, 1, 2, 1, 1]


def test_rotated_table_with_a_vertical_caption_is_found_by_position():
    from paperdesk.vision import table_batch as tb

    def p(ord_, kind, text, bbox):
        return {"ord": ord_, "page": 4, "kind": kind, "text": text, "bbox": bbox}

    # 整页旋转 90°：表题是左边一条窄长的竖条，表体（一行一段）排在它右边；序号上它们之间隔着别的段落
    passages = [
        p(0, "paragraph", "Intro text paragraph.", (300, 90, 540, 180)),
        p(1, "caption", "Table 1 | Evaluation of the discovered formula", (41, 148, 54, 741)),
        p(2, "paragraph", "Another unrelated paragraph in the right column of the page.", (300, 200, 540, 300)),
        p(3, "figure_text", "Dynamic | Network type | N | DC | RC", (60, 60, 75, 740)),
        p(4, "figure_text", "Cellular | Human | 355 | 9 | 9", (80, 60, 95, 740)),
        p(5, "figure_text", "Cellular | Yeast | 491 | 17 | 16", (100, 60, 115, 740)),
    ]
    got = tb._geometric_body(passages, passages[1])  # 按位置找（序号相邻的办法这里会把右栏的无关段落也算进去）
    assert sorted(g["ord"] for g in got) == [3, 4, 5]
    assert [t["label"] for t in tb.detect_tables([x for x in passages if x["ord"] != 2])] == ["Table 1"]


def test_mineru_table_html_becomes_a_grid_with_merged_headers():
    from paperdesk.parsing import mineru_adapter as ma

    h = ('<table><tr><td rowspan="2">Method</td><td colspan="2">Bayesian</td><td>Maayan</td></tr>'
         '<tr><td>(100)</td><td>(200)</td><td>(829)</td></tr>'
         '<tr><td>DC3</td><td>0.4849</td><td>0.4857</td><td>0.2438</td></tr>'
         '<tr><td>Ours</td><td>0.3816</td><td>0.3773</td><td>0.1993</td></tr>'
         '<tr><td colspan="4">' + "Lower is better. " * 6 + '</td></tr></table>')
    rows, foot = ma._split_footnote(ma._html_rows(h))
    g = ma.grid_data(rows, foot)
    assert g["columns"] == ["Method", "Bayesian · (100)", "Bayesian · (200)", "Maayan · (829)"]
    assert g["rows"] == [["DC3", "0.4849", "0.4857", "0.2438"], ["Ours", "0.3816", "0.3773", "0.1993"]]
    assert g["notes"].startswith("Lower is better")
    assert ma.grid_data([["a"]], "") is None


def test_mineru_table_cell_line_breaks_are_preserved():
    from paperdesk.parsing import mineru_adapter as ma

    h = ('<table><tr><td>Method</td><td>Food-<br>Bay-<br>Dry (128)</td><td>Food-<br>Bay-<br>Wet (128)</td></tr>'
         '<tr><td>DC3</td><td>0.48</td><td>0.38</td></tr></table>')
    rows, _ = ma._split_footnote(ma._html_rows(h))
    g = ma.grid_data(rows, "")
    assert g["columns"] == ["Method", "Food-\nBay-\nDry (128)", "Food-\nBay-\nWet (128)"]


def test_suspect_table_grids_are_flagged_for_vision_checking():
    from paperdesk.vision.table_batch import grid_suspect

    good = {"columns": ["Method", "Bayesian · (100)", "Maayan · (829)"], "rows": [["DC3", "0.48", "0.24"], ["Ours", "0.38", "0.19"]]}
    assert grid_suspect(good) == []
    # 表头只剩样本数：列名那一行丢了
    bad = {"columns": ["", "(1176)", "(4165)", "(6539)", "(12,591)"], "rows": [["DC3", "0.12", "0.06", "0.16", "0.12"]]}
    why = grid_suspect(bad)
    assert any("数字" in w for w in why)
    assert any("没有列名" in w for w in grid_suspect({"columns": ["A", "", "C"], "rows": [["1", "2", "3"]]}))
    assert any("重复" in w for w in grid_suspect({"columns": ["A", "x", "x"], "rows": [["1", "2", "3"]]}))
    assert any("断开" in w for w in grid_suspect({"columns": ["A", "Bay-", "Maayan"], "rows": [["1", "2", "3"]]}))
    assert any("空" in w for w in grid_suspect({"columns": ["A", "B", "C"], "rows": [["", "", "1"], ["", "", ""]]}))


def test_merged_cells_and_citation_superscripts_are_kept():
    from paperdesk.parsing import mineru_adapter as ma

    h = ('<table><tr><td rowspan="2">Dynamic</td><td colspan="2">Analytical method</td></tr>'
         '<tr><td>DC<sup>3</sup></td><td>GND<sup>24</sup></td></tr>'
         '<tr><td rowspan="2">Cellular</td><td>0.48</td><td>0.25</td></tr>'
         '<tr><td>0.12</td><td>0.14</td></tr></table>')
    rows, spans = ma._html_rows_spans(h)
    g = ma.grid_data(rows, "", spans)
    assert g["columns"] == ["Dynamic", "Analytical method · DC^{3}", "Analytical method · GND^{24}"]
    # 表头的合并结构：第一格跨 2 行，“Analytical method” 跨 2 列
    assert g["head"][0][0] == {"t": "Dynamic", "rs": 2, "cs": 1}
    assert g["head"][0][1] == {"t": "Analytical method", "rs": 1, "cs": 2} and g["head"][0][2] is None
    assert g["head"][1][0] is None  # 被上面那格占着
    # 正文里的合并：“Cellular” 跨 2 行
    assert g["spans"] == [[0, 0, 2, 1]]
    assert g["rows"][0][0] == "Cellular"
