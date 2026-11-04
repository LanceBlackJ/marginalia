from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pdfgen  # noqa: E402
from fakellm import FakeLLM  # noqa: E402

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(scope="session")
def sample_pdf(tmp_path_factory) -> Path:
    return pdfgen.build_paper(tmp_path_factory.mktemp("pdfs") / "dar.pdf")


@pytest.fixture(scope="session")
def scanned_pdf(tmp_path_factory) -> Path:
    return pdfgen.build_scanned(tmp_path_factory.mktemp("pdfs2") / "scan.pdf")


@pytest.fixture(scope="session")
def rotated_pdf(tmp_path_factory) -> Path:
    return pdfgen.build_rotated(tmp_path_factory.mktemp("pdfs3") / "rot.pdf")


@pytest.fixture(scope="session")
def chinese_pdf(tmp_path_factory) -> Path:
    return pdfgen.build_chinese(tmp_path_factory.mktemp("pdfs4") / "zh.pdf")


class Api:
    """带鉴权头的 TestClient 封装。"""

    def __init__(self, client: TestClient, app):
        self.c = client
        self.app = app
        self.ctx = app.state.ctx

    def get(self, path, **kw):
        return self.c.get("/api" + path, headers=AUTH, **kw)

    def post(self, path, json=None, **kw):
        return self.c.post("/api" + path, headers=AUTH, json=json, **kw)

    def put(self, path, json=None, **kw):
        return self.c.put("/api" + path, headers=AUTH, json=json, **kw)

    def patch(self, path, json=None, **kw):
        return self.c.patch("/api" + path, headers=AUTH, json=json, **kw)

    def delete(self, path, **kw):
        return self.c.delete("/api" + path, headers=AUTH, **kw)

    def wait_job(self, job_id: str, timeout: float = 30) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.get(f"/jobs/{job_id}").json()
            if job["status"] in ("completed", "failed", "cancelled"):
                return job
            time.sleep(0.05)
        raise TimeoutError(job_id)

    def sse(self, path: str, body: dict) -> list[tuple[str, dict]]:
        events: list[tuple[str, dict]] = []
        with self.c.stream("POST", "/api" + path, headers=AUTH, json=body) as r:
            assert r.status_code == 200, r.read()
            name = None
            for line in r.iter_lines():
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:") and name:
                    events.append((name, json.loads(line[5:].strip())))
                    name = None
        return events

    def import_pdf(self, path: Path) -> dict:
        job = self.wait_job(self.post("/import/files", {"paths": [str(path)]}).json()["job_id"])
        assert job["status"] == "completed", job
        return job["result"]["items"][0]

    def configure_ai(self, fast="fast-m", strong="strong-m", **extra):
        r = self.put("/settings/ai", {"fast_model": fast, "strong_model": strong, "base_url": "https://llm.test/v1", **extra})
        assert r.status_code == 200, r.text
        assert self.put("/settings/ai/secret", {"api_key": "sk-test-secret"}).status_code == 200


def make_api(tmp_path, *, handler=None) -> tuple[Api, FakeLLM]:
    from paperdesk.app import create_app

    fake = FakeLLM()
    sync = httpx.MockTransport(handler) if handler else httpx.MockTransport(lambda r: httpx.Response(503))
    app = create_app(tmp_path / "data", TOKEN, sync_transport=sync, async_transport=fake.transport)
    return Api(TestClient(app), app), fake


@pytest.fixture
def api(tmp_path):
    a, fake = make_api(tmp_path)
    a.fake = fake
    with a.c:
        yield a


@pytest.fixture
def imported(api, sample_pdf):
    """已导入示例论文并配置好假 LLM 的环境。"""
    item = api.import_pdf(sample_pdf)
    assert item["status"] == "imported", item
    api.configure_ai()
    return api, item["paper_id"]
