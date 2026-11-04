"""补充文件的安全处理：文件名清洗、类型判断、魔数校验、zip 安全列目录/解压、受限下载。

安全原则：绝不执行补充材料里的任何内容；解压前检查路径穿越/符号链接/zip 炸弹；下载校验大小与内容类型，
不是目标文件（登录页/付费墙网页）时明确失败，不绕过任何访问限制。
"""
from __future__ import annotations

import posixpath
import re
import shutil
import stat
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from ..util import ApiError

MAX_FILE_BYTES = 500 * 1024 * 1024  # 单个补充文件上限
AUTO_DOWNLOAD_LIMIT = 20 * 1024 * 1024  # 超过则需要用户批准
ZIP_MAX_FILES = 5000
ZIP_MAX_TOTAL = 800 * 1024 * 1024
ZIP_MAX_RATIO = 200  # 压缩比超过则视为 zip 炸弹

PDF_EXT = {".pdf"}
TABLE_EXT = {".csv", ".tsv", ".xlsx", ".xlsm", ".xls", ".json", ".txt_table"}
DOC_EXT = {".docx", ".doc", ".rtf", ".odt", ".md", ".txt", ".tex"}
ARCHIVE_EXT = {".zip", ".tar", ".gz", ".tgz", ".7z", ".rar"}
CODE_EXT = {".py", ".ipynb", ".m", ".r", ".java", ".cpp", ".c", ".h", ".js", ".ts", ".go", ".rs", ".sh", ".jl"}
ALLOWED_EXT = PDF_EXT | TABLE_EXT | DOC_EXT | ARCHIVE_EXT | CODE_EXT | {".png", ".jpg", ".jpeg", ".gif", ".svg", ".mp4", ".mov", ".npy", ".npz", ".mat", ".h5", ".pkl", ".pt", ".parquet"}

_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def sanitize_filename(name: str, default: str = "file") -> str:
    """只保留最后一段、去掉路径/控制字符/Windows 保留名，长度受限。"""
    name = unquote(name or "").replace("\\", "/").split("/")[-1].strip().strip(".")
    name = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        name = default
    stem, dot, ext = name.rpartition(".")
    if dot and stem.lower() in _WINDOWS_RESERVED:
        name = f"_{name}"
    elif name.lower() in _WINDOWS_RESERVED:
        name = f"_{name}"
    if len(name) > 120:
        s, d, e = name.rpartition(".")
        name = (s[: 120 - len(e) - 1] + "." + e) if d and len(e) <= 10 else name[:120]
    return name


def ext_of(filename: str) -> str:
    return Path(filename).suffix.lower()


def kind_of(filename: str) -> str:
    """文件大类：pdf | table | document | archive | code | other。"""
    e = ext_of(filename)
    if e in PDF_EXT:
        return "pdf"
    if e in TABLE_EXT:
        return "table"
    if e in DOC_EXT:
        return "document"
    if e in ARCHIVE_EXT:
        return "archive"
    if e in CODE_EXT:
        return "code"
    return "other"


MIME_BY_EXT = {
    ".pdf": "application/pdf", ".csv": "text/csv", ".tsv": "text/tab-separated-values",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".zip": "application/zip", ".json": "application/json", ".txt": "text/plain", ".md": "text/markdown",
}


def mime_of(filename: str) -> str:
    return MIME_BY_EXT.get(ext_of(filename), "application/octet-stream")


def check_magic(path: Path) -> str | None:
    """校验文件头与扩展名一致，返回错误说明；通过返回 None。"""
    e = ext_of(path.name)
    try:
        with open(path, "rb") as f:
            head = f.read(2048)
    except OSError as exc:
        return f"无法读取文件：{exc.strerror or exc}"
    if not head:
        return "文件为空"
    # 先判断“拿到的其实是网页”（登录页/付费墙），再判断与扩展名是否相符——前者对用户更有用
    lowered = head.lstrip().lower()
    if e not in (".html", ".htm") and (lowered.startswith(b"<!doctype html") or lowered.startswith(b"<html")):
        return "下载到的是网页而不是文件，可能需要登录或机构权限"
    if e == ".pdf" and b"%PDF-" not in head[:1024]:
        return "文件内容不是有效的 PDF"
    if e in (".zip", ".xlsx", ".xlsm", ".docx", ".odt") and not head.startswith(b"PK"):
        return f"文件内容与扩展名 {e} 不符"
    return None


# ---------------- zip 安全 ----------------
class UnsafeArchive(Exception):
    pass


def _member_problem(name: str) -> str | None:
    norm = name.replace("\\", "/")
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
        return "绝对路径"
    parts = posixpath.normpath(norm).split("/")
    if ".." in parts:
        return "路径穿越（..）"
    if any(p in _WINDOWS_RESERVED for p in (x.split(".")[0].lower() for x in parts if x)):
        return "Windows 保留名"
    return None


def list_zip(path: Path, limit: int = 400) -> dict:
    """只读地列出压缩包目录，不解压任何内容。"""
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ApiError("bad_request", "压缩包已损坏或不是 zip 格式") from exc
    with zf:
        infos = zf.infolist()
        total = sum(i.file_size for i in infos)
        comp = sum(i.compress_size for i in infos) or 1
        suspicious: list[str] = []
        entries = []
        for i in infos:
            prob = _member_problem(i.filename)
            is_link = stat.S_ISLNK(i.external_attr >> 16)
            if prob:
                suspicious.append(f"{i.filename}：{prob}")
            if is_link:
                suspicious.append(f"{i.filename}：符号链接")
            if len(entries) < limit:
                entries.append({"name": i.filename, "size": i.file_size, "is_dir": i.is_dir()})
        ratio = total / comp
        if len(infos) > ZIP_MAX_FILES:
            suspicious.append(f"文件数过多（{len(infos)}）")
        if total > ZIP_MAX_TOTAL or ratio > ZIP_MAX_RATIO:
            suspicious.append(f"解压后体积异常（{total // 1024 // 1024} MB，压缩比 {ratio:.0f}）")
        exts: dict[str, int] = {}
        for i in infos:
            if not i.is_dir():
                exts[ext_of(i.filename) or "(无扩展名)"] = exts.get(ext_of(i.filename) or "(无扩展名)", 0) + 1
        return {
            "total_files": len([i for i in infos if not i.is_dir()]), "total_uncompressed": total,
            "ratio": round(ratio, 1), "suspicious": suspicious[:20], "entries": entries,
            "truncated": len(infos) > limit, "extensions": dict(sorted(exts.items(), key=lambda kv: -kv[1])[:15]),
        }


def safe_extract_zip(zip_path: Path, dest: Path, *, max_files: int = ZIP_MAX_FILES,
                     max_total: int = ZIP_MAX_TOTAL, max_ratio: int = ZIP_MAX_RATIO) -> list[Path]:
    """安全解压：拒绝路径穿越/绝对路径/符号链接，限制文件数、总大小与压缩比。只写入 dest 之内，绝不执行任何文件。"""
    dest = dest.resolve()
    existed = dest.exists()
    written: list[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > max_files:
            raise UnsafeArchive(f"压缩包文件数过多（{len(infos)} > {max_files}）")
        total = sum(i.file_size for i in infos)
        comp = sum(i.compress_size for i in infos) or 1
        if total > max_total:
            raise UnsafeArchive(f"解压后总大小过大（{total // 1024 // 1024} MB）")
        if total / comp > max_ratio and total > 50 * 1024 * 1024:
            raise UnsafeArchive("压缩比异常，疑似 zip 炸弹")
        # 第一遍：先检查全部条目，任何一个不安全就整体拒绝，磁盘上不留下半个解压结果
        plan: list[tuple[zipfile.ZipInfo, Path]] = []
        for i in infos:
            prob = _member_problem(i.filename)
            if prob:
                raise UnsafeArchive(f"压缩包包含不安全路径：{i.filename}（{prob}）")
            if stat.S_ISLNK(i.external_attr >> 16):
                continue  # 符号链接一律跳过，不创建
            target = (dest / i.filename).resolve()
            if dest != target and dest not in target.parents:
                raise UnsafeArchive(f"压缩包包含越界路径：{i.filename}")
            plan.append((i, target))
        dest.mkdir(parents=True, exist_ok=True)
        try:
            for i, target in plan:
                if i.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                written_bytes = 0
                with zf.open(i) as src, open(target, "wb") as out:
                    while chunk := src.read(1 << 20):
                        written_bytes += len(chunk)
                        if written_bytes > i.file_size + 1024:  # 声明大小与实际不符
                            raise UnsafeArchive(f"文件实际大小与声明不符：{i.filename}")
                        out.write(chunk)
                written.append(target)
        except Exception:
            if not existed:
                shutil.rmtree(dest, ignore_errors=True)
            raise
    return written


# ---------------- 受限下载 ----------------
class DownloadError(Exception):
    pass


class TooLarge(DownloadError):
    """超过调用方给定的大小上限；size 为已知的声明大小（可能为 None）。"""

    def __init__(self, message: str, size: int | None = None):
        super().__init__(message)
        self.size = size


def filename_from_response(url: str, resp: httpx.Response) -> str:
    cd = resp.headers.get("content-disposition", "")
    m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)|filename=\"?([^\";]+)\"?", cd, re.I)
    if m:
        return sanitize_filename(m.group(1) or m.group(2))
    return sanitize_filename(urlparse(url).path.rsplit("/", 1)[-1] or "download")


def probe_size(client: httpx.Client, url: str) -> int | None:
    """HEAD 探测文件大小（尽力而为）。"""
    try:
        r = client.head(url, timeout=8)
        if r.status_code == 200 and r.headers.get("content-length", "").isdigit():
            return int(r.headers["content-length"])
    except httpx.HTTPError:
        pass
    return None


def download_file(client: httpx.Client, url: str, dest: Path, *, max_bytes: int = MAX_FILE_BYTES,
                  on_progress=None) -> int:
    if urlparse(url).scheme not in ("http", "https"):
        raise DownloadError("只支持 http/https 链接")
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with client.stream("GET", url) as r:
            if r.status_code in (401, 403):
                raise DownloadError("该文件需要登录或机构权限。请在浏览器中自行下载后，用“添加本地文件”导入")
            if r.status_code != 200:
                raise DownloadError(f"下载失败 {r.status_code}")
            declared = int(r.headers.get("content-length") or 0)
            if declared > max_bytes:
                raise TooLarge(f"文件过大（{declared // 1024 // 1024} MB，上限 {max_bytes // 1024 // 1024} MB）", declared)
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    total += len(chunk)
                    if total > max_bytes:
                        raise TooLarge("文件超过大小上限", None)
                    f.write(chunk)
                    if on_progress:
                        on_progress(total, declared)
    except httpx.HTTPError as exc:
        dest.unlink(missing_ok=True)
        raise DownloadError(f"网络错误：{type(exc).__name__}") from exc
    except DownloadError:
        dest.unlink(missing_ok=True)
        raise
    problem = check_magic(dest)
    if problem:
        dest.unlink(missing_ok=True)
        raise DownloadError(problem)
    return total
