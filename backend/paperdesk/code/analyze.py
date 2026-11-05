"""对一个已落盘的仓库做完整静态分析并写入数据库：文件/角色 → 符号 → 参数 → 调用图 → 项目概览。"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Callable

from ..context import AppContext
from ..util import dumps
from . import scan
from .graph import resolve_all
from .model import FileAnalysis
from .py_analyze import analyze_python
from .safeio import FileEntry
from .ts_analyze import EXT_LANG, analyze_ts

# 分析逻辑有实质变化（入口排序、第三方拷贝识别、论文关联候选等）时加 1：旧仓库会提示“有新版分析，可重新分析”。
# 版本 1 = 没有记录过版本的旧数据。
ANALYSIS_VERSION = 2
MAX_ANALYZE_FILES = 6000
MAX_ANALYZE_BYTES = 1_500_000
MANIFEST_NAMES = {"setup.py", "pyproject.toml", "package.json", "makefile"}
ROLE_ORDER = {"train": 0, "test": 1, "entry": 2, "data": 3, "model": 4}


def _read(root: Path, rel: str) -> str | None:
    try:
        return (root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def analyze_files(root: Path, entries: list[FileEntry]) -> tuple[dict[str, FileAnalysis], dict[str, str], dict[str, str]]:
    """返回 (分析结果, 依赖/清单/README 文本, 全部文本文件的语言)。"""
    analyses: dict[str, FileAnalysis] = {}
    manifests: dict[str, str] = {}
    langs: dict[str, str] = {}
    n = 0
    for e in entries:
        lang = scan.language_of(e.path)
        if lang:
            langs[e.path] = lang
        if e.skipped:
            continue
        p = PurePosixPath(e.path)
        name = p.name.lower()
        ext = p.suffix.lower()
        is_manifest = name in scan.DEP_FILES or name.startswith("readme") or name in MANIFEST_NAMES or (name.startswith("requirements") and name.endswith(".txt"))
        want_code = ext in (".py", ".pyx") or ext in EXT_LANG
        if not (is_manifest or want_code):
            continue
        text_bytes = None
        try:
            text_bytes = (root / e.path).read_bytes()
        except OSError:
            continue
        if is_manifest and len(text_bytes) < 400_000:
            manifests[e.path] = text_bytes.decode("utf-8", "replace")
        if want_code and n < MAX_ANALYZE_FILES and len(text_bytes) <= MAX_ANALYZE_BYTES:
            n += 1
            try:
                if ext in (".py", ".pyx"):
                    analyses[e.path] = analyze_python(e.path, text_bytes.decode("utf-8", "replace"), "cython" if ext == ".pyx" else "python")
                else:
                    analyses[e.path] = analyze_ts(e.path, ext, text_bytes)
            except Exception as exc:  # noqa: BLE001  单个文件失败不应拖垮整个仓库
                fa = FileAnalysis(path=e.path, language=lang or "", lines=text_bytes.count(b"\n") + 1)
                fa.errors.append(f"分析失败：{type(exc).__name__}")
                analyses[e.path] = fa
    return analyses, manifests, langs


def analyze_repo(app: AppContext, repo_id: str, root: Path, entries: list[FileEntry], log: Callable[[str], None] = lambda _m: None) -> dict:
    log(f"分析 {len(entries)} 个文件 …")
    analyses, manifests, langs = analyze_files(root, entries)
    log(f"静态分析完成：{len(analyses)} 个源码文件")

    # 依赖与框架
    deps_by_path: dict[str, list[str]] = {}
    dep_files: list[str] = []
    for path, text in manifests.items():
        d = scan.parse_dependencies(path, text)
        if d or PurePosixPath(path).name.lower() in scan.DEP_FILES:
            dep_files.append(path)
        deps_by_path[path] = d

    files_rows: list[dict] = []
    for e in entries:
        fa = analyses.get(e.path)
        lang = langs.get(e.path) or (fa.language if fa else None)
        lines = fa.lines if fa else 0
        if fa is None and not e.skipped and lang:
            text = _read(root, e.path)
            lines = (text.count("\n") + 1) if text else 0
        role = scan.classify_role(e.path, lang, fa)
        files_rows.append({"path": e.path, "language": lang, "size": e.size, "lines": lines, "role": role, "skipped": e.skipped})

    with app.db.session() as conn:
        for tbl in ("code_calls", "code_params", "code_symbols", "code_files"):
            conn.execute(f"DELETE FROM {tbl} WHERE repo_id = ?", (repo_id,))
        conn.executemany(
            "INSERT INTO code_files(repo_id, path, language, size, lines, role, skipped) VALUES (?,?,?,?,?,?,?)",
            [(repo_id, f["path"], f["language"], f["size"], f["lines"], f["role"], f["skipped"]) for f in files_rows],
        )
        ids: dict[tuple[str, str, str], int] = {}
        n_sym = 0
        for path, fa in analyses.items():
            for s in fa.symbols:
                cur = conn.execute(
                    "INSERT INTO code_symbols(repo_id, path, name, qualname, kind, parent, line_start, line_end, signature, doc, language) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (repo_id, path, s.name, s.qualname, s.kind, s.parent, s.line_start, s.line_end, s.signature, s.doc, fa.language),
                )
                ids[(path, s.qualname, s.kind)] = cur.lastrowid
                n_sym += 1
            if fa.params:
                conn.executemany(
                    "INSERT INTO code_params(repo_id, path, line, name, value, origin, context) VALUES (?,?,?,?,?,?,?)",
                    [(repo_id, path, p.line, p.name, p.value[:200], p.origin, p.context[:200]) for p in fa.params],
                )
        resolved = resolve_all(analyses, ids)
        conn.executemany(
            "INSERT INTO code_calls(repo_id, caller_id, callee_id, callee_name, line, certainty) VALUES (?,?,?,?,?,?)",
            [(repo_id, a, b, c[:200], d, e) for a, b, c, d, e in resolved],
        )
    log(f"符号 {n_sym} 个，调用关系 {len(resolved)} 条")

    # 拷贝来的第三方目录（如整个 transformers）：框架/依赖只按作者自己的部分识别，它们另外列出
    from .context import vendored_dirs

    vend = sorted(vendored_dirs(files_rows))
    vend = [d for d in vend if not any(d.startswith(o + "/") for o in vend)]  # 只留最外层

    def in_vendored(path: str) -> bool:
        return any(path.startswith(d + "/") for d in vend)

    py_imports = {
        imp.module.split(".")[0]
        for p, fa in analyses.items() if not in_vendored(p) and fa.language in ("python", "cython")
        for imp in fa.imports.values() if imp.module
    }
    own_deps = [x for p, d in deps_by_path.items() if not in_vendored(p) for x in d]
    frameworks = scan.detect_frameworks(own_deps, py_imports)
    vendored = [
        {"path": d, "files": sum(1 for f in files_rows if f["path"].startswith(d + "/")), "lines": sum(f["lines"] for f in files_rows if f["path"].startswith(d + "/"))}
        for d in vend
    ]

    # 入口点
    entry_points: list[dict] = []
    role_of = {f["path"]: f["role"] for f in files_rows}
    for path, fa in analyses.items():
        if fa.has_main:
            kind = "main_guard" if fa.language in ("python", "cython") else "main_function"
            entry_points.append({
                "path": path, "symbol": "<main>" if kind == "main_guard" else "main", "kind": kind,
                "reason": 'if __name__ == "__main__"' if kind == "main_guard" else "main 函数", "command": None, "role": role_of.get(path),
            })
    known = {e["path"] for e in entry_points}
    for m in scan.entry_points_from_manifests(manifests):
        if m["kind"] in ("readme_command", "console_script") and m["path"] in role_of:
            if m["path"] in known:
                for e in entry_points:
                    if e["path"] == m["path"] and not e.get("command"):
                        e["command"] = m["command"]
                        e["reason"] += f"；{m['reason']}"
                continue
            m["role"] = role_of.get(m["path"])
            entry_points.append(m)
        elif m["kind"] in ("npm_script", "make_target"):
            m["role"] = role_of.get(m["path"])
            entry_points.append(m)
    entry_points.sort(key=lambda e: (ROLE_ORDER.get(e.get("role") or "", 9), e["path"]))
    from .context import order_entry_points

    entry_points = order_entry_points(entry_points, files_rows)
    for e in entry_points:
        e["vendored"] = in_vendored(e["path"])

    # 数据集目录里的 json 等被归为配置文件，但它们是数据，不是“配置”
    DATA_DIRS = {"data", "dataset", "datasets", "fixtures", "test_data"}
    config_files = [f["path"] for f in files_rows if f["role"] == "config" and not DATA_DIRS & set(f["path"].lower().split("/")[:-1])]
    skipped = [f for f in files_rows if f["skipped"]]
    overview = {
        "files": len(files_rows),
        "source_files": len(analyses),
        "symbols": n_sym,
        "calls": len(resolved),
        "languages": scan.summarize_languages(files_rows),
        "frameworks": frameworks,
        "dependency_files": sorted(dep_files, key=in_vendored)[:30],
        "config_files": sorted(config_files, key=in_vendored)[:40],
        "entry_points": entry_points[:40],
        "vendored": vendored,
        "top_dirs": scan.dir_groups([f["path"] for f in files_rows]),
        "roles": _count([f["role"] for f in files_rows]),
        "skipped": {"binary": sum(1 for f in skipped if f["skipped"] == "binary"), "too_large": sum(1 for f in skipped if f["skipped"] == "too_large")},
        "parse_warnings": [{"path": p, "message": m} for p, fa in analyses.items() for m in fa.errors][:20],
    }
    with app.db.session() as conn:
        conn.execute("UPDATE code_repos SET overview_json = ?, flow_json = NULL, analysis_version = ? WHERE id = ?", (dumps(overview), ANALYSIS_VERSION, repo_id))
    return overview


def _count(items: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out
