"""发现、推荐、随机探索里找到的论文自动配上中文：标题一批一批译，摘要少量几篇一组译。

只把公开的论文标题和摘要发给用户自己配置的模型（记入隐私日志）；已经是中文的不译；没有配置模型就什么也不做。
译文缓存在本机缓存目录里，同一篇不会重复译。译不出来的条目不出现在结果里，界面上只显示英文原文。
"""
from __future__ import annotations

import hashlib
import logging
import json
import re
import threading

from ..ai import textcache
from ..context import AppContext
from ..llmjson import ask_json, llm_available

CJK = re.compile(r"[一-鿿]")
MAX_ITEMS = 12
MAX_CHARS = 1800  # 单篇摘要最多送多少字符

SYSTEM = (
    "你是学术翻译。把给定的论文标题或摘要翻译成通顺准确的中文。专有名词、模型名、数据集名、缩写（如 BERT、GNN、CIFAR-10）保留英文原样，"
    "公式和数字不改。只输出一个 JSON 对象：{\"items\": [{\"id\": \"原样返回的 id\", \"zh\": \"中文译文\"}]}，不要任何其它文字。"
)


async def translate_texts(app: AppContext, kind: str, items: list[dict]) -> dict[str, str]:
    """items: [{id, text}]，kind 是 title 或 abstract。返回 {id: 中文}，只含译成功的。"""
    if kind not in ("title", "abstract"):
        return {}
    todo = [{"id": str(i["id"]), "text": " ".join(str(i.get("text") or "").split())[:MAX_CHARS]} for i in items[:MAX_ITEMS]]
    todo = [t for t in todo if len(t["text"]) >= 6 and not CJK.search(t["text"])]
    if not todo:
        return {}
    cache = textcache.load(app)
    out = {t["id"]: cache[textcache.key(kind, t["text"])] for t in todo if textcache.key(kind, t["text"]) in cache}
    missing = [t for t in todo if t["id"] not in out]
    if missing and llm_available(app):
        user = json.dumps({"kind": "论文标题" if kind == "title" else "论文摘要", "items": [{"id": t["id"], "text": t["text"]} for t in missing]}, ensure_ascii=False)
        try:
            data = await ask_json(
                app, system=SYSTEM, user=user, task_type="discovery_translate", tier="fast",
                max_tokens=600 + 700 * len(missing) if kind == "abstract" else 200 + 120 * len(missing),
                includes={"question": False, "selection": True, "notes": False, "supplements": False, "code": False},
                summary=f"发送了 {len(missing)} 篇论文的{'标题' if kind == 'title' else '摘要'}（公开信息）用于翻译成中文",
            )
        except Exception as exc:  # noqa: BLE001  模型/网关出任何问题都只是“这批没译成”，界面照常显示英文
            logging.getLogger(__name__).warning("发现页翻译失败：%s", type(exc).__name__)
            return out
        by_text = {t["id"]: t["text"] for t in missing}
        fresh: dict[str, str] = {}
        for it in (data.get("items") if isinstance(data, dict) else None) or []:
            i, zh = str(it.get("id", "")), str(it.get("zh", "")).strip()
            if i in by_text and zh and CJK.search(zh):
                out[i] = zh
                fresh[textcache.key(kind, by_text[i])] = zh
        textcache.put_many(app, fresh)
    return out
