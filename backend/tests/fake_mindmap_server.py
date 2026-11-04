"""F4 联调用的假 LLM 服务（同 fake_llm_server，但额外支持思维导图与研究提示的输出），不是产品功能。

    python tests/fake_mindmap_server.py --port 18799

然后在 Marginalia 设置里把 API 地址设为 http://127.0.0.1:18799/v1，随便填一个 Key，
快速/强模型填 fast-m / strong-m。回答内容由 fakellm.FakeLLM 确定性地生成（引用上下文里真实存在的段落编号）。
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

from fake_mindmap_llm import FakeMindmapLLM  # noqa: E402

app = FastAPI()
fake = FakeMindmapLLM()


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    user = "\n".join(m["content"] for m in messages if m["role"] == "user")
    model = body.get("model", "")
    text = fake._answer(model, system, user)
    usage = {"prompt_tokens": max(1, len(system + user) // 4), "completion_tokens": max(1, len(text) // 3)}
    if not body.get("stream"):
        return JSONResponse({"model": model, "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], "usage": usage})

    async def gen():
        await asyncio.sleep(0.4)
        for i in range(0, len(text), 6):
            yield "data: " + json.dumps({"choices": [{"delta": {"content": text[i : i + 6]}}]}, ensure_ascii=False) + "\n\n"
            await asyncio.sleep(0.03)
        yield "data: " + json.dumps({"choices": [], "usage": usage}) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18799)
    args = ap.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
