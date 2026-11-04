"""假的 docker 可执行文件（只用于测试执行器的逻辑，不是真 Docker）。

状态目录由环境变量 FAKE_DOCKER_STATE 指定：每次调用把参数追加到 calls.jsonl，run 还会记下它能看到的环境变量名。
“容器里的命令”由 sh -c 后面的文本里的关键字模拟：
  sleep    一直运行，直到收到 kill（或被终止）
  fail     输出一行后以退出码 3 结束
  metrics  打印 "accuracy: 0.91" 等并在 /work 写 results.csv
  其它     打印 hello
"""
import json
import os
import sys
import time
from pathlib import Path

state = Path(os.environ["FAKE_DOCKER_STATE"])
state.mkdir(parents=True, exist_ok=True)
argv = sys.argv[1:]
with (state / "calls.jsonl").open("a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": argv, "env_names": sorted(os.environ)}, ensure_ascii=False) + "\n")

sub = argv[0] if argv else ""
if sub == "images":
    for line in (state / "images.txt").read_text("utf-8").splitlines() if (state / "images.txt").exists() else []:
        print(line)
elif sub == "info":
    print("29.9.2")
elif sub == "pull":
    print(f"Pulling {argv[1]}")
    (state / "images.txt").open("a", encoding="utf-8").write(f"{argv[1]}\t130MB\n")
elif sub in ("kill", "rm"):
    name = argv[-1]
    (state / f"{name}.{sub}").write_text("1")
elif sub == "run":
    name = argv[argv.index("--name") + 1]
    work = None
    for i, a in enumerate(argv):
        if a == "--mount" and "target=/work" in argv[i + 1]:
            work = Path(argv[i + 1].split("source=")[1].split(",")[0])
    script = argv[-1]
    print("container started", flush=True)
    if "sleep" in script:
        (state / f"{name}.started").write_text("1")
        while not (state / f"{name}.kill").exists():
            time.sleep(0.05)
        sys.exit(137)
    if "fail" in script:
        print("Traceback: boom", flush=True)
        sys.exit(3)
    if "metrics" in script:
        print("epoch: 1")
        print("epoch: 2")
        print("accuracy: 0.91", flush=True)
        print("loss = 0.25")
        print("not a metric line 1 2 3")
        if work:
            (work / "results.csv").write_text("metric,value\nf1,0.88\n", "utf-8")
        sys.exit(0)
    print("hello", flush=True)
