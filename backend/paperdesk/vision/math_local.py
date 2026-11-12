"""本地行内公式识别（texify，纯本机运行，不联网识别，不上传任何内容）。

怎么用才准：texify 要看到**带留白的整行**（正文 + 公式一起），它输出整行文字并把公式写成 $…$；只给它一小截紧贴字符的公式图它会乱写
（实测：小图 12/62 与视觉模型接近，整行加白边后规则还原出的公式有 82% 被原样认出，比网关的视觉模型 70% 还高，尤其是下标）。
所以这里按“整行”送图，再把输出里的 $…$ 对应回候选公式（见 inline_math_ai._match_segment）。

可选组件：需要 PyTorch + texify + transformers（体积大，见 requirements-local-math.txt），没装就是不可用，
行内公式仍走视觉模型兜底；模型权重（约 600 MB）第一次用到时下载到数据目录 models/hf，国内直连失败自动换镜像。
"""
from __future__ import annotations

import importlib.util
import io
import logging
import os
import re
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

PAD_PX = 40  # 整行图四周加的白边（实测没有白边时 texify 只有 71%，加了 82%）
BATCH = 6
MIRROR = "https://hf-mirror.com"

# 识别函数：[PNG 字节] → [每张图的转写文字（公式在 $…$ 里）]。测试可以注入假引擎。
EngineFn = Callable[[list[bytes]], list[str]]

_override: EngineFn | None = None
_state: dict = {}
_lock = threading.Lock()


def set_engine(fn: EngineFn | None) -> None:
    """仅供测试：注入识别函数（None 恢复真实引擎）。"""
    global _override
    _override = fn


def installed() -> bool:
    """依赖装了没有（不会真的导入 PyTorch，免得拖慢启动）。"""
    return all(importlib.util.find_spec(m) is not None for m in ("torch", "texify", "transformers"))


def available() -> bool:
    return _override is not None or installed()


def cache_dir(data_dir: Path) -> Path:
    return data_dir / "models" / "hf"


def _load(data_dir: Path):
    with _lock:
        if "model" in _state:
            return _state["model"], _state["processor"]
        os.environ.setdefault("HF_HUB_CACHE", str(cache_dir(data_dir)))
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        last: Exception | None = None
        for endpoint in (None, MIRROR):  # 先直连，不行换国内镜像
            if endpoint:
                os.environ["HF_ENDPOINT"] = endpoint
            try:
                from texify.model.model import load_model
                from texify.model.processor import load_processor

                model, processor = load_model(), load_processor()
                _state["model"], _state["processor"] = model, processor
                return model, processor
            except Exception as exc:  # noqa: BLE001
                last = exc
                log.warning("本地公式模型加载失败（%s）：%s", endpoint or "直连", exc)
        raise RuntimeError(f"本地公式模型加载失败：{last}")


def _prepare(png: bytes):
    from PIL import Image, ImageOps

    im = Image.open(io.BytesIO(png)).convert("RGB")
    return ImageOps.expand(im, border=PAD_PX, fill=(255, 255, 255))


def recognize(pngs: list[bytes], data_dir: Path) -> list[str]:
    """逐批识别整行图，返回和输入一一对应的转写文字。"""
    if _override is not None:
        return _override(pngs)
    from texify.inference import batch_inference

    model, processor = _load(data_dir)
    out: list[str] = []
    for i in range(0, len(pngs), BATCH):
        out += [t.strip() for t in batch_inference([_prepare(p) for p in pngs[i:i + BATCH]], model, processor)]
    return out


_DOLLAR = re.compile(r"\$\$([^$]+?)\$\$|\$([^$]+?)\$")
_PAREN = re.compile(r"\\\((.+?)\\\)")


def segments(text: str) -> list[str]:
    """转写文字里的所有公式片段（$…$、$$…$$、\\(…\\)），按出现顺序。"""
    out = [m.group(1) or m.group(2) for m in _DOLLAR.finditer(text or "")]
    out += [m.group(1) for m in _PAREN.finditer(text or "")]
    out = [s.strip() for s in out if s and s.strip()]
    if not out:
        # 整行就是一个公式时 texify 常不加 $：没有英文单词（去掉 \命令后）、又有等号/上下标/命令，就把整行当一个公式片段
        t = re.sub(r"\*\*|^#+\s*", "", text or "").strip()
        words = re.findall(r"[A-Za-z]{4,}", re.sub(r"\\[A-Za-z]+", "", t))
        if t and re.search(r"[=^_\\]", t) and len(words) <= 2:
            out = [t]
    return out
