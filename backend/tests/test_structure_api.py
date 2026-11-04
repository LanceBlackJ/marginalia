from __future__ import annotations

import pdfgen_journal


def _import(api, tmp_path):
    pdf = pdfgen_journal.build_journal_paper(tmp_path / "journal.pdf")
    item = api.import_pdf(pdf)
    assert item["status"] == "imported", item
    return item["paper_id"]


def test_figures_and_equations_are_exposed(api, tmp_path):
    pid = _import(api, tmp_path)
    figs = api.get(f"/papers/{pid}/figures").json()["items"]
    fig1 = next(f for f in figs if f["label"] == "Fig. 1")
    assert fig1["kind"] == "figure" and fig1["page"] == 2 and len(fig1["bbox"]) == 4
    assert fig1["caption"].startswith("Fig. 1") and "FINAL-CAPTION-WORD" in fig1["caption"]
    assert api.get(f"/papers/{pid}/figures", params={"page": 3}).json()["items"] == []

    eqs = api.get(f"/papers/{pid}/equations").json()["items"]
    assert eqs and eqs[0]["number"] == "1" and eqs[0]["page"] == 2 and "R(v1" in eqs[0]["text"]


def test_region_and_equation_images_are_real_pngs(api, tmp_path):
    pid = _import(api, tmp_path)
    eq = api.get(f"/papers/{pid}/equations").json()["items"][0]
    r = api.get(f"/papers/{pid}/equations/{eq['id']}/image")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    big = api.get(f"/papers/{pid}/equations/{eq['id']}/image", params={"dpi": 300}).content
    assert len(big) > len(r.content)  # 分辨率越高图越大

    x0, y0, x1, y1 = eq["bbox"]
    crop = api.get(f"/papers/{pid}/regions/image", params={"page": 2, "x0": x0, "y0": y0, "x1": x1, "y1": y1})
    assert crop.status_code == 200 and crop.content[:4] == b"\x89PNG"
    page = api.get(f"/papers/{pid}/regions/image", params={"page": 1, "dpi": 72})
    assert page.status_code == 200 and len(page.content) > 1000

    fig = api.get(f"/papers/{pid}/figures").json()["items"][0]
    assert api.get(f"/papers/{pid}/figures/{fig['id']}/image").content[:4] == b"\x89PNG"


def test_region_image_validation(api, tmp_path):
    pid = _import(api, tmp_path)
    assert api.get(f"/papers/{pid}/regions/image", params={"page": 99}).status_code == 400
    assert api.get(f"/papers/{pid}/regions/image", params={"page": 1, "x0": 1, "y0": 1}).status_code == 400  # 坐标不完整
    assert api.get(f"/papers/{pid}/regions/image", params={"page": 1, "x0": 900, "y0": 900, "x1": 950, "y1": 950}).status_code == 400
    assert api.get(f"/papers/{pid}/regions/image", params={"page": 1, "dpi": 9999}).status_code == 400  # 超出范围
    assert api.get(f"/papers/{pid}/equations/99999/image").status_code == 404
    assert api.get("/papers/nope/figures").status_code == 404


def test_region_svg_exports_a_cropped_vector_figure(imported):
    api, paper = imported
    r = api.get(f"/papers/{paper}/regions/svg", params={"page": 1, "x0": 50, "y0": 50, "x1": 300, "y1": 200})
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    head = r.text[: r.text.find(">") + 1]
    assert "<svg" in head and 'viewBox="' in head and 'width="' in head
    assert api.get(f"/papers/{paper}/regions/svg", params={"page": 99, "x0": 0, "y0": 0, "x1": 10, "y1": 10}).status_code == 400
    assert api.get(f"/papers/{paper}/regions/svg", params={"page": 1, "x0": 5000, "y0": 5000, "x1": 5100, "y1": 5100}).status_code == 400  # 区域在页面外


def test_table_equation_figure_images_can_be_vector(api, sample_pdf):
    """表格、公式、图的区域图都能要矢量（SVG），放大不糊；不要时仍然是 PNG。"""
    pid = api.import_pdf(sample_pdf)["paper_id"]
    with api.ctx.db.session() as conn:  # 样本 PDF 里没有图和公式：补两条记录，让接口真的走到
        conn.execute("INSERT INTO figures(paper_id, page, x0, y0, x1, y1, kind, label) VALUES (?, 1, 60, 80, 300, 220, 'figure', 'Fig. 1')", (pid,))
        conn.execute("INSERT INTO equations(paper_id, page, x0, y0, x1, y1, text) VALUES (?, 1, 60, 240, 300, 270, 'x')", (pid,))
    fid = api.get(f"/papers/{pid}/figures").json()["items"][0]["id"]
    eid = api.get(f"/papers/{pid}/equations").json()["items"][0]["id"]
    for kind, rid in (("figures", fid), ("equations", eid)):
        r = api.get(f"/papers/{pid}/{kind}/{rid}/image", params={"vector": "true"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml") and b"<svg" in r.content[:600], kind
        assert api.get(f"/papers/{pid}/{kind}/{rid}/image").headers["content-type"] == "image/png"
    assert api.get(f"/papers/{pid}/figures/99999/image", params={"vector": "true"}).status_code == 404
    assert api.get(f"/papers/{pid}/tables/99999/image", params={"vector": "true"}).status_code == 404
