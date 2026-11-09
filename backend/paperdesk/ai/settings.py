"""AI 设置的读写与校验。API Key 只在内存（SecretStore），其余配置落库。"""
from __future__ import annotations

from typing import Any

from ..context import AppContext
from ..util import ApiError, dumps, loads

SECRET_NAME = "api_key"

DEFAULTS: dict[str, Any] = {
    "provider_kind": "openai_compatible",
    "base_url": "http://aigw.dlut.edu.cn/v1",  # 默认指向校园网关（OpenAI 兼容）；换服务商时在设置里修改
    # 默认的模型组合来自真实网关上的实测（2026-10-30）：快速模型首字约 1–2 秒且输出干净；
    # 强模型回答结构最贴合要求（区分“作者明确陈述/AI 推断”）。换用其它服务商时需要改成对应的模型名。
    "fast_model": "DeepSeek-V4-Flash-0731-W8A8",
    "strong_model": "MiniMax-M2.7-bf16",
    # 视觉模型：公式转写、框架图/图表/表格抽取。Qwen3-VL-32B 在网关上实测能把公式截图准确转写成 LaTeX（约 3 秒）。
    "vision_model": "Qwen3-VL-32B-Instruct",
    # 第二视觉模型：行内公式识别时和主视觉模型并行转写、互相对照（一致才放心采用，不一致让模型对着原图裁决）。默认留空（只用一个）；可填 Qwen2-VL-72B-Instruct。
    "vision_model_2": "",
    # 嵌入模型：语义向量检索（多语言，中文问题可直接检索英文论文）。留空或关闭 semantic_retrieval 则只用关键词检索。
    "embedding_model": "bge-m3",
    "semantic_retrieval": True,
    "mode": "auto",
    "timeout_s": 60,
    "max_retries": 2,
    "temperature": 0.2,
    # 思考型模型（如 MiniMax-M2.7）的思考也计入输出额度，4096 / 16000 都可能被思考用光、正文为空，所以默认放到上限 64000（只按实际生成的字数计费）
    "max_output_tokens": 64000,
    "auto_analyze": True,
    "price_per_mtok": {},
    "monthly_budget": None,
}
# 校园网关价目表（2026-10-30，人民币元 / 百万 Token，按量计费）中本应用默认使用的模型。
# 用户在设置里填写的同名价格优先；改用其它服务商或币种时在设置里覆盖即可。
GATEWAY_PRICES: dict[str, dict[str, float]] = {
    "DeepSeek-V4-Flash-0731-W8A8": {"input": 2.0, "output": 8.0},  # 快速模型
    "MiniMax-M2.7-bf16": {"input": 2.097, "output": 8.388},  # 强模型
    "Qwen3-VL-32B-Instruct": {"input": 0.5, "output": 1.997},  # 视觉模型
    # 2026-11-07 用户给的网关价目表里的其它视觉/多模态模型（第二视觉模型、备选）
    "Qwen2-VL-72B-Instruct": {"input": 1.997, "output": 7.988},
    "Qwen3-VL-8B-Instruct": {"input": 0.25, "output": 0.998},
    "Qwen3.5-122B-A10B": {"input": 0.8, "output": 6.4},
    "Qwen3.5-35B-A3B": {"input": 0.998, "output": 3.994},
    "Qwen3.5-9B": {"input": 0.5, "output": 1.997},
    "bge-m3": {"input": 0.25, "output": 0.998},  # 嵌入模型（只按输入计量）
}
PROVIDER_KINDS = ("openai_compatible", "anthropic")
MODES = ("auto", "fast", "strong")
DEFAULT_BASE_URLS = {
    "openai_compatible": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
}


OLD_DEFAULT_MAX_OUTPUT = (4096, 16000)  # 以前的默认值：界面保存设置时会把它们原样存下来


def load_settings(app: AppContext) -> dict:
    with app.db.session() as conn:
        row = conn.execute("SELECT value_json FROM settings WHERE key = 'ai'").fetchone()
    saved = loads(row["value_json"], {}) if row else {}
    data = {**DEFAULTS, **saved}
    if saved.get("max_output_tokens") in OLD_DEFAULT_MAX_OUTPUT:  # 旧默认值：当作没改过，跟随新默认
        data["max_output_tokens"] = DEFAULTS["max_output_tokens"]
    # 内置价目表打底，用户填写的同名价格覆盖它
    data["price_per_mtok"] = {**GATEWAY_PRICES, **(saved.get("price_per_mtok") or {})}
    return data


def public_settings(app: AppContext) -> dict:
    data = load_settings(app)
    data["has_api_key"] = app.secrets.has(SECRET_NAME)
    data["configured"] = data["has_api_key"] and bool(data["fast_model"] or data["strong_model"])
    return data


def is_configured(app: AppContext) -> bool:
    return bool(public_settings(app)["configured"])


def save_settings(app: AppContext, incoming: dict) -> dict:
    cur = load_settings(app)
    allowed = set(DEFAULTS)
    for key, value in incoming.items():
        if key not in allowed or value is None and key not in ("monthly_budget",):
            continue
        cur[key] = value
    if cur["provider_kind"] not in PROVIDER_KINDS:
        raise ApiError("bad_request", "不支持的模型提供商类型")
    if cur["mode"] not in MODES:
        raise ApiError("bad_request", "不支持的模型调度模式")
    cur["base_url"] = str(cur["base_url"] or "").strip().rstrip("/") or DEFAULT_BASE_URLS[cur["provider_kind"]]
    if not cur["base_url"].startswith(("http://", "https://")):
        raise ApiError("bad_request", "API 地址必须以 http:// 或 https:// 开头")
    cur["semantic_retrieval"] = bool(cur.get("semantic_retrieval", True))
    for k in ("fast_model", "strong_model", "vision_model", "vision_model_2", "embedding_model"):
        cur[k] = str(cur[k] or "").strip()
    try:
        cur["timeout_s"] = max(5, min(600, int(cur["timeout_s"])))
        cur["max_retries"] = max(0, min(6, int(cur["max_retries"])))
        cur["temperature"] = max(0.0, min(2.0, float(cur["temperature"])))
        cur["max_output_tokens"] = max(256, min(64000, int(cur["max_output_tokens"])))
        if cur["monthly_budget"] is not None:
            cur["monthly_budget"] = max(0.0, float(cur["monthly_budget"]))
    except (TypeError, ValueError) as exc:
        raise ApiError("bad_request", "数值类设置无效") from exc
    cur["auto_analyze"] = bool(cur["auto_analyze"])
    prices = cur.get("price_per_mtok") or {}
    if not isinstance(prices, dict):
        raise ApiError("bad_request", "价格配置格式无效")
    cur["price_per_mtok"] = {
        str(m): {"input": float(p.get("input", 0) or 0), "output": float(p.get("output", 0) or 0)}
        for m, p in prices.items() if isinstance(p, dict)
    }
    with app.db.session() as conn:
        conn.execute(
            "INSERT INTO settings(key, value_json) VALUES ('ai', ?) "
            "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
            (dumps(cur),),
        )
    return public_settings(app)
