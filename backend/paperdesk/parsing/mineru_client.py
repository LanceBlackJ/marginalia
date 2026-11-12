"""MinerU 本地解析服务的客户端（Tasks API：POST /v1/tasks → PUT 上传 → 轮询 → 下载 zip）。

MinerU 装在独立的 Python 3.12 环境里，以 ``mineru-kit api-server`` 形式运行，本进程只经 HTTP 调用。
任何失败都抛 ``MineruUnavailable``，由调用方回退到规则解析。
"""
from __future__ import annotations

import io
import json
import os
import time
import zipfile
from pathlib import Path
from typing import Callable

import httpx

DEFAULT_URL = "http://127.0.0.1:8765"
ENABLED = True                      # 总开关；tests/conftest 默认关
TIMEOUT_S = 1800.0                  # 单篇最长等待（首次加载模型约 5 分钟）
_override: Callable[[Path], dict] | None = None   # 测试注入：返回 middle_json 字典


class MineruUnavailable(RuntimeError):
    pass


def set_engine(fn: Callable[[Path], dict] | None) -> None:
    global _override
    _override = fn


def base_url() -> str:
    return (os.environ.get("PAPERDESK_MINERU_URL") or DEFAULT_URL).rstrip("/")


def available() -> bool:
    if not ENABLED:
        return False
    if _override is not None:
        return True
    try:
        r = httpx.get(base_url() + "/openapi.json", timeout=2.0, trust_env=False)
        return r.status_code == 200
    except Exception:
        return False


def parse_pdf_file(path: Path, say: Callable[[str], None] | None = None,
                   timeout: float = TIMEOUT_S) -> dict:
    """解析一个 PDF，返回 middle_json 字典（页面坐标为 0~1 的归一化 bbox）。"""
    if _override is not None:
        return _override(path)
    say = say or (lambda _m: None)
    data = Path(path).read_bytes()
    try:
        with httpx.Client(timeout=60.0, trust_env=False) as c:
            r = c.post(base_url() + "/v1/tasks", json={
                "files": [{"file_name": "paper.pdf", "bytes": len(data)}],
                "tier": "standard"})
            if r.status_code not in (200, 201, 202):
                raise MineruUnavailable(f"提交失败 HTTP {r.status_code}: {r.text[:200]}")
            task = r.json()
            tid = task.get("task_id")
            f0 = (task.get("files") or [{}])[0]
            up = f0.get("upload") or task.get("upload")
            if not tid or not up:
                raise MineruUnavailable("服务未返回上传地址")
            u = up["url"]
            if u.startswith("/"):
                u = base_url() + u
            pr = c.put(u, content=data, headers=up.get("headers") or {})
            if pr.status_code >= 300:
                raise MineruUnavailable(f"上传失败 HTTP {pr.status_code}")
            say("MinerU 解析中…")
            deadline = time.time() + timeout
            result_url = None
            while time.time() < deadline:
                s = c.get(f"{base_url()}/v1/tasks/{tid}")
                s.raise_for_status()
                files = s.json().get("files") or []
                st = (files[0].get("status") if files else "") or ""
                if st == "completed":
                    result_url = files[0].get("result_url")
                    break
                if st in ("failed", "canceled"):
                    raise MineruUnavailable(f"解析{st}: {files[0].get('error')}")
                time.sleep(2.0)
            if not result_url:
                raise MineruUnavailable("等待 MinerU 超时")
            if result_url.startswith("/"):
                result_url = base_url() + result_url
            z = c.get(result_url, timeout=300.0)
            z.raise_for_status()
    except MineruUnavailable:
        raise
    except Exception as e:  # 网络、JSON、HTTP 状态等一律视为不可用
        raise MineruUnavailable(str(e)) from e
    try:
        with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
            name = next(n for n in zf.namelist() if n.endswith("middle_json.json") or n.endswith("middle.json"))
            return json.loads(zf.read(name).decode("utf-8"))
    except Exception as e:
        raise MineruUnavailable(f"结果解析失败: {e}") from e
