"""静态分析的中间数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CallRef:
    name: str   # 调用链，如 foo / self.bar / np.zeros / a.b.c
    line: int
    recv: str | None = None  # 接收者的构造调用链（var = Agent() 之后 var.learn() 的 recv 是 Agent）


@dataclass
class SymbolInfo:
    name: str
    qualname: str
    kind: str                 # function | method | class | module | main
    parent: str | None
    line_start: int
    line_end: int
    signature: str = ""
    doc: str = ""
    calls: list[CallRef] = field(default_factory=list)


@dataclass
class ParamInfo:
    name: str
    value: str
    line: int
    origin: str               # argparse | constant | kwarg | config
    context: str = ""


@dataclass
class ImportRef:
    module: str               # 模块路径（相对导入时不含前导点）
    name: str | None          # from x import name 的 name；import x 时为 None
    level: int = 0            # 相对导入的点数


@dataclass
class FileAnalysis:
    path: str
    language: str
    lines: int = 0
    symbols: list[SymbolInfo] = field(default_factory=list)
    imports: dict[str, ImportRef] = field(default_factory=dict)   # 别名 → 导入
    params: list[ParamInfo] = field(default_factory=list)
    has_main: bool = False    # Python: __main__ 守卫；C/C++/Go/Rust/Java: main 函数
    errors: list[str] = field(default_factory=list)
