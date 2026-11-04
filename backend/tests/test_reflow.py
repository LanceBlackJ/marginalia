from types import SimpleNamespace as NS

from paperdesk.parsing.reflow import caption_key, mentioned, reflow


def P(ord_, kind, text, page=1, box=(0, 0, 10, 10), sec=0):
    return NS(ord=ord_, kind=kind, text=text, page=page, bbox=box, section_ord=sec)


def test_caption_and_mention_parsing():
    assert caption_key("Figure 1: Overview of the model") == ("fig", "1")
    assert caption_key("Table 2. Results") == ("table", "2")
    assert caption_key("图 3 实验结果") == ("fig", "3")
    assert mentioned("As shown in Figures 1 and 3, and Table 2(a), 见图4。") == {("fig", "1"), ("fig", "3"), ("table", "2"), ("fig", "4")}
    assert caption_key("Some ordinary sentence.") is None


def test_figure_does_not_cut_a_sentence_and_follows_its_first_mention():
    ps = [
        P(0, "paragraph", "We build on equivalent methods that use", page=1),  # 被图切成两半的句子（前半）
        P(1, "caption", "Figure 1: Different schemes", page=1, box=(0, 100, 50, 110)),
        P(2, "figure_text", "scheme labels", page=1, box=(0, 60, 50, 90)),
        P(3, "paragraph", "pyramids of images (Figure 1, a) or filters.", page=2),  # 后半，里面第一次提到图 1
        P(4, "paragraph", "A later paragraph without any figure.", page=2),
        P(5, "caption", "Table 1: Results", page=2, box=(0, 300, 50, 310)),
        P(6, "paragraph", "As Table 1 shows, we win.", page=3),
        P(7, "paragraph", "Closing remarks.", page=3),
    ]
    figs = [NS(caption_ord=1, inner_ord=2, page=1, bbox=(0, 50, 50, 95), label="Figure 1", kind="figure"),
            NS(caption_ord=5, inner_ord=None, page=2, bbox=(0, 200, 50, 295), label="Table 1", kind="table")]
    eqs = [NS(passage_ord=7)]
    assert reflow(ps, figs, eqs) is True
    texts = [(p.kind, p.text[:12]) for p in ps]
    # 句子的两半相邻；图 1（图注+图内文字）紧跟在提到它的后半段后面；表 1 跟在提到它的段落后面
    assert [p.kind for p in ps] == ["paragraph", "paragraph", "caption", "figure_text", "paragraph", "paragraph", "caption", "paragraph"], texts
    assert ps[0].text.endswith("that use") and ps[1].text.startswith("pyramids")
    assert [p.ord for p in ps] == list(range(8))  # ord 就是新的阅读顺序
    assert ps[figs[0].caption_ord].text.startswith("Figure 1") and ps[figs[0].inner_ord].kind == "figure_text"
    assert ps[figs[1].caption_ord].text.startswith("Table 1")
    assert ps[eqs[0].passage_ord].text == "Closing remarks."  # 引用 ord 的实体同步更新


def test_unmentioned_figure_stays_where_it_was_and_noop_when_already_in_place():
    ps = [P(0, "paragraph", "Intro text."), P(1, "caption", "Figure 9: Unreferenced"), P(2, "paragraph", "Next text.")]
    assert reflow(ps, [], []) is False
    assert [p.kind for p in ps] == ["paragraph", "caption", "paragraph"]
