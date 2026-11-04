"""段落在 PDF 里的真实位置：跨栏、跨页的续行也包含。"""
from __future__ import annotations

from paperdesk.ai import passage_rects as reading


def test_passage_rects_cover_the_paragraph(imported):
    api, pid = imported
    items = [p for pg in (1, 2, 3) for p in api.get(f"/papers/{pid}/passages?page={pg}").json()["items"] if p["kind"] == "paragraph" and len(p["text"]) >= 60]
    p = items[0]
    r = api.get(f"/papers/{pid}/passages/{p['ord']}/rects").json()
    assert r["rects"] and all(len(x["bbox"]) == 4 and x["page"] >= 1 for x in r["rects"])
    assert r["rects"][0]["page"] == p["page"]


def _words(text, page, x, y0, blk, per_line=6, lh=12.0):
    """合成一列文字：每行 per_line 个词，返回 span_rects 要的词序列。"""
    out = []
    for n, w in enumerate(text.split()):
        ln = n // per_line
        out.append((reading._norm_tok(w), page, (x + (n % per_line) * 40, y0 + ln * lh, x + (n % per_line) * 40 + 36, y0 + ln * lh + 10), (blk, ln)))
    return out


def test_span_rects_follow_a_paragraph_across_columns_and_pages():
    para = " ".join(f"word{i}" for i in range(60))
    first, rest = " ".join(f"word{i}" for i in range(0, 25)), " ".join(f"word{i}" for i in range(25, 60))
    left = _words("Intro text before. " + first, 1, 50, 600, 1)  # 左栏底部：段落的前半
    right = _words(rest + " Next paragraph starts here.", 1, 320, 60, 2)  # 右栏顶部：续行
    seq = left + right
    flat = reading._norm_tok(para)
    rects = reading.span_rects(seq, flat, 1, [50, 600, 280, 700])  # 解析器只记了左栏那一块
    assert rects, "没有定位到"
    xs = sorted({round(r["bbox"][0] / 100) for r in rects})
    assert len(rects) >= 2 and xs[0] <= 1 and xs[-1] >= 3  # 左栏和右栏都被框上
    assert all(r["bbox"][1] >= 60 - 2 for r in rects)  # 前后无关的文字不会被框进来
    seq2 = left + [(t, 2, b, k) for t, _p, b, k in right]  # 跨页：续行在下一页
    r2 = reading.span_rects(seq2, flat, 1, [50, 600, 280, 700])
    assert r2 and {r["page"] for r in r2} == {1, 2}
    assert reading.span_rects(seq, flat, 1, [0, 0, 10, 10]) is None  # 对不上就返回 None，由调用方退回起始块
    assert reading._norm_tok("task-specific") == reading._norm_tok("taskspecific") and reading._norm_tok("ﬁne") == "fine"
