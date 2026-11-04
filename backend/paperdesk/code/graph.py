"""跨文件调用图解析。

确定性分级（写进 code_calls.certainty，界面会据此标注“不确定”）：
  resolved   通过 import / self / 同文件作用域确定指向
  name       只靠名称匹配，且全项目只有一个同名符号（可能是误匹配）
  ambiguous  有多个同名候选，无法确定
  external   项目之外（标准库/第三方），不进入流程图
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from .model import FileAnalysis, SymbolInfo

COMMON_ATTR = {
    "append", "extend", "get", "set", "items", "keys", "values", "update", "add", "pop", "remove", "copy", "close", "read", "write", "format",
    "join", "split", "strip", "replace", "startswith", "endswith", "sort", "count", "index", "insert", "clear", "lower", "upper", "encode", "decode",
    "readline", "readlines", "flush", "seek", "tell", "fit", "transform", "load", "dump", "save", "log", "print", "min", "max", "sum", "mean",
    "reshape", "astype", "item", "numpy", "cpu", "cuda", "to", "size", "view", "backward", "step", "zero_grad", "eval", "train", "run", "next",
    "push_back", "emplace_back", "begin", "end", "size", "length", "toString", "equals", "then", "catch", "map", "filter", "forEach", "push",
}


@dataclass
class SymRef:
    id: int
    path: str
    sym: SymbolInfo


class Resolver:
    def __init__(self, analyses: dict[str, FileAnalysis], ids: dict[tuple[str, str, str], int]):
        self.an = analyses
        self.by_file: dict[str, list[SymRef]] = defaultdict(list)
        self.by_name: dict[str, list[SymRef]] = defaultdict(list)
        for path, fa in analyses.items():
            for s in fa.symbols:
                sid = ids.get((path, s.qualname, s.kind))
                if sid is None:
                    continue
                ref = SymRef(sid, path, s)
                self.by_file[path].append(ref)
                if s.kind in ("function", "method", "class"):
                    self.by_name[s.name].append(ref)
        # 模块点号后缀索引：a/b/c.py → c, b.c, a.b.c；a/b/__init__.py → b, a.b
        self.mod_index: dict[str, set[str]] = defaultdict(set)
        for path in analyses:
            p = PurePosixPath(path)
            if p.suffix.lower() not in (".py", ".pyx"):
                continue
            parts = list(p.with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            for i in range(len(parts)):
                if parts[i:]:
                    self.mod_index[".".join(parts[i:])].add(path)

    # ---- 模块 / 符号查找 ----
    def module_file(self, module: str, importer: str, level: int = 0) -> str | None:
        if level > 0:
            base = PurePosixPath(importer).parent
            for _ in range(level - 1):
                base = base.parent
            rel = (str(base) + "/" if str(base) != "." else "") + module.replace(".", "/") if module else str(base)
            for cand in (rel + ".py", rel + ".pyx", rel + "/__init__.py"):
                if cand in self.an:
                    return cand
            return None
        cands = self.mod_index.get(module)
        if not cands:
            return None
        if len(cands) == 1:
            return next(iter(cands))
        # 多个候选：选与导入方目录最近的
        imp_dir = PurePosixPath(importer).parent.parts
        def closeness(p: str) -> int:
            n = 0
            for a, b in zip(imp_dir, PurePosixPath(p).parent.parts):
                if a != b:
                    break
                n += 1
            return n
        return max(sorted(cands), key=closeness)

    def find_in_file(self, path: str, name: str, parent: str | None = None) -> SymRef | None:
        for r in self.by_file.get(path, []):
            if r.sym.name == name and r.sym.kind in ("function", "method", "class") and (parent is None or r.sym.parent == parent):
                if parent is None and r.sym.parent is not None:
                    continue
                return r
        return None

    def ctor(self, cls: SymRef) -> SymRef:
        init = self.find_in_file(cls.path, "__init__", cls.sym.qualname)
        return init or cls

    def by_global_name(self, name: str, near: str | None = None) -> tuple[SymRef | None, str]:
        cands = self.by_name.get(name, [])
        if not cands:
            return None, "external"
        if len(cands) == 1:
            return cands[0], "name"
        if near:  # 研究代码里常有多份近似拷贝（FINDER_CN / FINDER_ND …）：同目录子树里最近且唯一的候选
            base = PurePosixPath(near).parent.parts
            def shared(p: str) -> int:
                n = 0
                for a, b in zip(base, PurePosixPath(p).parent.parts):
                    if a != b:
                        break
                    n += 1
                return n
            best = max(shared(c.path) for c in cands)
            top = [c for c in cands if shared(c.path) == best]
            if len(top) == 1 and best > 0:
                return top[0], "name"
        return None, "ambiguous"

    # ---- 单个调用 ----
    def class_of(self, ref: SymRef) -> SymRef | None:
        if ref.sym.kind == "class":
            return ref
        if ref.sym.kind == "method" and ref.sym.name == "__init__" and ref.sym.parent:
            return next((r for r in self.by_file.get(ref.path, []) if r.sym.kind == "class" and r.sym.qualname == ref.sym.parent), None)
        return None

    def resolve(self, path: str, caller: SymbolInfo, call: str, recv: str | None = None) -> tuple[SymRef | None, str]:
        fa = self.an[path]
        if recv and fa.language in ("python", "cython"):
            rparts = [p for p in recv.split(".") if p]
            r = self._resolve_py(fa, path, caller, rparts) if rparts else None
            cls = self.class_of(r[0]) if r is not None and r[0] is not None else None
            if cls is None and rparts:  # 构造调用在同名类上：整个项目里按类名找（多份拷贝时取最近的）
                cand, _ = self.by_global_name(rparts[-1], path)
                cls = self.class_of(cand) if cand is not None else None
            if cls is not None:
                meth = self.find_in_file(cls.path, re.split(r"\.|::|->", call)[-1], cls.sym.qualname)
                if meth:
                    return meth, "resolved"
        parts = [p for p in re.split(r"\.|::|->", call) if p]
        if not parts:
            return None, "external"
        head, last = parts[0], parts[-1]
        py = fa.language in ("python", "cython")
        if py:
            r = self._resolve_py(fa, path, caller, parts)
            if r is not None:
                return r
        else:
            if len(parts) == 1 or head in ("this", "self", "Self"):
                same = self.find_in_file(path, last)
                if same:
                    return same, "resolved"
        if len(parts) > 1 and last in COMMON_ATTR:
            return None, "external"
        return self.by_global_name(last, path)

    def _resolve_py(self, fa: FileAnalysis, path: str, caller: SymbolInfo, parts: list[str]) -> tuple[SymRef | None, str] | None:
        head, last = parts[0], parts[-1]
        if head in ("self", "cls") and len(parts) == 2:
            cls = caller.parent if caller.kind in ("method", "function") else caller.qualname
            if cls:
                m = self.find_in_file(path, parts[1], cls)
                if m:
                    return m, "resolved"
            return None if parts[1] not in COMMON_ATTR else (None, "external")
        if len(parts) == 1:
            local = self.find_in_file(path, head)
            if local:
                return (self.ctor(local) if local.sym.kind == "class" else local), "resolved"
            imp = fa.imports.get(head)
            if imp is not None:
                return self._from_import(fa, path, imp, head, [])
            return None
        imp = fa.imports.get(head)
        if imp is not None:
            return self._from_import(fa, path, imp, head, parts[1:])
        local_cls = self.find_in_file(path, head)
        if local_cls and local_cls.sym.kind == "class":
            m = self.find_in_file(path, parts[1], local_cls.sym.qualname)
            if m:
                return m, "resolved"
        return None

    def _from_import(self, fa: FileAnalysis, path: str, imp, alias: str, rest: list[str]) -> tuple[SymRef | None, str] | None:
        if imp.name is None:  # import a.b as alias → alias.f()
            for k in range(len(rest), 0, -1):
                mod = ".".join([imp.module, *rest[: k - 1]]) if k > 1 else imp.module
                f = self.module_file(mod, path, imp.level)
                if f and k - 1 < len(rest):
                    sym = self.find_in_file(f, rest[k - 1])
                    if sym:
                        return (self.ctor(sym) if sym.sym.kind == "class" else sym), "resolved"
            f = self.module_file(imp.module, path, imp.level)
            return (None, "external") if f is None else None
        # from m import n [as alias]
        f = self.module_file(imp.module, path, imp.level)
        if f:
            sym = self.find_in_file(f, imp.name)
            if sym:
                if rest and sym.sym.kind == "class":
                    m = self.find_in_file(f, rest[0], sym.sym.qualname)
                    return (m, "resolved") if m else (sym, "resolved")
                return (self.ctor(sym) if sym.sym.kind == "class" else sym), "resolved"
        sub = self.module_file(f"{imp.module}.{imp.name}" if imp.module else imp.name, path, imp.level)
        if sub and rest:
            sym = self.find_in_file(sub, rest[0])
            if sym:
                return (self.ctor(sym) if sym.sym.kind == "class" else sym), "resolved"
        if f is None and sub is None:
            return None, "external"
        return None


def resolve_all(analyses: dict[str, FileAnalysis], ids: dict[tuple[str, str, str], int]) -> list[tuple[int, int | None, str, int, str]]:
    """返回 [(caller_id, callee_id, callee_name, line, certainty)]。"""
    rs = Resolver(analyses, ids)
    out: list[tuple[int, int | None, str, int, str]] = []
    for path, fa in analyses.items():
        for s in fa.symbols:
            cid = ids.get((path, s.qualname, s.kind))
            if cid is None:
                continue
            seen: set[tuple[str, int]] = set()
            for c in s.calls:
                if (c.name, c.line) in seen:
                    continue
                seen.add((c.name, c.line))
                ref, cert = rs.resolve(path, s, c.name, c.recv)
                out.append((cid, ref.id if ref is not None else None, c.name, c.line, cert))
    return out
