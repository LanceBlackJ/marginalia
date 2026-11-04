"""程序运行流程：从入口出发，沿**静态调用图**按调用出现的先后生成有序步骤。

流程的结构完全来自静态分析（不让模型凭空画图）；阶段（参数解析/数据加载/…）由名称与调用的规则推断，
AI 只在可选的 annotate 步骤里给节点起名并写一句话解释（结果单独标注为 AI 生成）。
"""
from __future__ import annotations

import re

from ..context import AppContext
from ..util import ApiError, dumps, loads

PHASES = [
    ("参数解析", re.compile(r"argparse|ArgumentParser|add_argument|parse_args|parse_known_args|getopt|click|sys\.argv|flags|FLAGS|argv|parseArg", re.I)),
    ("配置读取", re.compile(r"config|cfg|yaml|settings|load_conf|read_conf|hparams|json\.load|toml", re.I)),
    ("数据加载", re.compile(r"load|read|dataset|dataloader|data_loader|generate|gen_graph|fetch|download|open|input", re.I)),
    ("数据预处理", re.compile(r"preprocess|prepare|normaliz|transform|augment|clean|tokeniz|encode_data|build_graph|batch", re.I)),
    ("模型/模块初始化", re.compile(r"init|build|create|setup|construct|model|net\b|agent|__init__|make_", re.I)),
    ("训练", re.compile(r"train|fit|learn|update|optimi|backward|loss|epoch|fit_", re.I)),
    ("评估/推理", re.compile(r"eval|test|infer|predict|metric|score|accuracy|validate|benchmark|decode|select", re.I)),
    ("结果保存", re.compile(r"save|dump|write|export|log|plot|report|store|checkpoint", re.I)),
]


def phase_of(name: str, doc: str, callee_names: list[str], kind: str = "") -> str:
    """按“调用了什么库”（强信号）→ 函数名 → 文档串 的顺序推断阶段。"""
    if kind == "main" or name in ("main", "run", "start", "__main__"):
        return "入口/调度"
    blob = " ".join(callee_names[:20])
    if PHASES[0][1].search(blob):  # 直接调用 argparse/click 等：参数解析
        return "参数解析"
    tokens = [t for t in re.split(r"[_\W]+|(?<=[a-z])(?=[A-Z])", name) if t]
    for tok in tokens:  # 先看动词（第一个词）：save_model 是“保存”而不是“模型”
        for label, rx in PHASES:
            if rx.search(tok):
                return label
    for label, rx in PHASES:
        if rx.search(name):
            return label
    if PHASES[1][1].search(blob):
        return "配置读取"
    for label, rx in PHASES:
        if rx.search(f"{name} {doc}"):
            return label
    return "算法执行"


def _load(app: AppContext, repo_id: str):
    with app.db.session() as conn:
        syms = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM code_symbols WHERE repo_id = ?", (repo_id,)).fetchall()}
        calls: dict[int, list[dict]] = {}
        for r in conn.execute("SELECT * FROM code_calls WHERE repo_id = ? ORDER BY caller_id, line, id", (repo_id,)).fetchall():
            calls.setdefault(r["caller_id"], []).append(dict(r))
        row = conn.execute("SELECT overview_json, flow_json FROM code_repos WHERE id = ?", (repo_id,)).fetchone()
    stored = loads(row["flow_json"], {}) if row else {}
    return syms, calls, loads(row["overview_json"], {}) if row else {}, (stored or {}).get("annotations", {})


def choose_roots(syms: dict[int, dict], overview: dict) -> list[dict]:
    """入口候选：每个入口返回 {symbol_id, path, label, reason}。"""
    roots: list[dict] = []
    seen: set[int] = set()
    for e in overview.get("entry_points", []):
        if e["kind"] not in ("main_guard", "main_function", "console_script"):
            continue
        cands = [s for s in syms.values() if s["path"] == e["path"] and (
            (e["symbol"] == "<main>" and s["kind"] == "main") or (e["symbol"] not in (None, "<main>") and s["name"] == e["symbol"]))]
        if not cands:
            continue
        s = cands[0]
        if s["id"] not in seen:
            seen.add(s["id"])
            roots.append({"symbol_id": s["id"], "path": s["path"], "label": f"{s['path']} · {s['name'] if s['kind'] != 'main' else '__main__'}", "reason": e["reason"], "role": e.get("role")})
    return roots


def build_flow(app: AppContext, repo_id: str, symbol_id: int | None = None, *, max_depth: int = 4, max_nodes: int = 60) -> dict:
    syms, calls, overview, annotations = _load(app, repo_id)
    if not syms:
        raise ApiError("not_found", "该仓库还没有可分析的源码符号。", 404)
    roots = choose_roots(syms, overview)
    if symbol_id is None:
        if roots:
            symbol_id = roots[0]["symbol_id"]
        else:  # 没有入口：退化为包含最多调用的函数，并如实说明
            symbol_id = max(syms, key=lambda i: len(calls.get(i, [])))
    root = syms.get(symbol_id)
    if root is None:
        raise ApiError("not_found", "指定的入口符号不存在", 404)

    nodes: dict[int, dict] = {}
    edges: list[dict] = []
    order = [0]

    def add_node(sid: int, depth: int, uncertain: bool) -> dict:
        s = syms[sid]
        callee_names = [c["callee_name"] for c in calls.get(sid, [])]
        order[0] += 1
        n = {
            "id": sid, "label": s["qualname"] if s["kind"] != "main" else "__main__", "name": s["name"], "path": s["path"], "kind": s["kind"],
            "line_start": s["line_start"], "line_end": s["line_end"], "depth": depth, "order": order[0], "signature": s["signature"],
            "doc": s["doc"], "phase": phase_of(s["name"], s["doc"] or "", callee_names, s["kind"]), "uncertain": uncertain,
        }
        nodes[sid] = n
        return n

    add_node(symbol_id, 0, False)
    truncated = False
    reasons: set[str] = set()  # 被截断的原因：depth（超过展开深度）/ nodes（超过节点数上限）

    def visit(sid: int, depth: int) -> None:
        nonlocal truncated
        for c in calls.get(sid, []):
            tgt = c["callee_id"]
            if c["certainty"] in ("external", "ambiguous") or tgt is None or tgt not in syms:
                continue
            uncertain = c["certainty"] != "resolved"
            if tgt in nodes:
                edges.append({"source": sid, "target": tgt, "line": c["line"], "certainty": c["certainty"], "order": len(edges) + 1})
                continue
            if depth + 1 > max_depth or len(nodes) >= max_nodes:
                truncated = True
                reasons.add("depth" if depth + 1 > max_depth else "nodes")
                continue
            add_node(tgt, depth + 1, uncertain)
            edges.append({"source": sid, "target": tgt, "line": c["line"], "certainty": c["certainty"], "order": len(edges) + 1})
            visit(tgt, depth + 1)

    visit(symbol_id, 0)
    ordered = sorted(nodes.values(), key=lambda n: n["order"])
    flow = {
        "root": symbol_id, "roots": roots, "nodes": ordered, "edges": edges, "truncated": truncated, "truncated_by": sorted(reasons), "max_depth": max_depth, "max_nodes": max_nodes,
        "note": "流程由静态调用图生成：只包含项目内部、能确定指向的调用；动态调用无法静态确定，可能缺失。",
        "annotations": {str(n["id"]): annotations[str(n["id"])] for n in ordered if str(n["id"]) in annotations},
    }
    if len(roots) == 0:
        flow["note"] += " 项目中没有识别到程序入口，已选择调用最多的函数作为起点。"
    return flow


def save_annotations(app: AppContext, repo_id: str, annotations: dict[str, dict]) -> None:
    """按符号 id 持久化 AI 生成的节点名称与一句话说明（与流程无关，任何流程里的同一个符号共用）。"""
    with app.db.session() as conn:
        row = conn.execute("SELECT flow_json FROM code_repos WHERE id = ?", (repo_id,)).fetchone()
        stored = loads(row["flow_json"], {}) if row else {}
        merged = {**(stored or {}).get("annotations", {}), **annotations}
        conn.execute("UPDATE code_repos SET flow_json = ? WHERE id = ?", (dumps({"annotations": merged}), repo_id))
