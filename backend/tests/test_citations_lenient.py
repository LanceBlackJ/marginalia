from paperdesk.references import citations as cit


def _para(i, text):
    return {"ord": i, "kind": "paragraph", "text": text}


def test_single_occurrence_word_accepted_when_paper_uses_sup_style():
    paras = [_para(i, f"Networks matter{i % 5 + 1} and graphs work{i % 5 + 1}, as shown. Graphs matter. Networks work.") for i in range(6)]
    paras.append(_para(99, "We are arresting criminal suspects3, then go on."))
    entries = [{"index": i, "year": None} for i in range(1, 6)]
    out = {o["ord"]: o for o in cit.detect_all(paras, entries, "numeric")}
    assert 99 in out and out[99]["markers"][0]["text"] == "3"


def test_not_lenient_without_sup_style():
    paras = [_para(1, "Some criminal suspects3, then go on.")]
    assert cit.detect_all(paras, [{"index": 3, "year": None}], "numeric") == []
