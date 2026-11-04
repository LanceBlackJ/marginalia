from __future__ import annotations

import asyncio
import time

import httpx
import pytest


def _slow_gateway(api, seconds: float = 30.0):
    """让网关故意拖很久才响应：取消必须能打断这种正在进行的请求。"""

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(seconds)
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    api.ctx.async_transport = httpx.MockTransport(slow)


def _cancel_and_time(api, job_id: str, *, warmup: float = 1.0) -> tuple[dict, float]:
    time.sleep(warmup)  # 让任务进入网关调用
    t0 = time.time()
    api.post(f"/jobs/{job_id}/cancel")
    while time.time() - t0 < 15:
        j = api.get(f"/jobs/{job_id}").json()
        if j["status"] in ("completed", "failed", "cancelled"):
            return j, time.time() - t0
        time.sleep(0.1)
    return api.get(f"/jobs/{job_id}").json(), time.time() - t0


@pytest.mark.parametrize("kind", ["translation", "analysis"])
def test_cancel_interrupts_a_gateway_call_in_flight(imported, kind):
    api, pid = imported
    api.put("/settings/ai", {"max_retries": 0, "timeout_s": 120, "auto_analyze": False})
    _slow_gateway(api)
    path = f"/papers/{pid}/translation" if kind == "translation" else f"/papers/{pid}/analysis/run"
    job_id = api.post(path).json()["job_id"]
    assert job_id
    j, took = _cancel_and_time(api, job_id)
    assert j["status"] == "cancelled", j
    assert took < 3.0, f"取消用了 {took:.1f}s"  # 网关要 30 秒才响应，但取消后 3 秒内就必须结束


def test_cancelled_translation_keeps_nothing_half_written(imported):
    api, pid = imported
    api.put("/settings/ai", {"max_retries": 0, "timeout_s": 120, "auto_analyze": False})
    _slow_gateway(api)
    job_id = api.post(f"/papers/{pid}/translation").json()["job_id"]
    _cancel_and_time(api, job_id)
    st = api.get(f"/papers/{pid}/translation").json()
    assert st["done"] == 0 and not st["complete"]  # 没有半截的译文入库
    # 取消后可以再次启动（不会被卡住的旧任务挡住）
    api.ctx.async_transport = None
    assert api.post(f"/papers/{pid}/translation").json()["job_id"]


def test_run_async_unit_cancels_promptly():
    import threading

    from paperdesk.jobs import JobCancelled, JobContext

    ev = threading.Event()
    ctx = JobContext(None, "j", {}, ev)  # type: ignore[arg-type]

    async def forever():
        await asyncio.sleep(60)

    threading.Timer(0.4, ev.set).start()
    t0 = time.time()
    with pytest.raises(JobCancelled):
        ctx.run_async(forever())
    assert time.time() - t0 < 2.5
