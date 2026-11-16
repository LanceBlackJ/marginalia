"""启动入口：python -m paperdesk --host 127.0.0.1 --port 8765 --data-dir <dir>

本地桌面端会把 API 和手机端页面一起绑定到局域网。局域网模式由
PAPERDESK_ALLOW_REMOTE=1 开启；它不是账号登录，安全边界是用户自己的局域网。
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
    parser.add_argument("--web-root", default=os.environ.get("PAPERDESK_WEB_ROOT"))
    args = parser.parse_args()

    allow_remote = os.environ.get("PAPERDESK_ALLOW_REMOTE") == "1"
    if args.host not in ("127.0.0.1", "localhost", "::1") and not allow_remote:
        print("出于安全考虑，公网监听需要先设置 PAPERDESK_ALLOW_REMOTE=1。", file=sys.stderr)
        return 2
    token = os.environ.get("PAPERDESK_TOKEN")
    if not token and not allow_remote:
        print("缺少环境变量 PAPERDESK_TOKEN。", file=sys.stderr)
        return 2
    if allow_remote:
        logging.getLogger("paperdesk").warning("LAN mode enabled; keep the data directory private and do not expose this port to the public internet")

    paths = Paths(Path(args.data_dir))
    paths.ensure()
    handler = RotatingFileHandler(paths.logs_dir / "server.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)

    app = create_app(paths.data_dir, token, web_root=Path(args.web_root) if args.web_root else None)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
