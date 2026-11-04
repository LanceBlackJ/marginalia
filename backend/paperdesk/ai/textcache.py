"""不属于某篇文库论文的文字（发现页里的标题、摘要，划词翻译的发现页摘要）的译文缓存：存在本机缓存目录的一个 JSON 文件里。

翻译过的文字直接存下来，下次不再调用模型。文库里论文的译文另存在数据库的 translations 表里（按论文）。
"""
from __future__ import annotations

import hashlib
import json
import threading

from ..context import AppContext

_LOCK = threading.Lock()


def key(kind: str, text: str) -> str:
    return kind + ":" + hashlib.sha1(" ".join(text.split()).encode("utf-8")).hexdigest()[:20]


def _file(app: AppContext):
    return app.paths.cache_dir / "work_translations.json"


def load(app: AppContext) -> dict[str, str]:
    try:
        return json.loads(_file(app).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get(app: AppContext, kind: str, text: str) -> str | None:
    with _LOCK:
        return load(app).get(key(kind, text))


def put_many(app: AppContext, entries: dict[str, str]) -> None:
    if not entries:
        return
    with _LOCK:
        data = load(app)
        data.update(entries)
        try:
            _file(app).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
