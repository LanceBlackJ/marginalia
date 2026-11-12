"""FastAPI 应用工厂。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .api import ai as ai_api
from .api import annotations, deps, imports, insight, jobs, papers, structure, system
from .api import compare as compare_api  # F3：多论文比较
from .api import discovery as discovery_api  # F3：学术发现
from .api import knowledge as knowledge_api  # F3：主题知识库/画像/导出
from .api import export as export_api  # 引文与阅读成果导出
from .api import code as code_api  # F2：代码理解
from .api import vision as vision_api  # F5：公式/图表理解
from .api import supplements as supplements_api  # F1：补充材料与数据集
from .api import mindmap as mindmap_api  # F4：AI 思维导图
from .api import findings as findings_api  # F4：主动研究提示
from .api import quiz as quiz_api  # 读完之后的自测（选择题）
from .api import repro as repro_api  # 第 4 阶段：复现（评估/计划/结果对比）
from .api import folders as folders_api  # 文库：文件夹、智能视图、建议归类
from .api import references as references_api  # 正文引用 → 参考文献详情/摘要/AI 概括
from .materials import jobs as materials_jobs  # F1
from .config import Paths
from .context import AppContext
from .db import Database
from .jobs import JobManager
from .library import import_jobs
from .library import layout_boxes as _layout_boxes  # noqa: F401  导入时就注册 page_boxes 表（必须在数据库初始化之前）
from .util import ApiError

log = logging.getLogger("paperdesk")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_app(
    data_dir: Path,
    token: str | None,
    *,
    sync_transport: httpx.BaseTransport | None = None,
    async_transport: httpx.AsyncBaseTransport | None = None,
    job_workers: int = 2,
) -> FastAPI:
    paths = Paths(Path(data_dir))
    paths.ensure()
    db = Database(paths.db_path)
    db.migrate()
    ctx = AppContext(paths=paths, db=db, sync_transport=sync_transport, async_transport=async_transport)
    manager = JobManager(ctx, max_workers=job_workers)
    ctx.jobs = manager
    import_jobs.register(manager)
    ai_api.register_jobs(manager)
    compare_api.register_jobs(manager)  # F3
    knowledge_api.register_jobs(manager)  # F3
    code_api.register_jobs(manager)  # F2
    vision_api.register_jobs(manager)  # F5
    materials_jobs.register(manager)  # F1
    mindmap_api.register_jobs(manager)  # F4
    findings_api.register_jobs(manager)  # F4
    repro_api.register_jobs(manager)  # 复现
    manager.recover()

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
        return {"status": "ok", "version": __version__}

    protected = [Depends(deps.require_token)]
    for module in (papers, folders_api, imports, jobs, annotations, system, ai_api, insight, structure):
        app.include_router(module.router, prefix="/api", dependencies=protected)
    for module in (supplements_api,):  # F1
        app.include_router(module.router, prefix="/api", dependencies=protected)
    for module in (discovery_api, compare_api, knowledge_api, export_api):  # F3
        app.include_router(module.router, prefix="/api", dependencies=protected)
    for module in (code_api,):  # F2
        app.include_router(module.router, prefix="/api", dependencies=protected)
    for module in (vision_api,):  # F5
        app.include_router(module.router, prefix="/api", dependencies=protected)
    for module in (references_api,):  # 参考文献
        app.include_router(module.router, prefix="/api", dependencies=protected)
    for module in (repro_api,):  # 复现
        app.include_router(module.router, prefix="/api", dependencies=protected)
    for module in (quiz_api,):  # 自测
        app.include_router(module.router, prefix="/api", dependencies=protected)
    for module in (mindmap_api, findings_api):  # F4
        app.include_router(module.router, prefix="/api", dependencies=protected)
    return app
