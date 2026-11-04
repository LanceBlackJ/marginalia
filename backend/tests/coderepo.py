"""测试用的合成代码仓库（类似小型科研项目：Python 为主，附带 C++ 与 JS）。"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

FILES: dict[str, str] = {
    "README.md": "# Demo\n\nTrain:\n\n```\npython train.py --lr 0.001 --nodes_min 30\n```\n\nEvaluate with `python evaluate.py`.\n",
    "requirements.txt": "torch>=1.8\nnumpy\nnetworkx==2.5\n# comment\ntqdm\n",
    "config/default.yaml": "lr: 0.0001\nbatch_size: 64\n",
    "train.py": '''"""Training entry."""
import argparse
import numpy as np
from model.agent import Agent
from data.loader import load_graphs, preprocess
from utils import save_model

LEARNING_RATE = 1e-4
N_STEP = 5


def build_parser():
    p = argparse.ArgumentParser()
    p.add_argument('--lr', type=float, default=0.0001, help='learning rate')
    p.add_argument('--batch_size', type=int, default=64)
    p.add_argument('--nodes_min', type=int, default=30)
    p.add_argument('--nodes_max', type=int, default=50)
    return p


def run_training(args):
    """Offline training loop."""
    graphs = load_graphs(args.nodes_min, args.nodes_max)
    agent = Agent(args.lr)
    for g in graphs:
        batch = preprocess(g)
        agent.learn(batch)
    save_model(agent)
    return agent


def main():
    args = build_parser().parse_args()
    run_training(args)


if __name__ == "__main__":
    main()
''',
    "model/__init__.py": "",
    "model/agent.py": '''import numpy as np
from utils import log


class Agent:
    """Q-learning agent."""

    def __init__(self, lr, gamma=0.99):
        self.lr = lr
        self.gamma = gamma
        self.opt = Adam(lr=0.0001)

    def act(self, state):
        return self.q_values(state).argmax()

    def q_values(self, state):
        return np.dot(state, self.w)

    def learn(self, batch):
        loss = self.q_values(batch).mean()
        log(loss)
        return self.opt.step(loss)
''',
    "data/__init__.py": "",
    "data/loader.py": '''import networkx as nx


def load_graphs(n_min, n_max):
    """Generate synthetic graphs."""
    return [nx.erdos_renyi_graph(n, 0.1) for n in range(n_min, n_max)]


def preprocess(g):
    return normalize(g)


def normalize(g):
    return g
''',
    "utils.py": "def log(x):\n    print(x)\n\n\ndef save_model(m):\n    log('saved')\n\n\nclass Hook:\n    def on_done(self):\n        log('done')\n\n\ndef run_hooks(h):\n    h.on_done()\n",
    "evaluate.py": '''from model.agent import Agent
from data.loader import load_graphs

if __name__ == "__main__":
    a = Agent(0.1)
    load_graphs(10, 20)
''',
    "native/graph.cpp": '''#include <vector>
class Graph {
 public:
  int degree(int v) { return helper(v); }
};
int helper(int v) { return v; }
int main() { Graph g; g.degree(1); return 0; }
''',
    "web/app.js": "function render(x) { return draw(x); }\nfunction draw(x) { return x; }\nrender(1);\n",
    "weights/model.pt": "BINARY",   # 二进制扩展名：只在树里标注，不落盘
}


def write_repo(root: Path, files: dict[str, str] | None = None) -> Path:
    for rel, content in (files or FILES).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def make_zip(files: dict[str, bytes | str], top: str | None = "repo-abc123") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(f"{top}/{rel}" if top else rel, data)
    return buf.getvalue()
