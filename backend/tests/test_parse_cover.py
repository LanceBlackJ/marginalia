"""知网式封面页（“引用本文 / 您可能感兴趣的其他文章”）：标题、作者、摘要从第 2 页取，封面里的刊名和 DOI 仍进题录。"""
import fitz

from paperdesk.parsing.pdf_parser import parse_pdf


def _make(path):
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 80), "A Cover Page Title Not The Real One", fontsize=12)
    p1.insert_text((72, 120), "引用本文: 张三. 示例论文[J]. 示例学报, 2025, 20(3): 530-547.", fontname="china-s", fontsize=10)
    p1.insert_text((72, 150), "您可能感兴趣的其他文章", fontname="china-s", fontsize=10)
    p1.insert_text((72, 180), "https://dx.doi.org/10.11992/tis.202405003", fontsize=10)
    p2 = doc.new_page()
    p2.insert_text((72, 90), "A Study of Cover Pages in Journal PDFs", fontsize=20)
    p2.insert_text((72, 130), "Zhang San, Li Si", fontsize=12)
    body = "This paper studies how journal exports put a cover page before the first page of the article. " * 4
    y = 180
    for i in range(6):
        p2.insert_text((72, y), body[:95], fontsize=10)
        y += 14
    doc.save(path)


def test_cover_page_is_not_treated_as_first_page(tmp_path):
    f = tmp_path / "x.pdf"
    _make(f)
    d = parse_pdf(f)
    assert d.metadata.title == "A Study of Cover Pages in Journal PDFs"
    assert d.metadata.doi == "10.11992/tis.202405003"
    assert not any(p.page == 1 for p in d.passages)  # 封面文字不进正文


def test_filename_titles_are_replaced_on_reparse_but_real_titles_are_not():
    from paperdesk.library.reparse import _looks_like_filename

    assert _looks_like_filename("paper") and _looks_like_filename("2401.12345v2") and _looks_like_filename("")
    assert not _looks_like_filename("Attention Is All You Need")
    assert not _looks_like_filename("深度学习研究综述")
    assert not _looks_like_filename("BERT")
