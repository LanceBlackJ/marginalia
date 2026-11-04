"""图内数据网格：解析归类（数字堆砌不进正文）、文字层词坐标重建成表、图内数据检测。纯函数测试，不依赖 PDF 文件。"""
from __future__ import annotations

from paperdesk.parsing import datablocks
from paperdesk.vision import layout_tables as LT
from paperdesk.vision import table_batch

H = 6.0  # 词高


def _w(x0: float, y: float, text: str, width: float | None = None):
    width = width if width is not None else max(4.0, 3.2 * len(text))
    return (x0, y, x0 + width, y + H, text)


def _grid_words(panels: int = 3, rows: int = 4, cols: int = 4):
    """合成一个“并排几块、每块行标签 + 数值、列名在下面、标题在上面、右边缘有色标刻度”的数据区，坐标仿照真实 Fig.5。"""
    words = []
    names = ["Crime", "Digg", "Enron", "Epinions"][:rows]
    methods = ["HDA", "CI", "RatioCut", "FINDER"][:cols]
    for k in range(panels):
        x_base = 50 + k * 170
        words.append(_w(x_base + 20, 40, "CN"))
        words.append(_w(x_base + 34, 40, "–"))
        words.append(_w(x_base + 42, 40, f"node-{k}-weighted", 60))
        for r, name in enumerate(names):
            y = 60 + r * 10
            words.append(_w(x_base, y, name))
            for c in range(cols):
                words.append(_w(x_base + 40 + c * 28, y, f"{(r + 1) * 3 + c + k:.2f}", 18))
        words.append(_w(x_base + 40 + cols * 28 + 6, 70, "20", 8))  # 色标刻度：没有行标签，不该变成一列
        for c, m in enumerate(methods):
            words.append(_w(x_base + 38 + c * 28, 60 + rows * 10 + 4, m))
        words.append(_w(x_base, 60 + rows * 10 + 14, "Avg"))
        words.append(_w(x_base + 14, 60 + rows * 10 + 14, "score"))
        for c in range(cols):
            words.append(_w(x_base + 40 + c * 28, 60 + rows * 10 + 14, f"{9 + c}.50", 18))
    return words


def test_panels_are_rebuilt_with_titles_headers_and_without_ticks():
    tabs = LT.reconstruct_panels(_grid_words())
    assert len(tabs) == 3
    for k, t in enumerate(tabs):
        assert t["title"] == f"CN – node-{k}-weighted"
        assert t["columns"] == ["", "HDA", "CI", "RatioCut", "FINDER"]  # 列名在数据行下面也能找到
        assert [r[0] for r in t["rows"]] == ["Crime", "Digg", "Enron", "Epinions", "Avg score"]
        assert all(len(r) == 5 and all(r) for r in t["rows"])  # 刻度 “20” 没有变成多出来的一列
        assert t["rows"][0][1] == f"{3 + k:.2f}" and t["confidence"] >= 0.8


def test_numbers_never_merge_into_row_labels():
    words = [_w(10, 10, "Avg"), _w(24, 10, "score"), _w(46, 10, "6.36", 14), _w(70, 10, "6.62", 14), _w(94, 10, "6.68", 14),
             _w(10, 20, "Crime"), _w(46, 20, "1.10", 14), _w(70, 20, "1.20", 14), _w(94, 20, "1.30", 14),
             _w(10, 30, "Digg"), _w(46, 30, "2.10", 14), _w(70, 30, "2.20", 14), _w(94, 30, "2.30", 14)]
    (t,) = LT.reconstruct_panels(words, min_rows=3)
    assert t["rows"][0] == ["Avg score", "6.36", "6.62", "6.68"]


def test_text_heavy_or_non_tabular_regions_are_not_turned_into_tables():
    prose = [_w(10 + i * 30, 10 + (i // 5) * 10, "word") for i in range(30)]
    assert LT.reconstruct_panels(prose) == []
    assert LT.reconstruct_panels(prose[:5]) == []


def test_datablocks_classification():
    assert datablocks.is_numeric_dense("Avg score 6.36 6.62 6.68 6.04 Avg score 36.96 38.18 26.52 8.33")
    assert datablocks.is_label_soup("HDA CI RatioCut FINDER HDA CI RatioCut FINDER HDA CI RatioCut FINDER")
    assert datablocks.is_tabular_rows("Crime 11.62 12.43 13.83 13.95 11.33 13.81 10.99 Crime 30.03 31.03 30.66")
    sentence = ("In this work we study how the removal of a small set of key nodes changes the connectivity of a large network, "
                "and we show that a learned policy can find such sets much faster than hand-designed heuristics.")
    assert datablocks.looks_like_sentence(sentence) and datablocks.data_reason(sentence, True) is None
    assert datablocks.data_reason("d CN – crime – node-unweighted e CN – crime – node-degree-weighted", True) == "位于图的版面内"


def _passage(o, page, kind, text, bbox):
    return {"ord": o, "page": page, "kind": kind, "text": text, "bbox": bbox}


def _fixture():
    passages = [
        _passage(1, 6, "paragraph", "Crime 11.62 12.43 13.83 13.95 Crime 30.03 31.03 30.66 33.25", (50, 100, 500, 110)),
        _passage(2, 6, "paragraph", "HDA CI RatioCut FINDER HDA CI RatioCut FINDER HDA CI RatioCut FINDER", (80, 150, 500, 156)),
        _passage(3, 6, "paragraph", "g ND – node-unweighted h ND – node-degree-weighted", (50, 160, 500, 170)),
        _passage(4, 6, "paragraph", "Digg 8.79 8.66 9.52 9.52 Digg 43.52 43.32 41.60 40.57", (50, 120, 500, 130)),
        _passage(5, 6, "caption", "Fig. 5 | Performance of FINDER on real-world networks.", (50, 200, 500, 230)),
        _passage(6, 6, "paragraph", ("Results are shown for nine networks and three weighting scenarios. FINDER performs best in most cases, " * 3 + "See the text."), (50, 240, 500, 300)),
    ]
    figs = [{"page": 6, "bbox": (60, 60, 200, 140), "kind": "figure", "label": None, "caption_ord": None},
            {"page": 6, "bbox": (220, 60, 360, 140), "kind": "figure", "label": None, "caption_ord": None}]
    return passages, figs


def test_reclassify_moves_only_non_sentences_inside_the_figure_zone():
    passages, figs = _fixture()
    objs = [dict(p) for p in passages]
    changed = datablocks.reclassify(objs, figs, lambda o, k: o[k], lambda o, v: o.__setitem__("kind", v))
    assert set(changed) == {1, 2, 3, 4}
    assert [o["kind"] for o in objs] == ["figure_text"] * 4 + ["caption", "paragraph"]  # 图注和正文句子保持原样


def test_figure_data_items_are_detected_per_figure_with_bands():
    passages, figs = _fixture()
    for p in passages[:4]:
        p["kind"] = "figure_text"
    items = table_batch.detect_figure_data(passages, figs)
    assert len(items) == 1
    it = items[0]
    assert it["kind"] == "figure_data" and it["caption_ord"] == 5 and it["label"] == "Fig. 5" and it["page"] == 6
    assert set(it["body_ords"]) == {1, 4}  # 行标签 + 数字的数据行；图例和子图标题不是“数据行”，不算表格主体
    assert len(it["bands"]) >= 1 and it["bbox"][0] <= 50 and it["bbox"][3] >= 130


def test_curve_plot_ticks_and_legends_are_not_data_rows():
    passages = [_passage(1, 6, "figure_text", "1.00 1.00 1.00 HDA HDA HDA CI CI CI 0.80 0.80 0.80", (50, 100, 500, 110)),
                _passage(2, 6, "caption", "Fig. 5 | Curves.", (50, 200, 500, 230))]
    figs = [{"page": 6, "bbox": (60, 60, 200, 140), "kind": "figure", "label": None, "caption_ord": None}]
    assert table_batch.detect_figure_data(passages, figs) == []


def test_wide_figure_zone_extends_to_the_margin_when_a_left_panel_is_missing():
    """通栏大图最左边一列没被识别成图区域时，版面区只缩在右边；图已经很宽并且贴着右边页边，就把左边扩到页边。"""
    from paperdesk.parsing import datablocks

    get = lambda o, k: o[k]  # noqa: E731
    passages = [
        {"ord": 0, "page": 7, "kind": "caption", "text": "Fig. 6 | Cost distributions.", "bbox": (43, 317, 555, 330)},
        {"ord": 1, "page": 7, "kind": "paragraph", "text": "HDA", "bbox": (128, 71, 139, 76)},
        {"ord": 2, "page": 7, "kind": "paragraph", "text": "Body text of the page that sits below the figure and is long enough to be a sentence. " * 3, "bbox": (43, 452, 294, 696)},
        {"ord": 3, "page": 7, "kind": "paragraph", "text": "right column text " * 8, "bbox": (304, 452, 555, 696)},
    ]
    figs = [{"page": 7, "kind": "figure", "bbox": (185, 60, 293, 162)}, {"page": 7, "kind": "figure", "bbox": (321, 60, 552, 162)}]
    z = datablocks.figure_zones(passages, figs, get)[0]
    assert z["x0"] <= 43 and z["x1"] >= 555
    changed = datablocks.reclassify(passages, figs, get, lambda o, v: o.__setitem__("kind", v))
    assert changed == {1: "位于图的版面内"} and passages[2]["kind"] == "paragraph"


def test_narrow_single_column_figure_zone_is_not_extended():
    from paperdesk.parsing import datablocks

    get = lambda o, k: o[k]  # noqa: E731
    passages = [
        {"ord": 0, "page": 3, "kind": "caption", "text": "Fig. 2 | A small figure.", "bbox": (43, 300, 290, 312)},
        {"ord": 1, "page": 3, "kind": "paragraph", "text": "right column body " * 10, "bbox": (304, 100, 555, 280)},
    ]
    figs = [{"page": 3, "kind": "figure", "bbox": (60, 80, 280, 290)}]
    z = datablocks.figure_zones(passages, figs, get)[0]
    assert z["x1"] < 300  # 单栏的图不会把右栏的正文也圈进来


def test_panel_letters_just_above_a_figure_become_figure_text_but_real_text_does_not():
    from paperdesk.parsing import datablocks

    get = lambda o, k: o[k]  # noqa: E731
    passages = [
        {"ord": 0, "page": 3, "kind": "paragraph", "text": "b a", "bbox": (60, 60, 80, 70)},  # 子图字母，紧贴图的上方
        {"ord": 1, "page": 3, "kind": "paragraph", "text": "A", "bbox": (60, 20, 70, 30)},  # 离得太远
        {"ord": 2, "page": 3, "kind": "paragraph", "text": "the end.", "bbox": (60, 62, 100, 72)},  # 有单词的文字
        {"ord": 3, "page": 3, "kind": "caption", "text": "Fig. 2 | A figure.", "bbox": (43, 300, 555, 312)},
    ]
    figs = [{"page": 3, "kind": "figure", "bbox": (60, 80, 550, 290)}]
    changed = datablocks.reclassify(passages, figs, get, lambda o, v: o.__setitem__("kind", v))
    assert changed == {0: "子图字母"}
    assert [p["kind"] for p in passages] == ["figure_text", "paragraph", "paragraph", "caption"]
