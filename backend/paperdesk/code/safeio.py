"""安全地把 zip/本地文件夹放进 Marginalia 管理目录。

防护：zip-slip（路径穿越/绝对路径）、符号链接、zip 炸弹（文件数/总大小/单文件大小上限）、
二进制大文件（模型权重/数据）只在文件树里标注而不落盘。下载来的代码是不可信内容，这里不会执行任何东西。
"""
from __future__ import annotations

import os
import shutil
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..util import ApiError

MAX_FILES = 20_000
MAX_TOTAL_BYTES = 600 * 1024 * 1024       # 解压后总大小上限
MAX_FILE_BYTES = 5 * 1024 * 1024          # 单个文本文件上限（更大的只标注，不提取）
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode", ".mypy_cache", ".pytest_cache", "build", "dist", ".ipynb_checkpoints"}
BINARY_EXT = {
    ".pt", ".pth", ".ckpt", ".h5", ".hdf5", ".npy", ".npz", ".pkl", ".pickle", ".bin", ".onnx", ".pb", ".tflite", ".safetensors",
    ".png", ".jpg", ".jpeg", ".jfif", ".gif", ".bmp", ".webp", ".avif", ".tif", ".tiff", ".ico", ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar",
    ".mp4", ".webm", ".m4v", ".avi", ".mov", ".mkv", ".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac",
    ".so", ".dll", ".dylib", ".exe", ".o", ".a", ".lib", ".pyc", ".pyd", ".mat", ".ttf", ".otf", ".woff", ".woff2",
    ".parquet", ".feather", ".sqlite", ".db", ".jar", ".class", ".whl", ".egg",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".psd", ".blend", ".obj", ".glb", ".fbx", ".stl", ".ply", ".pcd", ".bag", ".mdb",
}

# 能在界面里直接看的二进制文件：落盘保存（有大小上限），文件树里仍标“二进制”，但打开时显示图片/PDF 首页/播放器。
PREVIEW_KINDS: dict[str, str] = {
    **{e: "image" for e in (".png", ".jpg", ".jpeg", ".jfif", ".gif", ".bmp", ".webp", ".avif", ".ico", ".svg")},
    ".pdf": "pdf",
    **{e: "video" for e in (".mp4", ".webm", ".m4v", ".mov")},
    **{e: "audio" for e in (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac")},
}
PREVIEW_MAX_BYTES = {"image": 25 * 1024 * 1024, "pdf": 40 * 1024 * 1024, "video": 60 * 1024 * 1024, "audio": 40 * 1024 * 1024}


def preview_kind(path: str) -> str | None:
    return PREVIEW_KINDS.get(os.path.splitext(path)[1].lower())


def _keeps_preview(rel: str, size: int) -> bool:
    kind = preview_kind(rel)
    return kind is not None and size <= PREVIEW_MAX_BYTES[kind]


@dataclass
class FileEntry:
    path: str                 # 仓库内的相对路径（POSIX 风格）
    size: int
    skipped: str | None = None  # binary | too_large | unsafe


@dataclass
class ExtractReport:
    files: list[FileEntry] = field(default_factory=list)
    total_bytes: int = 0
    skipped_unsafe: list[str] = field(default_factory=list)
    skipped_symlinks: int = 0
    warnings: list[str] = field(default_factory=list)


def looks_binary(head: bytes) -> bool:
    return b"\x00" in head


def clean_member_path(name: str) -> str | None:
    """规范化 zip 成员路径；不安全（绝对路径、盘符、.. 穿越）返回 None。"""
    name = name.replace("\\", "/")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return None
    parts = [p for p in PurePosixPath(name).parts if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def _inside(root: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def safe_extract_zip(zip_path: Path, dest: Path, *, strip_top: bool = True) -> ExtractReport:
    """把 zip 解压到 dest。GitHub 的 zip 外面包着一层 <repo>-<sha>/，默认剥掉。"""
    report = ExtractReport()
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise ApiError("bad_request", "这不是有效的 zip 压缩包。") from exc
    with zf:
        infos = [i for i in zf.infolist()]
        if len(infos) > MAX_FILES * 2:
            raise ApiError("bad_request", f"压缩包里的条目过多（{len(infos)}），已拒绝处理。")
        declared = sum(i.file_size for i in infos)
        if declared > MAX_TOTAL_BYTES * 3:
            raise ApiError("bad_request", "压缩包解压后体积异常巨大，疑似压缩炸弹，已拒绝处理。")
        names = [clean_member_path(i.filename) for i in infos]
        prefix = ""
        if strip_top:
            tops = {n.split("/")[0] for n in names if n}
            if len(tops) == 1 and all(n and "/" in n or n in tops for n in names if n):
                prefix = next(iter(tops)) + "/"
        dest.mkdir(parents=True, exist_ok=True)
        for info, name in zip(infos, names):
            if info.is_dir():
                continue
            if name is None:
                report.skipped_unsafe.append(info.filename)
                continue
            if _is_symlink(info):
                report.skipped_symlinks += 1
                continue
            rel = name[len(prefix):] if prefix and name.startswith(prefix) else name
            if not rel or any(p in SKIP_DIRS for p in rel.split("/")[:-1]):
                continue
            if len(report.files) >= MAX_FILES:
                report.warnings.append(f"文件数超过上限 {MAX_FILES}，其余文件被忽略。")
                break
            ext = os.path.splitext(rel)[1].lower()
            if ext in BINARY_EXT:
                if _keeps_preview(rel, info.file_size) and report.total_bytes + info.file_size <= MAX_TOTAL_BYTES:
                    target = dest / rel
                    if _inside(dest, target):
                        target.parent.mkdir(parents=True, exist_ok=True)
                        limit = PREVIEW_MAX_BYTES[preview_kind(rel) or "image"]
                        written = 0
                        with zf.open(info) as src, open(target, "wb") as out:
                            while chunk := src.read(1 << 16):
                                written += len(chunk)
                                if written > limit:  # 声明的大小可能撒谎
                                    break
                                out.write(chunk)
                        if written > limit:
                            target.unlink(missing_ok=True)
                        else:
                            report.total_bytes += written
                        report.files.append(FileEntry(rel, written, "binary"))
                        continue
                report.files.append(FileEntry(rel, info.file_size, "binary"))
                continue
            if info.file_size > MAX_FILE_BYTES:
                report.files.append(FileEntry(rel, info.file_size, "too_large"))
                continue
            if report.total_bytes + info.file_size > MAX_TOTAL_BYTES:
                report.warnings.append("解压总大小超过上限，其余文件被忽略。")
                break
            target = dest / rel
            if not _inside(dest, target):
                report.skipped_unsafe.append(info.filename)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                head = src.read(4096)
                if looks_binary(head):
                    out.close()
                    target.unlink(missing_ok=True)
                    report.files.append(FileEntry(rel, info.file_size, "binary"))
                    continue
                written = len(head)
                out.write(head)
                while chunk := src.read(1 << 16):
                    written += len(chunk)
                    if written > MAX_FILE_BYTES:  # 声明的大小可能撒谎
                        break
                    out.write(chunk)
            if written > MAX_FILE_BYTES:
                target.unlink(missing_ok=True)
                report.files.append(FileEntry(rel, written, "too_large"))
                continue
            report.total_bytes += written
            report.files.append(FileEntry(rel, written))
    if report.skipped_unsafe:
        report.warnings.append(f"压缩包中有 {len(report.skipped_unsafe)} 个路径不安全，已忽略。")
    if report.skipped_symlinks:
        report.warnings.append(f"已跳过 {report.skipped_symlinks} 个符号链接。")
    return report


def copy_folder(src: Path, dest: Path) -> ExtractReport:
    """把本地文件夹复制进管理目录（只读源，不移动/删除用户文件）。"""
    report = ExtractReport()
    src = src.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not os.path.islink(os.path.join(dirpath, d))]
        for fn in filenames:
            full = Path(dirpath) / fn
            rel = full.relative_to(src).as_posix()
            if full.is_symlink():
                report.skipped_symlinks += 1
                continue
            if len(report.files) >= MAX_FILES:
                report.warnings.append(f"文件数超过上限 {MAX_FILES}，其余文件被忽略。")
                return report
            try:
                size = full.stat().st_size
            except OSError:
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in BINARY_EXT:
                if _keeps_preview(rel, size) and report.total_bytes + size <= MAX_TOTAL_BYTES:
                    target = dest / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(full, target)
                    report.total_bytes += size
                report.files.append(FileEntry(rel, size, "binary"))
                continue
            if size > MAX_FILE_BYTES:
                report.files.append(FileEntry(rel, size, "too_large"))
                continue
            if report.total_bytes + size > MAX_TOTAL_BYTES:
                report.warnings.append("总大小超过上限，其余文件被忽略。")
                return report
            with open(full, "rb") as f:
                head = f.read(4096)
            if looks_binary(head):
                report.files.append(FileEntry(rel, size, "binary"))
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            data = full.read_bytes()
            target.write_bytes(data)
            report.total_bytes += size
            report.files.append(FileEntry(rel, size))
    if report.skipped_symlinks:
        report.warnings.append(f"已跳过 {report.skipped_symlinks} 个符号链接。")
    return report
