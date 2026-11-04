"""复现（第 4 阶段）的 HTTP 接口（契约见 docs/API-code.md 的“复现”一节）。

评估、计划、批准、结果对比都不执行用户代码。只有“隔离运行”一节的接口会启动 Docker 容器，
且每次都要 confirm=true，代码只在容器里运行（见 repro/executor.py）。
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from ..code import jobs as code_jobs
from ..code import service
from ..context import AppContext
from ..jobs import JobContext, JobManager
from ..repro import assess, executor, plan, results, sandbox
from ..util import ApiError
from .deps import get_ctx

router = APIRouter(tags=["repro"])


def _handle_assess(ctx: JobContext) -> dict:
    ctx.progress(0.05, "开始静态评估")
    report = code_jobs._run_async(lambda: assess.run(ctx.app, ctx.payload["repo_id"], ctx), ctx)
    return {"effort": report["effort"]["level"], "ai_used": report["ai_used"]}


def register_jobs(manager: JobManager) -> None:
    manager.register("repro_assess", _handle_assess)
    manager.register("repro_exec", executor.handle_exec)
    manager.register("repro_pull", executor.handle_pull)
    executor.recover(manager.app)


def _repo_id(ctx: AppContext, paper_id: str, repo_id: str | None) -> str:
    """没有指定仓库时，取这篇论文最新的一个已分析完成的仓库。"""
    if repo_id:
        repo = service.get_repo(ctx, repo_id)
        if repo["paper_id"] != paper_id:
            raise ApiError("bad_request", "代码仓库不属于这篇论文")
        return repo_id
    ready = [r for r in service.list_repos(ctx, paper_id) if r["status"] == "ready"]
    if not ready:
        raise ApiError("conflict", "这篇论文还没有分析完成的代码仓库，请先在“概览”里获取或导入代码。", 409)
    return ready[-1]["id"]


def _paper_of_plan(ctx: AppContext, plan_id: str) -> str:
    with ctx.db.session() as conn:
        row = conn.execute("SELECT repo_id FROM repro_plans WHERE id = ?", (plan_id,)).fetchone()
    if row is None:
        raise ApiError("not_found", "计划不存在", 404)
    return row["repo_id"]


# ---- 隔离环境检测（只探测，不执行）----
@router.get("/system/sandbox")
def system_sandbox():
    return sandbox.detect()


# ---- A：可行性评估 ----
class RepoBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    repo_id: str | None = None
    force: bool = False


@router.get("/papers/{paper_id}/repro/assessment")
def get_assessment(paper_id: str, repo_id: str | None = Query(default=None), ctx: AppContext = Depends(get_ctx)):
    rid = _repo_id(ctx, paper_id, repo_id)
    return {"repo_id": rid, **assess.get(ctx, rid)}


@router.post("/papers/{paper_id}/repro/assessment")
def start_assessment(paper_id: str, body: RepoBody | None = None, ctx: AppContext = Depends(get_ctx)):
    """后台生成可行性评估：只读静态分析结果与论文原文；有 AI 时会发送论文里提到数据集的段落（记入隐私日志）。"""
    rid = _repo_id(ctx, paper_id, body.repo_id if body else None)
    job_id = ctx.jobs.submit("repro_assess", "复现可行性评估", {"repo_id": rid, "paper_id": paper_id}, paper_id)
    return {"job_id": job_id, "repo_id": rid}


# ---- B：复现计划 ----
@router.get("/papers/{paper_id}/repro/plan")
def get_plan(paper_id: str, repo_id: str | None = Query(default=None), ctx: AppContext = Depends(get_ctx)):
    rid = _repo_id(ctx, paper_id, repo_id)
    return {"repo_id": rid, **plan.get_plan(ctx, rid)}


@router.post("/papers/{paper_id}/repro/plan")
def generate_plan(paper_id: str, body: RepoBody | None = None, ctx: AppContext = Depends(get_ctx)):
    rid = _repo_id(ctx, paper_id, body.repo_id if body else None)
    return {"repo_id": rid, **plan.generate(ctx, rid, force=bool(body and body.force))}


class StepPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str | None = None
    detail: str | None = None
    command: str | None = None
    cwd: str | None = None
    note: str | None = None
    done: bool | None = None


@router.patch("/repro/steps/{step_id}")
def patch_step(step_id: str, body: StepPatch, ctx: AppContext = Depends(get_ctx)):
    """编辑步骤。命令或目录一旦改动，之前的批准自动作废（回到待批准）。"""
    return plan.update_step(ctx, step_id, body.model_dump(exclude_none=True))


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    decision: Literal["approve", "reject", "reset"]
    note: str = ""


@router.post("/repro/steps/{step_id}/approve")
def approve_step(step_id: str, body: DecisionBody, ctx: AppContext = Depends(get_ctx)):
    """批准/拒绝/撤销某一步（追加式记录，含当时的命令）。批准只是记录，不会执行任何东西（返回里 executed=false）。"""
    return plan.decide(ctx, step_id, body.decision, body.note)


class NewStep(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str
    detail: str = ""
    command: str = ""
    cwd: str = ""
    phase: str = "other"
    side_effects: list[str] = []


@router.post("/repro/plans/{plan_id}/steps")
def add_step(plan_id: str, body: NewStep, ctx: AppContext = Depends(get_ctx)):
    return plan.add_step(ctx, plan_id, body.model_dump())


@router.delete("/repro/steps/{step_id}")
def delete_step(step_id: str, ctx: AppContext = Depends(get_ctx)):
    return plan.delete_step(ctx, step_id)


@router.get("/repro/plans/{plan_id}/export")
def export_plan(plan_id: str, format: Literal["md", "sh"] = "md", ctx: AppContext = Depends(get_ctx)):
    """导出成 Markdown 说明或 shell 脚本（只含已批准步骤的命令）。导出的内容由用户在自己的隔离环境里手动运行。"""
    rid = _paper_of_plan(ctx, plan_id)
    return plan.export_markdown(ctx, rid) if format == "md" else plan.export_script(ctx, rid)


# ---- C：结果导入与对比 ----
class TargetBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    value: float | None = None
    std: float | None = None
    unit: str | None = None
    tol_rel: float | None = None
    tol_abs: float | None = None
    comparable: bool | None = None
    page: int | None = None
    ord: int | None = None
    quote: str | None = None
    source: str | None = None


@router.get("/papers/{paper_id}/repro/target-candidates")
def target_candidates(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """从论文文字里按规则找出的“可能的报告数值”候选（不是结论，需要用户确认含义）。"""
    return {"items": results.target_candidates(ctx, paper_id)}


@router.get("/papers/{paper_id}/repro/targets")
def list_targets(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    return {"items": results.list_targets(ctx, paper_id)}


@router.post("/papers/{paper_id}/repro/targets")
def create_target(paper_id: str, body: TargetBody, ctx: AppContext = Depends(get_ctx)):
    return results.save_target(ctx, paper_id, body.model_dump(exclude_none=True))


@router.patch("/papers/{paper_id}/repro/targets/{target_id}")
def patch_target(paper_id: str, target_id: str, body: TargetBody, ctx: AppContext = Depends(get_ctx)):
    return results.save_target(ctx, paper_id, body.model_dump(exclude_unset=True), target_id)


@router.delete("/papers/{paper_id}/repro/targets/{target_id}")
def delete_target(paper_id: str, target_id: str, ctx: AppContext = Depends(get_ctx)):
    results.delete_target(ctx, paper_id, target_id)
    return {"ok": True}


class RunBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = ""
    content: str | None = None
    path: str | None = None
    format: Literal["csv", "json", "text"] | None = None
    repo_id: str | None = None
    env: dict[str, str] = {}
    notes: str = ""


@router.post("/papers/{paper_id}/repro/runs")
def create_run(paper_id: str, body: RunBody, ctx: AppContext = Depends(get_ctx)):
    """导入你自己跑出的结果：粘贴内容（content）或本地文件路径（path，csv/tsv/json/txt/log/md，≤2MB）。"""
    return results.create_run(ctx, paper_id, body.model_dump())


@router.get("/papers/{paper_id}/repro/runs")
def list_runs(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    return {"items": results.list_runs(ctx, paper_id)}


@router.get("/repro/runs/{run_id}")
def get_run(run_id: str, ctx: AppContext = Depends(get_ctx)):
    return results.get_run(ctx, run_id)


class RunPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    env: dict[str, str] | None = None
    notes: str | None = None
    conclusion: str | None = None


@router.patch("/repro/runs/{run_id}")
def patch_run(run_id: str, body: RunPatch, ctx: AppContext = Depends(get_ctx)):
    return results.update_run(ctx, run_id, body.model_dump(exclude_none=True))


@router.delete("/repro/runs/{run_id}")
def delete_run(run_id: str, ctx: AppContext = Depends(get_ctx)):
    results.delete_run(ctx, run_id)
    return {"ok": True}


class MappingBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    target_id: str
    metric: str | None = None


@router.put("/repro/runs/{run_id}/mapping")
def put_mapping(run_id: str, body: MappingBody, ctx: AppContext = Depends(get_ctx)):
    """手动指定“论文里的这个数值”对应“你的结果里的哪个指标”（metric=null 表示取消，回到自动对应）。"""
    return results.set_mapping(ctx, run_id, body.target_id, body.metric)


@router.get("/repro/runs/{run_id}/compare")
def compare_run(run_id: str, ctx: AppContext = Depends(get_ctx)):
    return results.compare(ctx, run_id)


@router.get("/repro/runs/{run_id}/report")
def run_report(run_id: str, ctx: AppContext = Depends(get_ctx)):
    return results.report_markdown(ctx, run_id)


# ---- D：隔离运行（Docker）----
@router.get("/repro/exec/options")
def exec_options(image: str | None = Query(default=None)):
    """确认框需要的信息：能否运行、默认镜像与资源上限、本机是否已有该镜像（没有时给出需要拉取的提示）。"""
    return executor.options(image)


class PullBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    image: str
    confirm: bool = False


@router.post("/repro/images/pull")
def pull_image(body: PullBody, ctx: AppContext = Depends(get_ctx)):
    """用户同意后才拉取镜像（confirm=true）；以后台任务运行，可取消。"""
    return executor.submit_pull(ctx, body.model_dump())


class Limits(BaseModel):
    model_config = ConfigDict(extra="ignore")
    memory_mb: int | None = None
    cpus: float | None = None
    pids: int | None = None
    timeout_s: int | None = None


class ExecBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    confirm: bool = False
    command: str = ""
    cwd: str = ""
    image: str | None = None
    network: bool = False
    limits: Limits | None = None


@router.post("/repro/steps/{step_id}/execute")
def execute_step(step_id: str, body: ExecBody, ctx: AppContext = Depends(get_ctx)):
    """在 Docker 容器里运行这一步。必须 confirm=true，且 command/cwd 要与界面展示的一致；返回运行记录与 job_id。"""
    data = body.model_dump()
    data["limits"] = {k: v for k, v in (data["limits"] or {}).items() if v is not None}
    return executor.submit(ctx, step_id, data)


@router.get("/papers/{paper_id}/repro/exec")
def list_execs(paper_id: str, repo_id: str | None = Query(default=None), ctx: AppContext = Depends(get_ctx)):
    return {"items": executor.list_execs(ctx, paper_id, repo_id)}


@router.get("/repro/exec/{exec_id}")
def get_exec(exec_id: str, tail: int = Query(default=400, ge=1, le=5000), ctx: AppContext = Depends(get_ctx)):
    return {**executor.get_exec(ctx, exec_id), "log": executor.read_log(ctx, exec_id, tail)}


@router.post("/repro/exec/{exec_id}/cancel")
def cancel_exec(exec_id: str, ctx: AppContext = Depends(get_ctx)):
    run = executor.get_exec(ctx, exec_id)
    if run["status"] not in ("queued", "running") or not run["job_id"]:
        raise ApiError("conflict", "这次运行已经结束", 409)
    ctx.jobs.cancel(run["job_id"])
    return executor.get_exec(ctx, exec_id)


@router.delete("/repro/exec/{exec_id}")
def delete_exec(exec_id: str, ctx: AppContext = Depends(get_ctx)):
    executor.delete_exec(ctx, exec_id)
    return {"ok": True}


@router.get("/repro/exec/{exec_id}/metrics")
def exec_metrics(exec_id: str, ctx: AppContext = Depends(get_ctx)):
    """预览能从这次运行的输出文件和日志里解析出哪些数值，每个带来源（文件或日志行）。"""
    return executor.extract_metrics(ctx, exec_id)


class ImportBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = ""
    metrics: list[str] | None = None


@router.post("/repro/exec/{exec_id}/import")
def import_exec(exec_id: str, body: ImportBody | None = None, ctx: AppContext = Depends(get_ctx)):
    """把这次成功运行解析出的数值导入为一条“结果”，接回结果对比。失败、超时、取消的运行不能导入。"""
    return executor.import_as_result(ctx, exec_id, body.model_dump() if body else {})
