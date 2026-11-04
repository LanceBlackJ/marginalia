"""把一个补充文件落地并做本地分析：复制到 Marginalia 目录、校验、规则分类，PDF→子论文，表格→数据集检查，docx→文本，zip→目录。

只读、不执行；每一步都更新 supplementary_assets 的状态，失败写入 download_error（中文可展示）。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from ..context import AppContext
from ..library.importer import sha256_file
from ..util import ApiError
from . import classify, datasets, store
from . import files as F
from .pdf_child import import_supplement_pdf

Logger = Callable[[str], None]


def docx_summary(path: Path) -> dict:
    import docx  # python-docx

    try:
        d = docx.Document(str(path))
    except Exception as exc:  # noqa: BLE001
        raise ApiError("bad_request", f"无法打开 Word 文档：{str(exc)[:100]}") from exc
    paras = [p.text.strip() for p in d.paragraphs if p.text.strip()]
    headings = [p.text.strip() for p in d.paragraphs if p.style is not None and (p.style.name or "").lower().startswith("heading") and p.text.strip()]
    text = "\n".join(paras)
    return {"kind": "document", "format": "docx", "paragraphs": len(paras), "tables": len(d.tables),
            "headings": headings[:60], "chars": len(text), "text_sample": text[:4000]}


def text_sample_for(app: AppContext, asset_row_id: str) -> str:
    """分类/摘要用的文本样本：PDF 取章节标题与开头段落；其它取分析结果里的样本。"""
    with app.db.session() as conn:
        row = store.get_row(conn, asset_row_id)
        if row["child_paper_id"]:
            cid = row["child_paper_id"]
            secs = [r["title"] for r in conn.execute("SELECT title FROM sections WHERE paper_id = ? ORDER BY ord LIMIT 40", (cid,))]
            body = " ".join(r["text"] for r in conn.execute(
                "SELECT text FROM passages WHERE paper_id = ? AND kind = 'paragraph' ORDER BY ord LIMIT 12", (cid,)))
            return ("章节：" + "；".join(secs) + "\n" + body)[:6000]
    a = store.get_analysis(app, asset_row_id) or {}
    if a.get("kind") == "document":
        return (a.get("text_sample") or "")[:6000]
    if a.get("kind") == "table":
        return datasets.profile_brief(a)[:6000]
    if a.get("kind") == "archive":
        return "文件列表：" + "；".join(e["name"] for e in (a.get("entries") or [])[:80])
    return ""


def process_file(app: AppContext, asset_id: str, src: Path, *, source_url: str | None = None,
                 original_name: str | None = None, log: Logger | None = None) -> None:
    """落地并分析一个已存在于磁盘上的文件（本地选择的，或刚下载完成的临时文件）。"""
    say = log or (lambda _m: None)
    with app.db.session() as conn:
        row = store.get_row(conn, asset_id)
    paper_id = row["paper_id"]
    name = F.sanitize_filename(original_name or row["filename"])
    kind = F.kind_of(name)
    problem = F.check_magic(src)
    if problem:
        raise ApiError("bad_request", problem)
    size = src.stat().st_size
    if size > F.MAX_FILE_BYTES:
        raise ApiError("bad_request", f"文件过大（{size // 1024 // 1024} MB，上限 {F.MAX_FILE_BYTES // 1024 // 1024} MB）")
    digest = sha256_file(src)
    with app.db.session() as conn:
        dup = conn.execute(
            "SELECT id FROM supplementary_assets WHERE paper_id = ? AND sha256 = ? AND id != ?", (paper_id, digest, asset_id)
        ).fetchone()
    if dup:
        raise ApiError("conflict", "这个文件已经添加为补充材料了", 409)

    store.update_asset(app, asset_id, download_status="processing", download_error=None, filename=name, size=size,
                       mime=F.mime_of(name), sha256=digest)
    if kind == "pdf":
        child_id, parsed, _digest = import_supplement_pdf(app, paper_id, src, original_name=name, source_url=source_url, log=say)
        rel = f"papers/{child_id}/original.pdf"
        store.update_asset(app, asset_id, rel_path=rel, child_paper_id=child_id, analysis_status="ready")
    else:
        dest_dir = store.asset_dir(app, paper_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{asset_id}_{name}"
        shutil.copy2(src, dest)
        store.update_asset(app, asset_id, rel_path=dest.relative_to(app.paths.data_dir).as_posix())
        analysis = None
        try:
            if kind == "table":
                say("检查数据集…")
                analysis = datasets.profile_file(dest)
            elif kind == "document" and F.ext_of(name) == ".docx":
                analysis = docx_summary(dest)
            elif kind == "archive" and F.ext_of(name) == ".zip":
                analysis = {"kind": "archive", **F.list_zip(dest)}
        except ApiError as exc:  # 文件已保存，只是无法分析：原因放进分析结果，界面据此提示
            store.update_asset(app, asset_id, analysis_status="failed", analysis_json={"kind": "error", "error": exc.message})
        else:
            if analysis is not None:
                store.update_asset(app, asset_id, analysis_json=analysis, analysis_status="ready")
    cat, why = classify.rule_category(name, text_sample_for(app, asset_id))
    # 用户或 AI 已经指定过类别时（category_source != 'rule'）不覆盖
    if store.get_asset(app, asset_id)["category_source"] == "rule":
        store.update_asset(app, asset_id, category=cat, category_source="rule")
    with app.db.session() as conn:
        cur = store.get_row(conn, asset_id)
        if not cur["summary"]:
            conn.execute("UPDATE supplementary_assets SET summary = ? WHERE id = ?", (f"规则分类：{why}", asset_id))
    store.update_asset(app, asset_id, download_status="done", download_error=None)
    say(f"已添加：{name}（{cat}）")
