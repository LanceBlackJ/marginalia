"""本地版面模型：缓存、导入/重新解析里的使用，以及任何失败都退回规则解析。用假检测引擎，不下载、不跑真实模型。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pdfgen  # noqa: E402

from paperdesk.library import layout_boxes  # noqa: E402
from paperdesk.parsing import layout_model  # noqa: E402


@pytest.fixture
def boxes_on(monkeypatch):
    monkeypatch.setattr(layout_boxes, "ENABLED", True)
    yield
    layout_model.set_engine(None)


def _fake_table_engine(calls):
    def engine(img):
        calls.append(img.shape)
        return [("table", 0.9, 0.1, 0.5, 0.9, 0.7)]  # 每页中间一块表

    return engine


@pytest.fixture(scope="module")
def paper(tmp_path_factory) -> Path:
    return pdfgen.build_paper(tmp_path_factory.mktemp("lb") / "dar.pdf")


def test_detect_returns_boxes_per_page_with_the_injected_engine(boxes_on, api, paper):
    calls: list = []
    layout_model.set_engine(_fake_table_engine(calls))
    boxes = layout_boxes.detect(api.ctx, paper)
    assert boxes and all(v and v[0]["kind"] == "table" and v[0]["conf"] == 0.9 for v in boxes.values())
    assert len(calls) == len(boxes)


def test_import_saves_the_boxes_and_reparse_reuses_them_without_running_the_model_again(boxes_on, api, paper):
    calls: list = []
    layout_model.set_engine(_fake_table_engine(calls))
    item = api.import_pdf(paper)
    pid = item["paper_id"]
    n_import = len(calls)
    assert n_import >= 1
    cached = layout_boxes.load(api.ctx, pid)
    assert cached and set(cached) == set(range(1, len(cached) + 1))
    layout_model.set_engine(lambda img: (_ for _ in ()).throw(AssertionError("缓存有的时候不该再跑模型")))
    boxes = layout_boxes.ensure(api.ctx, pid, api.ctx.paths.paper_dir(pid) / "original.pdf")
    assert boxes == cached and len(calls) == n_import


def test_a_failing_detector_never_breaks_the_import(boxes_on, api, paper):
    def boom(img):
        raise RuntimeError("model exploded")

    layout_model.set_engine(boom)
    item = api.import_pdf(paper)
    assert item["status"] == "imported"
    assert layout_boxes.load(api.ctx, item["paper_id"]) == {}  # 没有缓存，下次重新解析会再试


def test_missing_model_and_failed_download_fall_back_to_the_rules(boxes_on, api, paper, monkeypatch):
    monkeypatch.setattr(layout_model, "runtime_ok", lambda: True)
    monkeypatch.setattr(layout_model, "download", lambda data_dir, progress=None: False)
    msgs: list[str] = []
    assert layout_boxes.detect(api.ctx, paper, msgs.append) == {}
    assert any("下载版面模型" in m for m in msgs) and any("按规则解析" in m for m in msgs)


def test_no_runtime_means_no_download_attempt_and_no_boxes(boxes_on, api, paper, monkeypatch):
    monkeypatch.setattr(layout_model, "runtime_ok", lambda: False)
    monkeypatch.setattr(layout_model, "download", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该下载")))
    assert layout_boxes.detect(api.ctx, paper) == {}


def test_incomplete_cache_is_ignored(boxes_on, api, paper):
    layout_model.set_engine(_fake_table_engine([]))
    item = api.import_pdf(paper)
    pid = item["paper_id"]
    with api.ctx.db.session() as conn:
        conn.execute("DELETE FROM page_boxes WHERE paper_id = ? AND page = 1", (pid,))
    assert layout_boxes.load(api.ctx, pid) == {}


def test_download_rejects_a_wrong_sized_file_and_keeps_no_partial_file(tmp_path, monkeypatch):
    import httpx

    class FakeStream:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_bytes(self, n):
            yield b"x" * 1000

    monkeypatch.setattr(httpx, "stream", lambda *a, **k: FakeStream())
    assert layout_model.download(tmp_path) is False
    assert not layout_model.model_path(tmp_path).exists() and not list((tmp_path / "models").glob("*.part"))


def test_reparse_detects_and_caches_when_the_import_had_no_model(api, paper, monkeypatch):
    item = api.import_pdf(paper)  # 导入时开关是关的：没有缓存
    pid = item["paper_id"]
    assert layout_boxes.load(api.ctx, pid) == {}
    monkeypatch.setattr(layout_boxes, "ENABLED", True)
    layout_model.set_engine(_fake_table_engine([]))
    try:
        job = api.wait_job(api.post(f"/papers/{pid}/reparse", {"force": True}).json()["job_id"], timeout=60)
        assert job["status"] == "completed", job
        assert layout_boxes.load(api.ctx, pid)
    finally:
        layout_model.set_engine(None)


def test_the_page_boxes_table_exists_in_a_freshly_created_app(api):
    """回归：模块是在导入函数里延迟导入的时候，表在数据库初始化之后才注册，生产环境里没有这张表（测试里因为顶部导入没暴露）。"""
    with api.ctx.db.session() as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'page_boxes'").fetchone() is not None


def test_deleting_a_paper_removes_its_cached_boxes(boxes_on, api, paper):
    layout_model.set_engine(_fake_table_engine([]))
    pid = api.import_pdf(paper)["paper_id"]
    assert layout_boxes.load(api.ctx, pid)
    assert api.delete(f"/papers/{pid}").status_code == 200
    with api.ctx.db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM page_boxes WHERE paper_id = ?", (pid,)).fetchone()[0] == 0


def test_numpy_numbers_from_the_real_model_are_stored_as_plain_json(boxes_on, api, paper):
    """回归：真实模型输出 numpy.float32，直接 json.dumps 会失败，缓存写不进去（以前被“失败不影响导入”吞掉了，没人发现）。"""
    import json

    import numpy as np

    layout_model.set_engine(lambda img: [("table", np.float32(0.9), np.float32(0.1), np.float32(0.5), np.float32(0.9), np.float32(0.7))])
    boxes = layout_boxes.detect(api.ctx, paper)
    json.dumps(boxes)  # 不该抛异常
    assert all(type(v) is float for b in boxes.values() for v in (b[0]["conf"], *b[0]["box"]))
    pid = api.import_pdf(paper)["paper_id"]
    assert layout_boxes.load(api.ctx, pid), "缓存没有写进去"
