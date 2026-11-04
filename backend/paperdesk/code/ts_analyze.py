"""基于 tree-sitter 的多语言符号/调用提取：JavaScript/TypeScript/Java/C/C++/Go/Rust/Bash。

只提取“符号（函数/方法/类）与调用名”，用于调用图；其它语言不做 import 解析，调用解析靠同名匹配，
因此在图里会被标注为“按名称匹配（不确定）”。
"""
from __future__ import annotations

import re
from functools import lru_cache

from .model import CallRef, FileAnalysis, SymbolInfo

EXT_LANG = {
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".java": "java",
    ".c": "c",
    ".h": "cpp", ".hpp": "cpp", ".hh": "cpp", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".cu": "cpp", ".cuh": "cpp",
    ".go": "go", ".rs": "rust", ".sh": "bash", ".bash": "bash",
}
DISPLAY = {"tsx": "typescript", "cpp": "c++"}


@lru_cache(maxsize=None)
def _parser(lang: str):
    from tree_sitter import Language, Parser

    if lang == "javascript":
        import tree_sitter_javascript as m
        ptr = m.language()
    elif lang == "typescript":
        import tree_sitter_typescript as m
        ptr = m.language_typescript()
    elif lang == "tsx":
        import tree_sitter_typescript as m
        ptr = m.language_tsx()
    elif lang == "java":
        import tree_sitter_java as m
        ptr = m.language()
    elif lang == "c":
        import tree_sitter_c as m
        ptr = m.language()
    elif lang == "cpp":
        import tree_sitter_cpp as m
        ptr = m.language()
    elif lang == "go":
        import tree_sitter_go as m
        ptr = m.language()
    elif lang == "rust":
        import tree_sitter_rust as m
        ptr = m.language()
    elif lang == "bash":
        import tree_sitter_bash as m
        ptr = m.language()
    else:
        raise ValueError(lang)
    return Parser(Language(ptr))


def _t(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _field(node, name: str):
    return node.child_by_field_name(name)


def _first_ident(node, src: bytes, types=("identifier", "field_identifier", "type_identifier", "qualified_identifier", "destructor_name", "operator_name")) -> str | None:
    """在声明符里找函数名（C/C++ 的嵌套 declarator）。"""
    cur = node
    for _ in range(12):
        if cur is None:
            return None
        if cur.type in types:
            return _t(cur, src)
        nxt = _field(cur, "declarator")
        if nxt is None:
            for ch in cur.children:
                if ch.type in types:
                    return _t(ch, src)
            return None
        cur = nxt
    return None


# 每种语言：节点类型 -> (符号种类, 取名函数)。取名函数返回 None 表示忽略该节点。
def _name_field(field_name="name"):
    return lambda n, s: (_t(_field(n, field_name), s) if _field(n, field_name) is not None else None)


def _declarator_name(n, s):
    return _first_ident(_field(n, "declarator"), s)


def _js_var_fn(n, s):
    val = _field(n, "value")
    if val is not None and val.type in ("arrow_function", "function_expression", "function", "generator_function"):
        nm = _field(n, "name")
        return _t(nm, s) if nm is not None else None
    return None


DEFS: dict[str, dict[str, tuple[str, object]]] = {
    "javascript": {
        "function_declaration": ("function", _name_field()), "generator_function_declaration": ("function", _name_field()),
        "method_definition": ("method", _name_field()), "class_declaration": ("class", _name_field()),
        "variable_declarator": ("function", _js_var_fn),
    },
    "java": {
        "class_declaration": ("class", _name_field()), "interface_declaration": ("class", _name_field()),
        "enum_declaration": ("class", _name_field()), "record_declaration": ("class", _name_field()),
        "method_declaration": ("method", _name_field()), "constructor_declaration": ("method", _name_field()),
    },
    "c": {"function_definition": ("function", _declarator_name), "struct_specifier": ("class", None)},
    "cpp": {
        "function_definition": ("function", _declarator_name), "class_specifier": ("class", _name_field()),
        "struct_specifier": ("class", _name_field()),
    },
    "go": {"function_declaration": ("function", _name_field()), "method_declaration": ("method", _name_field()), "type_spec": ("class", _name_field())},
    "rust": {"function_item": ("function", _name_field()), "struct_item": ("class", _name_field()), "enum_item": ("class", _name_field()), "trait_item": ("class", _name_field())},
    "bash": {"function_definition": ("function", _name_field())},
}
DEFS["typescript"] = {**DEFS["javascript"], "interface_declaration": ("class", _name_field()), "abstract_class_declaration": ("class", _name_field())}
DEFS["tsx"] = DEFS["typescript"]


def _call_name(lang: str, n, s: bytes) -> str | None:
    t = n.type
    if lang in ("javascript", "typescript", "tsx", "c", "cpp", "go", "rust"):
        if t == "call_expression":
            f = _field(n, "function")
            return re.sub(r"\s+", "", _t(f, s))[:120] if f is not None else None
        if t == "new_expression" and lang in ("javascript", "typescript", "tsx"):
            f = _field(n, "constructor")
            return _t(f, s)[:120] if f is not None else None
    elif lang == "java":
        if t == "method_invocation":
            name = _field(n, "name")
            obj = _field(n, "object")
            if name is None:
                return None
            return f"{_t(obj, s)}.{_t(name, s)}"[:120] if obj is not None else _t(name, s)
        if t == "object_creation_expression":
            ty = _field(n, "type")
            return _t(ty, s)[:120] if ty is not None else None
    elif lang == "bash":
        if t == "command":
            nm = _field(n, "name")
            return _t(nm, s)[:120] if nm is not None else None
    return None


_CALL_TYPES = {"call_expression", "new_expression", "method_invocation", "object_creation_expression", "command"}


def _doc_before(node, src: bytes) -> str:
    prev = node.prev_sibling
    if prev is not None and prev.type in ("comment", "line_comment", "block_comment") and prev.end_point[0] >= node.start_point[0] - 1:
        text = _t(prev, src)
        return re.sub(r"^\s*(?:/\*+|//+|#+|\*+/?)\s?|\*/\s*$", "", text, flags=re.M).strip().replace("\n", " ")[:300]
    return ""


def _signature(node, src: bytes) -> str:
    text = _t(node, src)
    first = text.split("{", 1)[0].split("\n", 1)[0] if "{" in text or "\n" in text else text
    return re.sub(r"\s+", " ", first).strip()[:200]


def analyze_ts(path: str, ext: str, source: bytes) -> FileAnalysis:
    lang = EXT_LANG[ext]
    out = FileAnalysis(path=path, language=DISPLAY.get(lang, lang), lines=source.count(b"\n") + 1)
    try:
        tree = _parser(lang).parse(source)
    except Exception as exc:  # noqa: BLE001
        out.errors.append(f"tree-sitter 解析失败：{type(exc).__name__}")
        return out
    if tree.root_node.has_error:
        out.errors.append("文件包含语法错误或不被支持的语法，符号提取可能不完整。")
    defs = DEFS.get(lang, {})
    module = SymbolInfo("<module>", "<module>", "module", None, 1, out.lines)
    # (node, 当前符号)  迭代而非递归，避免深层 AST 触发递归上限
    stack: list[tuple] = [(tree.root_node, module, None)]
    symbols: list[SymbolInfo] = []
    while stack:
        node, cur, cls_ctx = stack.pop()
        t = node.type
        spec = defs.get(t)
        new_cur, new_cls = cur, cls_ctx
        if spec is not None:
            kind, namer = spec
            name = namer(node, source) if namer else None
            if t == "struct_specifier" and lang == "c":
                nm = _field(node, "name")
                name = _t(nm, source) if nm is not None and _field(node, "body") is not None else None
            if t == "variable_declarator" and name is None:
                pass
            elif name:
                if t == "type_spec" and _field(node, "type") is not None and _field(node, "type").type != "struct_type":
                    name = None
            if name:
                parent = cls_ctx
                qual = f"{parent}.{name}" if parent else name
                if "::" in name:  # C++ 类外定义：Graph::add_edge
                    qual = name.replace("::", ".")
                    parent = qual.rsplit(".", 1)[0]
                    name = qual.rsplit(".", 1)[1]
                    kind = "method"
                elif lang == "go" and t == "method_declaration":  # 接收者类型作为所属类型
                    recv = _field(node, "receiver")
                    m = re.search(r"\*?\s*([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*\)", _t(recv, source)) if recv is not None else None
                    if m:
                        parent = m.group(1)
                        qual = f"{parent}.{name}"
                if kind == "function" and cls_ctx:
                    kind = "method"
                sym = SymbolInfo(name, qual, kind, parent, node.start_point[0] + 1, node.end_point[0] + 1, _signature(node, source), _doc_before(node, source))
                symbols.append(sym)
                new_cur = sym
                if kind == "class":
                    new_cls = qual
                if name == "main" and lang in ("java", "go", "rust", "c", "cpp") and kind in ("function", "method"):
                    out.has_main = True
        elif t == "impl_item" and lang == "rust":
            ty = _field(node, "type")
            new_cls = _t(ty, source) if ty is not None else cls_ctx
        elif t in _CALL_TYPES:
            nm = _call_name(lang, node, source)
            if nm and cur is not None:
                cur.calls.append(CallRef(nm, node.start_point[0] + 1))
        for ch in reversed(node.children):
            stack.append((ch, new_cur, new_cls))
    if module.calls:
        out.symbols.append(module)
    out.symbols.extend(sorted(symbols, key=lambda s: (s.line_start, s.line_end)))
    return out
