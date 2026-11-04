from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..context import AppContext
from ..util import ApiError, normalize_doi
from .deps import get_ctx

router = APIRouter(prefix="/import", tags=["import"])
MAX_FILES = 5000


class FilesBody(BaseModel):
    paths: list[str]


class FolderBody(BaseModel):
    path: str
    recursive: bool = True


class DoiBody(BaseModel):
    dois: list[str]
    metadata_only: bool = False  # true：只保存题录，不下载开放获取全文（之后可用“附加 PDF”补上）


class UrlBody(BaseModel):
    url: str
    metadata_only: bool = False  # true：只保存题录（需要链接能解析出题录：arXiv 或 doi.org），不下载 PDF


@router.post("/files")
def import_files(body: FilesBody, ctx: AppContext = Depends(get_ctx)):
    paths = [p for p in body.paths if p and p.strip()]
    if not paths:
        raise ApiError("bad_request", "没有选择任何文件")
    if len(paths) > MAX_FILES:
        raise ApiError("bad_request", f"一次最多导入 {MAX_FILES} 个文件")
    title = f"导入 {Path(paths[0]).name}" if len(paths) == 1 else f"导入 {len(paths)} 个 PDF"
    job_id = ctx.jobs.submit("import", title, {"items": [{"path": p, "source_folder": None} for p in paths]})
    return {"job_id": job_id}


@router.post("/folder")
def import_folder(body: FolderBody, ctx: AppContext = Depends(get_ctx)):
    root = Path(body.path)
    if not root.is_dir():
        raise ApiError("bad_request", "所选路径不是文件夹或无法访问")
    items = []
    walker = os.walk(root) if body.recursive else [(str(root), [], [f.name for f in root.iterdir() if f.is_file()])]
    for dirpath, _dirs, files in walker:
        for name in sorted(files):
            if name.lower().endswith(".pdf"):
                full = Path(dirpath) / name
                rel = Path(dirpath).relative_to(root)
                folder = root.name if str(rel) == "." else f"{root.name}/{rel.as_posix()}"
                items.append({"path": str(full), "source_folder": folder})
                if len(items) > MAX_FILES:
                    raise ApiError("bad_request", f"文件夹中的 PDF 超过 {MAX_FILES} 个，请分批导入")
    if not items:
        raise ApiError("bad_request", "文件夹中没有找到 PDF 文件")
    job_id = ctx.jobs.submit("import", f"导入文件夹 {root.name}（{len(items)} 个 PDF）", {"items": items})
    return {"job_id": job_id}


@router.post("/doi")
def import_doi(body: DoiBody, ctx: AppContext = Depends(get_ctx)):
    raw = [d.strip() for d in body.dois if d and d.strip()]
    if not raw:
        raise ApiError("bad_request", "请输入至少一个 DOI")
    seen, dois = set(), []
    for d in raw:
        key = normalize_doi(d) or d
        if key not in seen:
            seen.add(key)
            dois.append(d)
    title = f"导入 DOI {dois[0]}" if len(dois) == 1 else f"导入 {len(dois)} 个 DOI"
    return {"job_id": ctx.jobs.submit("import_doi", title, {"dois": dois, "metadata_only": body.metadata_only})}


@router.post("/url")
def import_url(body: UrlBody, ctx: AppContext = Depends(get_ctx)):
    url = body.url.strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ApiError("bad_request", "请输入以 http:// 或 https:// 开头的链接")
    return {"job_id": ctx.jobs.submit("import_url", f"导入链接 {url[:60]}", {"url": url, "metadata_only": body.metadata_only})}
