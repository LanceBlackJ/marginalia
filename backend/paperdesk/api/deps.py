from __future__ import annotations

import hmac

from fastapi import Header, Request

from ..context import AppContext
from ..util import ApiError


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def require_token(request: Request, authorization: str | None = Header(default=None)) -> None:
    expected: str | None = request.app.state.token
    if expected is None:
        return
    supplied = ""
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise ApiError("unauthorized", "未授权的请求", 401)
