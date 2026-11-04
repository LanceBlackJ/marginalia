"""扫描版 PDF 的本地 OCR：检测、识别、入库、取消/失败不留半截数据、未安装时如实提示。

假引擎只用来让“导入→后台任务→入库”的链路快速且确定；真实引擎（RapidOCR）有单独的冒烟测试。
"""
from __future__ import annotations

import pytest

import pdfgen
from paperdesk.parsing import ocr
from paperdesk.parsing.pdf_parser import parse_pdf

SCALE = 150 / 72
LINES = [  # (文字, x0, y0, x1, y1) 单位 pt，下面会换算成 150dpi 的像素多边形
    ("1 Introduction", 72, 84, 200, 104),
    ("Cascading failures propagate through coupled infrastructure networks when a single", 72, 130, 523, 143),
    ("overloaded node redistributes its load to neighbouring nodes. We study percolation", 72, 146, 523, 159),
    ("thresholds and resilience of power grids under targeted attacks.", 72, 162, 460, 175),
]


def _fake_engine(png: bytes):
    out = []
    for text, x0, y0, x1, y1 in LINES:
        a, b, c, d = x0 * SCALE, y0 * SCALE, x1 * SCALE, y1 * SCALE
        out.append(([[a, b], [c, b], [c, d], [a, d]], text, 0.95))
    return out


@pytest.fixture
def fake_ocr():
    ocr.set_engine(_fake_engine)
    yield
    ocr.set_engine(None)


@pytest.fixture(scope="session")
def scan_pdf(tmp_path_factory):
    return pdfgen.build_scanned_text(tmp_path_factory.mktemp("scan") / "scan.pdf", pages=3)


def test_only_real_scan_pages_are_flagged(scan_pdf, scanned_pdf, sample_pdf):
    assert parse_pdf(scan_pdf).needs_ocr == [1, 2, 3]
    assert parse_pdf(sample_pdf).needs_ocr == []  # 有文字层的论文不会被当成扫描版
    assert parse_pdf(scanned_pdf).needs_ocr == []  # 只有一张小图的页面不算扫描页


def test_ocr_spans_feed_the_normal_parser(scan_pdf, fake_ocr):
    spans = {1: ocr.ocr_page(scan_pdf, 1), 2: ocr.ocr_page(scan_pdf, 2), 3: ocr.ocr_page(scan_pdf, 3)}
    assert len(spans[1]) == len(LINES)
    s = spans[1][1]  # 坐标已换算回 PDF point，落在页面范围内
    assert 70 < s.x0 < 74 and 128 < s.y0 < 132 and s.x1 < 595 and s.y1 < 842
    parsed = parse_pdf(scan_pdf, ocr_pages=spans)
    assert parsed.has_text_layer and parsed.ocr_pages == [1, 2, 3] and not parsed.needs_ocr
    assert parsed.passages and all(p.source == "ocr" for p in parsed.passages)
    text = " ".join(p.text for p in parsed.passages).lower()
    assert "cascading failures" in text and "percolation" in text
    for p in parsed.passages:  # bbox 在页面范围内
        x0, y0, x1, y1 = p.bbox
        assert 0 <= x0 < x1 <= 595.5 and 0 <= y0 < y1 <= 842.5
    assert any("OCR" in w and "可能有识别错误" in w for w in parsed.warnings)


def test_import_runs_ocr_in_background_and_makes_text_searchable(api, scan_pdf, fake_ocr):
    item = api.import_pdf(scan_pdf)
    assert item["status"] == "imported" and item["ocr_job_id"] and item["ocr_pages"] == 3
    assert "扫描版" in item["message"] and "不会上传到云端" in item["message"]
    pid = item["paper_id"]
    job = api.wait_job(item["ocr_job_id"])
    assert job["status"] == "completed" and job["result"] == {"pages": 3, "chars": job["result"]["chars"]} and job["result"]["chars"] > 100
    text = api.get(f"/papers/{pid}/text").json()
    assert text["count"] > 0 and all(i["source"] == "ocr" for i in text["items"])
    hits = api.get(f"/papers/{pid}/search", params={"q": "percolation"}).json()["items"]
    assert hits
    detail = api.get(f"/papers/{pid}").json()
    assert "可能有识别错误" in (detail.get("parse_error") or detail.get("warning") or "")
    assert not api.fake.requests  # 全程本地：没有任何请求发往云端


def test_cancel_leaves_no_partial_data(api, scan_pdf):
    calls = []

    def engine(png):
        calls.append(1)
        if len(calls) == 1:  # 第一页识别时取消任务
            jobs = api.get("/jobs").json()["items"]
            jid = next(j["id"] for j in jobs if j["type"] == "ocr_paper")
            api.post(f"/jobs/{jid}/cancel")
        return _fake_engine(png)

    ocr.set_engine(engine)
    try:
        item = api.import_pdf(scan_pdf)
        job = api.wait_job(item["ocr_job_id"])
    finally:
        ocr.set_engine(None)
    assert job["status"] == "cancelled"
    pid = item["paper_id"]
    assert api.get(f"/papers/{pid}/text").json()["count"] == 0
    with api.ctx.db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM passages WHERE paper_id = ?", (pid,)).fetchone()[0] == 0
        note = conn.execute("SELECT parse_error FROM papers WHERE id = ?", (pid,)).fetchone()[0]
    assert "已取消" in note


def test_engine_failure_is_reported_and_leaves_no_data(api, scan_pdf):
    def boom(png):
        raise RuntimeError("模型文件损坏")

    ocr.set_engine(boom)
    try:
        item = api.import_pdf(scan_pdf)
        job = api.wait_job(item["ocr_job_id"])
    finally:
        ocr.set_engine(None)
    assert job["status"] == "failed" and "模型文件损坏" in (job["error"] or "")
    pid = item["paper_id"]
    assert api.get(f"/papers/{pid}/text").json()["count"] == 0
    with api.ctx.db.session() as conn:
        note = conn.execute("SELECT parse_error FROM papers WHERE id = ?", (pid,)).fetchone()[0]
    assert "没有完成" in note and "模型文件损坏" in note


def test_blank_result_says_nothing_was_recognised(api, scan_pdf):
    ocr.set_engine(lambda png: [])
    try:
        item = api.import_pdf(scan_pdf)
        job = api.wait_job(item["ocr_job_id"])
    finally:
        ocr.set_engine(None)
    assert job["status"] == "completed" and job["result"]["chars"] == 0
    with api.ctx.db.session() as conn:
        note = conn.execute("SELECT parse_error FROM papers WHERE id = ?", (item["paper_id"],)).fetchone()[0]
    assert "没有识别到可用的文字" in note


def test_without_ocr_component_the_import_says_so(api, scan_pdf, monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: False)
    item = api.import_pdf(scan_pdf)
    assert item["status"] == "imported" and not item.get("ocr_job_id")
    assert "没有安装本地 OCR 组件" in item["message"]


@pytest.mark.skipif(not ocr.available(), reason="没有安装 rapidocr-onnxruntime")
def test_real_engine_reads_a_rendered_page(scan_pdf):
    """真实 RapidOCR：识别渲染成位图的已知文字（词级召回应当很高）。"""
    spans = ocr.ocr_page(scan_pdf, 1)
    got = " ".join(s.text for s in spans).lower()
    for word in ("cascading", "failures", "infrastructure", "percolation", "resilience", "introduction"):
        assert word in got, (word, got[:200])
    assert all(0 <= s.x0 < s.x1 <= 595.5 and 0 <= s.y0 < s.y1 <= 842.5 for s in spans)
