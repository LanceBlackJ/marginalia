import asyncio

from paperdesk.discovery import translate as T


class _App:
    def __init__(self, tmp_path):
        self.paths = type("P", (), {"cache_dir": tmp_path})()


def test_titles_are_translated_cached_and_chinese_or_short_ones_skipped(tmp_path, monkeypatch):
    calls = []

    async def fake_ask(app, **kw):
        calls.append(kw["user"])
        import json

        items = json.loads(kw["user"])["items"]
        return {"items": [{"id": i["id"], "zh": "中文：" + i["text"][:10]} for i in items]}

    monkeypatch.setattr(T, "ask_json", fake_ask)
    monkeypatch.setattr(T, "llm_available", lambda app: True)
    app = _App(tmp_path)
    items = [{"id": "a", "text": "Graph attention networks for node classification"}, {"id": "b", "text": "已经是中文的标题不用翻译"}, {"id": "c", "text": "x"}]
    got = asyncio.run(T.translate_texts(app, "title", items))
    assert got == {"a": "中文：Graph atte"} and len(calls) == 1
    again = asyncio.run(T.translate_texts(app, "title", items))  # 第二次走缓存，不再调用模型
    assert again == got and len(calls) == 1


def test_no_model_configured_returns_nothing_and_does_not_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "llm_available", lambda app: False)
    assert asyncio.run(T.translate_texts(_App(tmp_path), "abstract", [{"id": "a", "text": "A long enough abstract about graphs."}])) == {}
