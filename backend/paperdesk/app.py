"""FastAPI 应用工厂。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .api import ai as ai_api
from .api import annotations, deps, imports, insight, jobs, papers, reviews as reviews_api, structure, system
from .api import compare as compare_api
from .api import discovery as discovery_api
from .api import knowledge as knowledge_api
from .api import export as export_api
from .api import code as code_api
from .api import vision as vision_api
from .api import supplements as supplements_api
from .api import mindmap as mindmap_api
from .api import findings as findings_api
from .api import quiz as quiz_api
from .api import repro as repro_api
from .api import folders as folders_api
from .api import references as references_api
from .materials import jobs as materials_jobs
from .config import Paths
from .context import AppContext
from .db import Database
from .jobs import JobManager, MAX_WORKERS
from .library import import_jobs
from .library import reviews as _reviews_schema  # noqa: F401
from .library import layout_boxes as _layout_boxes  # noqa: F401
from .util import ApiError

log = logging.getLogger("paperdesk")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _configure_jobs(ctx: AppContext, max_workers: int) -> JobManager:
    manager = JobManager(ctx, max_workers=max_workers)
    ctx.jobs = manager
    import_jobs.register(manager)
    ai_api.register_jobs(manager)
    compare_api.register_jobs(manager)
    knowledge_api.register_jobs(manager)
    code_api.register_jobs(manager)
    vision_api.register_jobs(manager)
    materials_jobs.register(manager)
    mindmap_api.register_jobs(manager)
    findings_api.register_jobs(manager)
    repro_api.register_jobs(manager)
    manager.recover()
    return manager


def create_app(
    data_dir: Path,
    token: str | None,
    *,
    sync_transport: httpx.BaseTransport | None = None,
    async_transport: httpx.AsyncBaseTransport | None = None,
    job_workers: int = MAX_WORKERS,
    web_root: Path | None = None,
) -> FastAPI:
    paths = Paths(Path(data_dir))
    paths.ensure()
    db = Database(paths.db_path)
    db.migrate()
    ctx = AppContext(paths=paths, db=db, sync_transport=sync_transport, async_transport=async_transport)
    manager = _configure_jobs(ctx, job_workers)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        manager.shutdown()

    app = FastAPI(title="Marginalia Backend", version=__version__, lifespan=lifespan)
    app.state.ctx = ctx
    app.state.token = token
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError):
        return _error(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        return _error(400, "bad_request", f"请求参数无效：{loc} {first.get('msg', '')}".strip())

    @app.exception_handler(StarletteHTTPException)
    async def _http(_req: Request, exc: StarletteHTTPException):
        code = "not_found" if exc.status_code == 404 else "bad_request"
        return _error(exc.status_code, code, "资源不存在" if exc.status_code == 404 else str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, exc: Exception):
        log.exception("unhandled error")
        return _error(500, "internal", f"内部错误：{type(exc).__name__}")

    @app.get("/api/health")
    def health():
        build = ""
        if web_root:
            try:
                build = str((web_root / "index.html").stat().st_mtime_ns)
            except OSError:
                pass
        return {"status": "ok", "version": __version__, "web_version": build}

    protected = [Depends(deps.require_token)]
    for module in (papers, folders_api, reviews_api, imports, jobs, annotations, system, ai_api, insight, structure):
        app.include_router(module.router, prefix="/api", dependencies=protected)
    for module in (supplements_api,):
        app.include_router(module.router, prefix="/api", dependencies=protected)
    for module in (discovery_api, compare_api, knowledge_api, export_api):
        app.include_router(module.router, prefix="/api", dependencies=protected)
    for module in (code_api,):
        app.include_router(module.router, prefix="/api", dependencies=protected)
    for module in (vision_api,):
        app.include_router(module.router, prefix="/api", dependencies=protected)
    for module in (references_api,):
        app.include_router(module.router, prefix="/api", dependencies=protected)
    for module in (repro_api,):
        app.include_router(module.router, prefix="/api", dependencies=protected)
    for module in (quiz_api,):
        app.include_router(module.router, prefix="/api", dependencies=protected)
    for module in (mindmap_api, findings_api):
        app.include_router(module.router, prefix="/api", dependencies=protected)

    # 手机上的 Web/PWA 直接从电脑端读取当前构建版本，API 和页面使用同一个来源。
    if web_root and web_root.is_dir():
        assets = web_root / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="web-assets")

        @app.get("/manifest.webmanifest", include_in_schema=False)
        async def manifest():
            return FileResponse(web_root / "manifest.webmanifest", headers={"Cache-Control": "no-store"})

        @app.get("/sw.js", include_in_schema=False)
        async def service_worker():
            return FileResponse(web_root / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-store"})

        @app.get("/", include_in_schema=False)
        async def web_index():
            return FileResponse(web_root / "index.html", headers={"Cache-Control": "no-store"})

        @app.get("/{path:path}", include_in_schema=False)
        async def web_fallback(path: str):
            if path == "api" or path.startswith("api/"):
                raise HTTPException(status_code=404, detail="资源不存在")
            candidate = web_root / path
            if candidate.is_file() and web_root in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(web_root / "index.html", headers={"Cache-Control": "no-store"})

    return app
