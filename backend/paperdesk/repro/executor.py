"""隔离执行器：把复现计划里的一步放进 Docker 容器里运行，并把输出收回来。

安全底线（每一条都有测试）：
- 第三方代码只在容器里运行，宿主机上只启动 docker 本身；docker 一律用 sandbox.find_docker() 的完整路径；
- 容器默认 --network none，只有用户在确认框里勾选“允许这一步联网”才开放，并写进日志与记录；
- 代码目录只读挂载到 /code，唯一可写的是这次运行自己的工作目录 /work；不挂载宿主机其它目录，不挂载 docker.sock；
- 非 root、--cap-drop ALL、no-new-privileges、只读根文件系统；限制内存、CPU、进程数、运行时长；不使用 --privileged；
- 不向容器传任何环境变量里的密钥：容器里只有本模块写死的几个变量；传给 docker 命令本身的环境也会剔除疑似密钥；
- 不自动拉取镜像：缺镜像时由接口告诉用户，用户同意后才由单独的拉取任务执行。
"""
from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from ..context import AppContext
from ..jobs import JobCancelled, JobContext
from ..util import ApiError, dumps, loads, new_id, utcnow
from . import results, sandbox

DEFAULT_IMAGE = "python:3.11-slim"
IMAGE_RX = re.compile(r"^[a-z0-9][a-z0-9._\-/]{0,120}(:[A-Za-z0-9_][A-Za-z0-9_.\-]{0,127})?(@sha256:[0-9a-f]{64})?$")
# 默认值与允许的最大值：界面只能往小调
DEFAULTS = {"memory_mb": 2048, "cpus": 2.0, "pids": 256, "timeout_s": 600}
MAXIMA = {"memory_mb": 8192, "cpus": 8.0, "pids": 1024, "timeout_s": 6 * 3600}
MINIMA = {"memory_mb": 128, "cpus": 0.25, "pids": 16, "timeout_s": 5}
PULL_TIMEOUT_S = 3600
MAX_LOG_BYTES = 4_000_000
MAX_DB_LOG_LINES = 1500
MAX_OUTPUT_FILES = 200
OUTPUT_EXT = (".csv", ".tsv", ".json", ".txt", ".log", ".md")
SKIP_OUT_DIRS = {".local", ".cache", ".config", "node_modules", "__pycache__", ".git", "site-packages"}
SECRET_ENV_RX = re.compile(r"(?i)(key|token|secret|passw|credential|auth|cookie|session)")
# 常见镜像的大致大小，仅作提示；不在表里就如实说不知道
SIZE_HINTS = {
    "python:3.9-slim": "约 130 MB", "python:3.10-slim": "约 130 MB", "python:3.11-slim": "约 130 MB", "python:3.12-slim": "约 130 MB",
    "python:3.11": "约 1 GB", "python:3.12": "约 1 GB", "ubuntu:22.04": "约 80 MB", "ubuntu:24.04": "约 80 MB",
    "node:20-slim": "约 230 MB", "alpine:3.20": "约 8 MB", "busybox:latest": "约 5 MB",
}


# ---------------------------------------------------------------- docker 调用
def docker_argv() -> list[str]:
    """docker 命令的前缀。永远用完整路径，不依赖 PATH。"""
    exe = sandbox.find_docker()
    if not exe:
        raise ApiError("conflict", "没有找到 docker 命令，无法在隔离环境中运行。", 409)
    return [exe]


def clean_env() -> dict[str, str]:
    """传给 docker 命令本身的环境：剔除名字像密钥的变量。容器里的环境另行写死，不继承这里的任何变量。"""
    return {k: v for k, v in os.environ.items() if not SECRET_ENV_RX.search(k)}


def _docker(args: list[str], timeout: float = 15) -> tuple[int | None, str]:
    try:
        r = subprocess.run([*docker_argv(), *args], capture_output=True, timeout=timeout, check=False, env=clean_env())  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return r.returncode, ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace").strip()


def check_image(image: str) -> str:
    image = (image or "").strip()
    if not IMAGE_RX.match(image):
        raise ApiError("bad_request", "镜像名不合法。示例：python:3.11-slim")
    return image


def list_images() -> list[dict]:
    code, out = _docker(["images", "--format", "{{.Repository}}:{{.Tag}}\t{{.Size}}"])
    if code != 0:
        return []
    items = []
    for ln in out.splitlines():
        name, _, size = ln.partition("\t")
        if name and "<none>" not in name:
            items.append({"image": name, "size": size.strip()})
    return items


def has_image(image: str) -> bool:
    return any(i["image"] == image or i["image"] == f"{image}:latest" for i in list_images())


def size_hint(image: str) -> str:
    return SIZE_HINTS.get(image, "大小未知，取决于镜像仓库，可能有几百 MB 到几个 GB")


def options(image: str | None = None) -> dict:
    info = sandbox.detect()
    img = check_image(image or DEFAULT_IMAGE)
    local = list_images() if info["docker"]["running"] else []
    present = any(i["image"] == img or i["image"] == f"{img}:latest" for i in local)
    return {
        "available": bool(info["can_run_here"]), "reason": info["reason"],
        "default_image": DEFAULT_IMAGE, "image": img, "image_present": present,
        "pull": None if present else {"image": img, "size_hint": size_hint(img)},
        "local_images": local, "defaults": DEFAULTS, "maxima": MAXIMA, "minima": MINIMA,
        "mounts": ["代码目录以只读方式挂到 /code", "这次运行自己的工作目录挂到 /work，可写", "没有挂载本机其它目录"],
    }


def normalize_limits(raw: dict | None) -> dict:
    raw = raw or {}
    out: dict = {}
    for k, default in DEFAULTS.items():
        v = raw.get(k, default)
        try:
            v = float(v) if k == "cpus" else int(v)
        except (TypeError, ValueError) as exc:
            raise ApiError("bad_request", f"资源上限 {k} 不是数字") from exc
        if v > MAXIMA[k]:
            raise ApiError("bad_request", f"资源上限 {k} 不能超过 {MAXIMA[k]}")
        out[k] = max(MINIMA[k], v)
    return out


def container_name(exec_id: str) -> str:
    return f"paperdesk-run-{exec_id}"


def check_cwd(cwd: str) -> str:
    sub = cwd.strip().replace("\\", "/").strip("/")
    if ".." in sub.split("/") or re.match(r"^[A-Za-z]:", sub) or re.search(r"[\x00-\x1f'\"`$;&|<>]", sub):
        raise ApiError("bad_request", "工作目录必须是仓库内的相对路径")
    return sub


def build_run_args(*, name: str, image: str, code_dir: Path, work_dir: Path, command: str, cwd: str, network: bool, limits: dict, exec_id: str) -> list[str]:
    """docker run 的完整参数（不含 docker 本身）。独立成函数，便于测试逐条核对安全选项。"""
    for p in (code_dir, work_dir):
        if "," in str(p) or "\n" in str(p):
            raise ApiError("bad_request", "数据目录路径含有逗号，无法安全挂载，请换一个数据目录。")
    sub = cwd.strip().strip("/")
    if ".." in sub.split("/") or sub.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", sub) or "\x00" in sub:
        raise ApiError("bad_request", "工作目录必须是仓库内的相对路径")
    mem = f"{limits['memory_mb']}m"
    args = [
        "run", "--rm", "--name", name, "--label", "paperdesk.run=" + exec_id,
        "--network", "bridge" if network else "none",
        "--user", "10001:10001", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--read-only", "--tmpfs", "/tmp:rw,size=256m",  # noqa: S108  容器内的 /tmp
        "--memory", mem, "--memory-swap", mem, "--cpus", str(limits["cpus"]), "--pids-limit", str(limits["pids"]),
        "--ulimit", "nofile=1024:1024",
        "--mount", f"type=bind,source={code_dir},target=/code,readonly",
        "--mount", f"type=bind,source={work_dir},target=/work",
        "--workdir", "/work",
        "-e", "HOME=/work", "-e", "PYTHONUNBUFFERED=1", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "PYTHONUSERBASE=/work/.local",
        "-e", "PIP_DISABLE_PIP_VERSION_CHECK=1", "-e", "MPLCONFIGDIR=/tmp/mpl",
        image, "sh", "-c", f"cd /code/{sub} && {command}" if sub else f"cd /code && {command}",
    ]
    return args


# ---------------------------------------------------------------- 记录与目录
def run_dir(app: AppContext, paper_id: str, exec_id: str) -> Path:
    return app.paths.paper_dir(paper_id) / "repro" / "runs" / exec_id


def _row_dict(row) -> dict:
    return {
        "id": row["id"], "paper_id": row["paper_id"], "repo_id": row["repo_id"], "step_id": row["step_id"], "job_id": row["job_id"],
        "title": row["title"], "command": row["command"], "cwd": row["cwd"], "image": row["image"], "network": bool(row["network"]),
        "limits": loads(row["limits_json"], {}), "status": row["status"], "reason": row["reason"], "exit_code": row["exit_code"],
        "files": loads(row["files_json"], []), "imported_run_id": row["imported_run_id"],
        "created_at": row["created_at"], "started_at": row["started_at"], "finished_at": row["finished_at"],
    }


def _update(app: AppContext, exec_id: str, **fields) -> None:
    sets = [f"{k} = ?" for k in fields]
    args = [dumps(v) if k.endswith("_json") else v for k, v in fields.items()]
    with app.db.session() as conn:
        conn.execute(f"UPDATE repro_exec_runs SET {', '.join(sets)} WHERE id = ?", (*args, exec_id))


def get_exec(app: AppContext, exec_id: str) -> dict:
    with app.db.session() as conn:
        row = conn.execute("SELECT * FROM repro_exec_runs WHERE id = ?", (exec_id,)).fetchone()
    if row is None:
        raise ApiError("not_found", "运行记录不存在", 404)
    return _row_dict(row)


def list_execs(app: AppContext, paper_id: str, repo_id: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM repro_exec_runs WHERE paper_id = ?", [paper_id]
    if repo_id:
        sql += " AND repo_id = ?"
        args.append(repo_id)
    with app.db.session() as conn:
        rows = conn.execute(sql + " ORDER BY created_at DESC, rowid DESC LIMIT 100", args).fetchall()
    return [_row_dict(r) for r in rows]


def read_log(app: AppContext, exec_id: str, tail: int = 400) -> list[str]:
    run = get_exec(app, exec_id)
    p = run_dir(app, run["paper_id"], exec_id) / "run.log"
    if not p.is_file():
        return []
    lines = p.read_text("utf-8", "replace").splitlines()
    return lines[-tail:]


def submit(app: AppContext, step_id: str, body: dict) -> dict:
    """用户在确认框里点“运行”之后调用。没有 confirm=true、命令与界面上展示的不一致，都不会启动。"""
    if body.get("confirm") is not True:
        raise ApiError("bad_request", "需要你在确认框里明确确认才能运行")
    info = sandbox.detect()
    if not info["can_run_here"]:
        raise ApiError("conflict", info["reason"], 409)
    with app.db.session() as conn:
        step = conn.execute("SELECT s.*, p.repo_id, p.paper_id FROM repro_steps s JOIN repro_plans p ON p.id = s.plan_id WHERE s.id = ?", (step_id,)).fetchone()
    if step is None:
        raise ApiError("not_found", "步骤不存在", 404)
    if not step["command"].strip():
        raise ApiError("conflict", "这一步没有命令", 409)
    if step["approval"] == "rejected":
        raise ApiError("conflict", "这一步已被你拒绝，先撤销决定再运行", 409)
    if str(body.get("command", "")) != step["command"] or str(body.get("cwd", "")) != step["cwd"]:
        raise ApiError("conflict", "这一步的命令在你确认之后被修改过，请重新确认", 409)
    check_cwd(step["cwd"])
    image = check_image(body.get("image") or DEFAULT_IMAGE)
    limits = normalize_limits(body.get("limits"))
    network = body.get("network") is True
    if not has_image(image):
        raise ApiError("conflict", f"本机没有镜像 {image}，需要先由你同意拉取（{size_hint(image)}）。", 409)
    with app.db.session() as conn:
        busy = conn.execute("SELECT 1 FROM repro_exec_runs WHERE repo_id = ? AND status IN ('queued','running')", (step["repo_id"],)).fetchone()
        if busy:
            raise ApiError("conflict", "这个仓库已经有一个正在运行的任务，请等它结束或先取消", 409)
        exec_id = new_id()
        conn.execute(
            "INSERT INTO repro_exec_runs(id, paper_id, repo_id, step_id, title, command, cwd, image, network, limits_json, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,'queued',?)",
            (exec_id, step["paper_id"], step["repo_id"], step_id, step["title"], step["command"], step["cwd"], image, int(network), dumps(limits), utcnow()),
        )
    job_id = app.jobs.submit("repro_exec", f"隔离运行：{step['title'][:40]}", {"exec_id": exec_id}, step["paper_id"])
    _update(app, exec_id, job_id=job_id)
    return get_exec(app, exec_id)


def delete_exec(app: AppContext, exec_id: str) -> None:
    run = get_exec(app, exec_id)
    if run["status"] in ("queued", "running"):
        raise ApiError("conflict", "还在运行，先取消", 409)
    with app.db.session() as conn:
        conn.execute("DELETE FROM repro_exec_runs WHERE id = ?", (exec_id,))
    shutil.rmtree(run_dir(app, run["paper_id"], exec_id), ignore_errors=True)


# ---------------------------------------------------------------- 运行
def _kill_container(name: str) -> None:
    _docker(["kill", name], timeout=20)
    _docker(["rm", "-f", name], timeout=20)


def _scan_outputs(work: Path) -> list[dict]:
    out: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(work, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in SKIP_OUT_DIRS and not os.path.islink(os.path.join(dirpath, d))]
        for fn in sorted(filenames):
            full = Path(dirpath) / fn
            if full.is_symlink() or full.suffix.lower() not in OUTPUT_EXT:
                continue
            size = full.stat().st_size
            out.append({"path": full.relative_to(work).as_posix(), "size": size, "parsable": size <= results.MAX_BYTES})
            if len(out) >= MAX_OUTPUT_FILES:
                return out
    return out


def handle_exec(ctx: JobContext) -> dict:
    exec_id = ctx.payload["exec_id"]
    try:
        return _handle_exec(ctx)
    except ApiError as exc:
        if get_exec(ctx.app, exec_id)["status"] in ("queued", "running"):  # 还没启动容器就失败了：记录也要如实写成失败
            _update(ctx.app, exec_id, status="failed", reason=exc.message, finished_at=utcnow())
        raise


def _handle_exec(ctx: JobContext) -> dict:
    app = ctx.app
    exec_id = ctx.payload["exec_id"]
    run = get_exec(app, exec_id)
    if run["status"] != "queued":
        raise ApiError("conflict", "这条运行记录已经执行过，请重新发起并确认")
    from ..code import service  # 延迟导入，避免循环

    code_dir = service.repo_root(app, run["paper_id"], run["repo_id"]).resolve()
    if not code_dir.is_dir():
        raise ApiError("not_found", "代码目录不存在")
    rdir = run_dir(app, run["paper_id"], exec_id)
    work = rdir / "work"
    work.mkdir(parents=True, exist_ok=True)
    limits, name = run["limits"], container_name(exec_id)
    args = build_run_args(name=name, image=run["image"], code_dir=code_dir, work_dir=work.resolve(), command=run["command"], cwd=run["cwd"],
                          network=run["network"], limits=limits, exec_id=exec_id)
    log_path = rdir / "run.log"
    logf = log_path.open("w", encoding="utf-8", newline="\n")
    written = [0]
    db_lines = [0]

    def emit(line: str, to_db: bool = True) -> None:
        line = line.rstrip("\r\n")
        if written[0] < MAX_LOG_BYTES:
            logf.write(line + "\n")
            logf.flush()
            written[0] += len(line.encode("utf-8", "replace")) + 1
            if written[0] >= MAX_LOG_BYTES:
                logf.write("…… 日志超过上限，后面的输出没有保存\n")
                logf.flush()
        if to_db and db_lines[0] < MAX_DB_LOG_LINES:
            db_lines[0] += 1
            ctx.log(line)

    emit(f"镜像：{run['image']}")
    emit(f"命令：{run['command']}" + (f"（目录 {run['cwd']}）" if run["cwd"] else ""))
    emit("网络：已按你的勾选开放，这一步可以联网" if run["network"] else "网络：关闭")
    emit(f"上限：内存 {limits['memory_mb']} MB，CPU {limits['cpus']} 核，进程 {limits['pids']} 个，时长 {limits['timeout_s']} 秒")
    _update(app, exec_id, status="running", started_at=utcnow())
    ctx.progress(0.05, "容器运行中")

    status, reason, exit_code = "failed", "", None
    proc = None
    try:
        try:
            proc = subprocess.Popen([*docker_argv(), *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=clean_env(),  # noqa: S603
                                    stdin=subprocess.DEVNULL, bufsize=0)
        except OSError as exc:
            raise ApiError("conflict", f"无法启动 docker：{exc.strerror or exc}", 409) from exc
        q: queue.Queue = queue.Queue()

        def pump() -> None:
            buf = b""
            assert proc and proc.stdout
            while True:
                chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for ln in lines:
                    q.put(ln.decode("utf-8", "replace"))
            if buf:
                q.put(buf.decode("utf-8", "replace"))
            q.put(None)

        threading.Thread(target=pump, daemon=True).start()
        deadline = time.monotonic() + limits["timeout_s"]
        drained = False
        while not drained:
            try:
                item = q.get(timeout=0.2)
                if item is None:
                    drained = True
                else:
                    emit(item)
            except queue.Empty:
                pass
            if ctx.cancelled:
                emit("收到取消，正在停止容器")
                _kill_container(name)
                status, reason = "cancelled", "你取消了这次运行"
                break
            if time.monotonic() > deadline:
                emit(f"超过 {limits['timeout_s']} 秒，正在停止容器")
                _kill_container(name)
                status, reason = "timeout", f"超过运行时长上限 {limits['timeout_s']} 秒，已被终止"
                break
        if status == "failed":
            exit_code = proc.wait(timeout=30)
            if exit_code == 0:
                status = "succeeded"
            elif exit_code == 137:
                reason = "进程被杀（退出码 137），多半是超出内存上限，或被外部终止"
            elif exit_code in (125, 126, 127):
                reason = f"docker 或容器没能启动这条命令（退出码 {exit_code}）"
            else:
                reason = f"命令以退出码 {exit_code} 结束"
        else:
            try:
                exit_code = proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
    except BaseException as exc:  # 任何异常路径都要清掉容器
        _kill_container(name)
        if proc and proc.poll() is None:
            proc.kill()
        if not isinstance(exc, JobCancelled) and status == "failed":
            reason = reason or f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if proc and proc.poll() is None:
            _kill_container(name)
            proc.kill()
        files = _scan_outputs(work)
        emit(f"结束：{status}" + (f"，{reason}" if reason else "") + f"；工作目录里有 {len(files)} 个可读的输出文件", to_db=True)
        logf.close()
        _update(app, exec_id, status=status, reason=reason, exit_code=exit_code, files_json=files, finished_at=utcnow())
    if status == "cancelled":
        raise JobCancelled()
    if status != "succeeded":
        raise ApiError("conflict", reason or "运行失败", 409)
    return {"exec_id": exec_id, "exit_code": exit_code, "files": len(files)}


# ---------------------------------------------------------------- 拉取镜像（只在用户同意后）
def submit_pull(app: AppContext, body: dict) -> dict:
    if body.get("confirm") is not True:
        raise ApiError("bad_request", "拉取镜像需要你明确同意")
    image = check_image(body.get("image"))
    if not sandbox.detect()["can_run_here"]:
        raise ApiError("conflict", "Docker 当前不可用", 409)
    job_id = app.jobs.submit("repro_pull", f"拉取镜像 {image}", {"image": image})
    return {"job_id": job_id, "image": image}


def handle_pull(ctx: JobContext) -> dict:
    image = check_image(ctx.payload["image"])
    ctx.log(f"按你的同意拉取镜像 {image}")
    proc = subprocess.Popen([*docker_argv(), "pull", image], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=clean_env(),  # noqa: S603
                            stdin=subprocess.DEVNULL)
    q: queue.Queue = queue.Queue()

    def pump() -> None:
        assert proc.stdout
        for raw in iter(proc.stdout.readline, b""):
            q.put(raw.decode("utf-8", "replace").rstrip())
        q.put(None)

    threading.Thread(target=pump, daemon=True).start()
    deadline, drained = time.monotonic() + PULL_TIMEOUT_S, False
    try:
        while not drained:
            try:
                item = q.get(timeout=0.3)
                if item is None:
                    drained = True
                elif item:
                    ctx.log(item)
            except queue.Empty:
                pass
            ctx.check_cancelled()
            if time.monotonic() > deadline:
                raise ApiError("conflict", "拉取镜像超时", 409)
        code = proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    if code != 0:
        raise ApiError("conflict", f"拉取镜像失败（退出码 {code}），详情见任务日志", 409)
    return {"image": image}


# ---------------------------------------------------------------- 启动清理
def recover(app: AppContext) -> None:
    """应用启动时：上次没跑完的记录标记为失败，并清掉可能残留的容器。"""
    with app.db.session() as conn:
        stale = [r["id"] for r in conn.execute("SELECT id FROM repro_exec_runs WHERE status IN ('queued','running')").fetchall()]
        conn.execute("UPDATE repro_exec_runs SET status='failed', reason='应用被中断，容器已清理', finished_at=? WHERE status IN ('queued','running')", (utcnow(),))
    for eid in stale:
        if sandbox.find_docker():
            _kill_container(container_name(eid))


# ---------------------------------------------------------------- 结果：只收真的解析出来的数值
LOG_METRIC_RX = re.compile(r"^\s*([A-Za-z][\w .\-/@()]{0,60}?)\s*[:=]\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(%)?\s*$")
MAX_LOG_METRICS = 200


def extract_metrics(app: AppContext, exec_id: str) -> dict:
    """从输出文件（csv/json）和日志里解析数值。每个数值带来源：哪次运行、哪个文件或日志的哪一行。解析不出来就是没有。"""
    run = get_exec(app, exec_id)
    rdir = run_dir(app, run["paper_id"], exec_id)
    base = {"exec_id": exec_id, "label": f"{run['title']}（{run['created_at'][:16].replace('T', ' ')}）"}
    metrics: list[dict] = []
    warnings: list[str] = []
    seen: set[str] = set()

    def add(m: dict, source: dict) -> None:
        name = m["name"]
        if name in seen:
            name = f"{name} [{source['path']}]"
        seen.add(name)
        metrics.append({**m, "name": name, "source": {"run_id": exec_id, **source}})

    for f in run["files"]:
        if not f["parsable"] or not f["path"].lower().endswith((".csv", ".tsv", ".json")):
            continue
        p = (rdir / "work" / f["path"])
        try:
            text = p.read_text("utf-8-sig", "replace")
            _fmt, ms, _w = results.parse_results(text)
        except (OSError, ApiError):
            continue
        for m in ms:
            add(m, {"kind": "file", "path": f["path"], "line": None})
    log_p = rdir / "run.log"
    if log_p.is_file():
        last: dict[str, tuple[int, dict, int]] = {}
        started = False
        for i, ln in enumerate(log_p.read_text("utf-8", "replace").splitlines(), 1):
            if not started:  # 前几行是执行器自己写的参数说明，不是程序输出
                started = ln.startswith("上限：")
                continue
            m = LOG_METRIC_RX.match(ln)
            if m:
                raw = m.group(2) + (m.group(3) or "")
                prev = last.get(m.group(1).strip())
                last[m.group(1).strip()] = (i, {"name": m.group(1).strip(), "value": float(m.group(2)), "std": None, "n": 1, "raw": raw}, (prev[2] + 1) if prev else 1)
        for _name, (line_no, m, count) in list(last.items())[:MAX_LOG_METRICS]:
            if count > 1:
                warnings.append(f"日志里“{m['name']}”出现了 {count} 次，只取最后一次（第 {line_no} 行）。")
            add(m, {"kind": "log", "path": "run.log", "line": line_no})
    if not metrics:
        warnings.append("没有从输出文件或日志里解析出任何数值。日志里需要单独一行形如“accuracy: 0.93”，或者工作目录里有 csv / json 结果文件。")
    return {**base, "status": run["status"], "metrics": metrics, "warnings": warnings}


def import_as_result(app: AppContext, exec_id: str, body: dict) -> dict:
    run = get_exec(app, exec_id)
    if run["status"] != "succeeded":
        raise ApiError("conflict", "只有成功结束的运行才能导入结果；失败、超时或取消的运行不会被当作结果。", 409)
    ext = extract_metrics(app, exec_id)
    wanted = body.get("metrics")
    metrics = [m for m in ext["metrics"] if wanted is None or m["name"] in set(wanted)]
    if not metrics:
        raise ApiError("bad_request", "没有可导入的数值")
    env = {"镜像": run["image"], "命令": run["command"][:250], "联网": "是" if run["network"] else "否", "运行记录": exec_id}
    created = results.create_run_from_metrics(app, run["paper_id"], run["repo_id"], str(body.get("name") or "").strip()[:100] or f"隔离运行：{run['title'][:50]}",
                                              "text", metrics, env, f"来自隔离运行 {exec_id}，每个数值的来源见各自的“来源”。")
    _update(app, exec_id, imported_run_id=created["id"])
    return created

