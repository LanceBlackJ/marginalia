"""获取代码：解析 GitHub 仓库信息（不依赖 git）、下载 codeload zip、本地 zip/文件夹导入。

流程：resolve（查大小/默认分支/最新 commit）→ 用户确认（大仓库）→ 下载**指定 commit** 的 zip → 安全解压 → 扫描。
记录仓库地址、分支、commit SHA、下载时间；论文发表年份与代码最后提交相差很大时给出提醒。
不绕过任何访问限制；私有仓库/需登录的资源会如实失败。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..util import ApiError
from .safeio import MAX_TOTAL_BYTES

API = "https://api.github.com"
CODELOAD = "https://codeload.github.com"
APPROVAL_BYTES = 20 * 1024 * 1024        # 超过这个大小下载前必须由用户确认
HARD_LIMIT_BYTES = 300 * 1024 * 1024     # 超过就不下载
STALE_YEARS = 2


@dataclass
class RepoRef:
    owner: str
    name: str
    branch: str | None = None


@dataclass
class RepoInfo:
    owner: str
    name: str
    url: str
    default_branch: str
    branch: str
    size_bytes: int           # GitHub 报告的仓库大小（近似，未压缩）
    commit_sha: str
    commit_date: str | None
    description: str | None
    archived: bool
    requires_approval: bool
    too_large: bool
    warnings: list[str]


def parse_github_url(url: str) -> RepoRef | None:
    u = urlparse(url.strip())
    if (u.hostname or "").lower().removeprefix("www.") != "github.com":
        return None
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, name = parts[0], parts[1]
    name = re.sub(r"\.git$", "", name)
    branch = None
    if len(parts) >= 4 and parts[2] in ("tree", "blob", "commit"):
        branch = parts[3]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        return None
    return RepoRef(owner, name, branch)


def _gh_error(resp: httpx.Response) -> ApiError:
    if resp.status_code == 404:
        return ApiError("not_found", "GitHub 上找不到该仓库，请检查链接。", 404)
    if resp.status_code in (403, 429):
        return ApiError("rate_limited", "GitHub 接口请求受限，请稍后再试。", 429)
    return ApiError("network", f"GitHub 接口返回错误 {resp.status_code}。", 502)


def resolve_github(client: httpx.Client, ref: RepoRef, *, paper_year: int | None = None) -> RepoInfo:
    headers = {"Accept": "application/vnd.github+json"}
    try:
        r = client.get(f"{API}/repos/{ref.owner}/{ref.name}", headers=headers)
        if r.status_code != 200:
            raise _gh_error(r)
        meta = r.json()
        branch = ref.branch or meta.get("default_branch") or "main"
        c = client.get(f"{API}/repos/{ref.owner}/{ref.name}/commits/{branch}", headers=headers)
        if c.status_code != 200:
            raise _gh_error(c)
        commit = c.json()
    except httpx.HTTPError as exc:
        raise ApiError("network", "无法连接 GitHub，请检查网络后重试。", 502) from exc
    sha = commit.get("sha") or ""
    date = ((commit.get("commit") or {}).get("committer") or {}).get("date")
    size = int(meta.get("size") or 0) * 1024
    warnings: list[str] = []
    real_owner = meta.get("owner", {}).get("login", ref.owner)
    real_name = meta.get("name", ref.name)
    if (real_owner.lower(), real_name.lower()) != (ref.owner.lower(), ref.name.lower()):
        warnings.append(f"链接里的仓库 {ref.owner}/{ref.name} 已改名或转移为 {real_owner}/{real_name}，已按新地址获取。")
    if meta.get("archived"):
        warnings.append("该仓库已归档，不再更新。")
    if date and paper_year:
        try:
            yr = int(date[:4])
            if yr - paper_year > STALE_YEARS:
                warnings.append(
                    f"论文发表于 {paper_year} 年，而代码最后一次提交在 {yr} 年，相差 {yr - paper_year} 年："
                    "代码可能已有实现变化，与论文描述不完全一致。"
                )
        except ValueError:
            pass
    return RepoInfo(
        owner=meta.get("owner", {}).get("login", ref.owner), name=meta.get("name", ref.name),
        url=meta.get("html_url") or f"https://github.com/{ref.owner}/{ref.name}",
        default_branch=meta.get("default_branch") or branch, branch=branch, size_bytes=size, commit_sha=sha,
        commit_date=date, description=meta.get("description"), archived=bool(meta.get("archived")),
        requires_approval=size > APPROVAL_BYTES, too_large=size > HARD_LIMIT_BYTES, warnings=warnings,
    )


def download_zip(client: httpx.Client, url: str, dest: Path, max_bytes: int = MAX_TOTAL_BYTES) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with client.stream("GET", url) as r:
            if r.status_code != 200:
                raise ApiError("network", f"下载失败 {r.status_code}：仓库可能是私有的或已删除。", 502)
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ApiError("bad_request", "压缩包超过大小上限，已停止下载。")
                    f.write(chunk)
    except httpx.HTTPError as exc:
        dest.unlink(missing_ok=True)
        raise ApiError("network", "下载中断，请检查网络后重试。", 502) from exc
    except ApiError:
        dest.unlink(missing_ok=True)
        raise
    with open(dest, "rb") as f:
        if f.read(4) != b"PK\x03\x04":
            dest.unlink(missing_ok=True)
            raise ApiError("bad_request", "下载内容不是 zip 压缩包。")
    return total


def codeload_url(owner: str, name: str, sha: str) -> str:
    return f"{CODELOAD}/{owner}/{name}/zip/{sha}"
