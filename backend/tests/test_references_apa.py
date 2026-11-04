"""APA 风格（作者-年份）的参考文献：个人作者、姓氏前缀、机构作者、省略号作者表、一条拆成两行，以及正文里的“(Smith et al., 2020)”映射。"""
from __future__ import annotations

from paperdesk.references.citations import detect_all
from paperdesk.references.parse import split_entries

RAW = [
    "Brown, T. B., Mann, B., Ryder, N., Subbiah, M., Kaplan, J., Dhariwal, P., & Amodei, D. (2020). Language models are few-shot learners. Advances in Neural Information Processing Systems, 33, 1877–1901.",
    "Devlin, J., Chang, M.-W., Lee, K., & Toutanova, K. (2019). BERT: Pre-training of deep bidirectional transformers for language understanding. Proceedings of NAACL-HLT, 4171–4186. https://doi.org/10.18653/v1/N19-1423",
    "van der Maaten, L., & Hinton, G. (2008). Visualizing data using t-SNE. Journal of Machine Learning Research, 9, 2579–2605.",
    "OpenAI. (2023). GPT-4 technical report. arXiv preprint arXiv:2303.08774.",
    "World Health Organization. (2020). Coronavirus disease (COVID-19) situation report.",
    "He, K., Zhang, X., Ren, S., & Sun, J. (2016). Deep residual learning for image recognition. In Proceedings of the IEEE Conference on Computer Vision and Pattern Recognition (pp. 770–778).",
    "Wang, L., Ma, C., Feng, X., Zhang, Z., Yang, H., Zhang, J., ... Wen, J. (2024). A survey on large language model based autonomous agents. Frontiers of Computer Science, 18(6), 186345.",
    "Smith, J. (2020a). Something about agents. Journal of AI, 1(1), 1–2.",
]


def _passages():
    out, o = [], 0
    for r in RAW:
        if len(r) > 120:  # 长条目在 PDF 里被拆成两行
            a = r[:110].rsplit(" ", 1)[0]
            out += [{"ord": o, "page": 9, "text": a, "bbox": [57, 100, 290, 110], "kind": "reference"},
                    {"ord": o + 1, "page": 9, "text": r[len(a):].strip(), "bbox": [70, 110, 290, 120], "kind": "reference"}]
            o += 2
        else:
            out.append({"ord": o, "page": 9, "text": r, "bbox": [57, 100, 290, 110], "kind": "reference"})
            o += 1
    return out


def test_apa_entries_are_all_recognized_with_correct_years():
    style, en = split_entries(_passages())
    assert style == "author-year" and len(en) == len(RAW)
    assert [e.year for e in en] == [2020, 2019, 2008, 2023, 2020, 2016, 2024, 2020]  # 年份是作者后面的那个，不是条目里最后出现的
    by = {e.index: e for e in en}
    assert by[3].authors[0] == "van der Maaten, L."  # 小写姓氏前缀
    assert by[4].authors == ["OpenAI"] and by[5].authors == ["World Health Organization"]  # 机构作者
    assert by[2].title.startswith("BERT") and by[2].doi == "10.18653/v1/n19-1423"
    assert by[7].authors[0] == "Wang, L." and by[7].title.startswith("A survey on large language model")  # APA 7 的省略号作者表


def test_apa_citations_in_text_map_to_the_right_entries():
    style, en = split_entries(_passages())
    body = [{"ord": 100, "kind": "paragraph", "text": (
        "Large language models (Brown et al., 2020) and BERT (Devlin et al., 2019) beat t-SNE (van der Maaten & Hinton, 2008). "
        "OpenAI (2023) and He et al. (2016) agree; (WHO, 2020) differs. Wang et al. (2024) surveyed agents; Smith (2020a) too.")}]
    marks = detect_all(body, [e.__dict__ for e in en], style)[0]["markers"]
    assert {m["text"]: m["refs"] for m in marks} == {
        "Brown et al., 2020": [1], "Devlin et al., 2019": [2], "van der Maaten & Hinton, 2008": [3], "OpenAI (2023)": [4],
        "He et al. (2016)": [6], "WHO, 2020": [5], "Wang et al. (2024)": [7], "Smith (2020a)": [8],
    }


def test_acl_style_full_names_with_hanging_indent():
    """ACL/NLP 论文常见格式：全名 + 年份 + 句点，没有括号；靠“首行顶格、续行缩进”分条，附录里混进来的正文段落不算参考文献。"""
    L, C = 72.0, 83.0

    def ps(rows):
        return [{"ord": i, "page": 10, "text": t, "bbox": [x, 100 + 11 * i, 290, 110 + 11 * i], "kind": "reference"} for i, (x, t) in enumerate(rows)]

    rows = [
        (L, "Alan Akbik, Duncan Blythe, and Roland Vollgraf."), (C, "2018. Contextual string embeddings for sequence labeling. In COLING."),
        (L, "Rie Kubota Ando and Tong Zhang. 2005. A framework for learning predictive structures from multiple tasks. JMLR, 6."),
        (L, "Daniel Cer, Mona Diab, Inigo Lopez- Gazpio, and Lucia Specia. 2017. Semeval-2017 task 1: Semantic textual similarity."),
        (L, "Jan van der Berg and Ming-Wei Chang. 2019. Another useful paper title here. In ACL."),
        (L, "Peter F Brown, Peter V Desouza, and Jenifer C Lai. 1992."), (C, "Class-based n-gram models of natural language. Computational linguistics, 18(4):467–479."),
        (L, "A Additional Details for the appendix section which is not a reference at all and has no year"),
    ]
    style, en = split_entries(ps(rows), {10: 595.0})
    assert style == "author-year" and len(en) == 5  # 没有年份的附录段落被去掉
    assert [e.index for e in en] == [1, 2, 3, 4, 5] and [e.year for e in en] == [2018, 2005, 2017, 2019, 1992]
    assert en[0].authors == ["Akbik, A.", "Blythe, D.", "Vollgraf, R."] and en[0].title.startswith("Contextual string embeddings")
    assert en[1].authors == ["Ando, R. K.", "Zhang, T."] and en[2].authors[2] == "Lopez-Gazpio, I."
    assert en[3].authors[0] == "van der Berg, J." and en[3].authors[1] == "Chang, M.-W."
    assert en[4].year == 1992 and en[4].title.startswith("Class-based n-gram") and len(en[4].raw.split(". ")) >= 3  # 续行并进了这一条
    marks = detect_all([{"ord": 100, "kind": "paragraph", "text": "As in (Akbik et al., 2018) and Ando and Zhang (2005), also (van der Berg and Chang, 2019)."}], [e.__dict__ for e in en], style)
    assert {m["text"]: m["refs"] for m in marks[0]["markers"]} == {"Akbik et al., 2018": [1], "Ando and Zhang (2005)": [2], "van der Berg and Chang, 2019": [4]}
