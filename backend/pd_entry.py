"""PyInstaller 入口：打包后的 paperdesk-backend.exe 等价于 `python -m paperdesk ...`。"""
import multiprocessing

from paperdesk.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
