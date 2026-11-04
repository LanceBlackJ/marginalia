from __future__ import annotations

from paperdesk.ai import settings as ai_settings
from paperdesk.ai import usage


def test_default_models_have_builtin_prices(api):
    prices = api.get("/settings/ai").json()["price_per_mtok"]
    cfg = ai_settings.DEFAULTS
    for key in ("fast_model", "strong_model", "vision_model", "embedding_model"):
        assert cfg[key] in prices, f"默认的{key}（{cfg[key]}）没有内置价格"
    assert prices["DeepSeek-V4-Flash-0731-W8A8"] == {"input": 2.0, "output": 8.0}
    assert prices["MiniMax-M2.7-bf16"] == {"input": 2.097, "output": 8.388}
    assert prices["Qwen3-VL-32B-Instruct"] == {"input": 0.5, "output": 1.997}
    assert prices["bge-m3"] == {"input": 0.25, "output": 0.998}


def test_user_price_overrides_builtin_and_survives_save(api):
    api.put("/settings/ai", {"price_per_mtok": {"MiniMax-M2.7-bf16": {"input": 1.0, "output": 2.0}, "my-model": {"input": 3, "output": 4}}})
    prices = api.get("/settings/ai").json()["price_per_mtok"]
    assert prices["MiniMax-M2.7-bf16"] == {"input": 1.0, "output": 2.0}  # 用户填的优先
    assert prices["my-model"] == {"input": 3.0, "output": 4.0}
    assert prices["bge-m3"]["input"] == 0.25  # 其它内置价格仍在


def test_usage_cost_is_estimated_without_any_manual_price(api):
    usage.record_usage(api.ctx, "ask", "DeepSeek-V4-Flash-0731-W8A8", 1_000_000, 500_000, estimated=False)
    s = api.get("/usage").json()
    m = next(x for x in s["by_model"] if x["model"] == "DeepSeek-V4-Flash-0731-W8A8")
    assert abs(m["estimated_cost"] - (2.0 + 0.5 * 8.0)) < 1e-9  # 100 万输入 + 50 万输出 = 6 元
    assert s["estimated_cost"] is not None
