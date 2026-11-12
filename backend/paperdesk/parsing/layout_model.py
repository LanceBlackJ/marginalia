"""本地版面检测模型（DocLayout-YOLO，ONNX，CPU 可跑）：只负责找出“表格 / 图 / 显示公式”的框。

为什么只要这三样：规则解析（字体、坐标、栏、标题）在文字上已经不比模型差，但规则认不出没有三线、没有编号的表格，
也认不出没有编号的显示公式；这两样在表格多的论文（VGG、JMLR、BERT）里漏得很多，漏了之后表里的数字和单元格就混进正文段落。
模型的框只用来补这些对象，文字、字体、坐标仍然取自 PDF 文字层。

- 模型文件（约 75 MB，Apache-2.0）第一次用到时下载到数据目录的 models/ 下；下载失败、没有 onnxruntime / opencv 都不影响解析，
  退回纯规则；
- 每篇论文检测一次，结果缓存在数据库（page_boxes 表），重新解析不用再跑模型；
- 单页约 0.6 秒（CPU），只在导入/重新解析时跑一次。
"""
from __future__ import annotations

import ast
import logging
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

MODEL_VERSION = "doclayout-yolo-docstructbench-1024-v1"
MODEL_FILE = "doclayout_yolo_docstructbench_imgsz1024.onnx"
MODEL_SIZE = 75_324_598
MODEL_URLS = (
    "https://huggingface.co/wybxc/DocLayout-YOLO-DocStructBench-onnx/resolve/main/" + MODEL_FILE,
    "https://hf-mirror.com/wybxc/DocLayout-YOLO-DocStructBench-onnx/resolve/main/" + MODEL_FILE,
)
SIZE = 1024
CONF = 0.25
KEEP = ("table", "figure", "formula")  # 缓存和使用的类别
_CLASS_TO_KIND = {"table": "table", "figure": "figure", "isolate_formula": "formula"}

# 识别函数：RGB 图像（numpy HxWx3 uint8）→ [(类别名, 置信度, x0, y0, x1, y1)]，坐标是 0–1 的比例。测试可以注入假引擎。
DetectFn = Callable[["object"], "list[tuple[str, float, float, float, float, float]]"]

_override: DetectFn | None = None
_session = None
_names: dict[int, str] = {}
_lock = threading.Lock()


class ModelUnavailable(Exception):
    """没有模型文件或缺运行库。"""


def set_engine(fn: DetectFn | None) -> None:
    """仅供测试：注入检测函数（None 恢复真实引擎）。"""
    global _override
    _override = fn


def models_dir(data_dir: Path) -> Path:
    return data_dir / "models"


def model_path(data_dir: Path) -> Path:
    return models_dir(data_dir) / MODEL_FILE


def runtime_ok() -> bool:
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def available(data_dir: Path) -> bool:
    if _override is not None:
        return True
    p = model_path(data_dir)
    return runtime_ok() and p.exists() and p.stat().st_size == MODEL_SIZE


def download(data_dir: Path, progress: Callable[[float], None] | None = None) -> bool:
    """下载模型文件；成功（或已存在）返回 True。先试 Hugging Face，再试国内镜像；先写临时文件，大小对了才改名。"""
    import httpx

    target = model_path(data_dir)
    if target.exists() and target.stat().st_size == MODEL_SIZE:
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    for url in MODEL_URLS:
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(30.0, read=60.0)) as r:
                if r.status_code != 200:
                    continue
                done = 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(min(1.0, done / MODEL_SIZE))
            if tmp.stat().st_size == MODEL_SIZE:
                tmp.replace(target)
                return True
            log.warning("版面模型下载大小不对（%s 字节），丢弃", tmp.stat().st_size)
        except Exception as exc:  # noqa: BLE001
            log.warning("版面模型下载失败（%s）：%s", url.split("/")[2], exc)
        finally:
            if tmp.exists() and not target.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
    return False


def _get_session(data_dir: Path):
    global _session, _names
    with _lock:
        if _session is None:
            if not runtime_ok():
                raise ModelUnavailable("缺少 onnxruntime / opencv")
            p = model_path(data_dir)
            if not p.exists():
                raise ModelUnavailable("还没有下载版面模型")
            import onnxruntime as ort

            _session = ort.InferenceSession(str(p), providers=["CPUExecutionProvider"])
            meta = _session.get_modelmeta().custom_metadata_map
            _names = {int(k): v for k, v in ast.literal_eval(meta["names"]).items()}
        return _session


def _detect_image(data_dir: Path, img) -> list[tuple[str, float, float, float, float, float]]:
    import cv2
    import numpy as np

    if _override is not None:
        return _override(img)
    sess = _get_session(data_dir)
    h, w = img.shape[:2]
    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    r = min(SIZE / h, SIZE / w)
    rh, rw = int(round(h * r)), int(round(w * r))
    bgr = cv2.resize(bgr, (rw, rh), interpolation=cv2.INTER_LINEAR)
    top, left = (SIZE - rh) // 2, (SIZE - rw) // 2
    pix = cv2.copyMakeBorder(bgr, top, SIZE - rh - top, left, SIZE - rw - left, cv2.BORDER_CONSTANT, value=(114, 114, 114))
    x = np.transpose(pix, (2, 0, 1))[None].astype(np.float32) / 255.0
    pred = sess.run(None, {"images": x})[0]
    pred = pred.reshape(-1, pred.shape[-1])
    pred = pred[pred[:, 4] > CONF]
    out = []
    for p in pred:
        x0, y0, x1, y1 = (p[0] - left) / r, (p[1] - top) / r, (p[2] - left) / r, (p[3] - top) / r
        out.append((_names.get(int(p[5]), "?"), float(p[4]), max(0.0, x0) / w, max(0.0, y0) / h, min(float(w), x1) / w, min(float(h), y1) / h))
    return out


def detect_pdf(pdf: Path, data_dir: Path, pages: list[int] | None = None, check: Callable[[], None] | None = None,
               on_page: Callable[[int, int], None] | None = None) -> dict[int, list[dict]]:
    """逐页检测：{页码(1 起): [{"kind": table|figure|formula, "conf", "box": [x0,y0,x1,y1]（0–1，显示方向的页面）}]}。
    渲染用 render.render_clip（与解析共用 PyMuPDF 锁），模型推理在锁外进行。"""
    import cv2
    import numpy as np

    from .render import render_clip

    import pymupdf

    with _lock_pdf():
        doc = pymupdf.open(str(pdf))
        try:
            n = len(doc)
            sizes = {i + 1: (doc[i].rect.width, doc[i].rect.height) for i in range(n)}
        finally:
            doc.close()
    todo = pages if pages is not None else list(range(1, n + 1))
    out: dict[int, list[dict]] = {}
    for k, pn in enumerate(todo, 1):
        if check:
            check()
        w, h = sizes[pn]
        dpi = int(max(40, min(200, SIZE / (max(w, h, 1.0) / 72.0))))
        png = render_clip(pdf, pn, None, dpi, pad=0)
        img = cv2.cvtColor(cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        boxes = []
        for name, conf, x0, y0, x1, y1 in _detect_image(data_dir, img):
            kind = _CLASS_TO_KIND.get(name)
            if kind and x1 > x0 and y1 > y0:
                boxes.append({"kind": kind, "conf": round(float(conf), 3), "box": [round(float(v), 4) for v in (x0, y0, x1, y1)]})  # 模型给的是 numpy 数，转成 Python 数才能存成 JSON
        out[pn] = boxes
        if on_page:
            on_page(k, len(todo))
    return out


def _lock_pdf():
    from .pdf_parser import _PARSE_LOCK

    return _PARSE_LOCK
