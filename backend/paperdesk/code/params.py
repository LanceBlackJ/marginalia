"""代码里的参数（argparse 默认值、常量、调用里的关键字参数、配置文件）和参数名的归一化。复现评估据此给出“训练规模”等信息。"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from ..context import AppContext
from . import service

# 规范名 → 论文用语 / 代码里常见变量名（比较前统一小写并去掉非字母数字）
CANON: dict[str, list[str]] = {
    "learning_rate": ["lr", "learningrate", "learning rate", "alpha", "initlr", "baselr"],
    "batch_size": ["batchsize", "batch size", "minibatch", "bs", "nbatch"],
    "embedding_dim": ["embdim", "embeddingsize", "embeddingdimension", "embeddim", "embeddingdim", "embsize", "embedsize", "embedding dimension", "embedding size", "embeddingdimensions"],
    "hidden_dim": ["hiddendim", "hiddensize", "hiddenunits", "hidden", "nhid", "hid", "hidden size", "hidden units", "hidden dimension"],
    "epochs": ["epoch", "epochs", "numepochs", "nepochs", "maxepoch", "maxepochs"],
    "iterations": ["iteration", "iterations", "maxiteration", "numiterations", "niter", "maxiter", "numiter", "maxiterations"],
    "gamma": ["gamma", "discount", "discountfactor", "discount factor", "discountrate"],
    "dropout": ["dropout", "droprate", "dropoutrate", "dropprob"],
    "n_step": ["nstep", "nsteps", "numstep", "nstepreturn", "n-step"],
    "nodes_min": ["nodesmin", "nummin", "nmin", "minnodes", "minn", "numnodesmin", "min_nodes", "numminnodes"],
    "nodes_max": ["nodesmax", "nummax", "nmax", "maxnodes", "maxn", "numnodesmax", "max_nodes", "nummaxnodes"],
    "num_graphs": ["numgraphs", "ngraphs", "graphnum", "numtraingraphs", "traingraphs", "numtrain", "ntrain", "numsamples", "graphs"],
    "replay_size": ["memorysize", "replaysize", "buffersize", "memsize", "maxmemory", "replaybuffer", "replaymemory", "memorycapacity"],
    "weight_decay": ["weightdecay", "wd", "l2", "reg", "regularization"],
    "epsilon": ["epsilon", "eps", "epsstart", "epsilonstart"],
    "num_layers": ["numlayers", "nlayers", "layers", "depth", "layernum", "maxbpiter", "numhops", "nhops"],
    "optimizer": ["optimizer", "optim", "opt"],
}
LOOKUP = {re.sub(r"[^a-z0-9]", "", alias.lower()): canon for canon, aliases in CANON.items() for alias in [canon, *aliases]}
def canon(name: str) -> str | None:
    hit = LOOKUP.get(re.sub(r"[^a-z0-9]", "", name.lower().replace("-", "")))
    if hit:
        return hit
    # AI 给的名字五花八门（min_training_nodes / experience_replay_buffer_size …）：按词元兜底归一
    t = set(re.split(r"[^a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1_\2", name).lower()))
    if t & {"node", "nodes"}:
        if t & {"min", "minimum"}:
            return "nodes_min"
        if t & {"max", "maximum"}:
            return "nodes_max"
    if t & {"graph", "graphs"} and t & {"num", "number", "training", "train", "count", "total"}:
        return "num_graphs"
    if t & {"replay", "memory", "buffer"} and t & {"size", "capacity", "length"}:
        return "replay_size"
    if "batch" in t and t & {"size", "num"}:
        return "batch_size"
    if "learning" in t and "rate" in t:
        return "learning_rate"
    if "embedding" in t and t & {"size", "dim", "dimension", "dimensions"}:
        return "embedding_dim"
    if t & {"discount", "gamma"}:
        return "gamma"
    if t & {"epoch", "epochs"}:
        return "epochs"
    if t & {"iteration", "iterations", "iter", "iters"}:
        return "iterations"
    return None


def config_params(app: AppContext, repo_id: str) -> list[dict]:
    with app.db.session() as conn:
        files = [r["path"] for r in conn.execute("SELECT path FROM code_files WHERE repo_id = ? AND role = 'config' AND skipped IS NULL LIMIT 40", (repo_id,)).fetchall()]
    out = []
    kv = re.compile(r"^\s*[\"']?([A-Za-z_][\w\-\.]*)[\"']?\s*[:=]\s*([\-+]?\d[\d\.eE\-+]*|true|false|[A-Za-z_]+)\s*,?\s*(?:[#;].*)?$")
    for path in files:
        lines = service.file_lines(app, repo_id, path) or []
        for i, line in enumerate(lines[:2000], 1):
            m = kv.match(line)
            if m and canon(m.group(1)):
                out.append({"path": path, "line": i, "name": m.group(1), "value": m.group(2), "origin": "config", "context": PurePosixPath(path).name})
    return out


def code_params(app: AppContext, repo_id: str) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute("SELECT path, line, name, value, origin, context FROM code_params WHERE repo_id = ?", (repo_id,)).fetchall()
    return [dict(r) for r in rows] + config_params(app, repo_id)
