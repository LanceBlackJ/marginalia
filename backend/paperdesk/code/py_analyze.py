"""Python（及 Cython .pyx）静态分析：符号、调用、导入、argparse/常量/关键字参数。

优先用标准库 ast（精确）；ast 失败（Python 2 语法、Cython 的 cdef 等）时退化为基于缩进的容错解析，
并在 errors 里注明“容错解析，结果可能不完整”。
"""
from __future__ import annotations

import ast
import re

from .model import CallRef, FileAnalysis, ImportRef, ParamInfo, SymbolInfo

HYPER_RE = re.compile(
    r"lr|rate|batch|size|dim|epoch|iter|step|layer|hidden|gamma|eps|drop|decay|num|max|min|^n_|_n$|alpha|beta|lambda|seed|temp|window|buffer|memory|reg",
    re.I,
)
KEYWORDS = {
    "if", "elif", "else", "while", "for", "return", "print", "assert", "del", "yield", "lambda", "with", "not", "and", "or",
    "in", "is", "except", "raise", "def", "cdef", "cpdef", "class", "sizeof", "range", "len", "int", "float", "str", "list", "dict",
    "set", "tuple", "isinstance", "super", "type", "max", "min", "abs", "sum", "enumerate", "zip", "open",
}


def _chain(node: ast.AST) -> str | None:
    """把 a.b.c / self.x.y / foo 还原成点号链；复杂表达式返回 None。"""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    if isinstance(node, ast.Call):  # foo().bar / super().__init__
        inner = _chain(node.func)
        if inner:
            return ".".join([inner.split(".")[-1], *reversed(parts)]) if parts else inner
    return ".".join(reversed(parts)) if parts else None


def _literal(node: ast.AST) -> str | None:
    try:
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, str) and len(v) > 60:
                return None
            return repr(v)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)) and isinstance(node.operand, ast.Constant):
            return ast.unparse(node)
        if isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) <= 8 and all(isinstance(e, (ast.Constant, ast.UnaryOp)) for e in node.elts):
            return ast.unparse(node)
        if isinstance(node, ast.BinOp) and isinstance(node.left, (ast.Constant, ast.UnaryOp)) and isinstance(node.right, (ast.Constant, ast.UnaryOp)):
            return ast.unparse(node)  # 如 1e-3 * 2、2 ** 10
    except Exception:  # noqa: BLE001
        return None
    return None


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(b) for b in node.bases)
        return f"class {node.name}({bases})" if bases else f"class {node.name}"
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    try:
        args = ast.unparse(node.args)
    except Exception:  # noqa: BLE001
        args = "…"
    ret = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    sig = f"{prefix} {node.name}({args}){ret}"
    return sig if len(sig) <= 220 else sig[:217] + "…"


def _is_main_guard(node: ast.AST) -> bool:
    if not isinstance(node, ast.If):
        return False
    t = node.test
    if isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) and t.left.id == "__name__" and len(t.comparators) == 1:
        c = t.comparators[0]
        return isinstance(c, ast.Constant) and c.value == "__main__"
    return False


class _Collector:
    def __init__(self, path: str, source: str):
        self.out = FileAnalysis(path=path, language="python", lines=source.count("\n") + 1)
        self.local_types: dict[str, str] = {}   # 当前函数里：变量名 -> 构造调用链
        self.attr_types: dict[str, dict[str, str]] = {}  # 类 qualname -> {属性: 构造调用链}
        self.cur_class: str | None = None

    # ---- 调用与参数 ----
    @staticmethod
    def collect_types(body: list[ast.stmt]) -> tuple[dict[str, str], dict[str, str]]:
        """函数体里的 var = Cls(...) 与 self.attr = Cls(...)（不进入嵌套 def/class）。"""
        local: dict[str, str] = {}
        attrs: dict[str, str] = {}
        stack: list[ast.AST] = list(reversed(body))
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
                chain = _chain(n.value.func)
                if chain and chain.split(".")[-1][:1].isupper():  # 约定：类名首字母大写
                    for t in n.targets:
                        if isinstance(t, ast.Name):
                            local.setdefault(t.id, chain)
                        elif isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                            attrs.setdefault(t.attr, chain)
            stack.extend(reversed(list(ast.iter_child_nodes(n))))
        return local, attrs

    def scan_body(self, body: list[ast.stmt], sym: SymbolInfo | None, ctx_name: str) -> None:
        """遍历语句树，把调用记到 sym（不进入嵌套的 def/class，它们各自是符号）。"""
        stack: list[ast.AST] = list(reversed(body))
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(node, ast.Call):
                self.on_call(node, sym, ctx_name)
            elif isinstance(node, ast.Assign):
                self.on_assign(node)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                self.on_assign(ast.Assign(targets=[node.target], value=node.value, lineno=node.lineno))
            stack.extend(reversed(list(ast.iter_child_nodes(node))))

    def on_call(self, node: ast.Call, sym: SymbolInfo | None, ctx_name: str) -> None:
        name = _chain(node.func)
        if name and sym is not None:
            parts = name.split(".")
            recv = None
            if len(parts) >= 2 and parts[0] in self.local_types:
                recv = self.local_types[parts[0]]
            elif len(parts) >= 3 and parts[0] == "self" and self.cur_class:
                recv = self.attr_types.get(self.cur_class, {}).get(parts[1])
            sym.calls.append(CallRef(name, node.lineno, recv))
        last = name.split(".")[-1] if name else ""
        if last == "add_argument" and node.args:
            self.on_argparse(node)
        elif last.startswith("DEFINE_") and len(node.args) >= 2:  # TF flags
            flag = _literal(node.args[0])
            default = _literal(node.args[1])
            if flag and default is not None:
                self.out.params.append(ParamInfo(flag.strip("'\""), default, node.lineno, "argparse", f"flags.{last}"))
        for kw in node.keywords:
            if kw.arg is None or last == "add_argument":
                continue  # add_argument 的 default/type 已经作为 argparse 参数记录
            val = _literal(kw.value)
            if val is None or (isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)):
                continue  # 字符串关键字参数太嘈杂，只记数值/布尔/元组
            if len(self.out.params) < 3000:
                self.out.params.append(ParamInfo(kw.arg, val, node.lineno, "kwarg", name or ctx_name))

    def on_argparse(self, node: ast.Call) -> None:
        flags = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if not flags:
            return
        long = next((f for f in flags if f.startswith("--")), flags[0])
        name = long.lstrip("-").replace("-", "_")
        default = None
        helptext = ""
        action = None
        for kw in node.keywords:
            if kw.arg == "default":
                default = _literal(kw.value) or (ast.unparse(kw.value) if kw.value is not None else None)
            elif kw.arg == "help" and isinstance(kw.value, ast.Constant):
                helptext = str(kw.value.value)[:200]
            elif kw.arg == "action" and isinstance(kw.value, ast.Constant):
                action = kw.value.value
        if default is None:
            default = "False" if action == "store_true" else "True" if action == "store_false" else "None"
        self.out.params.append(ParamInfo(name, default, node.lineno, "argparse", helptext))

    def on_assign(self, node: ast.Assign) -> None:
        val = _literal(node.value)
        if val is None:
            return
        for t in node.targets:
            name = None
            if isinstance(t, ast.Name):
                name = t.id
            elif isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                name = t.attr  # self.learning_rate = 0.001
            if name and (name.isupper() or HYPER_RE.search(name)) and len(self.out.params) < 3000:
                self.out.params.append(ParamInfo(name, val, getattr(node, "lineno", 0), "constant"))

    # ---- 符号 ----
    def add_def(self, node, parent: SymbolInfo | None) -> SymbolInfo:
        is_class = isinstance(node, ast.ClassDef)
        if is_class:
            kind = "class"
        elif parent is not None and parent.kind == "class":
            kind = "method"
        else:
            kind = "function"
        qual = f"{parent.qualname}.{node.name}" if parent is not None and parent.kind in ("class", "function", "method") else node.name
        doc = (ast.get_docstring(node) or "").strip().replace("\n", " ")[:300]
        sym = SymbolInfo(
            name=node.name, qualname=qual, kind=kind, parent=parent.qualname if parent is not None and parent.kind != "module" else None,
            line_start=node.lineno, line_end=getattr(node, "end_lineno", node.lineno) or node.lineno,
            signature=_signature(node), doc=doc,
        )
        self.out.symbols.append(sym)
        for dec in node.decorator_list:  # 装饰器调用记到自身
            if isinstance(dec, ast.Call):
                self.on_call(dec, sym, node.name)
        saved = (self.local_types, self.cur_class)
        if is_class:
            attrs: dict[str, str] = {}
            for m in ast.walk(node):
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    attrs.update({k: v for k, v in self.collect_types(m.body)[1].items() if k not in attrs})
            self.attr_types[sym.qualname] = attrs
            self.cur_class = sym.qualname
            self.local_types = {}
        else:
            self.local_types = self.collect_types(node.body)[0]
            if parent is not None and parent.kind == "class":
                self.cur_class = parent.qualname
        self.scan_body(node.body, sym, node.name)
        for child in node.body:
            self.walk_defs(child, sym)
        self.local_types, self.cur_class = saved
        return sym

    def walk_defs(self, node: ast.AST, parent: SymbolInfo | None) -> None:
        """在语句树里找 def/class（包括 if/try/with 里的）。"""
        stack: list[ast.AST] = [node]
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.add_def(n, parent)
                continue
            if _is_main_guard(n) and parent is None:
                continue
            stack.extend(reversed(list(ast.iter_child_nodes(n))))


def analyze_python(path: str, source: str, language: str = "python") -> FileAnalysis:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return analyze_pylike(path, source, language)
    col = _Collector(path, source)
    out = col.out
    out.language = language
    # 导入
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.imports[a.asname or a.name.split(".")[0]] = ImportRef(a.name, None, 0)
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name != "*":
                    out.imports[a.asname or a.name] = ImportRef(node.module or "", a.name, node.level or 0)
    # 模块级：符号 + 模块调用 + __main__ 守卫
    mod = SymbolInfo("<module>", "<module>", "module", None, 1, out.lines, "", "")
    for stmt in tree.body:
        if _is_main_guard(stmt):
            main = SymbolInfo("<main>", "<main>", "main", None, stmt.lineno, getattr(stmt, "end_lineno", stmt.lineno), 'if __name__ == "__main__":', "")
            col.local_types = col.collect_types(stmt.body)[0]
            col.scan_body(stmt.body, main, "<main>")
            col.local_types = {}
            out.symbols.append(main)
            out.has_main = True
            for child in stmt.body:  # main 守卫里不会有 def，但 try/with 里可能
                col.walk_defs(child, None)
            continue
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            col.add_def(stmt, None)
        else:
            col.local_types = col.collect_types([stmt])[0]
            col.scan_body([stmt], mod, "<module>")
            col.local_types = {}
            col.walk_defs(stmt, None)
    if mod.calls:
        out.symbols.insert(0, mod)
    return out


# ---------------- 容错解析（.pyx / 旧语法）----------------
_DEF_RE = re.compile(r"^(\s*)(?:cpdef|cdef|async\s+def|def)\s+(?:[\w\.\[\]\*&]+\s+)*?(\w+)\s*\(")
_CLASS_RE = re.compile(r"^(\s*)(?:cdef\s+class|class)\s+(\w+)")
_CALL_RE = re.compile(r"([A-Za-z_][\w\.]*)\s*\(")
_CONST_RE = re.compile(
    r"^\s*(?:cdef\s+(?:[\w\.\[\]\*]+\s+)+?|cpdef\s+)?(self\.)?([A-Za-z_]\w*)\s*=\s*([-+]?\d[\w\.\-+]*|True|False|'[^']{0,40}'|\"[^\"]{0,40}\")\s*(?:#.*)?$"
)
_ASSIGN_NEW_RE = re.compile(r"^\s*(self\.)?(\w+)\s*=\s*([A-Za-z_][\w\.]*)\s*\(")
_MAIN_RE = re.compile(r"^if\s+__name__\s*==\s*['\"]__main__['\"]\s*:")
_IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w\.]+)\s+(?:cimport|import)\s+(.+)|(?:cimport|import)\s+([\w\.]+)(?:\s+as\s+(\w+))?)")


def analyze_pylike(path: str, source: str, language: str = "python") -> FileAnalysis:
    lines = source.splitlines()
    out = FileAnalysis(path=path, language=language, lines=len(lines))
    out.errors.append("该文件无法用 Python 语法树解析，已用缩进规则容错解析，结果可能不完整。")
    n = len(lines)

    def indent(s: str) -> int:
        return len(s) - len(s.lstrip())

    def block_end(start: int) -> int:
        base = indent(lines[start])
        end = start
        for j in range(start + 1, n):
            if lines[j].strip() == "" or lines[j].lstrip().startswith("#"):
                continue
            if indent(lines[j]) <= base:
                break
            end = j
        return end

    stack: list[tuple[int, SymbolInfo]] = []
    class_attr_types: dict[str, dict[str, str]] = {}
    for i, line in enumerate(lines):
        mc_ = _CONST_RE.match(line)
        if mc_ and (mc_.group(2).isupper() or HYPER_RE.search(mc_.group(2))) and len(out.params) < 3000:
            out.params.append(ParamInfo(mc_.group(2), mc_.group(3), i + 1, "constant"))
        m_imp = _IMPORT_RE.match(line)
        if m_imp:
            if m_imp.group(1):
                for part in m_imp.group(2).split(","):
                    nm = part.strip().split(" as ")
                    out.imports[(nm[1] if len(nm) > 1 else nm[0]).strip()] = ImportRef(m_imp.group(1), nm[0].strip(), 0)
            elif m_imp.group(3):
                out.imports[m_imp.group(4) or m_imp.group(3).split(".")[0]] = ImportRef(m_imp.group(3), None, 0)
        if _MAIN_RE.match(line):
            end = block_end(i)
            main = SymbolInfo("<main>", "<main>", "main", None, i + 1, end + 1, 'if __name__ == "__main__":', "")
            types: dict[str, str] = {}
            for j in range(i + 1, end + 1):
                am = _ASSIGN_NEW_RE.match(lines[j])
                if am and not am.group(1) and am.group(3).split(".")[-1][:1].isupper():
                    types.setdefault(am.group(2), am.group(3))
                for cm in _CALL_RE.finditer(lines[j]):
                    if cm.group(1).split(".")[-1] not in KEYWORDS:
                        head = cm.group(1).split(".")[0]
                        main.calls.append(CallRef(cm.group(1), j + 1, types.get(head) if "." in cm.group(1) else None))
            out.symbols.append(main)
            out.has_main = True
            continue
        mc = _CLASS_RE.match(line)
        md = _DEF_RE.match(line) if not mc else None
        if mc or md:
            ind = indent(line)
            while stack and stack[-1][0] >= ind:
                stack.pop()
            parent = stack[-1][1] if stack else None
            name = (mc or md).group(2)
            end = block_end(i)
            kind = "class" if mc else ("method" if parent is not None and parent.kind == "class" else "function")
            qual = f"{parent.qualname}.{name}" if parent is not None else name
            sym = SymbolInfo(name, qual, kind, parent.qualname if parent else None, i + 1, end + 1, line.strip()[:200], "")
            types = dict(class_attr_types.get(parent.qualname, {})) if parent is not None and parent.kind == "class" else {}
            local: dict[str, str] = {}
            for j in range(i + 1, end + 1):
                if _DEF_RE.match(lines[j]) or _CLASS_RE.match(lines[j]):
                    continue
                am = _ASSIGN_NEW_RE.match(lines[j])
                if am and am.group(3).split(".")[-1][:1].isupper():
                    if am.group(1):
                        class_attr_types.setdefault(parent.qualname if parent is not None else "", {}).setdefault(am.group(2), am.group(3))
                    else:
                        local.setdefault(am.group(2), am.group(3))
                for cm in _CALL_RE.finditer(lines[j]):
                    if cm.group(1).split(".")[-1] not in KEYWORDS:
                        parts = cm.group(1).split(".")
                        recv = None
                        if len(parts) >= 2 and parts[0] in local:
                            recv = local[parts[0]]
                        elif len(parts) >= 3 and parts[0] == "self":
                            recv = types.get(parts[1])
                        sym.calls.append(CallRef(cm.group(1), j + 1, recv))
            out.symbols.append(sym)
            stack.append((ind, sym))
    return out
