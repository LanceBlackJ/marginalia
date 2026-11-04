"""仅用于手动联调的假视觉 LLM 服务（OpenAI 兼容，支持图片消息），不是产品功能。

    python tests/fake_vision_server.py --port 18800

用来在不连真实网关的情况下演练 scratchpad 里的 real_vision_check.py 与界面（回答由 VisionFakeLLM 确定性生成）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402

from fake_vision_llm import VisionFakeLLM  # noqa: E402
from fakellm import _flat  # noqa: E402

app = FastAPI()
fake = VisionFakeLLM()


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    system = "\n".join(_flat(m["content"]) for m in messages if m["role"] == "system")
    user = "\n".join(_flat(m["content"]) for m in messages if m["role"] == "user")
    model = body.get("model", "")
    text = fake._answer(model, system, user)
    usage = {"prompt_tokens": max(1, len(system + user) // 4), "completion_tokens": max(1, len(text) // 3)}
    if not body.get("stream"):
        return JSONResponse({"model": model, "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], "usage": usage})

    async def gen():
        await asyncio.sleep(0.3)
        for i in range(0, len(text), 8):
            yield "data: " + json.dumps({"choices": [{"delta": {"content": text[i : i + 8]}}]}, ensure_ascii=False) + "\n\n"
            await asyncio.sleep(0.02)
        yield "data: " + json.dumps({"choices": [], "usage": usage}) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18800)
    args = ap.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
