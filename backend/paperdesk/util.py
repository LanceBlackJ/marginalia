"""通用小工具：时间、ID、JSON、DOI 归一化、错误类型。"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class ApiError(Exception):
    """可直接展示给用户的业务错误，会被转换为 {"error": {...}} 响应。"""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9<>\[\]]+")


def normalize_doi(value: str | None) -> str | None:
    """归一化 DOI：去掉前缀、小写、去掉结尾标点。无法识别返回 None。"""
    if not value:
        return None
    text = _DOI_PREFIX.sub("", value.strip())
    m = DOI_RE.search(text)
    if not m:
        return None
    return m.group(0).rstrip(".,;:)]>").lower()


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
