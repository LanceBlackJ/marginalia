"""翻译时保护行内公式：公式换成占位符发给模型，译完放回原来的 src 文字（前端靠 src 把译文里的公式也渲染成 KaTeX）。"""
from __future__ import annotations

import json

from paperdesk.ai import citeguard as cg

SRC = "The path is divided into N − 1 intervals of equal length h = (sf)/(N−1). Accordingly (Smith et al., 2019), we denote b_i ≡ b(si)."
MATH = [{"src": "N − 1", "tex": "N-1"}, {"src": "h = (sf)/(N−1)", "tex": "h=\\frac{s_f}{N-1}"}, {"src": "b_i ≡ b(si)", "tex": "b_i\\equiv b(s_i)"}]


def test_formula_masking_round_trip_and_rules():
    slots: set[int] = set()
    masked, found = cg.mask(SRC, MATH, None, slots)
    assert masked == "The path is divided into ⟦1⟧ intervals of equal length ⟦2⟧. Accordingly ⟦3⟧, we denote ⟦4⟧."
    assert found == ["N − 1", "h = (sf)/(N−1)", "(Smith et al., 2019)", "b_i ≡ b(si)"] and slots == {1, 2, 4}  # 引用不是公式
    used: set[int] = set()
    assert cg.restore(masked, found, used) == SRC and used == {1, 2, 3, 4}
    # 没有 tex 的候选、找不到的、嵌在单词中间的单个变量：不保护
    assert cg.mask("a bx c", [{"src": "x", "tex": "x"}, {"src": "zz", "tex": "zz"}, {"src": "q", "tex": None}])[1] == []
    # 公式列表为空或没给：和以前完全一样
    assert cg.mask("见 [12]。", None) == ("见 ⟦1⟧。", ["[12]"])


def test_batch_numbering_is_unique_and_missing_formulas_are_appended():
    found: list[str] = []
    slots: set[int] = set()
    m1, _ = cg.mask("alpha x = 1 beta", [{"src": "x = 1", "tex": "x=1"}], found, slots)
    m2, _ = cg.mask("gamma y = 2 delta", [{"src": "y = 2", "tex": "y=2"}], found, slots)
    assert (m1, m2) == ("alpha ⟦1⟧ beta", "gamma ⟦2⟧ delta") and found == ["x = 1", "y = 2"]
    used: set[int] = set()
    out = cg.restore("甲 ⟦1⟧ 乙", found, used)
    assert out == "甲 x = 1 乙" and used == {1}
    assert cg.append_missing(out, found, used, slots, {1}) == out  # 这一段的公式都在
    assert cg.append_missing("丙 丁", found, used, slots, {2}) == "丙 丁 （y = 2）"  # 第二段的公式丢了：补在末尾
    assert cg.append_missing("丙 丁", found, used, {1}, {2}) == "丙 丁"  # 不是公式的占位符（引用）丢了：不补


def test_single_translation_sends_placeholders_and_restores_the_exact_source_text(imported):
    api, pid = imported
    ev = api.sse("/ai/translate", {"paper_id": pid, "text": SRC, "math": MATH})
    done = [d for n, d in ev if n == "done"]
    assert done, ev[-1]
    tr = done[0]["translation"]
    for run in MATH:
        assert run["src"] in tr  # 译文里是原来的 src：前端的 mathRanges 能在里面找到它并渲染
    assert "⟦" not in tr and "⟦" not in "".join(d["text"] for n, d in ev if n == "delta")
    sent = [r["body"]["messages"][-1]["content"] for r in api.fake.requests if "待翻译文本" in str(r["body"].get("messages", [""])[-1].get("content", ""))]
    assert sent and "h = (sf)" not in sent[-1] and "b_i" not in sent[-1] and "⟦2⟧" in sent[-1]
    system = [r["body"]["messages"][0]["content"] for r in api.fake.requests if "待翻译文本" in str(r["body"].get("messages", [""])[-1].get("content", ""))][-1]
    assert "行内数学公式" in system  # 提示词里说明了占位符规则


def test_single_translation_without_a_math_list_looks_the_passage_up(imported):
    """“重新翻译这一段”：前端没带公式列表时，用这一段解析时存下的 math_json。"""
    api, pid = imported
    with api.ctx.db.session() as conn:
        row = conn.execute("SELECT ord, text FROM passages WHERE paper_id = ? AND kind = 'paragraph' ORDER BY ord LIMIT 1", (pid,)).fetchone()
        word = row["text"].split()[3]
        conn.execute("UPDATE passages SET math_json = ? WHERE paper_id = ? AND ord = ?",
                     (json.dumps([{"src": word, "tex": "W"}], ensure_ascii=False), pid, row["ord"]))
    ev = api.sse("/ai/translate", {"paper_id": pid, "text": row["text"], "passage_ord": row["ord"], "force": True})
    done = [d for n, d in ev if n == "done"][0]
    assert word in done["translation"] and "⟦" not in done["translation"]
    sent = [r["body"]["messages"][-1]["content"] for r in api.fake.requests if "待翻译文本" in str(r["body"].get("messages", [""])[-1].get("content", ""))][-1]
    assert "⟦1⟧" in sent


def test_a_formula_the_model_dropped_is_put_back_at_the_end(imported):
    api, pid = imported
    api.fake.translate_mode = "DROP_PLACEHOLDERS"
    ev = api.sse("/ai/translate", {"paper_id": pid, "text": SRC, "math": MATH, "force": True})
    done = [d for n, d in ev if n == "done"]
    assert done, ev[-1]
    tr = done[0]["translation"]
    assert all(run["src"] in tr for run in MATH) and "⟦" not in tr  # 公式一个也没有悄悄消失
    assert tr.rstrip().endswith("）")


def test_full_translation_protects_formulas_per_passage_and_numbers_slots_across_the_batch(imported):
    api, pid = imported
    with api.ctx.db.session() as conn:
        rows = conn.execute("SELECT ord, text FROM passages WHERE paper_id = ? AND kind = 'paragraph' ORDER BY ord LIMIT 2", (pid,)).fetchall()
        for k, r in enumerate(rows):
            word = r["text"].split()[2 + k]
            conn.execute("UPDATE passages SET math_json = ? WHERE paper_id = ? AND ord = ?", (json.dumps([{"src": word, "tex": "W"}]), pid, r["ord"]))
    job_id = api.post(f"/papers/{pid}/translation").json()["job_id"]
    assert api.wait_job(job_id)["status"] == "completed"
    items = {i["ord"]: i["translation"] for i in api.get(f"/papers/{pid}/translation").json()["items"]}
    for k, r in enumerate(rows):
        assert r["text"].split()[2 + k] in items[r["ord"]] and "⟦" not in items[r["ord"]]
    batch = [r["body"]["messages"][-1]["content"] for r in api.fake.requests if "###1###" in str(r["body"].get("messages"))]
    assert batch and "⟦1⟧" in batch[0] and "⟦2⟧" in batch[0]  # 一批里两段各有一个公式：编号不重复
