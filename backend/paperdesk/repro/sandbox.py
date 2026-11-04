"""隔离执行环境检测：只探测 Docker / WSL2 是否存在，**不执行**任何用户代码。

执行器（executor.py）只使用 Docker；WSL2 只用于提示“可以导出计划后手动运行”。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

TIMEOUT = 6


def find_docker() -> str | None:
    """docker 命令的完整路径。Docker Desktop 装在用户目录时不一定在 PATH 里，所以也看几个固定的安装位置。"""
    found = shutil.which("docker")
    if found:
        return found
    if sys.platform != "win32":
        return None
    roots = [os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432")]
    for root in filter(None, roots):
        for rel in (("Programs", "DockerDesktop", "resources", "bin", "docker.exe"), ("Docker", "Docker", "resources", "bin", "docker.exe")):
            cand = Path(root).joinpath(*rel)
            if cand.is_file():
                return str(cand)
    return None


def _run(args: list[str]) -> tuple[int | None, str]:
    try:
        r = subprocess.run(args, capture_output=True, timeout=TIMEOUT, check=False)  # noqa: S603  只读探测命令，固定参数
    except (OSError, subprocess.SubprocessError):
        return None, ""
    raw = r.stdout or b""
    # wsl.exe 的输出是 UTF-16；其它命令是 UTF-8
    text = raw.decode("utf-16-le", "ignore") if raw[:2] in (b"\xff\xfe",) or b"\x00" in raw[:8] else raw.decode("utf-8", "replace")
    return r.returncode, text.replace("\x00", "").strip()


def detect() -> dict:
    docker = {"found": False, "running": False, "detail": ""}
    docker_exe = find_docker()
    if docker_exe:
        docker["found"] = True
        code, out = _run([docker_exe, "info", "--format", "{{.ServerVersion}}"])
        docker["running"] = code == 0 and bool(out)
        docker["detail"] = f"Docker 服务版本 {out}" if docker["running"] else "找到 docker 命令，但 Docker 服务没有运行"
    else:
        docker["detail"] = "没有找到 docker 命令"
    wsl = {"found": False, "distros": [], "detail": ""}
    if sys.platform == "win32" and shutil.which("wsl"):
        code, out = _run(["wsl", "-l", "-q"])
        names = [ln.strip() for ln in out.splitlines() if ln.strip()] if code == 0 else []
        wsl["found"] = bool(names)
        wsl["distros"] = names[:10]
        wsl["detail"] = f"已安装的发行版：{'、'.join(names[:5])}" if names else "WSL 没有可用的发行版，可能没有启用 WSL2 或没有安装发行版"
    else:
        wsl["detail"] = "不是 Windows 或没有 wsl 命令"
    available = docker["running"] or wsl["found"]
    can_run = bool(docker["running"])
    if can_run:
        reason = ("Docker 正在运行，可以在隔离容器里运行复现步骤：默认不联网、代码只读、限制内存和时长，每次运行都要你确认。"
                  "只有 Docker 用于运行，WSL2 不用。")
    elif docker["found"]:
        reason = "找到了 Docker，但服务没有运行。请先启动 Docker Desktop。也可以把计划导出，到别的环境里手动运行。"
    elif wsl["found"]:
        reason = "检测到 WSL2，但 Marginalia 只用 Docker 运行。请安装 Docker Desktop，或把计划导出后在 WSL2 里手动运行。"
    else:
        reason = "这台机器没有可用的隔离环境（Docker 或 WSL2），不会在本机直接运行任何代码。可以把计划导出，到别的机器上手动运行。"
    return {"docker": docker, "wsl2": wsl, "available": available, "can_run_here": can_run, "reason": reason}
