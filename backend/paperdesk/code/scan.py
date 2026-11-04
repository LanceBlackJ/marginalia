"""项目结构扫描：语言构成、依赖/框架、程序入口、文件角色。全部是静态规则，不执行任何代码。"""
from __future__ import annotations

import json
import re
import tomllib
from collections import Counter, defaultdict
from pathlib import PurePosixPath

from .model import FileAnalysis

LANG_BY_EXT = {
    ".py": "python", ".pyx": "cython", ".pxd": "cython", ".ipynb": "jupyter",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".java": "java", ".c": "c", ".h": "c/c++ header", ".hpp": "c++", ".hh": "c++", ".cpp": "c++", ".cc": "c++", ".cxx": "c++", ".cu": "cuda", ".cuh": "cuda",
    ".go": "go", ".rs": "rust", ".sh": "shell", ".bash": "shell", ".m": "matlab/objective-c", ".r": "r", ".jl": "julia", ".scala": "scala", ".kt": "kotlin",
    ".md": "markdown", ".rst": "rst", ".txt": "text", ".yaml": "yaml", ".yml": "yaml", ".json": "json", ".toml": "toml", ".ini": "ini", ".cfg": "ini",
    ".xml": "xml", ".html": "html", ".css": "css", ".csv": "csv", ".tsv": "csv", ".sql": "sql", ".tex": "latex", ".bat": "batch", ".cmake": "cmake",
}
NAME_LANG = {"makefile": "makefile", "dockerfile": "dockerfile", "cmakelists.txt": "cmake", "license": "text", "readme": "markdown"}
CODE_LANGS = {"python", "cython", "javascript", "typescript", "java", "c", "c++", "c/c++ header", "cuda", "go", "rust", "shell", "matlab/objective-c", "r", "julia", "scala", "kotlin"}
DEP_FILES = {
    "requirements.txt", "requirements-dev.txt", "environment.yml", "environment.yaml", "conda.yaml", "pyproject.toml", "setup.py", "setup.cfg",
    "pipfile", "package.json", "cargo.toml", "pom.xml", "build.gradle", "go.mod", "cmakelists.txt", "makefile", "dockerfile", "install.sh", "pipfile.lock",
}
FRAMEWORKS = {
    "torch": "PyTorch", "pytorch": "PyTorch", "tensorflow": "TensorFlow", "tensorflow-gpu": "TensorFlow", "keras": "Keras", "jax": "JAX", "flax": "Flax",
    "sklearn": "scikit-learn", "scikit-learn": "scikit-learn", "numpy": "NumPy", "scipy": "SciPy", "pandas": "pandas", "networkx": "NetworkX",
    "dgl": "DGL", "torch_geometric": "PyG", "torch-geometric": "PyG", "matplotlib": "Matplotlib", "cv2": "OpenCV", "opencv-python": "OpenCV",
    "transformers": "Transformers", "gym": "Gym", "gymnasium": "Gymnasium", "cython": "Cython", "igraph": "igraph", "graph-tool": "graph-tool",
    "flask": "Flask", "django": "Django", "fastapi": "FastAPI", "react": "React", "vue": "Vue", "express": "Express", "lightgbm": "LightGBM", "xgboost": "XGBoost",
    "pytorch-lightning": "PyTorch Lightning", "ray": "Ray", "mxnet": "MXNet", "paddlepaddle": "PaddlePaddle", "tqdm": None, "yaml": None,
}
ROLE_TOKENS = [
    ("train", re.compile(r"train|learn|fit|optimi[sz]|pretrain|finetune")),
    ("test", re.compile(r"test|eval|infer|predict|valid|benchmark|metric|score")),
    ("data", re.compile(r"data|dataset|loader|preprocess|prepare|generate|corpus|graph_gen|io")),
    ("model", re.compile(r"model|net|layer|module|agent|encoder|decoder|backbone|arch|policy|embed")),
    ("util", re.compile(r"util|helper|common|tools?|misc|lib")),
]
ENTRY_NAMES = re.compile(r"^(main|run|app|cli|train|test|eval|evaluate|infer|inference|demo|launch|start|index|server)([_\-\w]*)$")
README_CMD = re.compile(r"(?:^|\n)\s*(?:\$\s*)?((?:python3?|python -m|bash|sh|\./|node|npm run|make|java|cargo run|go run|Rscript|julia|matlab)\s+[^\n`]{2,120})")
CONFIG_NAME = re.compile(r"(config|conf|settings|params|hparams|hyper|\.cfg$|\.ini$|\.ya?ml$|\.toml$|\.json$)", re.I)


def language_of(path: str) -> str | None:
    p = PurePosixPath(path)
    return NAME_LANG.get(p.name.lower()) or LANG_BY_EXT.get(p.suffix.lower())


def _pkg_name(spec: str) -> str:
    return re.split(r"[<>=!~;\[\s@]", spec.strip(), maxsplit=1)[0].strip().lower()


def parse_dependencies(path: str, text: str) -> list[str]:
    name = PurePosixPath(path).name.lower()
    deps: list[str] = []
    try:
        if name.startswith("requirements") and name.endswith(".txt"):
            for line in text.splitlines():
                line = line.split("#", 1)[0].strip()
                if line and not line.startswith(("-", "git+", "http")):
                    deps.append(_pkg_name(line))
        elif name in ("environment.yml", "environment.yaml", "conda.yaml"):
            for m in re.finditer(r"^\s*-\s*([A-Za-z0-9_.\-]+)\s*(?:[=<>!].*)?$", text, re.M):
                deps.append(m.group(1).lower())
        elif name == "pyproject.toml":
            data = tomllib.loads(text)
            proj = data.get("project", {})
            deps += [_pkg_name(d) for d in proj.get("dependencies", [])]
            poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
            deps += [k.lower() for k in poetry if k.lower() != "python"]
        elif name == "setup.py":
            m = re.search(r"install_requires\s*=\s*\[(.*?)\]", text, re.S)
            if m:
                deps += [_pkg_name(x) for x in re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))]
        elif name == "package.json":
            data = json.loads(text)
            deps += list(data.get("dependencies", {})) + list(data.get("devDependencies", {}))
        elif name == "cargo.toml":
            deps += list(tomllib.loads(text).get("dependencies", {}))
        elif name == "pom.xml":
            deps += re.findall(r"<artifactId>([^<]+)</artifactId>", text)
        elif name == "go.mod":
            deps += [m.group(1) for m in re.finditer(r"^\s*([\w\.\-/]+)\s+v[\d\.]+", text, re.M)]
        elif name == "cmakelists.txt":
            deps += [m.group(1).lower() for m in re.finditer(r"find_package\(\s*(\w+)", text, re.I)]
    except (ValueError, tomllib.TOMLDecodeError, KeyError, TypeError):
        pass
    return [d for d in deps if d]


def detect_frameworks(deps: list[str], imports: set[str]) -> list[str]:
    found: dict[str, None] = {}
    for d in [*deps, *imports]:
        label = FRAMEWORKS.get(d.lower())
        if label:
            found[label] = None
    return list(found)


def entry_points_from_manifests(files: dict[str, str]) -> list[dict]:
    """console_scripts / package.json scripts / Makefile targets / README 里的运行命令。"""
    out: list[dict] = []
    for path, text in files.items():
        name = PurePosixPath(path).name.lower()
        try:
            if name == "setup.py":
                for m in re.finditer(r"['\"]([\w\-]+)\s*=\s*([\w\.]+):(\w+)['\"]", text):
                    out.append({"kind": "console_script", "path": m.group(2).replace(".", "/") + ".py", "symbol": m.group(3), "command": m.group(1), "reason": "setup.py console_scripts"})
            elif name == "pyproject.toml":
                scripts = tomllib.loads(text).get("project", {}).get("scripts", {})
                for k, v in scripts.items():
                    mod, _, fn = str(v).partition(":")
                    out.append({"kind": "console_script", "path": mod.replace(".", "/") + ".py", "symbol": fn or None, "command": k, "reason": "pyproject scripts"})
            elif name == "package.json":
                data = json.loads(text)
                for k, v in (data.get("scripts") or {}).items():
                    out.append({"kind": "npm_script", "path": path, "symbol": None, "command": f"npm run {k}", "reason": f"package.json scripts.{k}: {str(v)[:80]}"})
            elif name == "makefile":
                for m in re.finditer(r"^([a-zA-Z_][\w\-]*)\s*:(?!=)", text, re.M):
                    if m.group(1) not in ("all", "clean", ".PHONY"):
                        out.append({"kind": "make_target", "path": path, "symbol": None, "command": f"make {m.group(1)}", "reason": "Makefile target"})
            elif name.startswith("readme"):
                for m in README_CMD.finditer(text):
                    cmd = m.group(1).strip()
                    mf = re.search(r"([\w\./\-]+\.(?:py|sh|js|R|jl|m))\b", cmd)
                    out.append({"kind": "readme_command", "path": mf.group(1) if mf else path, "symbol": None, "command": cmd[:140], "reason": "README 中的运行命令"})
        except (ValueError, tomllib.TOMLDecodeError):
            continue
    return out


def classify_role(path: str, language: str | None, fa: FileAnalysis | None) -> str:
    p = PurePosixPath(path)
    name = p.name.lower()
    stem = p.stem.lower()
    if name in DEP_FILES or (name.startswith("requirements") and name.endswith(".txt")):
        return "dependency"
    if language in ("markdown", "rst", "latex") or (language == "text" and name in ("license", "readme")):
        return "doc"
    if language in ("yaml", "json", "toml", "ini", "xml") and CONFIG_NAME.search(name):
        return "config"
    if language in ("yaml", "toml", "ini"):
        return "config"
    if language in ("csv", "html", "css", "sql"):
        return "other"
    dirs = [d.lower() for d in p.parts[:-1]]
    if fa is not None and fa.has_main and ENTRY_NAMES.match(stem):
        for role, rx in ROLE_TOKENS[:2]:
            if rx.search(stem):
                return role
        return "entry"
    for role, rx in ROLE_TOKENS:
        if rx.search(stem):
            return role
    for role, rx in ROLE_TOKENS:
        if any(rx.search(d) for d in dirs):
            return role
    if fa is not None and fa.has_main:
        return "entry"
    if CONFIG_NAME.search(name) and language in CODE_LANGS:
        return "config"
    return "other"


def summarize_languages(files: list[dict]) -> dict[str, dict]:
    agg: dict[str, dict] = defaultdict(lambda: {"files": 0, "lines": 0, "bytes": 0})
    for f in files:
        lang = f.get("language")
        if not lang or lang not in CODE_LANGS:
            continue
        a = agg[lang]
        a["files"] += 1
        a["lines"] += f.get("lines", 0)
        a["bytes"] += f.get("size", 0)
    return dict(sorted(agg.items(), key=lambda kv: -kv[1]["lines"]))


def top_dirs(paths: list[str], depth: int = 1) -> list[dict]:
    c: Counter = Counter()
    for p in paths:
        parts = PurePosixPath(p).parts
        c["/".join(parts[:depth]) if len(parts) > depth else "."] += 1
    return [{"path": k, "files": v} for k, v in c.most_common(30)]
