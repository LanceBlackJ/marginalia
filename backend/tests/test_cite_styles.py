from paperdesk.library import citations as C

P = {"id": "p1", "title": "Attention Is All You Need.", "authors": ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"], "year": 2017,
     "venue": "Advances in Neural Information Processing Systems", "doi": "10.5555/3295222.3295349", "arxiv_id": None, "url": None}


def test_mla_and_ieee_use_only_known_fields():
    mla = C.to_mla([P]).strip()
    assert mla.startswith("Vaswani, Ashish, et al. “Attention Is All You Need.” Advances in Neural Information Processing Systems, 2017, ")
    assert "doi.org/10.5555/3295222.3295349" in mla
    ieee = C.to_ieee([P]).strip()
    assert ieee.startswith("[1] A. Vaswani, N. Shazeer, and N. Parmar, “Attention Is All You Need,” Advances in Neural Information Processing Systems, 2017, doi: 10.5555/3295222.3295349")
    assert "vol." not in ieee and "pp." not in ieee  # 没有卷期页码就不编造


def test_missing_fields_do_not_leave_gaps():
    bare = {"id": "p2", "title": "Untitled study", "authors": [], "year": None, "venue": None, "doi": None, "arxiv_id": "2401.00001", "url": None}
    assert C.to_mla([bare]).strip() == "“Untitled study.” arXiv, https://arxiv.org/abs/2401.00001."
    assert C.to_ieee([bare]).strip() == "[1] “Untitled study,” arXiv preprint, arXiv:2401.00001."


def test_formats_registered():
    assert "mla" in C.FORMATS and "ieee" in C.FORMATS and set(C.FORMATS) == set(C.RENDERERS)
