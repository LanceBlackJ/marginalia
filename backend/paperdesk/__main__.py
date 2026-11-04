"""启动入口：python -m paperdesk --host 127.0.0.1 --port 8765 --data-dir <dir>

令牌通过环境变量 PAPERDESK_TOKEN 传入（避免出现在进程命令行里）。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from .app import create_app
from .config import Paths


def main() -> int:
    parser = argparse.ArgumentParser(prog="paperdesk")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("出于安全考虑，Marginalia 后端只允许监听回环地址。", file=sys.stderr)
        return 2
    token = os.environ.get("PAPERDESK_TOKEN")
    if not token:
        print("缺少环境变量 PAPERDESK_TOKEN。", file=sys.stderr)
        return 2

    paths = Paths(Path(args.data_dir))
    paths.ensure()
    handler = RotatingFileHandler(paths.logs_dir / "server.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)

    app = create_app(paths.data_dir, token)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
