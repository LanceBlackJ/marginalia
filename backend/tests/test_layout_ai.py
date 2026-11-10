"""每页视觉划分版面：区域清洗、解析时的采用与退回、后台任务与自动重新解析。
假视觉模型只能证明工程链路，不能证明真实模型划得准；“完美模型”的划分用规则解析的结果反推。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pdfgen  # noqa: E402
from conftest import TOKEN, Api  # noqa: E402
from fake_vision_llm import VisionFakeLLM  # noqa: E402
from paperdesk.parsing import layout_hints as H  # noqa: E402
from paperdesk.parsing.pdf_parser import parse_pdf  # noqa: E402
from paperdesk.vision import layout_ai  # noqa: E402


def _perfect_hints(parsed) -> dict[int, list[dict]]:
    """“完美的视觉模型”：按规则解析出的每个段落框出一个区域。"""
    role_of = {"paragraph": "paragraph", "caption": "caption", "reference": "reference", "front": "sidebar", "formula": "formula", "figure_text": "figure"}
    raw: dict[int, list[dict]] = {}
    for q in parsed.passages:
        pw, ph = parsed.pages[q.page - 1]
        raw.setdefault(q.page, []).append({"role": role_of.get(q.kind, "paragraph"),
                                           "box": [q.bbox[0] / pw * 1000, q.bbox[1] / ph * 1000, q.bbox[2] / pw * 1000, q.bbox[3] / ph * 1000]})
    return {pg: H.normalize_regions(v) for pg, v in raw.items()}


# ---------------- 区域清洗 ----------------
def test_normalize_regions_drops_garbage_and_keeps_order():
    raw = {"regions": [
        {"role": "heading", "box": [100, 50, 400, 80]},
        {"role": "nonsense", "box": [0, 0, 10, 10]},          # 不认识的角色
        {"role": "paragraph", "box": [500, 100, 100, 600]},   # 坐标反了：对调
        {"role": "paragraph", "box": [0, 0, 5, 5]},           # 太小
        {"role": "figure", "box": [0, 0, 2000, 500]},         # 越界：夹到 1000
        {"role": "table", "box": "oops"},
        "not a dict",
    ]}
    out = H.normalize_regions(raw)
    assert [r["role"] for r in out] == ["heading", "paragraph", "figure"]
    assert out[1]["box"] == [0.1, 0.1, 0.5, 0.6] and out[2]["box"][2] == 1.0
    assert H.normalize_regions("nope") == [] and H.normalize_regions({"regions": 3}) == []
    assert layout_ai.parse_reply("好的：" + json.dumps(raw)) != [] and layout_ai.parse_reply("没有 JSON") == []


# ---------------- 解析：采用与退回 ----------------
@pytest.fixture(scope="module")
def paper(tmp_path_factory) -> Path:
    return pdfgen.build_paper(tmp_path_factory.mktemp("lh") / "dar.pdf")


def test_perfect_hints_give_the_same_structure_as_the_rules(paper):
    base = parse_pdf(paper)
    hinted = parse_pdf(paper, layout_hints=_perfect_hints(base))
    assert [s.title for s in hinted.sections] == [s.title for s in base.sections]
    assert hinted.metadata.title == base.metadata.title and hinted.metadata.authors == base.metadata.authors
    assert [(p.kind, p.text) for p in hinted.passages] == [(p.kind, p.text) for p in base.passages]


def test_roles_override_the_rules_for_title_abstract_and_headings(paper):
    base = parse_pdf(paper)
    hints = _perfect_hints(base)
    # 把摘要段改标成 abstract、正文里一个段落改标成 heading：解析按角色走
    abs_idx = next(i for i, p in enumerate(base.passages) if p.page == 1 and p.text.startswith("Cascading failures remain"))
    q = base.passages[abs_idx]
    pw, ph = base.pages[0]
    hints[1] = [r for r in hints[1]]
    for r in hints[1]:
        if abs(r["box"][1] - q.bbox[1] / ph) < 0.01 and abs(r["box"][0] - q.bbox[0] / pw) < 0.01:
            r["role"] = "abstract"
    parsed = parse_pdf(paper, layout_hints=hints)
    assert parsed.metadata.abstract and parsed.metadata.abstract.startswith("Cascading failures remain")


def test_a_box_over_both_columns_is_still_split_by_the_column_gutter(paper):
    """模型把左右两栏框成一块：区域内部仍按栏沟分栏，阅读顺序不乱。"""
    base = parse_pdf(paper)
    one_box = {pg: [{"role": "paragraph", "box": [0.0, 0.0, 1.0, 1.0]}] for pg in (1, 2, 3)}
    parsed = parse_pdf(paper, layout_hints=one_box)
    texts = " ".join(p.text for p in parsed.passages if p.page == 1)
    assert texts.index("Cascading failures occur") < texts.index("Our method, Dual Attention Routing")
    assert len(parsed.passages) >= len(base.passages) - 4


def test_a_page_with_too_many_unassigned_words_falls_back_to_the_rules(paper):
    base = parse_pdf(paper)
    tiny = {pg: [{"role": "paragraph", "box": [0.0, 0.0, 0.05, 0.03]}] for pg in range(1, 6)}  # 没框住任何文字
    parsed = parse_pdf(paper, layout_hints=tiny)
    assert [(p.kind, p.text) for p in parsed.passages] == [(p.kind, p.text) for p in base.passages]


def test_header_and_footer_regions_are_dropped(paper):
    base = parse_pdf(paper)
    hints = _perfect_hints(base)
    hints[2].append({"role": "footer", "box": [0.40, 0.955, 0.60, 0.99]})  # 页码所在位置
    parsed = parse_pdf(paper, layout_hints=hints)
    assert [(p.kind, p.text) for p in parsed.passages] == [(p.kind, p.text) for p in base.passages]


def test_formula_region_is_one_formula_block():
    """显示公式的分子、分母、上下限各占一行：视觉划分里整个公式区域是一个块，不被拆开。"""
    import pymupdf
    import tempfile

    doc = pymupdf.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((72, 100), "We define the score as follows in the paper and discuss it below at length here, with many more words so that the page has a text layer.", fontsize=10)
    pg.insert_text((120, 190), "R = ", fontsize=10)
    pg.insert_text((150, 180), "sigma(G - v)", fontsize=8)
    pg.draw_line((148, 184), (215, 184), width=0.5)
    pg.insert_text((165, 196), "sigma(G)", fontsize=8)
    pg.insert_text((72, 260), "where the numerator and the denominator are sizes of components in the graph, as explained in the following sections of the paper at length.", fontsize=10)
    path = Path(tempfile.mkdtemp()) / "f.pdf"
    doc.save(str(path))
    doc.close()
    hints = {1: H.normalize_regions([
        {"role": "paragraph", "box": [100, 100, 900, 130]},
        {"role": "formula", "box": [190, 205, 400, 250]},
        {"role": "paragraph", "box": [100, 300, 900, 330]},
    ])}
    parsed = parse_pdf(path, layout_hints=hints)
    assert [p.kind for p in parsed.passages] == ["paragraph", "formula", "paragraph"], [(p.kind, p.text) for p in parsed.passages]
    assert "sigma(G - v)" in parsed.passages[1].text and "sigma(G)" in parsed.passages[1].text


# ---------------- 后台任务 ----------------
@pytest.fixture
def lapi(tmp_path):
    from paperdesk.app import create_app

    fake = VisionFakeLLM()
    app = create_app(tmp_path / "data", TOKEN, sync_transport=httpx.MockTransport(lambda r: httpx.Response(503)), async_transport=fake.transport)
    api = Api(TestClient(app), app)
    api.fake = fake
    with api.c:
        yield api


def test_layout_job_caches_pages_and_reparses_with_them(lapi, paper):
    api = lapi
    pid = api.import_pdf(paper)["paper_id"]
    api.configure_ai()
    api.fake.layout_reply = json.dumps({"regions": [{"role": "paragraph", "box": [0, 0, 1000, 1000]}]})
    job_id = layout_ai.enqueue(api.ctx, pid)
    assert job_id
    job = api.wait_job(job_id)
    assert job["status"] == "completed" and job["result"]["ok"] == 5 and job["result"]["failed"] == 0, job
    st = layout_ai.status(api.ctx, pid)
    assert st["pages_ok"] == 5 and st["pages_failed"] == 0
    assert any(i["task_type"] == "vision_layout" for i in api.get("/privacy/requests").json()["items"])
    rep = job["result"].get("reparse_job_id")
    assert rep and api.wait_job(rep)["status"] == "completed"
    assert layout_ai.status(api.ctx, pid)["applied"] is True
    n = api.fake.layout_calls
    assert layout_ai.enqueue(api.ctx, pid) is None and api.fake.layout_calls == n  # 每页都有结论：不再调用模型


def test_layout_job_with_unusable_replies_does_not_reparse_and_does_not_block_other_steps(lapi, paper):
    api = lapi
    pid = api.import_pdf(paper)["paper_id"]
    api.configure_ai()  # fake.layout_reply 为 None：模型不按格式回答
    job = api.wait_job(layout_ai.enqueue(api.ctx, pid))
    assert job["status"] == "completed" and job["result"]["ok"] == 0 and job["result"]["failed"] == 5 and "reparse_job_id" not in job["result"]
    assert layout_ai.load_hints(api.ctx, pid) == {} and layout_ai.status(api.ctx, pid)["applied"] is False
    from paperdesk.library import processing

    assert processing._layout_pending(api.ctx, pid, fresh=True) is False  # 试过了：不再挡住公式、表格等步骤


def test_start_auto_does_layout_first_and_defers_the_other_steps(lapi, paper):
    api = lapi
    pid = api.import_pdf(paper)["paper_id"]
    api.configure_ai()
    from paperdesk.library import processing

    assert processing.start_auto(api.ctx, pid).get("layout") is None  # 打开旧论文：不会自动做视觉划分
    started = processing.start_auto(api.ctx, pid, fresh=True)
    assert started.get("layout") and "formulas" not in started and "index" not in started
    api.wait_job(started["layout"])
    again = processing.start_auto(api.ctx, pid)  # 划分试过了：这次照常启动其余步骤
    assert "layout" not in again or again["layout"] is None


def test_layout_api_status_and_start(lapi, paper):
    api = lapi
    pid = api.import_pdf(paper)["paper_id"]
    api.configure_ai()
    api.fake.layout_reply = json.dumps({"regions": [{"role": "paragraph", "box": [0, 0, 1000, 1000]}]})
    st = api.get(f"/papers/{pid}/layout-ai").json()
    assert st["pages_total"] == 5 and st["pages_ok"] == 0 and st["applied"] is False and st["active_job_id"] is None
    job_id = api.post(f"/papers/{pid}/layout-ai").json()["job_id"]
    assert job_id and api.wait_job(job_id)["status"] == "completed"
    assert api.get(f"/papers/{pid}/layout-ai").json()["pages_ok"] == 5
    assert api.post(f"/papers/{pid}/layout-ai").json()["job_id"] is None  # 都有结论了
    assert api.post(f"/papers/{pid}/layout-ai?force=true").json()["job_id"]  # force：全部重来
    assert api.get("/papers/nope/layout-ai").status_code == 404
