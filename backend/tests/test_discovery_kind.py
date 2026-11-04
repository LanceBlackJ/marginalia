import pytest

from paperdesk.discovery.kind import classify


@pytest.mark.parametrize("title,abstract,wtype,kind", [
    ("A survey on LLM-based multi-agent systems", None, "article", "review"),
    ("Deep learning for medical imaging: a systematic review and meta-analysis", None, None, "review"),
    ("图神经网络研究进展", None, None, "review"),
    ("Some method paper", None, "review", "review"),
    ("The Pile: An 800GB Dataset of Diverse Text for Language Modeling", None, None, "dataset"),
    ("A new benchmark for code generation", None, None, "dataset"),
    ("Position paper: agents need better memory", None, None, "perspective"),
    ("Editorial: the year ahead", None, "editorial", "perspective"),
    ("A gentle introduction to graph neural networks", None, None, "tutorial"),
    ("Doctoral work on routing", None, "dissertation", "thesis"),
    ("Handbook chapter on optimization", None, "book-chapter", "book"),
    ("Graph attention networks", "We present graph attention networks, novel architectures.", "article", "research"),
    ("A study of X", "In this paper we review the literature on X and its applications.", None, "review"),
    ("Y", "We introduce a new large-scale dataset of 10k images.", None, "dataset"),
])
def test_kinds(title, abstract, wtype, kind):
    got = classify(title, abstract, wtype)
    assert got["kind"] == kind and got["evidence"]


def test_unknown_is_research_with_honest_evidence():
    got = classify("Attention is all you need", None)
    assert got["kind"] == "research" and "没有看到其它类型的信号" in got["evidence"]


def test_work_dict_carries_kind():
    from paperdesk.discovery.models import Work

    d = Work(title="A survey of X", work_type="article").to_dict()
    assert d["kind"] == "review" and d["kind_label"] == "综述" and "survey" in d["kind_evidence"]


def test_venue_summary_uses_only_the_numbers_and_hedges():
    from paperdesk.discovery.venue_info import describe_stats

    txt = describe_stats("Nature Communications", {"name": "Nature Communications", "type": "journal", "publisher": "Springer Nature", "works_count": 54000,
                                                    "cited_by_count": 2500000, "h_index": 480})
    assert "Springer Nature" in txt and "54,000" in txt and "h 指数 480" in txt and "影响力很高" in txt and "不等于分区或影响因子" in txt
    small = describe_stats("X", {"type": "conference", "h_index": 12})
    assert "规模或影响力较小" in small and "会议" in small
    assert "h 指数" not in describe_stats("Y", {"type": "journal"})


def test_core_venues_are_labelled_as_important_not_top_and_stay_out_of_random():
    from paperdesk.discovery import venues as V
    from paperdesk.discovery.venue_info import curated

    nc = curated("Nature Communications")
    assert nc["label"] == "已确认重要期刊" and nc["level"] == "core" and nc["intro"]
    assert curated("Nature")["label"] == "已确认顶刊"
    assert curated("ICRA")["label"] == "已确认重要会议"
    assert all(v.level == "top" for v in V.venues_for_field(None)) and len(V.VENUES) > len(V.TOP_VENUES) >= 80
    # 别名没有跨来源冲突
    seen: dict[str, str] = {}
    for v in V.VENUES:
        for a in {V.norm_venue(x) for x in v.aliases}:
            assert seen.setdefault(a, v.id) == v.id, a
    assert all(v.intro for v in V.CORE_VENUES)
