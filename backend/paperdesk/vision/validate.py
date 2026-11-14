"""视觉模型输出的校验（纯函数，不调用模型）。

核心原则：视觉模型“看图”的结果不能直接信。
- 框架图：连线必须指向存在的模块；模块名要尽量能在图的 PDF 文字层里找到；找不到的标出来。
- 实验图：只有在图的文字层/图注里真的出现过的数字才允许标为“精确”，其余一律降级为“约/估读”；
  分析文字里出现的、既不在图里也不在所引原文里的数字会被指出来。绝不替模型把读不出的数值补上。
- 表格：每个单元格都和图的文字层比对，比对不上的单元格标记为“未核对”。
"""
from __future__ import annotations

import re

KINDS = {"input", "process", "model", "output", "data", "other"}
FLOW_KINDS = {"data", "control", "loss", "other"}
MAX_MODULES = 40
MAX_FLOWS = 80

_NUM_RE = re.compile(r"(?<![A-Za-z0-9_.])[-+]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?:[eE][-+]?\d+)?")
_MINUS = str.maketrans({"−": "-", "–": "-", "—": "-", "﹣": "-", "＋": "+"})


def clean(s) -> str:
    return re.sub(r"\s+", " ", str(s if s is not None else "")).strip()


def clean_cell(s) -> str:
    """清理表格单元格但保留单元格内部的换行。"""
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in re.split(r"\r?\n", str(s if s is not None else ""))]
    return "\n".join(line for line in lines if line)


def norm_text(s: str) -> str:
    return re.sub(r"[\W_]+", "", (s or "").translate(_MINUS).lower())


def to_float(tok: str) -> float | None:
    try:
        return float(tok.translate(_MINUS).replace(",", "").lstrip("+"))
    except ValueError:
        return None


def numbers_in(text: str) -> list[float]:
    out: list[float] = []
    for m in _NUM_RE.finditer((text or "").translate(_MINUS)):
        v = to_float(m.group(0))
        if v is not None:
            out.append(v)
    return out


def number_set(text: str) -> set[float]:
    return {round(v, 9) for v in numbers_in(text)}


def value_verified(value: str, source_nums: set[float]) -> bool:
    nums = numbers_in(value)
    return bool(nums) and all(round(n, 9) in source_nums for n in nums)


def label_in_text(label: str, source_norm: str) -> bool | None:
    """模块名是否出现在图的文字层里。文字层为空（位图）时返回 None（无法核对）。"""
    if not source_norm:
        return None
    n = norm_text(label)
    if not n:
        return None
    if n in source_norm:
        return True
    tokens = [norm_text(t) for t in re.findall(r"\w+", label.lower()) if len(t) > 1]
    if not tokens:
        return False
    hit = sum(1 for t in tokens if t in source_norm)
    return hit / len(tokens) >= 0.75


# ---------------- 框架图 ----------------
def validate_framework(raw: dict, source_text: str) -> tuple[dict, list[str]]:
    """返回 (规范化的结构, 告警)。少于 2 个模块视为识别失败（抛 ValueError）。"""
    warnings: list[str] = []
    source_norm = norm_text(source_text)
    mods: list[dict] = []
    idmap: dict[str, str] = {}
    used: set[str] = set()
    for i, m in enumerate(raw.get("modules") or []):
        if not isinstance(m, dict):
            continue
        label = clean(m.get("label"))
        if not label:
            continue
        orig = clean(m.get("id")) or f"m{i + 1}"
        mid = re.sub(r"[^A-Za-z0-9_]", "_", orig) or f"m{i + 1}"
        while mid in used:
            mid += "_"
        used.add(mid)
        idmap[orig] = mid
        kind = clean(m.get("kind")).lower()
        mods.append({
            "id": mid, "label": label, "role": clean(m.get("role")) or None,
            "kind": kind if kind in KINDS else "other", "group": clean(m.get("group")) or None,
            "in_text": label_in_text(label, source_norm),
        })
    if len(mods) > MAX_MODULES:
        warnings.append(f"识别出 {len(mods)} 个模块，只保留前 {MAX_MODULES} 个。")
        mods = mods[:MAX_MODULES]
    if len(mods) < 2:
        raise ValueError("没有识别出至少两个模块，这张图可能不是框架图。")
    ids = {m["id"] for m in mods}

    flows: list[dict] = []
    seen: set[tuple] = set()
    dropped = 0
    for f in raw.get("flows") or []:
        if not isinstance(f, dict):
            continue
        a, b = idmap.get(clean(f.get("from")), clean(f.get("from"))), idmap.get(clean(f.get("to")), clean(f.get("to")))
        if a not in ids or b not in ids or a == b:
            dropped += 1
            continue
        label = clean(f.get("label"))
        key = (a, b, label)
        if key in seen:
            continue
        seen.add(key)
        kind = clean(f.get("kind")).lower()
        flows.append({"from": a, "to": b, "label": label, "kind": kind if kind in FLOW_KINDS else "other"})
    if dropped:
        warnings.append(f"有 {dropped} 条连线引用了不存在的模块或首尾相同，已丢弃。")
    flows = flows[:MAX_FLOWS]
    linked = {x for f in flows for x in (f["from"], f["to"])}
    isolated = [m["label"] for m in mods if m["id"] not in linked]
    if flows and isolated:
        warnings.append("这些模块没有任何连线：" + "、".join(isolated[:6]))
    if not flows:
        warnings.append("没有识别出连线，只列出了模块。")

    groups: list[dict] = []
    for i, g in enumerate(raw.get("groups") or []):
        if not isinstance(g, dict) or not clean(g.get("label")):
            continue
        members = [idmap.get(clean(x), clean(x)) for x in (g.get("members") or [])]
        members = [x for x in dict.fromkeys(members) if x in ids]
        if members:
            groups.append({"id": clean(g.get("id")) or f"g{i + 1}", "label": clean(g.get("label")), "members": members})
    if not groups:  # 没有显式分组时，用模块自带的 group 字段归并
        by: dict[str, list[str]] = {}
        for m in mods:
            if m["group"]:
                by.setdefault(m["group"], []).append(m["id"])
        groups = [{"id": f"g{i + 1}", "label": k, "members": v} for i, (k, v) in enumerate(by.items())]

    checkable = [m for m in mods if m["in_text"] is not None]
    if not source_norm:
        warnings.append("这张图的文字不在 PDF 文字层里，模块名称无法逐一核对，请对照原图。")
    else:
        missing = [m["label"] for m in checkable if not m["in_text"]]
        if missing:
            warnings.append("这些模块名在 PDF 文字层里没找到，可能识别有误：" + "、".join(missing[:6]))
    uncertain = [clean(u) for u in (raw.get("uncertain") or []) if clean(u)][:8]
    return {
        "summary": clean(raw.get("summary")), "modules": mods, "flows": flows, "groups": groups, "uncertain": uncertain,
        "verifiable": bool(source_norm),
    }, warnings


def framework_text(fw: dict, label: str | None) -> str:
    """框架图的文字化描述，写回论文段落供检索（附明确的来源说明）。"""
    names = {m["id"]: m["label"] for m in fw["modules"]}
    lines = [f"【框架图识别结果 · {label or '图'}】（由视觉模型识别，后端校验了连线与模块名；非论文原文）"]
    if fw.get("summary"):
        lines.append("概述：" + fw["summary"])
    lines.append("模块：" + "；".join(m["label"] + (f"（{m['role']}）" if m.get("role") else "") for m in fw["modules"]))
    if fw["flows"]:
        lines.append("连线：" + "；".join(
            f"{names[f['from']]} → {names[f['to']]}" + (f"（{f['label']}）" if f.get("label") else "") for f in fw["flows"]))
    return "\n".join(lines)


# ---------------- 实验图 ----------------
def validate_chart(raw: dict, source_text: str, caption: str) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    src_nums = number_set(f"{source_text} {caption}")
    raw_panels = raw.get("panels")
    if not isinstance(raw_panels, list) or not raw_panels:
        raw_panels = [raw] if any(k in raw for k in ("series", "observations", "values", "chart_type")) else []
    panels: list[dict] = []
    downgraded = 0
    loose = 0
    for i, p in enumerate(raw_panels[:8]):
        if not isinstance(p, dict):
            continue

        def axis(a) -> dict:
            a = a if isinstance(a, dict) else {}
            return {"label": clean(a.get("label")), "unit": clean(a.get("unit")), "range": clean(a.get("range"))}

        values: list[dict] = []
        for v in p.get("values") or []:
            if not isinstance(v, dict):
                continue
            value, text = clean(v.get("value")), clean(v.get("text"))
            if not value:
                continue
            precision = "exact" if clean(v.get("precision")).lower() == "exact" else "approx"
            verified = False
            if precision == "exact":
                verified = value_verified(value, src_nums)
                if not verified:
                    precision = "approx"
                    downgraded += 1
            values.append({"text": text, "value": value, "precision": precision, "verified": verified})
        obs = []
        for o in p.get("observations") or []:
            t = clean(o if isinstance(o, str) else (o or {}).get("text"))
            if not t:
                continue
            bad = [n for n in numbers_in(t) if round(n, 9) not in src_nums and not (float(n).is_integer() and abs(n) < 10)]
            if bad:
                loose += 1
            obs.append({"text": t, "has_unverified_numbers": bool(bad)})
        panels.append({
            "label": clean(p.get("label")) or (chr(ord("a") + i) if len(raw_panels) > 1 else ""),
            "chart_type": clean(p.get("chart_type")).lower() or "other", "title": clean(p.get("title")),
            "x_axis": axis(p.get("x_axis")), "y_axis": axis(p.get("y_axis")),
            "series": [clean(s) for s in (p.get("series") or []) if clean(s)][:20],
            "observations": obs[:10], "values": values[:20],
        })
    if not panels:
        raise ValueError("没有识别出任何子图内容。")
    if downgraded:
        warnings.append(f"有 {downgraded} 个数值被模型标为“精确”，但在图的文字层/图注里没有找到，已降级为读图估计。")
    if loose:
        warnings.append(f"有 {loose} 条观察里含有图中找不到的具体数字，请对照原图确认。")
    if not number_set(source_text):
        warnings.append("这张图没有可用的文字层数字，所有数值只能是读图估计。")
    return {
        "panels": panels, "unreadable": [clean(u) for u in (raw.get("unreadable") or []) if clean(u)][:8],
        "verifiable": bool(number_set(source_text)),
    }, warnings


# ---------------- 表格 ----------------
def validate_table(raw: dict, source_text: str) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    columns = [clean_cell(c) for c in (raw.get("columns") or [])]
    rows = [[clean_cell(c) for c in r] for r in (raw.get("rows") or []) if isinstance(r, list)]
    if not rows:
        raise ValueError("没有识别出表格的数据行。")
    ncols = max([len(columns), *(len(r) for r in rows)])
    if len(columns) < ncols:
        if columns:
            warnings.append("表头的列数少于数据行，已用空表头补齐。")
        columns += [""] * (ncols - len(columns))
    ragged = sum(1 for r in rows if len(r) != ncols)
    if ragged:
        warnings.append(f"有 {ragged} 行的单元格数与列数不一致，已补空格。")
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    if len(rows) > 80:
        warnings.append("表格超过 80 行，只保留前 80 行。")
        rows = rows[:80]

    src_norm = norm_text(source_text)
    src_nums = number_set(source_text)
    unverified: list[list[int]] = []
    total = 0
    if src_norm:
        for ri, r in enumerate(rows):
            for ci, cell in enumerate(r):
                if not cell or cell == "?":
                    continue
                total += 1
                nums = numbers_in(cell)
                ok = (all(round(n, 9) in src_nums for n in nums) if nums and not re.search(r"[A-Za-z一-鿿]{2,}", cell)
                      else bool(label_in_text(cell, src_norm)))
                if not ok:
                    unverified.append([ri, ci])
    unreadable = sum(1 for r in rows for c in r if c == "?")
    ratio = None if not src_norm or not total else round(1 - len(unverified) / total, 3)
    if not src_norm:
        warnings.append("这张表的文字不在 PDF 文字层里，单元格无法逐格核对，请对照原图。")
    elif unverified:
        warnings.append(f"{len(unverified)}/{total} 个单元格在 PDF 文字层里没有找到，已标记，可能识别有误。")
    if unreadable:
        warnings.append(f"有 {unreadable} 个单元格模型标为看不清。")
    return {
        "title": clean(raw.get("title")), "columns": columns, "rows": rows, "notes": clean(raw.get("notes")),
        "unverified_cells": unverified, "verified_ratio": ratio, "verifiable": bool(src_norm),
    }, warnings


def table_markdown(t: dict) -> str:
    def esc(s: str) -> str:
        return s.replace("|", "\\|")

    head = "| " + " | ".join(esc(c) or " " for c in t["columns"]) + " |"
    sep = "|" + "|".join("---" for _ in t["columns"]) + "|"
    body = ["| " + " | ".join(esc(c) or " " for c in r) + " |" for r in t["rows"]]
    return "\n".join([head, sep, *body])


def table_text(t: dict, label: str | None) -> str:
    ratio = t.get("verified_ratio")
    note = f"，已与 PDF 文字层逐格比对（{round(ratio * 100)}% 的单元格找到）" if ratio is not None else "，未能与文字层比对"
    lines = [f"【表格识别结果 · {label or '表'}】（由视觉模型转录自截图{note}；非论文原文）"]
    if t.get("title"):
        lines.append(t["title"])
    lines.append(table_markdown(t))
    if t.get("notes"):
        lines.append("注：" + t["notes"])
    return "\n".join(lines)


# ---------------- 分析文字里的数字 ----------------
_SKIP_NUM_CTX = re.compile(r"(?:fig(?:ure)?s?\.?|table|eq(?:uation)?\.?|图|表|公式|第)\s*$", re.I)


def ungrounded_numbers(texts: list[str], allowed: str) -> list[str]:
    """分析文字里出现、但既不在图的识别结果/文字层、也不在所引原文里的具体数字（小整数与编号除外）。"""
    allowed_nums = number_set(allowed)
    bad: list[str] = []
    for t in texts:
        plain = re.sub(r"\[(?:S\d+)?P\d+\]", "", t)
        for m in _NUM_RE.finditer(plain.translate(_MINUS)):
            tok = m.group(0)
            v = to_float(tok)
            if v is None or round(v, 9) in allowed_nums:
                continue
            if _SKIP_NUM_CTX.search(plain[max(0, m.start() - 8):m.start()]):
                continue
            followed = plain[m.end():m.end() + 1]
            if float(v).is_integer() and abs(v) < 100 and followed != "%":
                continue  # “3 个模块”“第 2 步”这类小整数不值得追究
            if 1900 <= v <= 2100 and float(v).is_integer():
                continue  # 年份
            if tok not in bad:
                bad.append(tok)
    return bad
