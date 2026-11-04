"""对话附件：截图/图片、PDF、文本文件。

- 图片：整张发给视觉模型（所以带图片的提问会改用设置里的视觉模型回答）；
- PDF：本地抽取文字（前 30 页，总量有上限）后作为附件文字发送，不发送 PDF 文件本身；
- 文本类文件（txt、md、csv、json、代码…）：按 UTF-8 读出文字发送，二进制文件一律拒绝；
- 数量、大小都有上限，超出明说原因，不静默截掉；附件只用于这一次提问，不写进论文库。
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field

from ..util import ApiError

MAX_FILES = 4
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 24000
MAX_PDF_PAGES = 30
IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


@dataclass
class Prepared:
    images: list[dict] = field(default_factory=list)  # {name, mime, data(base64)}
    texts: list[dict] = field(default_factory=list)  # {name, text, truncated}
    names: list[dict] = field(default_factory=list)  # [{name, kind}] 存进卡片、显示用

    def __bool__(self) -> bool:
        return bool(self.images or self.texts)


def _pdf_text(raw: bytes, name: str) -> str:
    import pymupdf

    try:
        doc = pymupdf.open(stream=raw, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise ApiError("bad_request", f"附件《{name}》不是可读的 PDF。") from exc
    parts = []
    for i, page in enumerate(doc):
        if i >= MAX_PDF_PAGES:
            break
        parts.append(page.get_text("text"))
    text = "\n".join(parts).strip()
    if not text:
        raise ApiError("bad_request", f"附件《{name}》里没有可提取的文字（可能是扫描件）。")
    return text


def prepare(raw: list[dict] | None) -> Prepared:
    out = Prepared()
    if not raw:
        return out
    if len(raw) > MAX_FILES:
        raise ApiError("bad_request", f"一次最多附 {MAX_FILES} 个文件。")
    for a in raw:
        name = str(a.get("name") or "附件")[:80]
        mime = str(a.get("mime") or "").lower()
        try:
            data = base64.b64decode(a.get("data") or "", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ApiError("bad_request", f"附件《{name}》的内容读不出来。") from exc
        if not data:
            raise ApiError("bad_request", f"附件《{name}》是空的。")
        lower = name.lower()
        if mime in IMAGE_MIMES:
            if len(data) > MAX_IMAGE_BYTES:
                raise ApiError("bad_request", f"图片《{name}》超过 {MAX_IMAGE_BYTES // 1024 // 1024} MB。")
            out.images.append({"name": name, "mime": mime, "data": base64.b64encode(data).decode("ascii")})
            out.names.append({"name": name, "kind": "image"})
            continue
        if len(data) > MAX_FILE_BYTES:
            raise ApiError("bad_request", f"文件《{name}》超过 {MAX_FILE_BYTES // 1024 // 1024} MB。")
        if mime == "application/pdf" or lower.endswith(".pdf"):
            text, kind = _pdf_text(data, name), "pdf"
        else:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ApiError("bad_request", f"不支持《{name}》这种格式。可以附图片、PDF 或文本类文件。") from exc
            if "\x00" in text:
                raise ApiError("bad_request", f"不支持《{name}》这种格式。可以附图片、PDF 或文本类文件。")
            kind = "text"
        truncated = len(text) > MAX_TEXT_CHARS
        out.texts.append({"name": name, "text": text[:MAX_TEXT_CHARS], "truncated": truncated})
        out.names.append({"name": name, "kind": kind})
    return out
