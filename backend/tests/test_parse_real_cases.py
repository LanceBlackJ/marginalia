"""在真实论文里发现的解析问题（作者并排排版、标题缺词、参考文献里折行的半条）的回归测试。"""
from paperdesk.parsing import metadata as M
from paperdesk.parsing.pdf_parser import _looks_like_names


def test_authors_side_by_side_on_one_line_are_paired():
    assert M.guess_authors(["Jonathan Ho Ajay Jain Pieter Abbeel"]) == ["Jonathan Ho", "Ajay Jain", "Pieter Abbeel"]
    assert M.guess_authors(["Tomas Mikolov Kai Chen"]) == ["Tomas Mikolov", "Kai Chen"]


def test_author_row_followed_by_affiliations_on_the_same_line():
    row = "Gao Huang∗ Zhuang Liu∗ Laurens van der Maaten Cornell University Tsinghua University Facebook AI Research"
    assert M.guess_authors([row, "Kilian Q. Weinberger"]) == ["Gao Huang", "Zhuang Liu", "Laurens van der Maaten", "Kilian Q. Weinberger"]


def test_author_grid_block_takes_the_first_line_as_the_name():
    assert M.guess_authors(["Jonathan Ho\nUC Berkeley\njonathanho@berkeley.edu"]) == ["Jonathan Ho"]


def test_non_names_and_marker_symbols_are_dropped():
    assert M.split_authors("Open Access") == []
    assert M.split_authors("Ethan Perez⋆, Aleksandra Piktus") == ["Ethan Perez", "Aleksandra Piktus"]


def test_long_comma_separated_name_list_is_names_not_an_abstract():
    names = "Jack W. Rae, Sebastian Borgeaud, Trevor Cai, Katie Millican, Diego de Las Casas, Lisa Anne Hendricks"
    assert _looks_like_names(names)
    assert not _looks_like_names("Language modelling provides a step towards intelligent communication systems by harnessing, large amounts of data")


def test_layout_title_missing_words_is_replaced_by_the_complete_pdf_title():
    md = M.extract_metadata({"title": "Intraspecific host variation plays a key role in virus community assembly"},
                            "Intraspeci host variation plays key role in virus community assembly", [], "", None)
    assert md.title == "Intraspecific host variation plays a key role in virus community assembly"
    other = M.extract_metadata({"title": "Something entirely different here"}, "Intraspeci host variation plays key role", [], "", None)
    assert other.title == "Intraspeci host variation plays key role"
