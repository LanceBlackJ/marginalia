"""A：静态可行性评估。只读代码仓库的静态分析结果与论文文字，**不执行任何东西**。

每条结论都带证据（`path:行` 或 论文页码+原文片段）；找不到的写“没找到”，拿不准的写“不确定”，不给乐观结论。
论文里的数据集线索可以由 AI 抽取，但原文引文必须真实出现在对应段落里，否则丢弃（引文必须真实存在才保留）。
"""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from ..ai import usage as usage_log
from ..ai.providers import LLMError
from ..ai.router import TaskType, choose_model
from ..ai.service import AIService
from ..code import params as code_params_mod, service
from ..code.links import find_code_links
from ..code.scan import parse_dependencies
from ..context import AppContext
from ..util import ApiError, dumps, loads, utcnow

MAX_FILES = 300
MAX_FILE_BYTES = 300_000
SOURCE_EXT = (".py", ".sh", ".js", ".ts", ".cpp", ".cc", ".c", ".cu", ".h", ".hpp", ".pyx", ".pxd", ".ipynb", ".r", ".jl", ".m")
DOC_NAMES = ("readme", "install", "setup", "requirements", "environment", "dockerfile", "makefile", "cmakelists")
COMPILE_EXT = (".pyx", ".pxd", ".cu", ".cpp", ".cc", ".cxx")
WEIGHT_EXT = (".pt", ".pth", ".ckpt", ".h5", ".hdf5", ".pkl", ".npz", ".npy", ".onnx", ".bin", ".safetensors", ".weights")
KEY_FRAMEWORKS = ("tensorflow", "torch", "pytorch", "jax", "keras", "cuda", "cudnn", "numpy", "scipy", "networkx", "dgl", "torch_geometric", "scikit-learn")

GPU_RX = re.compile(r"\.cuda\(|torch\.device\(\s*['\"]cuda|\bcuda:\d|tf\.device\(|CUDA_VISIBLE_DEVICES|tensorflow-gpu|gpu_options|DataParallel|\bnvidia\b", re.I)
TF1_RX = re.compile(r"\btf\.(?:placeholder|Session|contrib)\b")
DOWNLOAD_RX = re.compile(r"\b(?:wget|curl|gdown|urlretrieve|requests\.get|kaggle|huggingface|zenodo|figshare|dropbox|drive\.google|git clone)\b|\bdownload", re.I)
PY_VER_RX = re.compile(r"python_requires\s*=\s*['\"]([^'\"]+)['\"]|\bpython\s*(?:==|>=|<=|~=|=|:)\s*(3(?:\.\d+)+)|FROM\s+python:(\d[\w.\-]*)|\bPython\s+(3(?:\.\d+)+)", re.I)
HW_RX = re.compile(r"\b(?:NVIDIA\s+)?(?:GeForce\s+)?(?:Tesla\s+)?(?:RTX\s*\d{4}\w*|GTX\s*\d{3,4}\w*|V100|A100|H100|P100|T4|TITAN\s*\w*)\b[^.]{0,80}|\b\d+\s*(?:GB|GiB)\s+(?:of\s+)?(?:GPU\s+|RAM\s+|memory)[^.]{0,60}|\b\d+\s*(?:CPU\s+)?cores?\b[^.]{0,60}", re.I)
PIN_RX = re.compile(r"\b(?:tensorflow(?:-gpu)?|torch|pytorch|torchvision|keras|numpy|scipy|networkx|pandas|scikit-learn|cython|python|cuda|cudnn)\s*(?:==|>=|~=|<=)\s*\d+(?:\.\d+)*", re.I)
README_RUN_RX = re.compile(r"^\s*(?:\$\s*)?((?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*python3?\s+[\w./\-]+\.py\b[^\n]*)")
DATA_STMT_RX = re.compile(r"data(?:sets?)?\s+(?:are|is|can be|were)\s+(?:publicly\s+)?(?:available|obtained|downloaded|collected)|available (?:at|from|in)|can be (?:downloaded|obtained)", re.I)


def _ev_code(path: str, line: int, quote: str = "", end: int | None = None) -> dict:
    return {"type": "code", "path": path, "line_start": line, "line_end": end or line, "quote": quote.strip()[:200]}


def _ev_repo(quote: str) -> dict:
    """仓库级事实（来源、提交日期、扫描范围等）：不对应某一行代码，但是可核对的记录。"""
    return {"type": "repo", "quote": quote.strip()[:240]}


def _ev_paper(page: int | None, ord_: int | None, quote: str) -> dict:
    return {"type": "paper", "page": page, "ord": ord_, "quote": quote.strip()[:240]}


def _item(key: str, status: str, title: str, text: str, evidence: list[dict] | None = None, note: str = "") -> dict:
    """status: found（有证据）| missing（没找到）| uncertain（不确定）| risk（风险）| info。
    硬性规则：found/risk 必须带证据（文件:行或论文页）。拿不出证据就降为 uncertain，并如实写明，不给“已找到”的结论。"""
    evidence = evidence or []
    if status in ("found", "risk") and not evidence:
        status = "uncertain"
        note = (note + "；" if note else "") + "没有可核对的证据，所以只能算“不确定”。"
    return {"key": key, "status": status, "title": title, "text": text, "evidence": evidence, "note": note}


class _Repo:
    """把仓库文件读一遍（有上限），提供按正则查找并返回 path:line 的能力。"""

    def __init__(self, app: AppContext, repo: dict):
        self.app, self.repo = app, repo
        with app.db.session() as conn:
            rows = conn.execute("SELECT path, language, size, role, skipped FROM code_files WHERE repo_id = ? ORDER BY path", (repo["id"],)).fetchall()
        self.files = [dict(r) for r in rows]
        self._cache: dict[str, list[str]] = {}

    def lines(self, path: str) -> list[str]:
        if path not in self._cache:
            self._cache[path] = service.file_lines(self.app, self.repo["id"], path) or []
        return self._cache[path]

    def readable(self, *, source: bool = True, docs: bool = True) -> list[str]:
        out = []
        for f in self.files:
            if f["skipped"] or f["size"] > MAX_FILE_BYTES:
                continue
            p = f["path"]
            low = PurePosixPath(p).name.lower()
            is_doc = low.startswith(DOC_NAMES) or low.endswith((".md", ".rst", ".txt", ".yml", ".yaml", ".toml", ".cfg"))
            if (source and p.lower().endswith(SOURCE_EXT)) or (docs and is_doc):
                out.append(p)
        return out[:MAX_FILES]

    def grep(self, rx: re.Pattern, *, source: bool = True, docs: bool = True, limit: int = 8, skip: re.Pattern | None = None) -> list[dict]:
        hits: list[dict] = []
        for p in self.readable(source=source, docs=docs):
            for i, ln in enumerate(self.lines(p), 1):
                if rx.search(ln) and not (skip and skip.search(ln)):
                    hits.append(_ev_code(p, i, ln))
                    if len(hits) >= limit:
                        return hits
        return hits

    def find_file(self, *names: str) -> list[str]:
        want = {n.lower() for n in names}
        return [f["path"] for f in self.files if PurePosixPath(f["path"]).name.lower() in want]


# ---------------------------------------------------------------- 环境
def _environment(r: _Repo, overview: dict) -> tuple[list[dict], dict]:
    items: list[dict] = []
    flags = {"compile": False, "gpu": False, "tf1": False, "unpinned": False, "dep_files": 0}
    langs = overview.get("languages") or {}
    fw = overview.get("frameworks") or []
    lang_txt = "、".join(f"{k}（{v['files']} 个文件）" for k, v in sorted(langs.items(), key=lambda kv: -kv[1]["files"])[:5]) or "没有识别到源码语言"
    lang_ev: list[dict] = []
    for lang in sorted(langs, key=lambda k: -langs[k]["files"])[:5]:  # 每种主要语言给一个真实存在的源码文件作为证据
        f = next((f for f in r.files if (f.get("language") or "").lower() == lang.lower() and not f["skipped"]), None)
        if f:
            lang_ev.append(_ev_code(f["path"], 1, f"{lang}：{langs[lang]['files']} 个文件，如 {f['path']}"))
    items.append(_item("languages", "found" if langs else "missing", "语言与框架",
                       f"语言：{lang_txt}；框架/库：{'、'.join(fw) if fw else '没有识别到'}。", lang_ev))
    # 依赖文件
    pins_total = deps_total = 0
    key_specs: list[str] = []
    key_spec_ev: list[dict] = []
    unpinned_ev: list[dict] = []
    for path in (overview.get("dependency_files") or [])[:12]:
        lines = r.lines(path)
        flags["dep_files"] += 1
        if PurePosixPath(path).name.lower() == "dockerfile":
            # Dockerfile 是环境构建说明，不是依赖清单：取基础镜像与安装命令作为证据
            ev = [_ev_code(path, i, ln) for i, ln in enumerate(lines, 1) if re.match(r"\s*(?:FROM|RUN\s+.*(?:pip|conda|apt)[\w\-]*\s+install)", ln, re.I)][:5]
            items.append(_item("deps:" + path, "found" if ev else "uncertain", f"Dockerfile {path}",
                               "Dockerfile 给出了环境构建步骤，可作为环境版本的参考。" if ev else "找到 Dockerfile，但没有识别出基础镜像或安装命令。", ev or [_ev_code(path, 1)]))
            continue
        specs = parse_dependencies(path, "\n".join(lines))
        if not specs:
            items.append(_item("deps:" + path, "uncertain", f"依赖文件 {path}", "读到了文件，但没有解析出依赖项。", [_ev_code(path, 1)]))
            continue
        pinned = [s for s in specs if re.search(r"==|===", s)]
        pins_total += len(pinned)
        deps_total += len(specs)
        for sp in [x for x in specs if x not in pinned][:3]:  # 证据：依赖文件里没有固定版本的那几行
            nm = re.split(r"[<>=~!\[; ]", sp.strip(), maxsplit=1)[0].lower()
            ln_no = next((i for i, ln in enumerate(lines, 1) if nm and nm in ln.lower()), None)
            if ln_no and len(unpinned_ev) < 6:
                unpinned_ev.append(_ev_code(path, ln_no, lines[ln_no - 1]))
        for s in specs:
            if any(k in s.lower() for k in KEY_FRAMEWORKS):
                key_specs.append(f"{s}（{path}）")
                name = re.split(r"[<>=~!\[; ]", s.strip(), maxsplit=1)[0].lower()
                ln_no = next((i for i, ln in enumerate(lines, 1) if name and name in ln.lower()), None)
                if ln_no:  # 证据：依赖文件里真正写着这个依赖的那一行
                    key_spec_ev.append(_ev_code(path, ln_no, lines[ln_no - 1]))
        items.append(_item("deps:" + path, "found", f"依赖文件 {path}", f"{len(specs)} 个依赖，其中 {len(pinned)} 个固定了版本。",
                           [_ev_code(path, 1, ", ".join(specs[:6]))]))
    if deps_total and pins_total < deps_total / 2:
        flags["unpinned"] = True
        flags["unpinned_ev"] = unpinned_ev
    if key_specs:
        items.append(_item("framework_versions", "found", "关键框架的版本要求", "；".join(key_specs[:8]) + "。没有写版本的，需要你自己选择与论文年代相符的版本。",
                           key_spec_ev[:8]))
    # README/文档里明确写出的版本（依赖文件里常常没有固定版本，真正的版本线索在文档里）
    pins = r.grep(PIN_RX, source=False, docs=True, limit=8)
    if pins:
        items.append(_item("doc_versions", "found", "文档里写明的版本", "README 或文档里明确写出了这些版本，通常比依赖文件里的更可靠：" + "；".join(sorted({m.group(0) for p in pins for m in [PIN_RX.search(p["quote"])] if m})[:6]) + "。", pins))
    # Python 版本线索
    pyv: list[dict] = []
    for p in r.readable(source=False, docs=True):
        if PurePosixPath(p).name.lower().endswith((".md", ".rst", ".txt", ".yml", ".yaml", ".toml", ".cfg", ".py")) or "dockerfile" in p.lower():
            for i, ln in enumerate(r.lines(p), 1):
                m = PY_VER_RX.search(ln)
                if m:
                    pyv.append(_ev_code(p, i, ln))
                    if len(pyv) >= 4:
                        break
        if len(pyv) >= 4:
            break
    if pyv:
        items.append(_item("python_version", "found", "Python 版本线索", "文档或配置里提到了 Python 版本：" + "；".join(e["quote"] for e in pyv[:3]), pyv))
    else:
        items.append(_item("python_version", "uncertain", "Python 版本线索", "没有在 README、配置或依赖文件里找到 Python 版本要求，不确定该用哪个版本。"))
    # 容器/环境文件
    for label, names in (("Dockerfile", ("dockerfile",)), ("conda 环境文件", ("environment.yml", "environment.yaml")), ("Makefile/CMake", ("makefile", "cmakelists.txt"))):
        found = r.find_file(*names)
        if found:
            items.append(_item("envfile:" + label, "found", label, f"仓库带有 {label}：{', '.join(found[:3])}。", [_ev_code(found[0], 1)]))
    # 编译
    comp = [f["path"] for f in r.files if f["path"].lower().endswith(COMPILE_EXT)]
    setup_hits = r.grep(re.compile(r"cythonize|ext_modules|Extension\("), source=True, docs=False, limit=3)
    if comp or setup_hits:
        flags["compile"] = True
        ev = setup_hits[:2] + [_ev_code(p, 1) for p in comp[:2]]
        items.append(_item("compile", "risk", "需要编译", f"仓库含 {len(comp)} 个需要编译的 Cython/C++/CUDA 文件，需要本机有对应的编译工具链。编译失败是复现老代码最常见的障碍。", ev))
    # GPU
    gpu = r.grep(GPU_RX, limit=6)
    if gpu:
        flags["gpu"] = True
        items.append(_item("gpu", "found", "GPU 使用", "代码里有 GPU 相关用法，复现很可能需要 NVIDIA GPU 与匹配的 CUDA。", gpu))
    else:
        items.append(_item("gpu", "uncertain", "GPU 使用", "没有在代码里发现 GPU 相关用法。这不能说明只用 CPU 就能在合理时间内跑完，是否需要 GPU：不确定。"))
    # TF1
    tf1 = r.grep(TF1_RX, limit=3)
    if tf1:
        flags["tf1"] = True
        items.append(_item("tf1", "risk", "使用 TensorFlow 1.x 接口", "代码使用 tf.placeholder / tf.Session / tf.contrib 等 TensorFlow 1.x 接口，通常需要旧版 Python 与 TensorFlow，无法直接用最新版运行。", tf1))
    return items, flags


# ---------------------------------------------------------------- 入口
def _entries(r: _Repo, overview: dict) -> tuple[list[dict], dict]:
    items: list[dict] = []
    eps = overview.get("entry_points") or []
    by_role: dict[str, list[dict]] = {}
    for e in eps:
        by_role.setdefault(e.get("role") or e["kind"], []).append(e)
    # README 里的命令常被合并进对应的入口点（main_guard 等），所以取所有带命令的入口，而不只是 readme_command
    commands = [e for e in eps if e.get("command")]
    for role, label in (("train", "训练入口"), ("test", "评估/测试入口")):
        got = by_role.get(role, [])
        if got:
            items.append(_item("entry:" + role, "found", label, "；".join(f"{e['path']}" for e in got[:4]) + f"（共 {len(got)} 个）。",
                               [_ev_code(e["path"], 1, e.get("reason", "")) for e in got[:4]]))
        else:
            items.append(_item("entry:" + role, "missing", label, f"没有识别到{label}。", note="可能入口写在 README 的命令里，或仓库只提供了部分流程。"))
    if commands:
        ev = []
        for e in commands[:6]:
            line = 1
            for p in r.readable(source=False, docs=True):
                if PurePosixPath(p).name.lower().startswith("readme"):
                    for i, ln in enumerate(r.lines(p), 1):
                        if e["command"][:40] in ln:
                            ev.append(_ev_code(p, i, ln))
                            break
                    break
            else:
                ev.append(_ev_code(e["path"], line, e["command"]))
    else:
        ev = []
    # 入口分析只认以 python 开头的命令；README 里常见 “CUDA_VISIBLE_DEVICES=0 python train.py” 这类带环境变量的写法，这里补读
    cmds = [e["command"] for e in commands]
    for p in r.readable(source=False, docs=True):
        if not PurePosixPath(p).name.lower().startswith("readme"):
            continue
        for i, ln in enumerate(r.lines(p), 1):
            m = README_RUN_RX.match(ln)
            if m and m.group(1).strip() not in cmds and len(cmds) < 12:
                cmds.append(m.group(1).strip())
                ev.append(_ev_code(p, i, ln))
    if cmds:
        items.append(_item("commands", "found", "README 里的运行命令", "；".join(cmds[:5]) + "。命令里的参数只是示例，请对照论文设置核对。", ev[:8]))
    else:
        items.append(_item("commands", "missing", "README 里的运行命令", "README 里没有找到可运行的命令示例，需要自己根据入口脚本的参数拼出命令。"))
    return items, {"has_train": bool(by_role.get("train")), "has_test": bool(by_role.get("test")), "commands": cmds}


# ---------------------------------------------------------------- 数据
def _data(app: AppContext, r: _Repo, paper_id: str, repo: dict, ai_facts: list[dict]) -> tuple[list[dict], dict]:
    items: list[dict] = []
    # 放在 data/ 目录里的源码（如 loader.py）是处理数据的代码，不是数据本身
    data_files = [f for f in r.files if f["role"] == "data" and not f["path"].lower().endswith(SOURCE_EXT)]
    weights = [f["path"] for f in r.files if f["path"].lower().endswith(WEIGHT_EXT)]
    if data_files:
        items.append(_item("repo_data", "found", "仓库自带的数据文件", f"{len(data_files)} 个，例如 {', '.join(f['path'] for f in data_files[:3])}。",
                           [_ev_code(f["path"], 1) for f in data_files[:3]]))
    else:
        items.append(_item("repo_data", "missing", "仓库自带的数据文件", "仓库里没有识别出数据文件，数据需要另外获取。"))
    if weights:
        # 二进制文件没有“行”：行号 0 表示整个文件
        items.append(_item("weights", "found", "仓库自带的模型权重/大文件", f"{len(weights)} 个，例如 {', '.join(weights[:3])}，均为二进制，未读取内容。", [_ev_code(weights[0], 0)]))
    dl = r.grep(DOWNLOAD_RX, limit=6, skip=re.compile(r"^\s*(?:#|//)\s*$"))
    if dl:
        items.append(_item("download_hints", "found", "下载或获取数据的线索", "代码或文档里出现下载相关语句，需要你确认下载的是什么、来自哪里。", dl))
    # 论文声明
    paper_ev = []
    for link in find_code_links(app, paper_id):
        if link["kind"] != "github" and (link.get("is_code_statement") or DATA_STMT_RX.search(link["context"])):
            paper_ev.append(_ev_paper(link["page"], link["ord"], f"{link['url']} —— {link['context']}"))
    if paper_ev:
        items.append(_item("paper_data_links", "found", "论文声明的数据/材料链接", "论文里给出了数据或材料的获取地址，需要你自己确认并获取。", paper_ev[:4]))
    for f in ai_facts:
        if f["kind"] == "dataset":
            items.append(_item("dataset:" + f["text"][:24], "found", f"论文提到的数据集：{f['text']}", "由 AI 从论文原文抽取，引文已核对确实出现在该段落里；获取方式需要你自己确认。",
                               [_ev_paper(f["page"], f["ord"], f["quote"])]))
    if not (data_files or dl or paper_ev or any(f["kind"] == "dataset" for f in ai_facts)):
        items.append(_item("data_missing", "missing", "数据获取方式", "论文和代码里都没有找到明确的数据获取方式。这是复现的主要缺口，需要联系作者或自行构造数据。"))
    return items, {"has_data": bool(data_files or dl or paper_ev), "needs_download": bool(dl or paper_ev)}


# ---------------------------------------------------------------- 资源
def _resources(app: AppContext, repo_id: str, paper_id: str, flags: dict) -> list[dict]:
    items: list[dict] = []
    wanted = {"batch_size": "批大小", "epochs": "训练轮数", "iterations": "迭代数", "num_graphs": "训练样本/图数量", "replay_size": "经验回放容量"}
    seen: set[str] = set()
    for p in code_params_mod.code_params(app, repo_id):
        c = code_params_mod.canon(p["name"])
        if c in wanted and (c, p["value"]) not in seen and len(seen) < 8:
            seen.add((c, p["value"]))
            items.append(_item("param:" + c, "found", f"{wanted[c]}（代码取值）", f"{p['name']} = {p['value']}（{p['origin']}）。", [_ev_code(p["path"], p["line"], p.get("context") or "")]))
    with app.db.session() as conn:
        rows = conn.execute("SELECT ord, page, text FROM passages WHERE paper_id = ? AND kind = 'paragraph' ORDER BY ord", (paper_id,)).fetchall()
    hw: list[dict] = []
    for row in rows:
        m = HW_RX.search(row["text"])
        if m:
            hw.append(_ev_paper(row["page"], row["ord"], m.group(0)))
            if len(hw) >= 3:
                break
    if hw:
        items.append(_item("paper_hardware", "found", "论文提到的硬件", "论文里写到了运行所用的硬件，可作为资源参考（不是最低要求）。", hw))
    else:
        items.append(_item("paper_hardware", "uncertain", "论文提到的硬件", "论文正文里没有找到所用硬件的描述，所需资源不确定。"))
    if not seen:
        items.append(_item("resources_unknown", "uncertain", "训练规模", "没有从代码里识别出批大小、轮数等规模参数，训练耗时与显存需求：不确定。"))
    return items


def _risks(app: AppContext, r: _Repo, repo: dict, paper_year: int | None, flags: dict, entry: dict, data: dict) -> list[dict]:
    items: list[dict] = []
    n_scanned = len([f for f in r.files if not f["skipped"]])
    if repo["authored_by"] == "user":
        items.append(_item("risk:not_author", "risk", "代码来源未确认", "这份代码是你提供的，来源没有确认是论文作者的官方实现，行为可能和论文不一致。",
                           [_ev_repo("仓库来源记录：由用户提供，没有确认是论文作者的官方实现")]))
    elif repo["authored_by"] == "self":
        items.append(_item("risk:self_impl", "risk", "不是作者的代码", "这是根据论文自行实现的代码，不是作者的官方实现，对比结果时要考虑实现差异。",
                           [_ev_repo("仓库来源记录：根据论文自行实现")]))
    for w in repo.get("warnings", []):
        items.append(_item("risk:warning", "info", "获取仓库时的提示", str(w)))
    if repo.get("commit_date") and paper_year:
        try:
            gap = int(repo["commit_date"][:4]) - paper_year
            if gap > 2:
                items.append(_item("risk:stale", "risk", "代码版本可能晚于论文", f"代码最后提交于 {repo['commit_date'][:4]} 年，论文发表于 {paper_year} 年，实现可能已变化；复现论文数值时应尽量使用论文发表时的版本。",
                                   [_ev_repo(f"仓库最后提交 {str(repo['commit_date'])[:10]}；论文发表于 {paper_year} 年")]))
        except ValueError:
            pass
    if flags["unpinned"]:
        items.append(_item("risk:unpinned", "risk", "依赖版本没有固定", "依赖文件里多数依赖没有固定版本，用最新版本安装很可能与代码不兼容。",
                           flags.get("unpinned_ev") or []))
    if not r.find_file("license", "license.md", "license.txt", "licence", "copying"):
        items.append(_item("risk:license", "info", "没有找到许可证文件", "仓库里没有 LICENSE 文件，使用、修改或再分发前请先确认许可。"))
    if entry["has_train"] and not entry["has_test"]:
        items.append(_item("risk:no_eval", "risk", "只有训练、没有评估入口", "识别到训练入口，但没有评估/测试入口：论文里的评估结果可能需要自己写评估脚本。",
                           [_ev_repo(f"已识别训练入口，没有评估/测试入口（扫描了 {n_scanned} 个文件）")]))
    if entry["has_test"] and not entry["has_train"]:
        items.append(_item("risk:no_train", "risk", "只有评估、没有训练入口", "识别到评估入口，但没有训练入口：可能依赖作者提供的预训练权重。",
                           [_ev_repo(f"已识别评估入口，没有训练入口（扫描了 {n_scanned} 个文件）")]))
    if not entry["has_train"] and not entry["has_test"]:
        items.append(_item("risk:no_entry", "risk", "没有识别到任何入口", "没有识别到训练或评估入口，需要先通读代码才能确定怎么运行。",
                           [_ev_repo(f"扫描了 {n_scanned} 个文件，没有识别到训练或评估入口")]))
    if not data["has_data"]:
        items.append(_item("risk:no_data", "risk", "缺少数据", "没有找到数据或获取方式，不拿到数据就无法复现实验结果。",
                           [_ev_repo(f"扫描了 {n_scanned} 个文件与论文文字，没有找到数据文件或获取数据的说明")]))
    parse_warn = (repo.get("overview") or {}).get("parse_warnings") or []
    if parse_warn:
        items.append(_item("risk:parse", "info", "部分文件无法完整解析", f"{len(parse_warn)} 个文件没能用语法树解析，静态分析的结果可能不完整。", [_ev_code(w["path"], 1, w["message"]) for w in parse_warn[:3]]))
    return items


def _effort(flags: dict, entry: dict, data: dict, repo: dict) -> dict:
    basis: list[tuple[int, str]] = []
    if flags["compile"]:
        basis.append((2, "需要编译 Cython/C++/CUDA 代码"))
    if flags["tf1"]:
        basis.append((2, "使用 TensorFlow 1.x 接口，需要旧版环境"))
    if flags["gpu"]:
        basis.append((1, "需要 GPU"))
    if data["needs_download"] or not data["has_data"]:
        basis.append((1 if data["has_data"] else 2, "数据需要另外获取" if data["has_data"] else "没有找到数据获取方式"))
    if flags["unpinned"]:
        basis.append((1, "依赖版本没有固定"))
    if not (entry["has_train"] or entry["has_test"]):
        basis.append((2, "没有识别到入口"))
    if repo["authored_by"] != "author":
        basis.append((1, "代码不是作者官方实现"))
    score = sum(w for w, _ in basis)
    level = "低" if score <= 1 else ("中" if score <= 4 else "高")
    return {"level": level, "basis": [t for _, t in basis] or ["没有发现明显的阻碍，仍可能有静态分析看不到的问题"],
            "note": "这是粗档，不是时间估计；它只反映静态分析能看到的障碍。"}


# ---------------------------------------------------------------- AI：论文里的数据集（可选）
async def _ai_dataset_facts(app: AppContext, paper_id: str, ctx=None) -> list[dict]:
    try:
        provider, cfg = AIService(app).provider()
    except LLMError:
        return []
    with app.db.session() as conn:
        rows = conn.execute("SELECT ord, page, text FROM passages WHERE paper_id = ? AND kind = 'paragraph' ORDER BY ord", (paper_id,)).fetchall()
    kw = re.compile(r"dataset|data set|benchmark|corpus|collected|publicly available|data availability|we (?:use|used|evaluate)", re.I)
    chosen = [r for r in rows if kw.search(r["text"])]
    body, used, byord = [], 0, {}
    for r in chosen:
        t = r["text"][:900]
        if used + len(t) > 10000:
            break
        body.append(f"[P{r['ord']}] {t}")
        byord[r["ord"]] = r
        used += len(t)
    if not body:
        return []
    system = ("你是严谨的论文信息抽取器。从【论文摘录】里抽取论文**明确使用的数据集或数据来源名称**（如 ImageNet、某某网络数据集）。"
              "每条给出：text（名称）、ord（所在摘录编号整数）、quote（支撑它的原文片段，必须逐字出自摘录）。不要推测、不要补充摘录里没有的名称。"
              '只输出 JSON：{"datasets": [{"text": "", "ord": 0, "quote": ""}]}')
    messages = [{"role": "system", "content": system}, {"role": "user", "content": "【论文摘录】\n" + "\n\n".join(body)}]
    choice = choose_model(TaskType.METHOD, cfg)
    usage_log.record_request(app, task_type="repro_assess", model=choice.model, paper_id=paper_id, passage_ords=sorted(byord), chars_sent=used,
                             includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": False},
                             summary=f"发送了 {len(body)} 段论文原文（约 {used:,} 字）用于抽取数据集名称（复现评估）")
    try:
        res = await provider.complete(choice.model, messages, temperature=0, max_tokens=1500, json_mode=True)
    except LLMError:
        return []
    AIService(app)._log_usage("repro_assess", choice.model, res.usage, messages, res.text, paper_id)
    m = re.search(r"\{.*\}", res.text, re.S)
    try:
        data = json.loads(m.group(0)).get("datasets", []) if m else []
    except ValueError:
        return []
    norm = lambda s: re.sub(r"\s+", "", str(s)).lower()  # noqa: E731
    out, seen = [], set()
    for d in data:
        if not isinstance(d, dict) or not isinstance(d.get("ord"), int) or d["ord"] not in byord:
            continue
        text, quote = str(d.get("text", "")).strip(), str(d.get("quote", ""))
        if not (text and quote) or norm(quote) not in norm(byord[d["ord"]]["text"]) or norm(text) not in norm(byord[d["ord"]]["text"]) or text.lower() in seen:
            continue  # 引文或名称不在原段落里 → 丢弃（防止编造）
        seen.add(text.lower())
        out.append({"kind": "dataset", "text": text[:80], "ord": d["ord"], "page": byord[d["ord"]]["page"], "quote": quote[:300]})
    return out[:8]


# ---------------------------------------------------------------- 入口
async def run(app: AppContext, repo_id: str, ctx=None) -> dict:
    repo = service.get_repo(app, repo_id)
    if repo["status"] != "ready" or not repo.get("overview"):
        raise ApiError("conflict", "代码仓库还没有分析完成，无法评估。", 409)
    with app.db.session() as conn:
        paper = conn.execute("SELECT id, year, title FROM papers WHERE id = ?", (repo["paper_id"],)).fetchone()
    r = _Repo(app, repo)
    if ctx:
        ctx.progress(0.15, "读取代码与配置")
    env_items, flags = _environment(r, repo["overview"])
    entry_items, entry = _entries(r, repo["overview"])
    if ctx:
        ctx.progress(0.45, "查找数据与资源线索")
    ai_facts = await _ai_dataset_facts(app, paper["id"], ctx)
    data_items, data = _data(app, r, paper["id"], repo, ai_facts)
    res_items = _resources(app, repo_id, paper["id"], flags)
    risk_items = _risks(app, r, repo, paper["year"], flags, entry, data)
    report = {
        "generated_at": utcnow(), "ai_used": bool(ai_facts),
        "repo": {"id": repo_id, "url": repo["url"], "commit_sha": repo["commit_sha"], "commit_date": repo["commit_date"], "authored_by": repo["authored_by"]},
        "sections": [
            {"key": "environment", "title": "环境", "items": env_items},
            {"key": "entry", "title": "入口与命令", "items": entry_items},
            {"key": "data", "title": "数据", "items": data_items},
            {"key": "resources", "title": "资源与规模", "items": res_items},
            {"key": "risks", "title": "风险与缺口", "items": risk_items},
        ],
        "effort": _effort(flags, entry, data, repo),
        "facts": {"commands": entry["commands"][:8], "has_train": entry["has_train"], "has_test": entry["has_test"], "compile": flags["compile"],
                  "gpu": flags["gpu"], "needs_download": data["needs_download"], "has_data": data["has_data"], "dep_files": flags["dep_files"]},
        "note": "评估只来自静态分析和论文原文，没有运行任何代码；“没找到”不等于“不存在”，“不确定”表示现有证据不足以判断。",
    }
    with app.db.session() as conn:
        conn.execute(
            "INSERT INTO repro_assessments(repo_id, paper_id, status, report_json, error, updated_at) VALUES (?,?, 'ready', ?, NULL, ?) "
            "ON CONFLICT(repo_id) DO UPDATE SET status='ready', report_json=excluded.report_json, error=NULL, updated_at=excluded.updated_at",
            (repo_id, paper["id"], dumps(report), utcnow()),
        )
    if ctx:
        ctx.progress(1.0, "评估完成")
    return report


def get(app: AppContext, repo_id: str) -> dict:
    with app.db.session() as conn:
        row = conn.execute("SELECT * FROM repro_assessments WHERE repo_id = ?", (repo_id,)).fetchone()
    if row is None:
        return {"status": "none", "report": None, "updated_at": None, "error": None}
    return {"status": row["status"], "report": loads(row["report_json"]), "updated_at": row["updated_at"], "error": row["error"]}
