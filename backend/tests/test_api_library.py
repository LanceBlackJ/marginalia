from __future__ import annotations

import shutil

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import AUTH, make_api


def test_health_and_auth(api):
    assert api.c.get("/api/health").json()["status"] == "ok"
    assert api.c.get("/api/papers").status_code == 401
    assert api.c.get("/api/papers", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = api.get("/papers")
    assert r.status_code == 200 and r.json() == {"items": [], "total": 0}
    assert api.get("/papers/nope").json()["error"]["code"] == "not_found"


def test_import_list_and_dedupe(api, sample_pdf, tmp_path):
    item = api.import_pdf(sample_pdf)
    assert item["status"] == "imported"
    pid = item["paper_id"]

    detail = api.get(f"/papers/{pid}").json()
    assert detail["title"] == "Dual Attention Routing for Robust Cascade Recovery"
    assert detail["authors"][0] == "Alice Zhang"
    assert detail["doi"] == "10.1234/dar.2023.001" and detail["year"] == 2023
    assert detail["status"] == "ready" and detail["has_pdf"] and detail["page_count"] == 5
    assert detail["original_path"] == str(sample_pdf)
    assert (api.ctx.paths.paper_dir(pid) / "original.pdf").is_file()
    assert (api.ctx.paths.paper_dir(pid) / "metadata.json").is_file()

    # 同一文件（哪怕换了路径/文件名）不会重复入库
    copy = tmp_path / "renamed.pdf"
    shutil.copy(sample_pdf, copy)
    dup = api.import_pdf(copy)
    assert dup["status"] == "duplicate" and dup["paper_id"] == pid
    assert api.get("/papers").json()["total"] == 1


def test_import_failures_are_reported(api, tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 garbage")
    txt = tmp_path / "x.txt"
    txt.write_text("hello")
    job = api.wait_job(api.post("/import/files", {"paths": [str(bad), str(txt), str(tmp_path / "missing.pdf")]}).json()["job_id"])
    assert job["status"] == "completed"
    statuses = [i["status"] for i in job["result"]["items"]]
    assert statuses == ["failed", "failed", "failed"]
    assert all(i["message"] for i in job["result"]["items"])
    assert api.get("/papers").json()["total"] == 0


def test_list_search_sort_favorite_and_reading_state(api, sample_pdf, tmp_path):
    from pdfgen import build_paper

    pid = api.import_pdf(sample_pdf)["paper_id"]
    other = build_paper(tmp_path / "other.pdf", doi=None, pages=4)
    # 第二篇换个文件内容，避免哈希重复
    import pymupdf

    doc = pymupdf.open(str(other))
    doc[0].insert_text((50, 20), "Second paper marker", fontsize=6)
    doc.save(str(tmp_path / "other2.pdf"))
    doc.close()
    pid2 = api.import_pdf(tmp_path / "other2.pdf")["paper_id"]

    assert api.get("/papers", params={"q": "cascade"}).json()["total"] == 2
    assert api.get("/papers", params={"q": "alice zhang"}).json()["total"] == 2
    assert api.get("/papers", params={"q": "no-such-thing"}).json()["total"] == 0
    assert api.get("/papers", params={"q": "100%"}).json()["total"] == 0  # 通配符被转义

    r = api.patch(f"/papers/{pid}", {"favorite": True, "reading_page": 3, "read_progress": 0.4,
                                       "reading_state": {"zoom": 1.25, "mode": "continuous"}, "touch": True})
    assert r.status_code == 200
    d = r.json()
    assert d["favorite"] and d["reading_page"] == 3 and d["reading_state"]["zoom"] == 1.25 and d["last_opened_at"]
    assert [p["id"] for p in api.get("/papers", params={"view": "favorite"}).json()["items"]] == [pid]
    assert [p["id"] for p in api.get("/papers", params={"view": "recent"}).json()["items"]] == [pid]
    by_title = api.get("/papers", params={"sort": "title", "order": "asc"}).json()["items"]
    assert len(by_title) == 2 and pid2 in [p["id"] for p in by_title]
    assert api.get("/papers", params={"sort": "bogus"}).status_code == 400


def test_reader_endpoints(api, sample_pdf):
    pid = api.import_pdf(sample_pdf)["paper_id"]
    outline = api.get(f"/papers/{pid}/outline").json()
    assert outline["source"] == "detected"
    level1 = [i["title"] for i in outline["items"] if i["level"] == 1]
    assert [t for t in level1 if t in ("1 Introduction", "2 Method", "3 Experiments")] == ["1 Introduction", "2 Method", "3 Experiments"]

    page = api.get(f"/papers/{pid}/passages", params={"page": 1}).json()
    assert page["width"] == 595 and page["height"] == 842 and page["items"]
    assert all(len(p["bbox"]) == 4 for p in page["items"])
    assert api.get(f"/papers/{pid}/passages", params={"page": 99}).status_code == 404

    # 选中文字 → 段落定位（含换行/连字符噪声）
    sel = "topology attention module computes attention weights over neighbouring"
    m = api.post(f"/papers/{pid}/locate", {"text": sel, "page": 1}).json()["matches"]
    assert m and m[0]["page"] == 1 and m[0]["score"] >= 0.9
    p = api.get(f"/papers/{pid}/passages/{m[0]['ord']}").json()
    assert "topology attention" in p["text"]
    # 页码给错也能在全文里找回
    m2 = api.post(f"/papers/{pid}/locate", {"text": sel, "page": 4}).json()["matches"]
    assert m2 and m2[0]["ord"] == m[0]["ord"]

    hits = api.get(f"/papers/{pid}/search", params={"q": "temporal attent"}).json()["items"]
    assert hits and hits[0]["highlights"]
    s, e = hits[0]["highlights"][0]
    assert hits[0]["snippet"][s:e].lower() in ("temporal", "attent", "attention")
    assert api.get(f"/papers/{pid}/search", params={"q": "zzzzqqq"}).json()["items"] == []


def test_pdf_file_supports_range(api, sample_pdf):
    pid = api.import_pdf(sample_pdf)["paper_id"]
    full = api.get(f"/papers/{pid}/file")
    assert full.status_code == 200 and full.headers["content-type"] == "application/pdf" and full.content[:5] == b"%PDF-"
    part = api.c.get(f"/api/papers/{pid}/file", headers={**AUTH, "Range": "bytes=0-9"})
    assert part.status_code == 206 and len(part.content) == 10


def test_bookmarks_notes_annotations(api, sample_pdf):
    pid = api.import_pdf(sample_pdf)["paper_id"]
    b = api.post(f"/papers/{pid}/bookmarks", {"page": 2, "label": "方法"}).json()
    assert api.get(f"/papers/{pid}/bookmarks").json()["items"][0]["label"] == "方法"
    assert api.delete(f"/papers/{pid}/bookmarks/{b['id']}").json()["ok"]
    assert api.delete(f"/papers/{pid}/bookmarks/{b['id']}").status_code == 404

    n = api.post(f"/papers/{pid}/notes", {"text": "这里很关键", "page": 1, "quote": "Cascading failures", "passage_ord": 3}).json()
    assert api.patch(f"/notes/{n['id']}", {"text": "改过了"}).json()["text"] == "改过了"
    assert api.post(f"/papers/{pid}/notes", {"text": "  "}).status_code == 400
    assert len(api.get(f"/papers/{pid}/notes").json()["items"]) == 1
    assert api.delete(f"/notes/{n['id']}").json()["ok"]

    a = api.post(f"/papers/{pid}/annotations", {"page": 1, "color": "blue", "text": "abc", "rects": [[10, 10, 100, 24]]}).json()
    assert a["rects"] == [[10, 10, 100, 24]]
    assert api.post(f"/papers/{pid}/annotations", {"page": 1, "rects": [[1, 2, 3]]}).status_code == 400
    assert api.delete(f"/papers/{pid}/annotations/{a['id']}").json()["ok"]


def test_import_folder_keeps_source_structure(api, sample_pdf, tmp_path):
    import pymupdf

    root = tmp_path / "lib"
    (root / "ml" / "rl").mkdir(parents=True)
    from pdfgen import build_paper

    for i, sub in enumerate(["", "ml", "ml/rl"]):
        src = build_paper(tmp_path / f"src{i}.pdf", doi=None)  # 无 DOI，避免被 DOI 去重
        doc = pymupdf.open(str(src))
        doc[0].insert_text((20, 20), f"unique {i}", fontsize=5)
        out = root / sub / f"p{i}.pdf"
        doc.save(str(out))
        doc.close()
    job = api.wait_job(api.post("/import/folder", {"path": str(root)}).json()["job_id"])
    assert [i["status"] for i in job["result"]["items"]].count("imported") == 3
    folders = sorted(p["source_folder"] for p in api.get("/papers").json()["items"])
    assert folders == ["lib", "lib/ml", "lib/ml/rl"]
    assert api.post("/import/folder", {"path": str(tmp_path / "nope")}).status_code == 400
    empty = tmp_path / "empty"
    empty.mkdir()
    assert api.post("/import/folder", {"path": str(empty)}).status_code == 400


def test_delete_paper_keeps_original_file(api, sample_pdf):
    pid = api.import_pdf(sample_pdf)["paper_id"]
    pdir = api.ctx.paths.paper_dir(pid)
    assert pdir.exists()
    assert api.delete(f"/papers/{pid}").json()["ok"]
    assert not pdir.exists()
    assert sample_pdf.exists()  # 用户原文件绝不被触碰
    assert api.get(f"/papers/{pid}").status_code == 404
    with api.ctx.db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM passages").fetchone()[0] == 0


def test_scanned_pdf_is_imported_with_warning(api, scanned_pdf):
    item = api.import_pdf(scanned_pdf)
    assert item["status"] == "imported"
    d = api.get(f"/papers/{item['paper_id']}").json()
    assert d["status"] == "ready" and "文字层" in d["parse_error"]
    assert api.get(f"/papers/{item['paper_id']}/passages", params={"page": 1}).json()["items"] == []


# ---- 任务系统 ----
def test_job_failure_retry_and_recovery(api):
    calls = []

    def boom(ctx):
        calls.append(1)
        ctx.log("开始")
        raise RuntimeError("kaboom")

    api.ctx.jobs.register("boom", boom)
    jid = api.ctx.jobs.submit("boom", "测试失败任务")
    job = api.wait_job(jid)
    assert job["status"] == "failed" and "kaboom" in job["error"] and job["can_retry"]
    detail = api.get(f"/jobs/{jid}").json()
    assert any("开始" in line for line in detail["log"])
    new_id = api.post(f"/jobs/{jid}/retry").json()["job_id"]
    assert new_id != jid and api.wait_job(new_id)["status"] == "failed" and len(calls) == 2
    assert api.post(f"/jobs/{new_id}/cancel").status_code == 409

    # 重启恢复：运行中 → 失败；排队中 → 继续执行
    with api.ctx.db.session() as conn:
        conn.execute("INSERT INTO jobs(id,type,title,status,payload_json,created_at) VALUES ('r1','boom','中断的','running','{}','2026-01-01T00:00:00Z')")
        conn.execute("INSERT INTO jobs(id,type,title,status,payload_json,created_at) VALUES ('p1','boom','排队的','pending','{}','2026-01-01T00:00:01Z')")
    api.ctx.jobs.recover()
    assert api.get("/jobs/r1").json()["status"] == "failed"
    assert "中断" in api.get("/jobs/r1").json()["error"]
    assert api.wait_job("p1")["status"] == "failed" and len(calls) == 3


def test_job_cancel_running(api):
    import threading

    started = threading.Event()

    def slow(ctx):
        started.set()
        for _ in range(200):
            ctx.check_cancelled()
            import time

            time.sleep(0.02)

    api.ctx.jobs.register("slow", slow)
    jid = api.ctx.jobs.submit("slow", "慢任务")
    assert started.wait(5)
    assert api.post(f"/jobs/{jid}/cancel").status_code == 200
    assert api.wait_job(jid)["status"] == "cancelled"
    listing = api.get("/jobs", params={"status": "cancelled"}).json()["items"]
    assert any(j["id"] == jid for j in listing)


# ---- 在线导入（DOI / arXiv / 链接）----
def _online_handler(pdf_bytes: bytes, *, oa: bool = True):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.crossref.org/works/10.1234%2Fdar" in url or "api.crossref.org/works/10.1234/dar" in url:
            return httpx.Response(200, json={"message": {
                "title": ["Dual Attention Routing for Robust Cascade Recovery"],
                "author": [{"given": "Alice", "family": "Zhang"}, {"given": "Bob", "family": "Miller"}],
                "issued": {"date-parts": [[2023, 5]]}, "container-title": ["Journal of Cascade Recovery"],
                "abstract": "<jats:p>We propose DAR.</jats:p>", "URL": "https://doi.org/10.1234/dar.2023.001"}})
        if "api.crossref.org/works/10.9999" in url:
            return httpx.Response(404)
        if "api.openalex.org" in url and "10.9999" in url:
            return httpx.Response(404)
        if "api.openalex.org" in url:
            locs = [{"pdf_url": "https://oa.example.org/dar.pdf", "landing_page_url": "https://oa.example.org/dar"}] if oa else \
                   [{"pdf_url": None, "landing_page_url": "https://publisher.example.org/dar"}]
            return httpx.Response(200, json={"title": "DAR", "locations": locs, "open_access": {"is_oa": oa},
                                              "authorships": [], "publication_year": 2023})
        if url == "https://oa.example.org/dar.pdf":
            return httpx.Response(200, content=pdf_bytes, headers={"content-type": "application/pdf"})
        if "export.arxiv.org/api/query" in url:
            return httpx.Response(200, text="""<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>
              <entry><title>Arxiv   Paper Title</title><summary> An abstract. </summary><published>2022-03-01T00:00:00Z</published>
              <author><name>Zed Author</name></author></entry></feed>""")
        if url.startswith("https://arxiv.org/pdf/"):
            return httpx.Response(200, content=pdf_bytes, headers={"content-type": "application/pdf"})
        return httpx.Response(404)

    return handler


def test_doi_import_with_open_access_pdf(tmp_path, sample_pdf):
    api, _ = make_api(tmp_path, handler=_online_handler(sample_pdf.read_bytes()))
    with api.c:
        job = api.wait_job(api.post("/import/doi", {"dois": ["https://doi.org/10.1234/DAR.2023.001"]}).json()["job_id"])
        item = job["result"]["items"][0]
        assert item["status"] == "imported", item
        d = api.get(f"/papers/{item['paper_id']}").json()
        assert d["metadata_source"] == "crossref" and d["venue"] == "Journal of Cascade Recovery"
        assert d["authors"] == ["Alice Zhang", "Bob Miller"] and d["source_url"] == "https://doi.org/10.1234/dar.2023.001"
        # 再导入同一 DOI → 重复
        again = api.wait_job(api.post("/import/doi", {"dois": ["10.1234/dar.2023.001"]}).json()["job_id"])
        assert again["result"]["items"][0]["status"] == "duplicate"


def test_doi_import_without_full_text_then_attach_pdf(tmp_path, sample_pdf):
    api, _ = make_api(tmp_path, handler=_online_handler(sample_pdf.read_bytes(), oa=False))
    with api.c:
        job = api.wait_job(api.post("/import/doi", {"dois": ["10.1234/dar.2023.001", "10.9999/missing", "not a doi"]}).json()["job_id"])
        items = job["result"]["items"]
        assert [i["status"] for i in items] == ["metadata_only", "failed", "failed"]
        assert items[0]["needs_manual_download"] is True  # 试过自动下载但没下到：前端据此弹出“去下载”的窗口
        pid = items[0]["paper_id"]
        d = api.get(f"/papers/{pid}").json()
        assert d["status"] == "metadata_only" and not d["has_pdf"]
        assert any("doi.org" in a["url"] for a in d["access_info"])
        assert api.get(f"/papers/{pid}/file").status_code == 404

        # 用户自行获取 PDF 后附加：合并进同一条记录，而不是新建
        att = api.wait_job(api.post(f"/papers/{pid}/attach_pdf", {"path": str(sample_pdf)}).json()["job_id"])
        assert att["status"] == "completed", att
        d2 = api.get(f"/papers/{pid}").json()
        assert d2["status"] == "ready" and d2["has_pdf"] and d2["page_count"] == 5
        assert api.get("/papers").json()["total"] == 1
        assert api.get(f"/papers/{pid}/outline").json()["items"]
        assert api.post(f"/papers/{pid}/attach_pdf", {"path": str(sample_pdf)}).status_code == 409


def test_url_import_arxiv_and_unsupported(tmp_path, sample_pdf):
    api, _ = make_api(tmp_path, handler=_online_handler(sample_pdf.read_bytes()))
    with api.c:
        job = api.wait_job(api.post("/import/url", {"url": "https://arxiv.org/abs/2203.01234v2"}).json()["job_id"])
        item = job["result"]["items"][0]
        assert item["status"] == "imported", item
        d = api.get(f"/papers/{item['paper_id']}").json()
        assert d["arxiv_id"] == "2203.01234" and d["title"] == "Arxiv Paper Title" and d["year"] == 2022
        bad = api.wait_job(api.post("/import/url", {"url": "https://example.com/some/page"}).json()["job_id"])
        assert bad["result"]["items"][0]["status"] == "failed"
        assert api.post("/import/url", {"url": "ftp://x"}).status_code == 400


def test_download_rejects_non_pdf_and_never_bypasses_paywall(tmp_path, sample_pdf):
    def handler(request):
        url = str(request.url)
        if "crossref" in url:
            return httpx.Response(200, json={"message": {"title": ["Paywalled"], "author": [], "issued": {"date-parts": [[2020]]}}})
        if "openalex" in url:
            return httpx.Response(200, json={"title": "Paywalled", "locations": [{"pdf_url": "https://pay.example.org/p.pdf"}]})
        return httpx.Response(200, text="<html>please log in</html>", headers={"content-type": "text/html"})

    api, _ = make_api(tmp_path, handler=handler)
    with api.c:
        job = api.wait_job(api.post("/import/doi", {"dois": ["10.5555/paywalled"]}).json()["job_id"])
        item = job["result"]["items"][0]
        assert item["status"] == "metadata_only" and "不是 PDF" in item["message"] and item["needs_manual_download"] is True


def test_concurrent_import_jobs_do_not_crash_parser(api, tmp_path):
    """两个导入任务并行运行（任务池有 2 个线程）：PyMuPDF 解析被串行化，结果必须全部正确。"""
    import pymupdf
    from pdfgen import build_paper

    groups: list[list[str]] = [[], []]
    for i in range(8):
        src = build_paper(tmp_path / f"c{i}.pdf", doi=None)
        doc = pymupdf.open(str(src))
        doc[0].insert_text((20, 20), f"concurrent {i}", fontsize=5)
        out = tmp_path / f"cc{i}.pdf"
        doc.save(str(out))
        doc.close()
        groups[i % 2].append(str(out))
    jobs = [api.post("/import/files", {"paths": g}).json()["job_id"] for g in groups]
    results = [api.wait_job(j, timeout=60) for j in jobs]
    assert all(j["status"] == "completed" for j in results)
    items = [i for j in results for i in j["result"]["items"]]
    assert [i["status"] for i in items].count("imported") == 8
    for i in items:
        assert api.get(f"/papers/{i['paper_id']}").json()["page_count"] == 5


def test_chinese_paper_search_and_retrieval(api, chinese_pdf):
    from paperdesk.ai import retrieval
    from paperdesk.ai.router import TaskType

    pid = api.import_pdf(chinese_pdf)["paper_id"]
    assert api.get(f"/papers/{pid}").json()["language"] == "zh"
    # FTS 无法切分中文：应退化为子串检索
    hits = api.get(f"/papers/{pid}/search", params={"q": "时间注意力"}).json()["items"]
    assert hits and "时间注意力模块" in hits[0]["snippet"]
    s, e = hits[0]["highlights"][0]
    assert hits[0]["snippet"][s:e] == "时间注意力"
    # 选中中文文字能定位
    m = api.post(f"/papers/{pid}/locate", {"text": "拓扑模块计算邻居节点的注意力权重", "page": 2}).json()["matches"]
    assert m and m[0]["score"] >= 0.9
    # 中文问题直接检索中文论文（不需要查询扩展）
    pack = retrieval.build_context(api.ctx, pid, question="时间注意力模块关注什么", selection=None, task=TaskType.METHOD)
    assert any("时间模块关注节点近期的负载历史" in p.text for p in pack.passages)


def test_housekeeping_removes_stale_temporary_cards(imported):
    api, pid = imported
    sel = {"text": "The routing score of node i is defined as", "page": 2}
    temp = api.sse("/ai/ask", {"paper_id": pid, "intent": "explain", "selection": sel, "save": False})[-1][1]["card"]
    kept = api.sse("/ai/ask", {"paper_id": pid, "intent": "motivation", "question": "为什么", "selection": sel})[-1][1]["card"]
    with api.ctx.db.session() as conn:  # 把临时卡片和保存卡片都“变老”
        conn.execute("UPDATE insight_cards SET created_at = '2020-01-01T00:00:00.000Z'")
    api.ctx.jobs.recover()
    ids = [c["id"] for c in api.get(f"/papers/{pid}/cards").json()["items"]]
    assert temp["id"] not in ids and kept["id"] in ids


def test_doi_import_metadata_only_never_downloads_the_pdf(tmp_path, sample_pdf):
    seen: list[str] = []
    inner = _online_handler(sample_pdf.read_bytes())  # 有开放获取全文：默认会自动下载

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return inner(request)

    api, _ = make_api(tmp_path, handler=handler)
    with api.c:
        job = api.wait_job(api.post("/import/doi", {"dois": ["10.1234/dar.2023.001"], "metadata_only": True}).json()["job_id"])
        item = job["result"]["items"][0]
        assert item["status"] == "metadata_only" and "只保存了题录" in item["message"], item
        assert "needs_manual_download" not in item  # 用户自己选了不下载：不弹窗
        assert not any(u.endswith(".pdf") or "/pdf/" in u for u in seen), seen  # 没有任何 PDF 下载请求
        d = api.get(f"/papers/{item['paper_id']}").json()
        assert d["status"] == "metadata_only" and not d["has_pdf"] and d["doi"] == "10.1234/dar.2023.001"
        # 之后仍可附加 PDF，合并进同一条记录
        att = api.wait_job(api.post(f"/papers/{item['paper_id']}/attach_pdf", {"path": str(sample_pdf)}).json()["job_id"])
        assert att["status"] == "completed", att
        assert api.get(f"/papers/{item['paper_id']}").json()["has_pdf"]


def test_default_doi_import_still_downloads_open_access_pdf(tmp_path, sample_pdf):
    api, _ = make_api(tmp_path, handler=_online_handler(sample_pdf.read_bytes()))
    with api.c:
        item = api.wait_job(api.post("/import/doi", {"dois": ["10.1234/dar.2023.001"]}).json()["job_id"])["result"]["items"][0]
        assert item["status"] == "imported"  # metadata_only 默认 false，保持原有行为


def test_url_import_metadata_only(tmp_path, sample_pdf):
    seen: list[str] = []
    inner = _online_handler(sample_pdf.read_bytes())

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return inner(request)

    api, _ = make_api(tmp_path, handler=handler)
    with api.c:
        ok = api.wait_job(api.post("/import/url", {"url": "https://arxiv.org/abs/2203.00001", "metadata_only": True}).json()["job_id"])
        assert ok["result"]["items"][0]["status"] == "metadata_only"
        assert not any(u.startswith("https://arxiv.org/pdf/") for u in seen)
        # 直链 PDF 没有题录：无法“仅保存元数据”，如实失败而不是偷偷下载
        bad = api.wait_job(api.post("/import/url", {"url": "https://oa.example.org/dar.pdf", "metadata_only": True}).json()["job_id"])
        assert bad["result"]["items"][0]["status"] == "failed" and "仅保存元数据" in bad["result"]["items"][0]["message"]
        assert not any(u == "https://oa.example.org/dar.pdf" for u in seen)
