"""思维导图的纯逻辑：模型输出的校验/规范化，以及“重新生成时保留用户编辑”的合并策略。

这里没有数据库与网络，便于单元测试。要点：
- 模型只能引用我们实际发给它的摘录编号；任何不在 valid_ords 里的编号都会被丢弃（计入 report），
  一个没有任何真实依据的叶子节点会被整个丢弃，而不是留下一个“看起来有道理”的节点。
- 合并策略见 merge_trees 的文档：用户改过/新建的节点永远保留，未改动的 AI 节点被新结果覆盖。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable

VIEWS = ("logic", "method", "experiment")
VIEW_LABELS = {"logic": "逻辑视角", "method": "方法视角", "experiment": "实验视角"}

# 节点类型 → 界面标签。未知类型一律归为 topic，避免前端出现无法理解的标签。
KIND_LABELS = {
    "root": "主题", "topic": "要点",
    "problem": "问题", "motivation": "动机", "challenge": "挑战", "idea": "核心思想", "contribution": "贡献",
    "method": "方法", "module": "模块", "step": "步骤", "formula": "公式", "concept": "概念",
    "setup": "设置", "dataset": "数据集", "baseline": "基线", "metric": "指标", "result": "结果", "ablation": "消融",
    "finding": "发现", "conclusion": "结论", "limitation": "局限",
}
KIND_ALIASES = {
    "background": "problem", "gap": "motivation", "insight": "idea", "approach": "method", "algorithm": "method",
    "architecture": "module", "component": "module", "stage": "step", "equation": "formula", "loss": "formula",
    "experiment": "setup", "evaluation": "setup", "benchmark": "dataset", "data": "dataset", "comparison": "baseline",
    "analysis": "finding", "observation": "finding", "future": "limitation", "limitations": "limitation",
    "contributions": "contribution", "challenges": "challenge", "results": "result", "summary": "conclusion",
}

MAX_DEPTH = 4  # 根为第 1 层
TITLE_MAX = 24
SUMMARY_MAX = 80
MAX_CHILDREN = 8
MAX_NODES = 90  # 生成结果的节点总数上限（含根）
MIN_NODES = 4  # 全图至少要有根 + 3 个有依据的节点，否则视为无效并重试
USER_TITLE_MAX = 60
USER_SUMMARY_MAX = 400

_CITE = re.compile(r"\[P(\d+)\]")
_BULLET = re.compile(r"^\s*(?:[-*•·]|\d+[.、)）]|[一二三四五六七八九十]+[、.])\s*")


class TreeError(ValueError):
    """模型输出的结构无法使用。消息是写给模型看的（重试时原样附上）。"""


@dataclass
class GNode:
    title: str
    summary: str = ""
    kind: str = "topic"
    ords: list[int] = field(default_factory=list)
    figure_ids: list[int] = field(default_factory=list)
    equation_ids: list[int] = field(default_factory=list)
    derived: bool = False  # 证据是从下级节点汇总来的（分组节点自己没有直接引用）
    children: list["GNode"] = field(default_factory=list)

    def count(self) -> int:
        return 1 + sum(c.count() for c in self.children)


@dataclass
class ParseReport:
    """生成结果的核实报告：给用户看（“有多少引用被丢弃”），也是测试断言的依据。"""
    dropped_refs: int = 0  # 模型引用了不存在的摘录编号
    dropped_nodes: int = 0  # 因为没有任何有效依据而被丢弃的节点
    dropped_figure_refs: int = 0
    trimmed_text: int = 0  # 标题/摘要被截断的次数
    depth_cut: int = 0  # 因层级过深被丢弃的节点
    child_cut: int = 0  # 因同级过多被丢弃的节点
    duplicates: int = 0  # 同级重名被合并
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "dropped_refs": self.dropped_refs, "dropped_nodes": self.dropped_nodes,
            "dropped_figure_refs": self.dropped_figure_refs, "trimmed_text": self.trimmed_text,
            "depth_cut": self.depth_cut, "child_cut": self.child_cut, "duplicates": self.duplicates, "notes": self.notes,
        }


# ---------------------------------------------------------------- 文本规范化
def clean_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


_REF = r"P(\d+)(?!\d)"
# 一个或多个编号（P46、[P46]、【P46、P47】、(P46, P47)…）连成一段
_REF_RUN = re.compile(
    r"[\[【(（]?\s*(?<![A-Za-z0-9])" + _REF + r"(?:\s*[、,，/和及与]\s*(?:" + _REF + r"))*\s*[\]】)）]?"
)
_JARGON = (("摘录里", "所引原文里"), ("摘录中", "所引原文中"), ("摘录的", "所引原文的"), ("摘录", "所引原文"))


def humanize(text: str, pages: dict[int, int] | None = None, *, strict: bool = False) -> str:
    """把模型写进正文里的内部段落编号换成读者看得懂的页码，并去掉暴露检索机制的措辞。
    “论文在P46明确指出” → “论文在第 12 页明确指出”；编号对应不到页码时直接去掉；“摘录里没有…” → “所引原文里没有…”。"""
    pages = pages or {}

    def repl(m: re.Match) -> str:
        nums = [int(n) for n in re.findall(r"P(\d+)(?!\d)", m.group(0))]
        shown = sorted({pages[n] for n in nums if n in pages})
        if shown:
            return f"第 {'、'.join(map(str, shown))} 页"
        # 对应不到页码：带括号的明显是内部引用，直接去掉；没括号的可能是正文里的真实记号（如“P3 蛋白”），保持原样
        return "" if strict or m.group(0)[:1] in "[【(（" else m.group(0)

    out = _REF_RUN.sub(repl, text or "")
    for old, new in _JARGON:
        out = out.replace(old, new)
    out = re.sub(r"\s+([，。；、）])", r"\1", out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def norm_title(text: str) -> str:
    """用于比较：小写、去标点与空白。"""
    return re.sub(r"[\s\W_]+", "", (text or "").lower())


def title_similarity(a: str, b: str) -> float:
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def normalize_kind(kind: Any, *, root: bool = False) -> str:
    if root:
        return "root"
    k = clean_text(kind).lower()
    k = KIND_ALIASES.get(k, k)
    return k if k in KIND_LABELS and k != "root" else "topic"


def _as_ints(value: Any) -> list[int]:
    """容忍 [12, "P13", "[P14]"] 这类写法，保序去重。"""
    out: list[int] = []
    if isinstance(value, (int, str)):
        value = [value]
    if not isinstance(value, list):
        return out
    for v in value:
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            n = v
        elif isinstance(v, str):
            m = re.fullmatch(r"\s*\[?\s*[PFE]?\s*(\d+)\s*\]?\s*", v, re.I)
            if not m:
                continue
            n = int(m.group(1))
        else:
            continue
        if n not in out:
            out.append(n)
    return out


# ---------------------------------------------------------------- 模型输出 → 规范化树
def _find_root(data: Any) -> dict:
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict):
        raise TreeError("输出不是 JSON 对象。请输出形如 {\"root\": {...}} 的 JSON。")
    for key in ("root", "tree", "mindmap", "map"):
        if isinstance(data.get(key), dict):
            return data[key]
    if "title" in data and isinstance(data.get("children"), list):
        return data
    raise TreeError("JSON 里没有 root 节点。请输出形如 {\"root\": {\"title\": ..., \"children\": [...]}} 的 JSON。")


def parse_tree(
    raw: Any, *, valid_ords: set[int], valid_figures: set[int] | None = None, valid_equations: set[int] | None = None,
    min_nodes: int = MIN_NODES, max_nodes: int = MAX_NODES, max_depth: int = MAX_DEPTH,
) -> tuple[GNode, ParseReport]:
    """校验并规范化模型输出。无法使用时抛 TreeError（带可读原因，用于让模型重写一次）。"""
    report = ParseReport()
    valid_figures = valid_figures or set()
    valid_equations = valid_equations or set()
    root_raw = _find_root(raw)

    def build(node: Any, depth: int, is_root: bool) -> GNode | None:
        if not isinstance(node, dict):
            return None
        title_raw = clean_text(node.get("title") or node.get("name") or node.get("label"))
        summary_raw = clean_text(node.get("summary") or node.get("description") or "")
        cited = [int(m) for m in _CITE.findall(f"{title_raw} {summary_raw}")]
        title = humanize(_BULLET.sub("", _CITE.sub("", title_raw)).strip(), strict=True)
        summary = humanize(_CITE.sub("", summary_raw).strip(), strict=True)
        if not title:
            return None
        if len(title) > TITLE_MAX:
            report.trimmed_text += 1
            title = clip(title, TITLE_MAX)
        if len(summary) > SUMMARY_MAX:
            report.trimmed_text += 1
            summary = clip(summary, SUMMARY_MAX)

        listed = _as_ints(node.get("evidence_ords") if "evidence_ords" in node else node.get("evidence"))
        ords: list[int] = []
        for o in [*listed, *cited]:
            if o in valid_ords:
                if o not in ords:
                    ords.append(o)
            else:
                report.dropped_refs += 1
        figs = []
        for f in _as_ints(node.get("figure_ids")):
            if f in valid_figures:
                figs.append(f)
            else:
                report.dropped_figure_refs += 1
        eqs = []
        for e in _as_ints(node.get("equation_ids")):
            if e in valid_equations:
                eqs.append(e)
            else:
                report.dropped_figure_refs += 1

        g = GNode(title=title, summary=summary, kind=normalize_kind(node.get("kind"), root=is_root), ords=ords[:6],
                  figure_ids=figs[:4], equation_ids=eqs[:4])
        kids_raw = node.get("children")
        if isinstance(kids_raw, list):
            if depth >= max_depth:
                report.depth_cut += sum(1 for k in kids_raw if isinstance(k, dict))
            else:
                if len(kids_raw) > MAX_CHILDREN:
                    report.child_cut += len(kids_raw) - MAX_CHILDREN
                seen: list[str] = []
                for k in kids_raw[:MAX_CHILDREN]:
                    child = build(k, depth + 1, False)
                    if child is None:
                        continue
                    if any(title_similarity(child.title, s) >= 0.95 for s in seen):
                        report.duplicates += 1
                        continue
                    seen.append(child.title)
                    g.children.append(child)
        # 分组节点自己没有直接引用时，汇总下级节点的依据（标记 derived，界面上如实标注）
        if not g.ords:
            for c in g.children:
                for o in c.ords:
                    if o not in g.ords and len(g.ords) < 3:
                        g.ords.append(o)
            g.derived = bool(g.ords)
        # 叶子（或下级全被丢弃的）节点没有任何真实依据：整个丢弃
        if not is_root and not g.ords:
            report.dropped_nodes += 1 + g.count() - 1
            return None
        return g

    root = build(root_raw, 1, True)
    if root is None:
        raise TreeError("根节点缺少 title。")
    _cap_nodes(root, max_nodes, report)
    if root.count() < min_nodes:
        raise TreeError(
            f"有效节点太少（只有 {root.count()} 个）。每个节点都必须带有摘录里真实存在的 evidence_ords；"
            "不要引用摘录以外的编号，也不要只输出根节点。"
        )
    if report.dropped_refs:
        report.notes.append(f"模型引用了 {report.dropped_refs} 个不存在于所给摘录的段落编号，已丢弃。")
    if report.dropped_nodes:
        report.notes.append(f"{report.dropped_nodes} 个节点没有任何有效依据，已丢弃。")
    return root, report


def _cap_nodes(root: GNode, limit: int, report: ParseReport) -> None:
    """总节点数超限：按层序保留靠前的节点，丢弃最深、最靠后的。"""
    total = root.count()
    if total <= limit:
        return
    order: list[tuple[GNode, GNode | None]] = []
    queue: list[tuple[GNode, GNode | None]] = [(root, None)]
    while queue:
        node, parent = queue.pop(0)
        order.append((node, parent))
        queue.extend((c, node) for c in node.children)
    for node, parent in reversed(order):
        if total <= limit:
            break
        if parent is not None and not node.children:
            parent.children.remove(node)
            total -= 1
            report.child_cut += 1


def flatten(root: GNode) -> list[GNode]:
    out = [root]
    for c in root.children:
        out.extend(flatten(c))
    return out


# ---------------------------------------------------------------- 合并策略
@dataclass
class OldNode:
    id: str
    parent_id: str | None
    ord: int
    title: str
    summary: str
    kind: str
    ords: list[int]
    figure_ids: list[int]
    equation_ids: list[int]
    user_edited: bool
    origin: str  # ai | user

    @property
    def protected(self) -> bool:
        return self.user_edited or self.origin == "user"


@dataclass
class MergePlan:
    """把新生成结果应用到数据库所需的全部操作（按此顺序执行：delete → update → insert → reorder）。"""
    inserts: list[dict] = field(default_factory=list)  # {id, parent_id, title, summary, kind, ords, figure_ids, equation_ids, derived}
    updates: dict[str, dict] = field(default_factory=dict)  # id → {title, summary, kind, ords, ...}
    deletes: list[str] = field(default_factory=list)  # 被删除子树的根（子节点随外键级联删除）
    order: dict[str, list[str]] = field(default_factory=dict)  # parent_id → 子节点 id 的最终顺序
    stats: dict[str, int] = field(default_factory=lambda: {"added": 0, "updated": 0, "removed": 0, "kept_user": 0, "kept_scaffold": 0})


MATCH_THRESHOLD = 0.75


def merge_trees(
    old: list[OldNode], new_root: GNode, *, scope_root_id: str | None, new_id: Callable[[], str],
) -> MergePlan:
    """把新生成的树合并进旧树。

    策略（“覆盖 + 保留用户编辑”，不保留历史版本）：
    1. 用户改过标题/摘要/位置之外内容、移动过或新建的节点（protected）永远保留，原样不动；
       它们的所有祖先也保留（否则会断开），祖先若没被改过仍沿用旧内容。
    2. 新节点按“同一父节点下标题相同/高度相似”匹配旧节点：
       - 匹配到 protected 节点 → 保留用户的版本，丢弃模型的同名新节点；模型给出的子节点继续往下合并；
       - 匹配到未改动的 AI 节点 → 沿用旧 id（所以位置、折叠状态、关联的解释卡片不丢），内容更新为新结果；
       - 没匹配到 → 作为新节点插入。
    3. 旧的、未改动、没被匹配的 AI 节点被删除（它们已被新结果取代）；
       未被匹配、但受保护的节点留在原父节点下，按原序号插回。
    scope_root_id 为 None 表示整张图（含根节点）；否则只合并该节点之下的分支，该节点自身不变。
    """
    by_id = {n.id: n for n in old}
    kids: dict[str | None, list[OldNode]] = {}
    for n in sorted(old, key=lambda x: (x.ord, x.id)):
        kids.setdefault(n.parent_id, []).append(n)

    plan = MergePlan()
    keep: set[str] = set()  # 受保护节点 + 它们的祖先
    for n in old:
        if n.protected:
            cur: OldNode | None = n
            while cur is not None and cur.id not in keep:
                keep.add(cur.id)
                if cur.id == scope_root_id:
                    break
                cur = by_id.get(cur.parent_id) if cur.parent_id else None
    plan.stats["kept_user"] = sum(1 for n in old if n.protected)

    def fields(g: GNode) -> dict:
        return {"title": g.title, "summary": g.summary, "kind": g.kind, "ords": list(g.ords),
                "figure_ids": list(g.figure_ids), "equation_ids": list(g.equation_ids), "derived": g.derived}

    def place(parent_id: str, parent_is_old: bool, gchildren: list[GNode]) -> None:
        candidates = list(kids.get(parent_id, [])) if parent_is_old else []
        matched: set[str] = set()
        final: list[str] = []
        for g in gchildren:
            best, best_score = None, 0.0
            for c in candidates:
                if c.id in matched:
                    continue
                s = title_similarity(g.title, c.title)
                if s > best_score:
                    best, best_score = c, s
            if best is not None and best_score >= MATCH_THRESHOLD:
                matched.add(best.id)
                final.append(best.id)
                if not best.protected:
                    plan.updates[best.id] = fields(g)
                    plan.stats["updated"] += 1
                place(best.id, True, g.children)
            else:
                nid = new_id()
                plan.inserts.append({"id": nid, "parent_id": parent_id, **fields(g)})
                plan.stats["added"] += 1
                final.append(nid)
                place(nid, False, g.children)
        # 没有被匹配的旧子节点：受保护/必须保留的留下，其余删除
        leftover = [c for c in candidates if c.id not in matched]
        for c in leftover:
            if c.id in keep:
                idx = min(c.ord, len(final))
                final.insert(idx, c.id)
                if not c.protected:
                    plan.stats["kept_scaffold"] += 1
                prune(c)
            else:
                plan.deletes.append(c.id)
                plan.stats["removed"] += len(_subtree(c.id, kids))
        plan.order[parent_id] = final

    def prune(node: OldNode) -> None:
        """保留下来的旧节点（未被新结果匹配）：它的子节点里只有受保护/祖先的留下。"""
        final: list[str] = []
        for c in kids.get(node.id, []):
            if c.id in keep:
                final.append(c.id)
                if not c.protected:
                    plan.stats["kept_scaffold"] += 1
                prune(c)
            else:
                plan.deletes.append(c.id)
                plan.stats["removed"] += len(_subtree(c.id, kids))
        if final:
            plan.order[node.id] = final

    if scope_root_id is None:
        old_root = next((n for n in old if n.parent_id is None), None)
        if old_root is None:
            rid = new_id()
            plan.inserts.append({"id": rid, "parent_id": None, **fields(new_root)})
            plan.stats["added"] += 1
            place(rid, False, new_root.children)
        else:
            if not old_root.protected:
                plan.updates[old_root.id] = fields(new_root)
                plan.stats["updated"] += 1
            place(old_root.id, True, new_root.children)
    else:
        place(scope_root_id, True, new_root.children)
    return plan


def _subtree(node_id: str, kids: dict[str | None, list[OldNode]]) -> list[str]:
    out = [node_id]
    for c in kids.get(node_id, []):
        out.extend(_subtree(c.id, kids))
    return out


# ---------------------------------------------------------------- 导出
def export_markdown(title: str, view_label: str, nodes: list[dict]) -> str:
    """节点是 store.get_tree 给出的字典列表（已排好序）。输出 Markdown 大纲，依据以页码标注。"""
    kids: dict[str | None, list[dict]] = {}
    for n in nodes:
        kids.setdefault(n["parent_id"], []).append(n)
    lines = [f"# {title}", "", f"> 思维导图 · {view_label}（由 Marginalia 生成，括号内为原文页码）", ""]

    def pages(n: dict) -> str:
        ps = sorted({r["page"] for r in n.get("refs", [])})
        return f"（p.{', '.join(str(p) for p in ps)}）" if ps else ""

    def walk(n: dict, depth: int) -> None:
        mark = "·用户添加" if n.get("origin") == "user" else ""
        line = f"{'  ' * depth}- **{n['title']}**{pages(n)}{mark}"
        if n.get("summary"):
            line += f"：{n['summary']}"
        lines.append(line)
        for c in kids.get(n["id"], []):
            walk(c, depth + 1)

    roots = kids.get(None, [])
    for r in roots:
        walk(r, 0)
    return "\n".join(lines) + "\n"
