"""重新解析已导入的论文：解析版本、原子替换、用户数据重映射、缓存重新关联、失败/取消不改旧数据、撤销、删除清理。

“旧数据”的造法：导入后把所有段落序号整体平移 +1000（位置框与文字不变），再造引用这些旧序号的用户数据；
重新解析会得到 0..n 的新序号，按“页码 + 位置框 + 文本”必须能映射回来。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from paperdesk.library import reparse
from paperdesk.parsing.pdf_parser import PARSER_VERSION

SHIFT = 1000


def _passages(api, pid):
    with api.ctx.db.session() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT ord, page, kind, text, x0, y0, x1, y1 FROM passages WHERE paper_id = ? ORDER BY ord", (pid,))]


def _age(api, pid, shift=SHIFT):
    """把这篇论文变成“旧解析”的样子：段落序号整体平移，解析版本记为 1。返回平移前的段落（按旧序号索引）。"""
    before = _passages(api, pid)
    with api.ctx.db.session() as conn:
        conn.execute("UPDATE passages SET ord = ord + ? WHERE paper_id = ?", (shift, pid))
        conn.execute("UPDATE equations SET passage_ord = passage_ord + ? WHERE paper_id = ? AND passage_ord IS NOT NULL", (shift, pid))
        conn.execute("UPDATE figures SET caption_ord = caption_ord + ? WHERE paper_id = ? AND caption_ord IS NOT NULL", (shift, pid))
        conn.execute("UPDATE figures SET inner_ord = inner_ord + ? WHERE paper_id = ? AND inner_ord IS NOT NULL", (shift, pid))
        conn.execute("UPDATE papers SET parser_version = 1 WHERE id = ?", (pid,))
    return before


def _reparse(api, pid, **body):
    r = api.post(f"/papers/{pid}/reparse", body or None)
    assert r.status_code == 200, r.text
    job = api.wait_job(r.json()["job_id"], timeout=60)
    return job


def _long_passage(passages):
    return next(p for p in passages if p["kind"] == "paragraph" and len(p["text"]) > 80)


def test_new_import_has_current_parser_version_and_old_ones_are_flagged(imported):
    api, pid = imported
    d = api.get(f"/papers/{pid}").json()
    assert d["parser_version"] == PARSER_VERSION and d["parser_outdated"] is False
    st = api.get(f"/papers/{pid}/reparse").json()
    assert st["can_reparse"] and not st["outdated"] and st["backup"] is None
    _age(api, pid)
    d = api.get(f"/papers/{pid}").json()
    assert d["parser_version"] == 1 and d["parser_outdated"] is True
    row = next(i for i in api.get("/papers").json()["items"] if i["id"] == pid)
    assert row["parser_outdated"] is True  # 列表里也带着，文库行据此显示“有新版解析”


def test_metadata_only_paper_cannot_be_reparsed(api):
    with api.ctx.db.session() as conn:
        conn.execute("INSERT INTO papers(id, title, has_pdf, status, imported_at, parser_version) VALUES ('m1','只有题录',0,'metadata_only','2026-01-01T00:00:00Z',1)")
    assert api.get("/papers/m1").json()["parser_outdated"] is False  # 没有 PDF 就没有“可重新解析”
    r = api.post("/papers/m1/reparse")
    assert r.status_code == 400 and "没有 PDF" in r.text


def test_reparse_replaces_derived_data_and_remaps_user_data(imported):
    api, pid = imported
    fresh = _passages(api, pid)
    old = _age(api, pid)
    target = _long_passage(old)
    old_ord = target["ord"] + SHIFT
    other = next(p for p in old if p is not target and p["kind"] == "paragraph" and len(p["text"]) > 60)
    # —— 用户数据：都引用“旧”序号 ——
    ok_note = api.post(f"/papers/{pid}/notes", {"text": "有引用位置的笔记", "page": target["page"], "quote": target["text"][:90], "passage_ord": old_ord}).json()
    only_ord = api.post(f"/papers/{pid}/notes", {"text": "只有序号", "page": other["page"], "passage_ord": other["ord"] + SHIFT}).json()
    lost = api.post(f"/papers/{pid}/notes", {"text": "对不上的笔记", "page": 1, "quote": "zzqq 这段文字在论文里根本不存在 qqzz", "passage_ord": 77777}).json()
    page_only = api.post(f"/papers/{pid}/notes", {"text": "只记在某一页", "page": 2}).json()
    bm_ok = api.post(f"/papers/{pid}/bookmarks", {"page": target["page"], "label": "这一段", "quote": target["text"][:90], "passage_ord": old_ord}).json()
    bm_lost = api.post(f"/papers/{pid}/bookmarks", {"page": 1, "label": "没了", "quote": "zzqq 这段文字在论文里根本不存在 qqzz", "passage_ord": 77777}).json()
    now = "2026-01-01T00:00:00Z"
    with api.ctx.db.session() as conn:
        conn.execute(
            "INSERT INTO kb_items(id, title, content, kind, source_paper_id, evidence_json, origin, created_at, updated_at) VALUES ('i1','t','c','other',?,?, 'manual',?,?)",
            (pid, json.dumps([{"paper_id": pid, "ord": old_ord, "page": target["page"], "quote": "q"}, {"paper_id": pid, "ord": 88888, "page": 1, "quote": "gone"}]), now, now))
        conn.execute(
            "INSERT INTO kb_items(id, title, content, kind, source_paper_id, evidence_json, origin, created_at, updated_at) VALUES ('i2','别的论文','c','other',?,?, 'manual',?,?)",
            (pid, json.dumps([{"paper_id": "another-paper", "ord": old_ord}]), now, now))  # 引用别的论文的不能动
        conn.execute(
            "INSERT INTO paper_findings(id, paper_id, type, title, evidence_json, created_at, updated_at) VALUES ('f1',?, 'x','提示',?,?,?)",
            (pid, json.dumps([old_ord, 99999]), now, now))
        conn.execute("UPDATE papers SET reading_state_json = ? WHERE id = ?", (json.dumps({"textAnchor": old_ord, "mode": "continuous"}), pid))
        conn.execute("DELETE FROM passage_embeddings WHERE paper_id = ?", (pid,))
    api.post(f"/papers/{pid}/bookmarks", {"page": 1, "label": "书签"})
    api.post(f"/papers/{pid}/annotations", {"page": 1, "color": "blue", "text": "abc", "rects": [[10, 10, 100, 24]]})
    n_before = len(_passages(api, pid))

    job = _reparse(api, pid)
    assert job["status"] == "completed", job
    item = job["result"]["items"][0]
    assert job["result"]["undo_available"] is True and item["new"] == len(fresh)

    # 派生数据与新解析一致，版本更新
    now_p = _passages(api, pid)
    assert [p["text"] for p in now_p] == [p["text"] for p in fresh] and now_p[0]["ord"] == fresh[0]["ord"]
    assert api.get(f"/papers/{pid}").json()["parser_outdated"] is False
    assert len(now_p) == n_before

    notes = {n["id"]: n for n in api.get(f"/papers/{pid}/notes").json()["items"]}
    new_target = next(p for p in fresh if p["text"] == target["text"])
    assert notes[ok_note["id"]]["passage_ord"] == new_target["ord"] and notes[ok_note["id"]]["stale"] is False
    assert notes[only_ord["id"]]["passage_ord"] == next(p for p in fresh if p["text"] == other["text"])["ord"]
    # 对不上的：不删除，保留原文引用和页码，标为位置已失效
    assert notes[lost["id"]]["stale"] is True and notes[lost["id"]]["passage_ord"] is None
    assert "不存在" in notes[lost["id"]]["quote"] and notes[lost["id"]]["page"] == 1 and notes[lost["id"]]["text"] == "对不上的笔记"
    assert notes[page_only["id"]]["stale"] is False and notes[page_only["id"]]["passage_ord"] is None
    assert len(notes) == 4

    # 段落书签跟笔记一样：按开头的原文找回新位置；对不上的保留页码和标签，标为位置已失效
    bms = {b["id"]: b for b in api.get(f"/papers/{pid}/bookmarks").json()["items"]}
    assert bms[bm_ok["id"]]["passage_ord"] == new_target["ord"] and bms[bm_ok["id"]]["stale"] is False
    assert bms[bm_lost["id"]]["passage_ord"] is None and bms[bm_lost["id"]]["stale"] is True and bms[bm_lost["id"]]["page"] == 1

    with api.ctx.db.session() as conn:
        ev = json.loads(conn.execute("SELECT evidence_json FROM kb_items WHERE id='i1'").fetchone()[0])
        other_ev = json.loads(conn.execute("SELECT evidence_json FROM kb_items WHERE id='i2'").fetchone()[0])
        finding = json.loads(conn.execute("SELECT evidence_json FROM paper_findings WHERE id='f1'").fetchone()[0])
        state = json.loads(conn.execute("SELECT reading_state_json FROM papers WHERE id = ?", (pid,)).fetchone()[0])
        bm = conn.execute("SELECT COUNT(*) FROM bookmarks WHERE paper_id = ?", (pid,)).fetchone()[0]
        an = conn.execute("SELECT COUNT(*) FROM annotations WHERE paper_id = ?", (pid,)).fetchone()[0]
    assert ev[0]["ord"] == new_target["ord"] and ev[0].get("stale") is None
    assert ev[1]["ord"] is None and ev[1]["stale"] is True and ev[1]["quote"] == "gone"  # 失效的保留引用文字
    assert other_ev[0]["ord"] == old_ord  # 引用别的论文的证据不被改写
    assert finding == [new_target["ord"]]
    assert state["textAnchor"] == new_target["ord"] and state["mode"] == "continuous"
    assert bm == 3 and an == 1  # 书签、批注按页码，不会丢（书签 = 页码书签 + 两个段落书签）

    stats = item["stats"]
    assert stats["notes"] == {"total": 3, "mapped": 2, "stale": 1}
    assert stats["passages"]["old"] == n_before and stats["passages"]["mapped"] >= n_before - 2
    # 处理进度里重新解析已经结束
    assert api.get(f"/papers/{pid}/processing").json().get("reparsing") is False


def test_equation_and_figure_caches_are_rebound_to_new_ids(api, tmp_path):
    import pdfgen_journal

    item = api.import_pdf(pdfgen_journal.build_journal_paper(tmp_path / "journal.pdf"))
    assert item["status"] == "imported", item
    pid = item["paper_id"]
    with api.ctx.db.session() as conn:
        eqs = [dict(r) for r in conn.execute("SELECT id, page, x0, y0, x1, y1, number FROM equations WHERE paper_id = ?", (pid,))]
        figs = [dict(r) for r in conn.execute("SELECT id, page, x0, y0, x1, y1, label, caption_ord FROM figures WHERE paper_id = ?", (pid,))]
    assert eqs and figs, "期刊样例应当有公式和图"
    _age(api, pid)
    with api.ctx.db.session() as conn:
        for e in eqs:
            conn.execute(
                "INSERT INTO equation_latex(equation_id, paper_id, latex, confidence, status, warnings_json, error, model, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (e["id"], pid, f"x_{e['id']}", "high", "ok", "[]", None, "m", "2026-01-01T00:00:00Z"))
        for f in figs:  # 图理解缓存里带着引用旧段落序号的 JSON
            conn.execute(
                "INSERT INTO figure_analysis(figure_id, paper_id, kind, status, data_json, evidence_json, derived_ord, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (f["id"], pid, "chart", "ready", "{}", json.dumps([{"ord": f["caption_ord"] + SHIFT, "page": f["page"], "quote": "cap"}]) if f["caption_ord"] is not None else "[]",
                 None, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"))
        # 一条对不上的缓存：指向一个位置完全不同的假公式
        conn.execute("INSERT INTO equations(paper_id, page, x0, y0, x1, y1, number, text, passage_ord) VALUES (?,1,500,800,520,810,'99','',NULL)", (pid,))
        ghost = conn.execute("SELECT MAX(id) FROM equations").fetchone()[0]
        conn.execute(
            "INSERT INTO equation_latex(equation_id, paper_id, latex, confidence, status, warnings_json, error, model, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (ghost, pid, "ghost", "low", "ok", "[]", None, "m", "2026-01-01T00:00:00Z"))
    assert _reparse(api, pid)["status"] == "completed"
    with api.ctx.db.session() as conn:
        rows = {r["number"]: r["latex"] for r in conn.execute(
            "SELECT e.number AS number, l.latex AS latex FROM equations e JOIN equation_latex l ON l.equation_id = e.id WHERE e.paper_id = ?", (pid,))}
        all_latex = [r[0] for r in conn.execute("SELECT latex FROM equation_latex WHERE paper_id = ?", (pid,))]
        fa = [dict(r) for r in conn.execute(
            "SELECT f.label AS label, f.caption_ord AS cap, a.evidence_json AS ev FROM figures f JOIN figure_analysis a ON a.figure_id = f.id WHERE f.paper_id = ?", (pid,))]
    assert len(rows) == len(eqs)  # 每个真实公式都重新关联到了新的公式 id
    assert "ghost" not in all_latex  # 对不上的缓存被丢弃（会重新识别）
    assert len(fa) == len(figs)  # 图理解缓存按位置重新关联
    for r in fa:
        ev = json.loads(r["ev"])
        if ev:
            assert ev[0]["ord"] == r["cap"]  # 缓存里引用的段落序号也映射到了新的图注段落
    st = api.get(f"/papers/{pid}/reparse").json()["backup"]["stats"]
    assert st["equation_latex"]["kept"] == len(eqs) and st["equation_latex"]["dropped"] == 1
    assert st["figure_analysis"]["kept"] == len(figs)


def test_failure_inside_the_transaction_leaves_old_data_unchanged(imported, monkeypatch):
    api, pid = imported
    _age(api, pid)
    api.post(f"/papers/{pid}/notes", {"text": "n", "page": 1, "quote": "x", "passage_ord": 5 + SHIFT})
    before = _passages(api, pid)
    from paperdesk.library import importer

    real = importer._store_parsed

    def boom(conn, paper_id, parsed):
        real(conn, paper_id, parsed)  # 先把新数据写进事务里……
        raise RuntimeError("模拟：写入中途出错")  # ……再失败：整个事务必须回滚

    monkeypatch.setattr(reparse, "_store_parsed", boom)
    job = _reparse(api, pid)
    assert job["status"] == "failed" and "模拟" in (job["error"] or "")
    assert _passages(api, pid) == before  # 段落与旧序号原样
    assert api.get(f"/papers/{pid}").json()["parser_version"] == 1
    assert api.get(f"/papers/{pid}/notes").json()["items"][0]["passage_ord"] == 5 + SHIFT
    assert api.get(f"/papers/{pid}/reparse").json()["backup"] is None  # 没有留下半截快照


def test_parse_error_and_suspicious_result_keep_old_data(imported, monkeypatch):
    api, pid = imported
    _age(api, pid)
    before = _passages(api, pid)
    from paperdesk.parsing.pdf_parser import PdfError, parse_pdf

    def bad(path, **kw):
        raise PdfError("模拟：PDF 损坏")

    monkeypatch.setattr(reparse, "parse_pdf", bad)
    job = _reparse(api, pid)
    assert job["status"] == "failed" and "原有数据保持不变" in job["error"] and _passages(api, pid) == before

    def too_few(path, **kw):  # 新解析只得到很少的段落：默认中止
        doc = parse_pdf(path, **kw)
        doc.passages = doc.passages[:3]
        return doc

    # 样例论文段落不够多时这条保护不会触发，所以把门槛条件造出来：复制出足够多的旧段落
    with api.ctx.db.session() as conn:
        for i in range(30):
            conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0, y0, x1, y1, source) VALUES (?,?,?,?,?,0,0,1,1,'text')",
                         (pid, 5000 + i, 1, "paragraph", f"额外的旧段落 {i}"))
    monkeypatch.setattr(reparse, "parse_pdf", too_few)
    job = _reparse(api, pid)
    assert job["status"] == "failed" and "差别太大" in job["error"]
    job = _reparse(api, pid, force=True)  # 用户确认后可以强制替换
    assert job["status"] == "completed"


def test_cancel_leaves_old_data_unchanged(imported, monkeypatch):
    api, pid = imported
    _age(api, pid)
    before = _passages(api, pid)
    from paperdesk.parsing.pdf_parser import parse_pdf

    def slow(path, **kw):
        time.sleep(1.5)
        return parse_pdf(path, **kw)

    monkeypatch.setattr(reparse, "parse_pdf", slow)
    job_id = api.post(f"/papers/{pid}/reparse").json()["job_id"]
    time.sleep(0.4)
    assert api.post(f"/jobs/{job_id}/cancel").status_code == 200
    job = api.wait_job(job_id, timeout=30)
    assert job["status"] == "cancelled"
    assert _passages(api, pid) == before and api.get(f"/papers/{pid}").json()["parser_version"] == 1


def test_undo_restores_old_parse_and_user_data(imported):
    api, pid = imported
    old = _age(api, pid)
    target = _long_passage(old)
    old_ord = target["ord"] + SHIFT
    n_old = api.post(f"/papers/{pid}/notes", {"text": "旧笔记", "page": target["page"], "quote": target["text"][:90], "passage_ord": old_ord}).json()
    lost = api.post(f"/papers/{pid}/notes", {"text": "对不上", "page": 1, "quote": "zzqq 根本不存在 qqzz", "passage_ord": 77777}).json()
    snapshot = _passages(api, pid)
    assert _reparse(api, pid)["status"] == "completed"
    # 重新解析之后用户又新建了一条笔记（引用新序号）
    new_ord = next(p for p in _passages(api, pid) if p["text"] == target["text"])["ord"]
    after = api.post(f"/papers/{pid}/notes", {"text": "重新解析之后写的", "page": target["page"], "quote": target["text"][:90], "passage_ord": new_ord}).json()

    r = api.post(f"/papers/{pid}/reparse/undo")
    assert r.status_code == 200, r.text
    assert _passages(api, pid) == snapshot  # 旧的段落序号与内容原样恢复
    assert api.get(f"/papers/{pid}").json()["parser_version"] == 1
    notes = {n["id"]: n for n in api.get(f"/papers/{pid}/notes").json()["items"]}
    assert notes[n_old["id"]]["passage_ord"] == old_ord
    assert notes[lost["id"]]["passage_ord"] == 77777 and notes[lost["id"]]["stale"] is False  # 失效标记也撤销了
    assert notes[after["id"]]["passage_ord"] == old_ord  # 之后新建的笔记按新→旧映射挪回旧段落
    assert api.get(f"/papers/{pid}/reparse").json()["backup"] is None
    assert api.post(f"/papers/{pid}/reparse/undo").status_code == 404  # 只能撤销一次


def test_backup_expires_after_seven_days(imported):
    api, pid = imported
    _age(api, pid)
    assert _reparse(api, pid)["status"] == "completed"
    assert api.get(f"/papers/{pid}/reparse").json()["backup"] is not None
    with api.ctx.db.session() as conn:
        conn.execute("UPDATE reparse_backup SET created_at = '2020-01-01T00:00:00.000Z' WHERE paper_id = ?", (pid,))
    assert api.get(f"/papers/{pid}/reparse").json()["backup"] is None
    assert api.post(f"/papers/{pid}/reparse/undo").status_code == 404


def test_deleting_the_paper_removes_the_backup(imported):
    api, pid = imported
    _age(api, pid)
    assert _reparse(api, pid)["status"] == "completed"
    with api.ctx.db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM reparse_backup WHERE paper_id = ?", (pid,)).fetchone()[0] == 1
    assert api.delete(f"/papers/{pid}").status_code == 200
    with api.ctx.db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM reparse_backup WHERE paper_id = ?", (pid,)).fetchone()[0] == 0


def test_reparse_cancels_other_jobs_and_shows_reparsing_state(imported, monkeypatch):
    api, pid = imported
    _age(api, pid)
    from paperdesk.parsing.pdf_parser import parse_pdf

    seen = {}

    def slow(path, **kw):
        seen["processing"] = api.get(f"/papers/{pid}/processing").json()  # 解析进行中查看处理进度
        return parse_pdf(path, **kw)

    monkeypatch.setattr(reparse, "parse_pdf", slow)
    assert _reparse(api, pid)["status"] == "completed"
    p = seen["processing"]
    assert p["reparsing"] is True and p["overall"] == "processing" and p["label"] == "重新解析中"


def test_supplement_children_are_reparsed_with_the_main_paper(api, sample_pdf, tmp_path):
    from test_materials import build_supplement

    pid = api.import_pdf(sample_pdf)["paper_id"]
    api.configure_ai()
    supp = build_supplement(tmp_path / "supp.pdf")
    r = api.post(f"/papers/{pid}/supplements/files", {"paths": [str(supp)]})
    assert r.status_code == 200, r.text
    assert api.wait_job(r.json()["job_id"])["status"] == "completed"
    child = api.get(f"/papers/{pid}/supplements").json()["items"][0]["child_paper_id"]
    assert child
    _age(api, pid)
    _age(api, child)
    child_before = _passages(api, child)
    assert api.get(f"/papers/{child}").json()["parser_outdated"] is True
    job = _reparse(api, pid)
    assert job["status"] == "completed" and {i["paper_id"] for i in job["result"]["items"]} == {pid, child}
    assert api.get(f"/papers/{child}").json()["parser_outdated"] is False
    assert [p["text"] for p in _passages(api, child)] == [p["text"] for p in child_before]
    assert _passages(api, child)[0]["ord"] == 0  # 补充材料的段落序号也换成了新序号
    assert api.post(f"/papers/{child}/reparse").status_code == 400  # 补充材料不能单独重新解析


def test_mapping_helpers_handle_split_merged_and_missing_passages():
    old = [
        {"ord": 1, "page": 1, "text": "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda.", "x0": 0, "y0": 0, "x1": 100, "y1": 20},
        {"ord": 2, "page": 1, "text": "Completely different second paragraph about networks and cascades in graphs.", "x0": 0, "y0": 30, "x1": 100, "y1": 50},
        {"ord": 3, "page": 2, "text": "A paragraph that no longer exists anywhere in the new parse at all.", "x0": 0, "y0": 0, "x1": 100, "y1": 20},
    ]
    new = [
        {"ord": 10, "page": 1, "text": "Alpha beta gamma delta epsilon zeta eta theta", "x0": 0, "y0": 0, "x1": 100, "y1": 10},  # 被拆成两段
        {"ord": 11, "page": 1, "text": "iota kappa lambda.", "x0": 0, "y0": 10, "x1": 60, "y1": 20},
        {"ord": 12, "page": 1, "text": "Completely different second paragraph about networks and cascades in graphs.", "x0": 0, "y0": 31, "x1": 100, "y1": 51},
    ]
    m = reparse.map_passages(old, new)
    assert m[1] == 10 and m[2] == 12 and 3 not in m
    assert reparse.find_by_quote("completely different second paragraph", 1, new) == 12
    assert reparse.find_by_quote("短", 1, new) is None  # 太短的引用不用来定位
    st = {"mapped": 0, "stale": 0}
    out = reparse.walk_json({"evidence": [{"ord": 1, "page": 1, "quote": "q"}, {"ord": 3, "page": 2, "quote": "gone"}], "ords": [1, 2, 3], "figure_ids": [5, 6]},
                            m, {5: 50}, {}, st, "p")
    assert out["evidence"][0]["ord"] == 10 and out["evidence"][1]["ord"] is None and out["evidence"][1]["stale"] is True
    assert out["ords"] == [10, 12] and out["stale_ords"] == [3] and out["figure_ids"] == [50] and out["stale_figure_ids"] == [6]
    assert st["stale"] == 2


def test_scanned_paper_is_reparsed_through_ocr(api, tmp_path):
    """扫描版：重新解析要和导入时一样走本地 OCR，而不是把文字弄丢。"""
    import pdfgen

    from paperdesk.parsing import ocr

    if not ocr.available():
        pytest.skip("没有本地 OCR 组件")
    item = api.import_pdf(pdfgen.build_scanned_text(tmp_path / "scan.pdf"))
    pid = item["paper_id"]
    assert item.get("ocr_job_id"), item
    assert api.wait_job(item["ocr_job_id"], timeout=180)["status"] == "completed"
    before = _passages(api, pid)
    if not before:
        pytest.skip("OCR 没有识别出文字，无法验证")
    _age(api, pid)
    job = _reparse(api, pid)
    assert job["status"] == "completed", job
    after = _passages(api, pid)
    assert len(after) >= max(1, len(before) // 2)
    with api.ctx.db.session() as conn:
        sources = {r[0] for r in conn.execute("SELECT DISTINCT source FROM passages WHERE paper_id = ?", (pid,))}
    assert sources == {"ocr"}  # 重新解析后仍然标注“来自 OCR，可能有错”


REAL_ARTICLE = Path(r"C:\Users\华为\Desktop\Recovery") / "Fan 等 - 2020 - Finding key players in complex networks through de .pdf"


def _digit_ratio(text: str) -> float:
    chars = [c for c in text if not c.isspace()]
    return sum(c.isdigit() or c in ".-%" for c in chars) / len(chars) if chars else 0.0


@pytest.mark.skipif(not REAL_ARTICLE.exists(), reason="本机没有用户的真实论文")
def test_real_article_reparse_moves_figure_numbers_out_of_the_text_flow_and_rebuilds_tables(api):
    """用户截图里的那篇：Fig.5 的数字堆砌在重新解析之后归为图内文字，图内数据表能用文字层重建（只读原文件）。"""
    item = api.import_pdf(REAL_ARTICLE)  # 复制到测试数据目录，不改用户的文件
    assert item["status"] == "imported", item
    pid = item["paper_id"]
    _age(api, pid)  # 假装它是旧版本解析的：序号整体平移，版本记为 1
    # 旧用户数据：一条引用 Fig.5 图注的笔记
    cap = next(p for p in _passages(api, pid) if p["text"].startswith("Fig. 5"))
    note = api.post(f"/papers/{pid}/notes", {"text": "Fig.5 笔记", "page": cap["page"], "quote": cap["text"][:80], "passage_ord": cap["ord"] + SHIFT}).json()
    job = _reparse(api, pid)
    assert job["status"] == "completed", job
    passages = _passages(api, pid)
    soup = [p for p in passages if p["page"] == 6 and len(p["text"]) > 40 and _digit_ratio(p["text"]) > 0.6]
    assert soup, "第 6 页应有 Fig.5 里数字密集的文字"
    assert all(p["kind"] == "figure_text" for p in soup), [(p["ord"], p["kind"]) for p in soup if p["kind"] != "figure_text"]
    assert not any(p["kind"] == "paragraph" and _digit_ratio(p["text"]) > 0.6 and len(p["text"]) > 40 for p in passages)
    # 笔记跟着 Fig.5 图注挪到了新序号
    new_cap = next(p for p in passages if p["text"].startswith("Fig. 5"))
    n = next(x for x in api.get(f"/papers/{pid}/notes").json()["items"] if x["id"] == note["id"])
    assert n["passage_ord"] == new_cap["ord"] and n["stale"] is False
    # 图内数据表能用文字层重建（不需要模型）
    from paperdesk.vision import table_batch as TB

    # 默认不把 Fig. 里的数据转成表格
    assert not [t for t in TB.tables_for(api.ctx, pid) if t.get("kind") == "figure_data"]
    TB.FIGURE_DATA_AS_TABLE = True
    try:
        figdata = [t for t in TB.tables_for(api.ctx, pid) if t.get("kind") == "figure_data" and t["page"] == 6]
    finally:
        TB.FIGURE_DATA_AS_TABLE = False
    assert figdata, "Fig.5 的图内数据网格应被检测到"
    data = TB.layout_figure_data(api.ctx, pid, figdata[0])
    assert data and len(data["blocks"]) >= 6 and all(b["confidence"] >= 0.8 for b in data["blocks"])
    assert any("Avg score" in " ".join(r) for b in data["blocks"] for r in b["rows"])
