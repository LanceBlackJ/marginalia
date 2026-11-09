"""B：复现计划与批准闸门。计划只是文档：任何有命令的步骤都要用户逐项批准，批准只落库，**不会在本机执行**。

- 计划由评估结果（静态证据）按固定模板生成，不靠模型“发挥”，每步带评估里的证据；
- 有命令的步骤（下载、安装、编译、运行）一律是“待批准”；批准记录追加式保存，并记下批准当时的命令；
- 批准之后再改命令，批准自动作废（回到待批准），避免“批准了 A、实际运行了 B”；
- 导出为 Markdown 说明或 shell 脚本，由用户在自己的隔离环境里手动运行。
"""
from __future__ import annotations

from pathlib import PurePosixPath

from ..context import AppContext
from ..util import ApiError, dumps, loads, new_id, utcnow
from . import assess

PHASES = ("env", "data", "build", "smoke", "run", "compare", "other")
PHASE_LABEL = {"env": "准备环境", "data": "获取数据", "build": "编译", "smoke": "最小验证", "run": "完整运行", "compare": "对比结果", "other": "其它"}
MAX_STEPS = 60


def _step_row(row, log: list[dict]) -> dict:
    return {
        "id": row["id"], "ord": row["ord"], "phase": row["phase"], "phase_label": PHASE_LABEL.get(row["phase"], row["phase"]),
        "title": row["title"], "detail": row["detail"], "command": row["command"], "cwd": row["cwd"],
        "side_effects": loads(row["side_effects_json"], []), "risk": row["risk"], "evidence": loads(row["evidence_json"], []),
        "needs_approval": bool(row["needs_approval"]), "approval": row["approval"], "done": bool(row["done"]), "note": row["note"],
        "origin": row["origin"], "updated_at": row["updated_at"], "approval_log": log,
    }


def _needs_approval(command: str) -> bool:
    return bool(command.strip())


def _items(report: dict) -> dict[str, dict]:
    return {it["key"]: it for sec in report["sections"] for it in sec["items"]}


def _dirname(path: str) -> str:
    d = str(PurePosixPath(path).parent)
    return "" if d == "." else d


def _evidence(items: dict[str, dict], *keys: str, prefix: tuple[str, ...] = ()) -> list[dict]:
    out: list[dict] = []
    for k, it in items.items():
        if k in keys or any(k.startswith(p) for p in prefix):
            out.extend(it["evidence"][:2])
    return out[:5]


def _build_steps(report: dict, overview: dict) -> list[dict]:
    it = _items(report)
    facts = report["facts"]
    steps: list[dict] = []
    ov_eps = overview.get("entry_points") or []

    def add(phase, title, detail, command="", cwd="", side=None, risk="", evidence=None, note=""):
        steps.append({"phase": phase, "title": title, "detail": detail, "command": command, "cwd": cwd, "side_effects": side or [],
                      "risk": risk, "evidence": evidence or [], "note": note})

    # 1 环境
    deps = [d for d in (overview.get("dependency_files") or [])]
    conda = [d for d in deps if PurePosixPath(d).name.lower() in ("environment.yml", "environment.yaml")]
    reqs = [d for d in deps if PurePosixPath(d).name.lower().startswith("requirements")]
    pyv = it.get("python_version", {})
    pyv_txt = pyv.get("text", "") if pyv.get("status") == "found" else "Python 版本要求：不确定，请先确定与论文年代相符的版本。"
    if conda:
        cmd, cwd = f"conda env create -f {PurePosixPath(conda[0]).name}", _dirname(conda[0])
    elif reqs:
        cmd, cwd = f"python -m venv .venv && . .venv/bin/activate && pip install -r {PurePosixPath(reqs[0]).name}", _dirname(reqs[0])
    elif any(PurePosixPath(d).name.lower() == "setup.py" for d in deps):
        sp = next(d for d in deps if PurePosixPath(d).name.lower() == "setup.py")
        cmd, cwd = "python -m venv .venv && . .venv/bin/activate && pip install -e .", _dirname(sp)
    else:
        cmd, cwd = "", ""
    add("env", "准备隔离的 Python 环境并安装依赖",
        f"{pyv_txt}\n建议在虚拟环境或容器里安装，不要装进系统环境。" + ("" if cmd else "\n没有找到依赖文件，需要你根据代码里的 import 自己确定要装什么。"),
        cmd, cwd, ["安装", "写文件"], "依赖没有固定版本时，最新版本很可能与老代码不兼容。" if any(i["key"] == "risk:unpinned" for s in report["sections"] for i in s["items"]) else "",
        _evidence(it, "python_version", "framework_versions", prefix=("deps:", "envfile:")),
        "命令是按依赖文件类型生成的常规写法，请自己核对后再批准。" if cmd else "")
    # 2 数据
    data_ev = _evidence(it, "repo_data", "download_hints", "paper_data_links", "data_missing", prefix=("dataset:",))
    if facts["needs_download"]:
        add("data", "获取数据", "数据需要另外获取：论文或代码里给出了下载或获取地址。请自己确认来源、许可与大小，下载命令由你填写后再批准。",
            "", "", ["下载"], "下载的数据可能需要注册、付费或很大；本应用不会替你下载。", data_ev)
    elif facts["has_data"]:
        add("data", "确认仓库自带的数据", "仓库里带有数据文件，先确认它们就是论文实验用的数据。", "", "", [], "", data_ev)
    else:
        add("data", "找到数据（缺口）", "论文和代码里都没有找到明确的数据获取方式。这是复现的主要缺口：需要联系作者、自行构造数据，或在论文的补充材料里继续找。", "", "", [], "没有数据就无法复现实验结果。", data_ev)
    # 3 编译
    if facts["compile"]:
        setup = next((d for d in deps if PurePosixPath(d).name.lower() == "setup.py"), "")
        add("build", "编译扩展", "仓库含 Cython/C++/CUDA 代码，需要编译后才能导入。需要本机有对应编译器和头文件。",
            "python setup.py build_ext --inplace" if setup else "", _dirname(setup) if setup else "", ["编译", "写文件"],
            "编译失败是复现老代码最常见的障碍，常见原因是编译器或依赖版本太新。", _evidence(it, "compile"),
            "" if setup else "没有找到 setup.py，请对照 Makefile/CMake 自己写编译命令。")

    def entry_cmd(role: str) -> tuple[str, str, str]:
        """返回 (命令, cwd, 说明)：优先 README 里的命令，其次由入口脚本路径推出。"""
        eps = [e for e in ov_eps if (e.get("role") or e["kind"]) == role]
        if not eps:
            return "", "", ""
        e = eps[0]
        base = PurePosixPath(e["path"]).name
        for rc in facts["commands"]:
            if base in rc:
                return rc, _dirname(e["path"]), "命令来自 README，请核对参数是否与论文设置一致。"
        return f"python {base}", _dirname(e["path"]), "命令由入口脚本路径推出，没有参数；真实参数请对照 README、argparse 默认值和论文设置。"

    # 4 最小验证
    t_cmd, t_cwd, _ = entry_cmd("train")
    if facts["has_train"]:
        base = t_cmd.split()[1] if t_cmd.startswith("python ") and len(t_cmd.split()) > 1 else ""
        add("smoke", "查看训练脚本的参数说明", "先只看参数说明，确认参数名与论文设置的对应关系，再决定试跑的规模。官方没有给出“最小试跑”的方法时，建议先用很小的数据量/轮数跑通流程。",
            f"python {base} --help" if base.endswith(".py") else "", t_cwd, ["执行"], "运行任何仓库代码前请先阅读它，确认没有不想要的副作用（删文件、联网、写系统目录）。",
            _evidence(it, "entry:train", "commands"))
    # 5 完整运行
    if facts["has_train"]:
        add("run", "运行训练", "按论文设置的参数运行训练。命令里的参数只是占位，需要你对照论文填写。", t_cmd, t_cwd,
            ["执行"] + (["占用GPU"] if facts["gpu"] else []), "训练可能耗时很长、占用大量显存；先确认资源。", _evidence(it, "entry:train", "commands", "gpu"),
            entry_cmd("train")[2])
    else:
        add("run", "确定训练方式（缺口）", "没有识别到训练入口：需要你通读代码或联系作者确认怎么训练，或直接使用作者提供的预训练权重。", "", "", [], "", _evidence(it, "entry:train"))
    e_cmd, e_cwd, e_note = entry_cmd("test")
    if facts["has_test"]:
        add("run", "运行评估", "用训练得到的模型或作者提供的权重运行评估，得到论文里报告的指标。", e_cmd, e_cwd, ["执行"], "评估使用的数据划分和指标定义要与论文一致。",
            _evidence(it, "entry:test"), e_note)
    else:
        add("run", "确定评估方式（缺口）", "没有识别到评估入口：论文里的指标可能需要你自己写评估脚本，并确认指标定义与论文一致。", "", "", [], "", _evidence(it, "entry:test"))
    # 6 对比
    add("compare", "把结果带回来对比", "运行完成后，在“结果对比”里导入你得到的数值，与论文报告的数值逐项对比；是否算复现成功由你自己判断。", "", "", [], "", [])
    return steps


def _insert_steps(conn, plan_id: str, steps: list[dict], start: int = 1) -> None:
    now = utcnow()
    for i, s in enumerate(steps, start):
        needs = _needs_approval(s["command"])
        conn.execute(
            "INSERT INTO repro_steps(id, plan_id, ord, phase, title, detail, command, cwd, side_effects_json, risk, evidence_json, "
            "needs_approval, approval, done, note, origin, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)",
            (new_id(), plan_id, i, s["phase"], s["title"], s["detail"], s["command"], s["cwd"], dumps(s["side_effects"]), s["risk"],
             dumps(s["evidence"]), 1 if needs else 0, "pending" if needs else "none", s.get("note", ""), s.get("origin", "generated"), now),
        )


def generate(app: AppContext, repo_id: str, force: bool = False) -> dict:
    repo = assess.service.get_repo(app, repo_id)
    ev = assess.get(app, repo_id)
    if ev["status"] != "ready" or not ev["report"]:
        raise ApiError("conflict", "请先生成“可行性评估”：复现计划是根据评估结果的证据生成的。", 409)
    with app.db.session() as conn:
        paper = conn.execute("SELECT title FROM papers WHERE id = ?", (repo["paper_id"],)).fetchone()
        old = conn.execute("SELECT id FROM repro_plans WHERE repo_id = ?", (repo_id,)).fetchone()
        if old and not force:
            raise ApiError("conflict", "已经有一份复现计划了。重新生成会覆盖你的编辑和批准记录，如确实要重新生成，请确认覆盖。", 409)
        if old:
            conn.execute("DELETE FROM repro_plans WHERE id = ?", (old["id"],))
        plan_id, now = new_id(), utcnow()
        conn.execute("INSERT INTO repro_plans(id, paper_id, repo_id, title, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                     (plan_id, repo["paper_id"], repo_id, f"复现计划：{paper['title'][:60]}", now, now))
        _insert_steps(conn, plan_id, _build_steps(ev["report"], repo["overview"] or {}))
    return get_plan(app, repo_id)


def _plan_row(app: AppContext, repo_id: str):
    with app.db.session() as conn:
        return conn.execute("SELECT * FROM repro_plans WHERE repo_id = ?", (repo_id,)).fetchone()


def get_plan(app: AppContext, repo_id: str) -> dict:
    plan = _plan_row(app, repo_id)
    if plan is None:
        return {"plan": None}
    with app.db.session() as conn:
        rows = conn.execute("SELECT * FROM repro_steps WHERE plan_id = ? ORDER BY ord", (plan["id"],)).fetchall()
        logs: dict[str, list[dict]] = {}
        for a in conn.execute("SELECT * FROM repro_approvals WHERE plan_id = ? ORDER BY id", (plan["id"],)):
            logs.setdefault(a["step_id"], []).append({"decision": a["decision"], "actor": a["actor"], "command": a["command_snapshot"], "note": a["note"], "at": a["at"]})
    steps = [_step_row(r, logs.get(r["id"], [])) for r in rows]
    summary = {k: sum(1 for s in steps if s["approval"] == k) for k in ("pending", "approved", "rejected")}
    summary["done"] = sum(1 for s in steps if s["done"])
    return {"plan": {"id": plan["id"], "paper_id": plan["paper_id"], "repo_id": plan["repo_id"], "title": plan["title"], "created_at": plan["created_at"],
                     "updated_at": plan["updated_at"], "steps": steps, "summary": summary,
                     "note": "计划只是文档：批准只是记录你的决定，Marginalia 不会在本机运行这些命令。"}}


def _step(conn, step_id: str):
    row = conn.execute("SELECT * FROM repro_steps WHERE id = ?", (step_id,)).fetchone()
    if row is None:
        raise ApiError("not_found", "步骤不存在", 404)
    return row


def _log(conn, step, decision: str, note: str = "") -> None:
    conn.execute("INSERT INTO repro_approvals(step_id, plan_id, decision, actor, command_snapshot, note, at) VALUES (?,?,?,?,?,?,?)",
                 (step["id"], step["plan_id"], decision, "user", step["command"], note[:500], utcnow()))


def _touch(conn, plan_id: str) -> None:
    conn.execute("UPDATE repro_plans SET updated_at = ? WHERE id = ?", (utcnow(), plan_id))


def update_step(app: AppContext, step_id: str, patch: dict) -> dict:
    with app.db.session() as conn:
        s = _step(conn, step_id)
        title = str(patch.get("title", s["title"])).strip()[:200] or s["title"]
        detail = str(patch.get("detail", s["detail"]))[:4000]
        command = str(patch.get("command", s["command"]))[:4000]
        cwd = str(patch.get("cwd", s["cwd"])).strip()[:300]
        note = str(patch.get("note", s["note"]))[:1000]
        done = int(bool(patch["done"])) if "done" in patch else s["done"]
        if "\x00" in command or "\x00" in cwd:
            raise ApiError("bad_request", "命令里含有非法字符")
        changed = command != s["command"] or cwd != s["cwd"]
        needs = 1 if _needs_approval(command) else 0
        approval = s["approval"]
        if not needs:
            approval = "none"
        elif changed or not s["needs_approval"]:
            approval = "pending"  # 命令变了或第一次有命令：之前的批准作废
        conn.execute("UPDATE repro_steps SET title=?, detail=?, command=?, cwd=?, needs_approval=?, approval=?, done=?, note=?, updated_at=? WHERE id=?",
                     (title, detail, command, cwd, needs, approval, done, note, utcnow(), step_id))
        if changed and s["approval"] in ("approved", "rejected"):
            _log(conn, {**dict(s), "command": command}, "reset", "命令或目录被修改，之前的决定作废")
        _touch(conn, s["plan_id"])
        plan_id = s["plan_id"]
        repo_id = conn.execute("SELECT repo_id FROM repro_plans WHERE id = ?", (plan_id,)).fetchone()["repo_id"]
    return get_plan(app, repo_id)


def decide(app: AppContext, step_id: str, decision: str, note: str = "") -> dict:
    if decision not in ("approve", "reject", "reset"):
        raise ApiError("bad_request", "decision 必须是 approve / reject / reset")
    with app.db.session() as conn:
        s = _step(conn, step_id)
        if not s["needs_approval"]:
            raise ApiError("conflict", "这一步没有命令，不需要批准。", 409)
        new = {"approve": "approved", "reject": "rejected", "reset": "pending"}[decision]
        conn.execute("UPDATE repro_steps SET approval = ?, updated_at = ? WHERE id = ?", (new, utcnow(), step_id))
        _log(conn, s, {"approve": "approved", "reject": "rejected", "reset": "reset"}[decision], note)
        _touch(conn, s["plan_id"])
        repo_id = conn.execute("SELECT repo_id FROM repro_plans WHERE id = ?", (s["plan_id"],)).fetchone()["repo_id"]
    out = get_plan(app, repo_id)
    out["executed"] = False  # 批准只是记录，没有执行任何东西
    return out


def add_step(app: AppContext, plan_id: str, body: dict) -> dict:
    with app.db.session() as conn:
        plan = conn.execute("SELECT * FROM repro_plans WHERE id = ?", (plan_id,)).fetchone()
        if plan is None:
            raise ApiError("not_found", "计划不存在", 404)
        n = conn.execute("SELECT COUNT(*) FROM repro_steps WHERE plan_id = ?", (plan_id,)).fetchone()[0]
        if n >= MAX_STEPS:
            raise ApiError("bad_request", f"步骤数量最多 {MAX_STEPS} 个")
        title = str(body.get("title", "")).strip()[:200]
        if not title:
            raise ApiError("bad_request", "步骤标题不能为空")
        phase = body.get("phase") if body.get("phase") in PHASES else "other"
        command = str(body.get("command", ""))[:4000]
        side = [str(x)[:20] for x in (body.get("side_effects") or [])][:6]
        _insert_steps(conn, plan_id, [{"phase": phase, "title": title, "detail": str(body.get("detail", ""))[:4000], "command": command, "cwd": str(body.get("cwd", ""))[:300],
                                       "side_effects": side, "risk": "", "evidence": [], "origin": "user"}], start=n + 1)
        _touch(conn, plan_id)
    return get_plan(app, plan["repo_id"])


def delete_step(app: AppContext, step_id: str) -> dict:
    with app.db.session() as conn:
        s = _step(conn, step_id)
        conn.execute("DELETE FROM repro_steps WHERE id = ?", (step_id,))
        _touch(conn, s["plan_id"])
        repo_id = conn.execute("SELECT repo_id FROM repro_plans WHERE id = ?", (s["plan_id"],)).fetchone()["repo_id"]
    return get_plan(app, repo_id)


# ---------------------------------------------------------------- 导出
HEADER = ("> 由 Marginalia 生成。**Marginalia 不会在本机运行这份说明里的任何命令。**\n"
          "> 请在你自己的隔离环境（WSL2、Docker、远程机器）里，逐条阅读后再手动运行；没有批准的步骤不要运行。\n")
APPROVAL_LABEL = {"none": "无需批准", "pending": "待批准", "approved": "已批准", "rejected": "已拒绝"}


def export_markdown(app: AppContext, repo_id: str) -> dict:
    plan = get_plan(app, repo_id)["plan"]
    if plan is None:
        raise ApiError("not_found", "还没有复现计划", 404)
    out = [f"# {plan['title']}", "", HEADER, f"生成时间：{plan['updated_at']}", ""]
    for s in plan["steps"]:
        mark = "x" if s["done"] else " "
        out += [f"## {s['ord']}. [{mark}] {s['title']}（{s['phase_label']}）", "", f"- 批准状态：**{APPROVAL_LABEL[s['approval']]}**"]
        if s["side_effects"]:
            out.append(f"- 会产生的副作用：{'、'.join(s['side_effects'])}")
        if s["cwd"]:
            out.append(f"- 工作目录（相对仓库根）：`{s['cwd']}`")
        if s["risk"]:
            out.append(f"- 风险：{s['risk']}")
        out += ["", s["detail"], ""]
        if s["command"]:
            out += ["```bash", s["command"], "```", ""]
        if s["note"]:
            out += [f"备注：{s['note']}", ""]
        if s["evidence"]:
            out.append("证据：")
            for e in s["evidence"]:
                out.append(f"- {e['path']}:{e['line_start']}" + (f"　`{e['quote'][:100]}`" if e.get("quote") else "") if e["type"] == "code"
                           else f"- 论文第 {e.get('page')} 页：{e.get('quote', '')[:120]}")
            out.append("")
        for a in s["approval_log"]:
            out.append(f"- {a['at']} 用户{ {'approved': '批准', 'rejected': '拒绝', 'reset': '撤销决定'}[a['decision']] }" + (f"（{a['note']}）" if a["note"] else ""))
        if s["approval_log"]:
            out.append("")
    return {"filename": "复现说明.md", "format": "md", "content": "\n".join(out)}


def export_script(app: AppContext, repo_id: str) -> dict:
    plan = get_plan(app, repo_id)["plan"]
    if plan is None:
        raise ApiError("not_found", "还没有复现计划", 404)
    out = ["#!/usr/bin/env bash",
           "# 由 Marginalia 生成。Marginalia 不会在本机运行它；请在你自己的隔离环境里阅读后再运行。",
           "# 只有“已批准”的步骤的命令是启用的；其它步骤以注释保留。",
           "set -euo pipefail", ""]
    for s in plan["steps"]:
        out.append(f"# ---- 步骤 {s['ord']}：{s['title']}（{APPROVAL_LABEL[s['approval']]}）")
        if not s["command"].strip():
            out += [f"# （没有命令，需要手动完成）{s['detail'].splitlines()[0] if s['detail'] else ''}", ""]
            continue
        cmd = s["command"].strip()
        body = f'( cd "{s["cwd"]}" && {cmd} )' if s["cwd"] else cmd
        if s["approval"] == "approved":
            out += [body, ""]
        else:
            out += ["# [未批准，已跳过] " + ln for ln in body.splitlines()] + [""]
    return {"filename": "复现步骤.sh", "format": "sh", "content": "\n".join(out)}

