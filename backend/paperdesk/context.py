"""应用上下文：集中持有数据库、路径、密钥、任务管理器与可注入的 HTTP 传输层（便于测试）。"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

import httpx

from .config import Paths
from .db import Database

USER_AGENT = "Marginalia/0.1 (local research reader)"


class SecretStore:
    """API Key 仅保存在内存中；后端永不落盘。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, str] = {}

    def set(self, name: str, value: str) -> None:
        with self._lock:
            self._values[name] = value

    def get(self, name: str) -> str | None:
        with self._lock:
            return self._values.get(name) or None

    def clear(self, name: str) -> None:
        with self._lock:
            self._values.pop(name, None)

    def has(self, name: str) -> bool:
        return self.get(name) is not None

    def redact(self, text: str) -> str:
        """把文本里出现的任何已保存密钥替换成 ***（错误信息可能带出请求 URL）。"""
        with self._lock:
            values = [v for v in self._values.values() if v]
        for v in values:
            text = text.replace(v, "***")
        return text


_API_KEY_PARAM = re.compile(r"(api_key=)[^&\s'\"]+")


def redact_url_keys(text: object) -> str:
    """错误信息里如果带着请求 URL，抹掉其中的 api_key=…，避免密钥出现在界面或日志里。"""
    return _API_KEY_PARAM.sub(r"\1***", str(text))


OPENALEX_HOST = "api.openalex.org"
OPENALEX_KEY = "openalex_api_key"  # 可选：OpenAlex API key，只发给 api.openalex.org，不发往其它主机


S2_HOST = "api.semanticscholar.org"
S2_KEY = "s2_api_key"  # 可选：Semantic Scholar API Key（免费申请），只发给 api.semanticscholar.org，放在 x-api-key 请求头里


def _add_openalex_key(secrets: SecretStore, request: httpx.Request) -> None:
    """可选的数据源 Key：各自只发给自己的主机，绝不发往其它主机。"""
    if request.url.host == OPENALEX_HOST:
        key = secrets.get(OPENALEX_KEY)
        if key and "api_key" not in request.url.params:
            request.url = request.url.copy_merge_params({"api_key": key})
    elif request.url.host == S2_HOST:
        key = secrets.get(S2_KEY)
        if key and "x-api-key" not in request.headers:
            request.headers["x-api-key"] = key


@dataclass
class AppContext:
    paths: Paths
    db: Database
    secrets: SecretStore = field(default_factory=SecretStore)
    # 测试时注入 httpx.MockTransport；生产环境为 None（真实网络）
    sync_transport: httpx.BaseTransport | None = None
    async_transport: httpx.AsyncBaseTransport | None = None
    jobs: "object | None" = None  # JobManager，避免循环导入

    def http(self, timeout: float = 20.0) -> httpx.Client:
        return httpx.Client(
            timeout=timeout, follow_redirects=True, transport=self.sync_transport,
            headers={"User-Agent": USER_AGENT},
            event_hooks={"request": [lambda r: _add_openalex_key(self.secrets, r)]},
        )

    def async_http(self, timeout: float = 20.0) -> httpx.AsyncClient:
        async def hook(request: httpx.Request) -> None:
            _add_openalex_key(self.secrets, request)

        return httpx.AsyncClient(
            timeout=timeout, transport=self.async_transport, follow_redirects=True,
            headers={"User-Agent": USER_AGENT}, event_hooks={"request": [hook]},
        )
