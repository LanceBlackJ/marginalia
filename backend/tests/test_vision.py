"""F5：公式理解、框架图/实验图/表格理解。

AI 部分使用确定性的假视觉模型（tests/fake_vision_llm.py）：只证明链路与校验逻辑，不证明视觉模型真的看得懂图。
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import pdfgen_journal
from conftest import AUTH, TOKEN, Api
from fake_vision_llm import VisionFakeLLM
from paperdesk.ai import providers
from paperdesk.ai.retrieval import ContextPack, CtxPassage
from paperdesk.vision import formula, groups
from paperdesk.vision import parse as vparse
from paperdesk.vision import validate as V


# ---------------- 多模态消息构造 ----------------
def _msgs(png: bytes = b"\x89PNG\r\n\x1a\nfake"):
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": [{"type": "text", "text": "看图"}, providers.image_part(png)]},
    ]


def test_image_part_and_counting():
    m = _msgs()
    assert providers.count_images(m) == 1 and providers.content_text(m[1]["content"]) == "看图"
    assert providers.count_images([{"role": "user", "content": "纯文字"}]) == 0
    assert providers.estimate_messages_tokens(m) >= providers.IMAGE_TOKENS  # 图片按固定 token 估算


def test_openai_wire_uses_image_url_data_url():
    prov = providers.OpenAICompatibleProvider("https://x.test/v1", "k", timeout=5, max_retries=0)
    body = prov._payload("vl", _msgs(), temperature=0, max_tokens=10, stream=False, json_mode=False)
    assert body["messages"][0]["content"] == "sys"  # 文字消息保持字符串，不破坏旧网关
    part = body["messages"][1]["content"][1]
    assert part["type"] == "image_url" and part["image_url"]["url"].startswith("data:image/png;base64,")
    assert base64.b64decode(part["image_url"]["url"].split(",", 1)[1]).startswith(b"\x89PNG")


def test_anthropic_wire_uses_image_blocks():
    prov = providers.AnthropicProvider("https://a.test", "k", timeout=5, max_retries=0)
    body = prov._payload("claude", _msgs(), temperature=0, max_tokens=10, stream=False, json_mode=False)
    assert body["system"] == "sys"
    blocks = body["messages"][0]["content"]
    img = next(b for b in blocks if b["type"] == "image")
    assert img["source"]["type"] == "base64" and img["source"]["media_type"] == "image/png"
    assert base64.b64decode(img["source"]["data"]).startswith(b"\x89PNG")


# ---------------- LaTeX 清洗 ----------------
def test_clean_latex_variants():
    latex, conf, warn = formula.clean_latex("```latex\n\\frac{1}{N}\\sum_{k=1}^{N} x_k \\tag{1}\n```\nCONFIDENCE: high")
    assert latex == "\\frac{1}{N}\\sum_{k=1}^{N} x_k \\tag{1}" and conf == "high" and warn == []
    assert formula.clean_latex("$$ a^2 + b^2 = c^2 $$")[0] == "a^2 + b^2 = c^2"
    assert formula.clean_latex("\\[ x = 1 \\]")[0] == "x = 1"
    assert formula.clean_latex("\\begin{equation} y = 2 \\label{e1} \\end{equation}")[0] == "y = 2"


def test_clean_latex_flags_structural_problems():
    latex, conf, warn = formula.clean_latex("```latex\n\\frac{1}{N\n```\nCONFIDENCE: high")
    assert conf == "low" and any("花括号" in w for w in warn)
    _, conf, warn = formula.clean_latex("```latex\n\\left( x + y\n```\nCONFIDENCE: high")
    assert conf == "medium" and any("left" in w for w in warn)
    _, _, warn = formula.clean_latex("```latex\nx = ? + 1\n```")
    assert any("占位符" in w for w in warn)
    assert formula.clean_latex("")[0] == "" and formula.clean_latex("x" * 5000)[0] == ""


# ---------------- 校验：框架图 ----------------
def test_validate_framework_drops_bad_flows_and_checks_labels():
    raw = {
        "modules": [{"id": "a", "label": "Encoder"}, {"id": "b", "label": "Decoder"}, {"id": "c", "label": "Hallucinated Box"}],
        "flows": [{"from": "a", "to": "b"}, {"from": "a", "to": "zz"}, {"from": "b", "to": "b"}, {"from": "a", "to": "b"}],
    }
    data, warns = V.validate_framework(raw, "Encoder Decoder f_E")
    assert [f["from"] + ">" + f["to"] for f in data["flows"]] == ["a>b"]  # 不存在的、自环、重复都被丢弃
    assert {m["label"]: m["in_text"] for m in data["modules"]} == {"Encoder": True, "Decoder": True, "Hallucinated Box": False}
    assert any("丢弃" in w for w in warns) and any("文字层里没找到" in w and "Hallucinated Box" in w for w in warns)
    assert any("没有任何连线" in w for w in warns)


def test_validate_framework_raster_figure_is_unverifiable_not_wrong():
    data, warns = V.validate_framework({"modules": [{"id": "m1", "label": "A"}, {"id": "m2", "label": "B"}]}, "")
    assert all(m["in_text"] is None for m in data["modules"]) and data["verifiable"] is False
    assert any("文字层" in w for w in warns)
    with pytest.raises(ValueError):
        V.validate_framework({"modules": [{"id": "m1", "label": "only one"}]}, "")


# ---------------- 校验：防编造（图表） ----------------
def test_numbers_helpers():
    assert V.numbers_in("ANC = 1,000.5 and −0.25, 3%") == [1000.5, -0.25, 3.0]
    assert V.value_verified("0.52", V.number_set("axis 0.2 0.52 0.8"))
    assert not V.value_verified("0.53", V.number_set("axis 0.2 0.52 0.8"))
    assert V.value_verified("52%", V.number_set("52"))


def test_chart_exact_values_need_text_layer_support():
    raw = {"panels": [{"label": "a", "chart_type": "bar", "values": [
        {"text": "x", "value": "0.52", "precision": "exact"},   # 文字层里有
        {"text": "y", "value": "0.77", "precision": "exact"},   # 文字层里没有 → 降级
        {"text": "z", "value": "0.3", "precision": "approx"},
    ], "observations": ["FINDER 提升了 31.5%"]}]}
    data, warns = V.validate_chart(raw, "FINDER 0.52 HDA", "Fig. 4 | ANC")
    vals = data["panels"][0]["values"]
    assert [(v["value"], v["precision"], v["verified"]) for v in vals] == [("0.52", "exact", True), ("0.77", "approx", False), ("0.3", "approx", False)]
    assert any("降级" in w for w in warns)
    assert data["panels"][0]["observations"][0]["has_unverified_numbers"] is True  # 31.5% 不在图里


def test_chart_without_text_layer_has_no_exact_values():
    raw = {"panels": [{"values": [{"text": "x", "value": "0.52", "precision": "exact"}]}]}
    data, warns = V.validate_chart(raw, "", "")
    v = data["panels"][0]["values"][0]
    assert v["precision"] == "approx" and v["verified"] is False and data["verifiable"] is False
    assert any("文字层" in w for w in warns)
    with pytest.raises(ValueError):
        V.validate_chart({}, "", "")


def test_ungrounded_numbers_ignores_small_ints_years_and_labels():
    bad = V.ungrounded_numbers(["共 3 个模块，见 Fig. 4 和表 2，发表于 2020 年 [P12]；性能提升 99.7%，ANC 为 0.1234"], "0.5")
    assert bad == ["99.7", "0.1234"]
    assert V.ungrounded_numbers(["ANC 为 0.5"], "values 0.5") == []


# ---------------- 校验：表格 ----------------
def test_table_cells_checked_against_text_layer():
    raw = {"columns": ["Network", "Nodes"], "rows": [["Karate", "34"], ["Ghost", "999"], ["Dolphins"]]}
    data, warns = V.validate_table(raw, "Network Nodes Karate 34 Dolphins 62")
    assert data["rows"][2] == ["Dolphins", ""]  # 缺格补空
    assert data["unverified_cells"] == [[1, 0], [1, 1]] and data["verified_ratio"] == pytest.approx(3 / 5, abs=0.01)
    assert any("没有找到" in w for w in warns) and any("单元格数" in w for w in warns)
    d2, w2 = V.validate_table(raw, "")
    assert d2["verifiable"] is False and d2["verified_ratio"] is None and any("文字层" in w for w in w2)
    assert "| Network | Nodes |" in V.table_markdown(data)


# ---------------- 公式解释的解析与核实 ----------------
def _pack() -> ContextPack:
    ps = [CtxPassage(5, 2, "paragraph", "Method", "where N is the total number of nodes", [0, 0, 1, 1]),
          CtxPassage(6, 2, "paragraph", "Method", "σ(G) is the size of the largest connected component", [0, 0, 1, 1])]
    return ContextPack("T", 2020, [], [], ps, None, [], [])


def test_parse_equation_answer_symbol_statuses():
    md = ("## 直接回答\n计算平均鲁棒性 [P5]。\n## 符号表\n- $N$：节点总数 [P5]\n- $\\sigma(G)$：最大连通分量 [P6, P7777]\n"
          "- $c(v)$：论文未定义\n- $Z$：某个常数\n## 与其他公式的关系\n见公式 (2) [P6]\n## 依据\n- [P5] 定义了 N\n")
    parsed = vparse.parse_equation_answer(md, _pack())
    syms = {s["symbol"]: s for s in parsed.answer["symbols"]}
    assert syms["N"]["status"] == "defined" and syms["N"]["ords"] == [5]
    assert syms["\\sigma(G)"]["status"] == "defined" and syms["\\sigma(G)"]["ords"] == [6]  # 编造的 P7777 被剔除
    assert syms["c(v)"]["status"] == "undefined" and syms["Z"]["status"] == "unsupported"
    assert 7777 in parsed.verification["invalid_citations"] and parsed.verification["status"] == "partial"
    assert any("Z" in n for n in parsed.verification["notes"])
    assert parsed.answer["relations"] and {e["ord"] for e in parsed.evidence} == {5, 6}


# ---------------- 图区域归并 ----------------
def _paper_with_figures(api, tmp_path, rows):
    pid = api.import_pdf(pdfgen_journal.build_journal_paper(tmp_path / "j.pdf"))["paper_id"]
    with api.ctx.db.session() as conn:
        conn.execute("DELETE FROM figures WHERE paper_id = ?", (pid,))
        for (page, x0, y0, x1, y1, kind, label, cap) in rows:
            conn.execute("INSERT INTO figures(paper_id, page, x0, y0, x1, y1, kind, label, caption_ord) VALUES (?,?,?,?,?,?,?,?,?)",
                         (pid, page, x0, y0, x1, y1, kind, label, cap))
    return pid


def test_fragmented_figure_regions_are_merged(vapi, tmp_path):
    pid = _paper_with_figures(vapi, tmp_path, [
        (2, 60, 60, 200, 160, "figure", None, None), (2, 220, 60, 360, 160, "figure", None, None),  # 上面一排无标签
        (2, 60, 190, 200, 290, "figure", "Fig. 4", 3), (2, 220, 190, 360, 290, "figure", "Fig. 4", 3),
        (2, 60, 600, 360, 700, "figure", None, None),  # 离图注太远：独立
    ])
    gs = groups.figure_groups(vapi.ctx, pid)
    merged = next(g for g in gs if g["label"] == "Fig. 4")
    b = merged["bbox"]  # 至少包住四个子区域；紧挨着的图内文字（刻度、标题）也会并进来
    assert len(merged["member_ids"]) == 4 and b[0] <= 60 and b[1] <= 60 and b[2] >= 360 and b[3] >= 290
    assert len(gs) == 2 and sum(1 for g in gs if g["label"] is None) == 1
    # 任一子区域 id 都能解析到整张图
    assert groups.group_for(vapi.ctx, pid, merged["member_ids"][0])["id"] == merged["id"]
    assert vapi.get(f"/papers/{pid}/vision/figures").json()["items"][0]["analysis_status"] == "none"


# ---------------- API：fixtures ----------------
@pytest.fixture
def vapi(tmp_path):
    from paperdesk.app import create_app

    fake = VisionFakeLLM()
    app = create_app(tmp_path / "data", TOKEN, sync_transport=httpx.MockTransport(lambda r: httpx.Response(503)),
                     async_transport=fake.transport)
    a = Api(TestClient(app), app)
    a.fake = fake
    with a.c:
        yield a


@pytest.fixture
def journal(vapi, tmp_path):
    item = vapi.import_pdf(pdfgen_journal.build_journal_paper(tmp_path / "journal.pdf"))
    assert item["status"] == "imported"
    vapi.configure_ai()
    pid = item["paper_id"]
    eq = vapi.get(f"/papers/{pid}/equations").json()["items"][0]
    fig = next(f for f in vapi.get(f"/papers/{pid}/figures").json()["items"] if f["label"] == "Fig. 1")
    return vapi, pid, eq, fig


def _image_messages(fake):
    out = []
    for r in fake.requests:
        for m in r["body"].get("messages", []):
            if isinstance(m["content"], list) and any(p.get("type") == "image_url" for p in m["content"]):
                out.append(m)
    return out


# ---------------- API：公式转写 ----------------
def test_transcribe_equation_sends_real_png_and_caches(journal):
    api, pid, eq, _ = journal
    assert api.get(f"/papers/{pid}/equations/{eq['id']}/latex").json()["status"] == "none"
    r = api.post(f"/papers/{pid}/equations/{eq['id']}/latex", {})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "ok" and "\\frac{1}{N}" in d["latex"] and d["confidence"] == "high" and d["cached"] is False
    # 发出去的确实是公式区域的 PNG，且使用的是视觉模型
    imgs = _image_messages(api.fake)
    assert len(imgs) == 2  # 两种分辨率各一张：用于交叉核对
    sizes = [len(base64.b64decode(next(p for p in m["content"] if p["type"] == "image_url")["image_url"]["url"].split(",", 1)[1]))
             for m in imgs]
    assert sizes[1] > sizes[0]  # 第二张分辨率更高
    url = next(p for p in imgs[0]["content"] if p["type"] == "image_url")["image_url"]["url"]
    assert base64.b64decode(url.split(",", 1)[1]).startswith(b"\x89PNG")
    assert api.fake.requests[-1]["body"]["model"] == "Qwen3-VL-32B-Instruct"
    # 隐私日志与用量
    log = api.get("/privacy/requests").json()["items"][0]
    assert log["task_type"] == "vision_formula" and log["includes"]["images"] is True and "公式截图" in log["summary"]
    # 缓存：第二次不再调用模型；force 才重新识别
    n = len(api.fake.requests)
    again = api.post(f"/papers/{pid}/equations/{eq['id']}/latex", {}).json()
    assert again["cached"] is True and len(api.fake.requests) == n
    assert api.get(f"/papers/{pid}/equations/{eq['id']}/latex").json()["latex"] == d["latex"]
    api.post(f"/papers/{pid}/equations/{eq['id']}/latex", {"force": True})
    assert len(api.fake.requests) == n + 2


def test_tex_key_ignores_formatting_but_not_symbols():
    a = "R = \\frac{1}{N} \\sum_{k=1}^{N} \\frac{\\sigma(\\mathcal{G} \\setminus \\{v_1, \\ldots, v_k\\})}{\\sigma(\\mathcal{G})} \\tag{1}"
    b = "R=\\frac{1}{N}\\sum_{k=1}^{N}\\frac{\\sigma\\left(\\mathcal{G}\\backslash\\{v_1,\\dots,v_k\\}\\right)}{\\sigma(\\mathcal{G})}"
    assert formula.tex_key(a) == formula.tex_key(b)
    dropped = a.replace("\\setminus ", "")  # 真实网关上出现过的错误：漏掉集合差
    assert formula.tex_key(dropped) != formula.tex_key(a)
    assert formula.tex_key("x_{1}") == formula.tex_key("x_1") and formula.tex_key("a+b") != formula.tex_key("a-b")


def test_transcribe_cross_check_flags_inconsistent_readings(journal):
    api, pid, eq, _ = journal
    full = api.fake.latex_reply
    # 第二次识别漏掉了一个符号：不假装可靠，置信度压到 low 并提示对照原图
    api.fake.latex_reply_second = full.replace("\\frac{1}{N}", "")
    d = api.post(f"/papers/{pid}/equations/{eq['id']}/latex", {"force": True}).json()
    assert d["status"] == "ok" and d["confidence"] == "low"
    assert any("不一致" in w for w in d["warnings"])
    assert api.get(f"/papers/{pid}/equations/{eq['id']}/latex").json()["confidence"] == "low"  # 持久化
    # 只是写法不同（\ldots 与 \dots、空白、\left/\right）：不算不一致
    api.fake.formula_calls = 0
    api.fake.latex_reply_second = full.replace("\\ldots", "\\dots").replace("R(", "R\\left(").replace(", v_N)", ", v_N\\right)")
    d = api.post(f"/papers/{pid}/equations/{eq['id']}/latex", {"force": True}).json()
    assert d["confidence"] == "high" and d["warnings"] == []
    # 第二次（核对）调用失败：保留第一次的结果，不报错、也不凭空加告警
    api.fake.latex_reply_second = None
    api.fake.formula_requests = 0
    api.fake.fail_formula_call = 2
    d = api.post(f"/papers/{pid}/equations/{eq['id']}/latex", {"force": True}).json()
    assert d["status"] == "ok" and d["latex"] and d["confidence"] == "high" and d["warnings"] == []


def test_transcribe_failure_states(journal):
    api, pid, eq, _ = journal
    api.fake.latex_reply = "抱歉，我看不清这张图。"
    d = api.post(f"/papers/{pid}/equations/{eq['id']}/latex", {}).json()
    assert d["status"] == "failed" and d["latex"] is None  # 一段说明文字不能当作公式
    api.fake.latex_reply = "```latex\n\\frac{1}{N\n```\nCONFIDENCE: high"
    d = api.post(f"/papers/{pid}/equations/{eq['id']}/latex", {"force": True}).json()
    assert d["confidence"] == "low" and any("花括号" in w for w in d["warnings"])
    api.fake.latex_reply = ""
    d = api.post(f"/papers/{pid}/equations/{eq['id']}/latex", {"force": True}).json()
    assert d["status"] == "failed" and d["latex"] is None and d["error"]
    # 失败的结果不当作缓存命中
    n = len(api.fake.requests)
    api.post(f"/papers/{pid}/equations/{eq['id']}/latex", {})
    assert len(api.fake.requests) == n + 1


def test_arbiter_picks_the_more_faithful_reading_but_keeps_the_warning(journal):
    api, pid, eq, _ = journal
    full = api.fake.latex_reply
    api.fake.latex_reply = full.replace("\\setminus ", "")  # 第一次漏了集合差（真实网关上出现过）
    api.fake.latex_reply_second = full
    api.fake.arbiter_reply = '{"better": "B", "differences": ["A 少了集合差运算符"]}'
    d = api.post(f"/papers/{pid}/equations/{eq['id']}/latex", {"force": True}).json()
    assert "\\setminus" in d["latex"] and d["confidence"] == "low"  # 采用了更完整的一份，但仍然是低置信度
    w = " ".join(d["warnings"])
    assert "不一致" in w and "第二次" in w and "集合差" in w
    assert len([m for m in _image_messages(api.fake)]) == 3  # 200 dpi、300 dpi 各一张，再加一张对照
    # 裁决认为第一次更准：保留第一次
    api.fake.formula_calls = 0
    api.fake.arbiter_reply = '{"better": "A", "differences": []}'
    d = api.post(f"/papers/{pid}/equations/{eq['id']}/latex", {"force": True}).json()
    assert "\\setminus" not in d["latex"] and "第一次" in " ".join(d["warnings"])
    # 裁决输出看不懂 / 模型判不准：保留第一次并如实说没能判定
    api.fake.formula_calls = 0
    api.fake.arbiter_reply = "我觉得两份都差不多"
    d = api.post(f"/papers/{pid}/equations/{eq['id']}/latex", {"force": True}).json()
    assert "\\setminus" not in d["latex"] and "没能判定" in " ".join(d["warnings"]) and d["confidence"] == "low"


def test_repair_truncated_json_keeps_only_complete_parts():
    from paperdesk.llmjson import repair_truncated_json

    full = '{"panels": [{"label": "a", "values": [1, 2]}, {"label": "b", "values": [3, 4]}], "unreadable": ["x"]}'
    # 在第二个子图中间被截断：只保留完整的第一个子图
    cut = full[: full.index('"b"') + 1]  # 停在第二个子图的标签字符串中间
    assert repair_truncated_json(cut) == {"panels": [{"label": "a", "values": [1, 2]}]}
    # 带代码块围栏、末尾没有闭合
    assert repair_truncated_json("```json\n" + cut) == {"panels": [{"label": "a", "values": [1, 2]}]}
    # 完整的 JSON 原样返回；字符串里的括号和逗号不会被误当作结构
    assert repair_truncated_json('{"t": "a, b ] c", "n": 1}') == {"t": "a, b ] c", "n": 1}
    # 连第一个完整元素都没有：无法修复，而不是编一个出来
    with pytest.raises(ValueError):
        repair_truncated_json('{"panels": [{"label": "a')
    with pytest.raises(ValueError):
        repair_truncated_json("没有 JSON")


def test_parse_equation_answer_accepts_markdown_symbol_table_and_dash_bullets():
    md = ("## 直接回答\n计算平均鲁棒性 [P5]。\n## 符号表\n\n| 符号 | 含义 | 来源 |\n|------|------|------|\n"
          "| $N$ | 节点总数 | [P5] |\n| $\\sigma(G)$ | 最大连通分量的大小 | [P6] |\n| $\\gamma$ | 折扣因子，论文未定义其取值 | 论文未定义 |\n"
          "| $Z$ | 某个常数 | |\n- $k$ — 当前被删除的节点序号 [P5]\n## 依据\n- [P5] 定义了 N\n")
    parsed = vparse.parse_equation_answer(md, _pack())
    syms = {s["symbol"]: s for s in parsed.answer["symbols"]}
    assert set(syms) == {"N", "\\sigma(G)", "\\gamma", "Z", "k"}  # 表头和分隔行不会被当作符号
    assert syms["N"]["status"] == "defined" and syms["N"]["ords"] == [5] and syms["N"]["meaning"] == "节点总数"
    assert syms["\\gamma"]["status"] == "undefined" and syms["Z"]["status"] == "unsupported"
    assert syms["k"]["status"] == "defined"
    # 有内容却一个符号也解析不出：不显示空表，而是提示格式无法解析
    bad = vparse.parse_equation_answer("## 直接回答\n计算平均鲁棒性 [P5]。\n## 符号表\n这些符号都是常见的，不再逐个说明。\n", _pack())
    assert bad.answer["symbols"] == [] and any("符号表的格式无法解析" in n for n in bad.verification["notes"])
    assert bad.verification["status"] == "partial"


def test_symbol_meaning_keeps_sentence_subject_when_citation_is_mid_sentence():
    # 真实输出里的写法：引用标记在句中充当主语。直接删掉会留下“但 指出……”这种残缺句子
    md = ("## 直接回答\n策略贯穿整个 episode [P5]。\n## 符号表\n"
          "- $\\pi$：论文未明确定义符号，但 [P5] 指出策略贯穿整个 episode\n"
          "- $N$：网络的节点总数 [P5][P6]\n"
          "- $k$：被删除的节点序号，见 [P6] 的定义。\n"
          "## 依据\n- [P5] 提到了策略\n")
    parsed = vparse.parse_equation_answer(md, _pack())
    syms = {s["symbol"]: s for s in parsed.answer["symbols"]}
    assert syms["\\pi"]["meaning"] == "论文未明确定义符号，但原文指出策略贯穿整个 episode"
    assert syms["\\pi"]["status"] == "undefined" and syms["\\pi"]["ords"] == [5]  # “未明确定义”也算论文没定义，但出处仍保留
    assert syms["N"]["meaning"] == "网络的节点总数" and syms["N"]["ords"] == [5, 6]  # 句末的标记照旧直接去掉
    assert syms["k"]["meaning"] == "被删除的节点序号，见原文的定义" and syms["k"]["status"] == "defined"
    for s in syms.values():
        assert "[P" not in s["meaning"] and "但 指" not in s["meaning"]


def _chart_text_cut_inside_second_panel(api) -> str:
    panel = api.fake._chart()["panels"][0]
    text = json.dumps({"panels": [panel, {**panel, "label": "b"}], "unreadable": []}, ensure_ascii=False)
    return text[: text.rindex('"label": "b"') + 10]  # 停在第二个子图的标签字符串中间


def test_truncated_chart_output_is_repaired_retried_and_flagged(journal):
    api, pid, _, fig = journal
    cut = _chart_text_cut_inside_second_panel(api)
    # 第一次被长度限制截断（可修复）：用更大的上限和更精简的要求重试；重试成功就用完整结果
    api.fake.override_queue = [("实验结果图", cut)]
    api.fake.finish_overrides = ["length"]
    a = _run_analysis(api, pid, fig["id"], kind="chart")
    assert a["status"] == "ready" and not any("截断" in w for w in a["data"]["warnings"])
    chart_reqs = [r["body"] for r in api.fake.requests if "实验结果图" in str(r["body"]["messages"][0]["content"])]
    assert len(chart_reqs) == 2 and chart_reqs[1]["max_tokens"] > chart_reqs[0]["max_tokens"]
    assert "更精简" in json.dumps(chart_reqs[1]["messages"], ensure_ascii=False)
    # 两次都被截断：用丢弃不完整末尾后的结果，并且明确告知可能缺内容
    api.fake.override_queue = [("实验结果图", cut), ("实验结果图", cut)]
    api.fake.finish_overrides = ["length", "length"]
    a = _run_analysis(api, pid, fig["id"], kind="chart", force=True)
    assert a["status"] == "ready" and len(a["data"]["panels"]) == 1
    assert any("截断" in w for w in a["data"]["warnings"])
    jid = api.get(f"/papers/{pid}/figures/{fig['id']}/analysis").json()["job_id"]
    log = "\n".join(api.get(f"/jobs/{jid}").json().get("log", []))
    assert "原始输出前 300 字" in log and "length" in log  # 失败/截断时保留原始输出开头和结束原因，便于排查


def test_unrepairable_truncation_fails_with_a_clear_reason_and_raw_head(journal):
    api, pid, _, fig = journal
    api.fake.override_queue = [("实验结果图", '{"panels": [{"label": "一个很长的标题'),
                               ("实验结果图", '{"panels": [{"label": "一个很长的标题')]
    api.fake.finish_overrides = ["length", "length"]
    r = api.post(f"/papers/{pid}/figures/{fig['id']}/analyze", {"kind": "chart"})
    job = api.wait_job(r.json()["job_id"])
    assert job["status"] == "failed" and "截断" in (job["error"] or "")
    a = api.get(f"/papers/{pid}/figures/{fig['id']}/analysis").json()
    assert a["status"] == "failed" and "截断" in a["error"]
    log = "\n".join(api.get(f"/jobs/{r.json()['job_id']}").json().get("log", []))
    assert "一个很长的标题" in log  # 原始输出的开头写进了任务日志


def test_truncated_analysis_retries_with_the_fast_model(journal):
    api, pid, _, fig = journal
    api.put("/settings/ai", {"mode": "auto", "strong_model": "strong-think", "fast_model": "fast-plain", "max_output_tokens": 8000})
    api.fake.override_queue = [("规则：", '{"direct": "先写了一半", "points": [{"text": "要点一", "ords": []}, {"text": "要点二')]
    api.fake.finish_overrides = ["stop", "length"]  # 第 1 次请求是读图，第 2 次是解读（被截断）
    a = _run_analysis(api, pid, fig["id"], kind="chart")
    assert a["status"] == "ready" and a["analysis"]["direct"]
    reqs = [r["body"] for r in api.fake.requests if "规则：" in str(r["body"]["messages"][0]["content"])]
    assert len(reqs) == 2 and reqs[0]["model"] == "strong-think" and reqs[1]["model"] == "fast-plain"
    assert reqs[0]["max_tokens"] >= 6000 and reqs[1]["max_tokens"] > reqs[0]["max_tokens"]
    jid = a["job_id"]
    log = "\n".join(api.get(f"/jobs/{jid}").json().get("log", []))
    assert "改用快速模型 fast-plain" in log


def test_vision_model_must_be_configured(journal):
    api, pid, eq, fig = journal
    api.put("/settings/ai", {"vision_model": ""})
    r = api.post(f"/papers/{pid}/equations/{eq['id']}/latex", {})
    assert r.status_code == 400 and r.json()["error"]["code"] == "vision_not_configured"
    assert api.post(f"/papers/{pid}/figures/{fig['id']}/analyze", {}).json()["error"]["code"] == "vision_not_configured"
    assert api.get("/settings/ai").json()["vision_model"] == ""
    assert api.get(f"/papers/{pid}/equations/99999/latex").status_code == 404


def test_default_vision_model_and_connection_test(journal):
    api, *_ = journal
    assert api.get("/settings/ai").json()["vision_model"] == "Qwen3-VL-32B-Instruct"
    res = api.post("/settings/ai/test", {}).json()
    assert res["vision"]["ok"] is True and res["vision"]["model"] == "Qwen3-VL-32B-Instruct"
    assert _image_messages(api.fake)  # 连接测试确实发了一张小图


def test_locate_equation_by_region_and_text(journal):
    api, pid, eq, _ = journal
    x0, y0, x1, y1 = eq["bbox"]
    hit = api.post(f"/papers/{pid}/equations/locate", {"page": eq["page"], "bbox": [x0 + 5, y0 + 2, x1 - 5, y1 - 2]}).json()
    assert hit["equation"]["id"] == eq["id"]
    by_text = api.post(f"/papers/{pid}/equations/locate", {"text": "R(v1; v2; …; vN) = 1 N σ(G)"}).json()
    assert by_text["equation"] and by_text["equation"]["id"] == eq["id"]
    far = api.post(f"/papers/{pid}/equations/locate", {"page": eq["page"], "bbox": [0, 0, 5, 5]}).json()
    assert far["equation"] is None


# ---------------- API：公式解释 ----------------
def test_explain_equation_stream_card_and_grounding(journal):
    api, pid, eq, _ = journal
    evs = api.sse("/ai/explain-equation", {"paper_id": pid, "equation_id": eq["id"]})
    names = [n for n, _ in evs]
    assert names[0] == "stage" and "latex" in names and "meta" in names and "delta" in names and names[-1] == "done"
    assert names.index("latex") < names.index("meta") < names.index("delta")
    latex_ev = next(d for n, d in evs if n == "latex")
    assert latex_ev["status"] == "ok" and "\\frac" in latex_ev["latex"]
    card = evs[-1][1]["card"]
    assert card["object_type"] == "equation" and card["intent"] == "formula" and card["selection"]["equation_id"] == eq["id"]
    ans = card["answer"]
    syms = {s["symbol"]: s["status"] for s in ans["symbols"]}
    assert syms == {"N": "defined", "\\sigma(\\mathcal{G})": "defined", "c(v)": "undefined"}
    assert ans["equation"]["latex"] == latex_ev["latex"] and ans["relations"]
    assert card["verification"]["status"] == "verified" and card["evidence"] and all(e["quote"] for e in card["evidence"])
    assert card["saved"] is True and api.get(f"/papers/{pid}/cards").json()["items"][0]["id"] == card["id"]
    # 讲解步骤只发送文字（LaTeX + 摘录），不再发送图片；只有转写那一步发了图
    assert len(_image_messages(api.fake)) == 2  # 转写那一步的两次（200/300 dpi）交叉核对
    last = api.fake.requests[-1]["body"]["messages"]
    assert isinstance(last[1]["content"], str) and "\\frac{1}{N}" in last[1]["content"] and "论文摘录" in last[1]["content"]
    log = api.get("/privacy/requests").json()["items"][0]
    assert log["task_type"] == "formula" and log["includes"]["images"] is False
    # 同一公式再次解释：复用缓存的 LaTeX，不再识别图片
    api.sse("/ai/explain-equation", {"paper_id": pid, "equation_id": eq["id"], "question": "推导一下"})
    assert len(_image_messages(api.fake)) == 2  # 仍是第一次转写的那两张


def test_explain_equation_flags_unsupported_symbols(journal):
    api, pid, eq, _ = journal
    api.fake.explain_extra_symbol = True
    card = api.sse("/ai/explain-equation", {"paper_id": pid, "equation_id": eq["id"]})[-1][1]["card"]
    assert {s["symbol"]: s["status"] for s in card["answer"]["symbols"]}["Z"] == "unsupported"
    assert card["verification"]["status"] == "partial" and any("Z" in n for n in card["verification"]["notes"])


def test_explain_equation_without_vision_model_degrades_honestly(journal):
    api, pid, eq, _ = journal
    api.put("/settings/ai", {"vision_model": ""})
    evs = api.sse("/ai/explain-equation", {"paper_id": pid, "equation_id": eq["id"]})
    assert evs[-1][0] == "done"
    assert next(d for n, d in evs if n == "latex")["latex"] is None
    v = evs[-1][1]["card"]["verification"]
    assert v["status"] == "partial" and any("公式截图" in n for n in v["notes"])
    assert not _image_messages(api.fake)


def test_explain_equation_errors(journal):
    api, pid, eq, _ = journal
    evs = api.sse("/ai/explain-equation", {"paper_id": pid, "equation_id": 99999})
    assert evs[-1][0] == "error" and evs[-1][1]["code"] == "not_found"
    assert api.get(f"/papers/{pid}/cards").json()["items"] == []  # 失败不留下空卡片
    assert api.post("/ai/explain-equation", {"paper_id": "nope", "equation_id": 1}).status_code == 404


# ---------------- API：框架图 ----------------
def _run_analysis(api, pid, fig_id, **body):
    r = api.post(f"/papers/{pid}/figures/{fig_id}/analyze", body)
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    if jid:
        job = api.wait_job(jid)
        assert job["status"] == "completed", job
    return api.get(f"/papers/{pid}/figures/{fig_id}/analysis").json()


def test_framework_pipeline_validates_and_grounds(journal):
    api, pid, _, fig = journal
    a = _run_analysis(api, pid, fig["id"], kind="framework")
    assert a["status"] == "ready" and a["kind"] == "framework"
    mods = {m["label"]: m for m in a["data"]["modules"]}
    assert set(mods) == {"Synthetic graphs", "Encoder", "Decoder", "Experience replay buffer"}
    assert len(a["data"]["flows"]) == 3 and any("丢弃" in w for w in a["data"]["warnings"])  # m99 的连线被丢弃
    # 这张图的文字层里没有这些模块名：必须标记为“未在文字层找到”，而不是默认通过
    assert a["data"]["verifiable"] is True and all(m["in_text"] is False for m in mods.values())
    assert any("文字层里没找到" in w for w in a["data"]["warnings"]) and a["verification"]["status"] == "partial"
    # 论文描述：有可核实引用的保留，没有的显示为“正文里没找到”
    mp = a["mapping"]["modules"]
    assert mp["m2"]["found"] and mp["m2"]["ords"] and mp["m1"]["found"] is False and mp["m1"]["description"] is None
    assert a["analysis"]["overview"] and a["evidence"] and a["verification"]["status"] in ("verified", "partial")
    # 发出的截图是整张图的 PNG，分类/识别各一次视觉调用
    assert len(_image_messages(api.fake)) == 1  # 图注含 Fig. 的启发式无法判断时才会多一次分类调用
    log = [r for r in api.get("/privacy/requests").json()["items"] if r["task_type"] == "vision_framework"]
    assert log and log[0]["includes"]["images"] is True


def test_framework_unsupported_module_description_is_dropped(journal):
    api, pid, _, fig = journal
    api.fake.analysis_fake_ord = True
    a = _run_analysis(api, pid, fig["id"], kind="framework")
    m1 = a["mapping"]["modules"]["m1"]
    assert m1["description"] is None and m1["found"] is False  # 编造的段落引用：描述被丢弃
    assert a["mapping"]["dropped_unsupported"] == 1 and any("丢弃" in n for n in a["verification"]["notes"])
    assert 9999 in a["verification"]["invalid_citations"] or a["verification"]["status"] != "verified"


def test_framework_written_back_as_searchable_passage(journal):
    api, pid, _, fig = journal
    a = _run_analysis(api, pid, fig["id"], kind="framework")
    ord_ = a["derived_ord"]
    assert ord_ is not None
    p = api.get(f"/papers/{pid}/passages/{ord_}").json()
    assert p["kind"] == "figure_data" and "Encoder → Decoder" in p["text"] and "非论文原文" in p["text"]
    hits = api.get(f"/papers/{pid}/search", params={"q": "Experience replay buffer"}).json()
    assert any(h["ord"] == ord_ for h in hits.get("items", hits.get("results", [])))
    # 重新分析覆盖同一段落，而不是新增
    again = _run_analysis(api, pid, fig["id"], kind="framework", force=True)
    assert again["derived_ord"] == ord_
    # 删除分析同时删除写回的段落
    assert api.delete(f"/papers/{pid}/figures/{fig['id']}/analysis").status_code == 204
    assert api.get(f"/papers/{pid}/passages/{ord_}").status_code == 404
    assert api.get(f"/papers/{pid}/figures/{fig['id']}/analysis").json()["status"] == "none"


def test_vision_json_retry_and_reroute(journal):
    api, pid, _, fig = journal
    api.fake.json_garbage_first = True  # 第一次不是 JSON：自动要求重输出
    assert _run_analysis(api, pid, fig["id"], kind="framework")["status"] == "ready"
    api.fake.not_framework = True  # 模型认为这不是框架图：改按实验图处理
    a = _run_analysis(api, pid, fig["id"], kind="framework", force=True)
    assert a["kind"] == "chart" and a["data"]["panels"]


def test_classification_call_only_when_caption_is_ambiguous(journal):
    api, pid, _, fig = journal
    api.fake.classify_kind = "chart"
    a = _run_analysis(api, pid, fig["id"])  # kind 不指定
    assert a["status"] == "ready"
    kinds = [r["task_type"] for r in api.get("/privacy/requests").json()["items"]]
    assert a["kind"] == "chart" and "vision_classify" in kinds and "vision_chart" in kinds


# ---------------- API：实验图 / 表格 ----------------
def test_chart_numbers_are_never_fabricated(journal):
    api, pid, _, fig = journal
    a = _run_analysis(api, pid, fig["id"], kind="chart")
    vals = a["data"]["panels"][0]["values"]
    assert all(v["precision"] == "approx" and v["verified"] is False for v in vals)  # 没有文字层：没有任何“精确”数值
    assert any("降级" in w for w in a["data"]["warnings"])  # “精确”但文字层里没有的 0.2345 被降级
    assert a["derived_ord"] is None  # 实验图不写回检索
    assert a["analysis"]["direct"] and a["analysis"]["paper_claims"][0]["supported"] == "partly"


def test_chart_analysis_flags_invented_numbers(journal):
    api, pid, _, fig = journal
    api.fake.analysis_bad_number = True
    a = _run_analysis(api, pid, fig["id"], kind="chart")
    v = a["verification"]
    assert "99.7" in v["ungrounded_numbers"] and v["status"] == "partial" and any("编造" in n or "找不到" in n for n in v["notes"])


def test_table_pipeline_checks_cells_and_writes_back(journal):
    api, pid, _, _ = journal
    with api.ctx.db.session() as conn:
        n = conn.execute("SELECT MAX(ord) FROM passages WHERE paper_id = ?", (pid,)).fetchone()[0]
        conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0, y0, x1, y1) VALUES (?,?,?,?,?,?,?,?,?)",
                     (pid, n + 1, 2, "figure_text", "Network Nodes Edges Karate 34 78 Dolphins 62 159", 100, 400, 300, 440))
        conn.execute("INSERT INTO figures(paper_id, page, x0, y0, x1, y1, kind, label) VALUES (?,?,?,?,?,?,?,?)",
                     (pid, 2, 90, 390, 320, 450, "table", "Table 1"))
        tid = conn.execute("SELECT id FROM figures WHERE kind = 'table'").fetchone()["id"]
    a = _run_analysis(api, pid, tid)
    assert a["kind"] == "table"
    t = a["data"]
    assert t["columns"] == ["Network", "Nodes", "Edges"] and len(t["rows"]) == 3
    assert t["unverified_cells"] == [[2, 0], [2, 1], [2, 2]] and t["verifiable"] is True  # 编造的 Fake/999/12345 被标出
    assert t["verified_ratio"] == pytest.approx(6 / 9, abs=0.01)  # 只统计数据单元格
    p = api.get(f"/papers/{pid}/passages/{a['derived_ord']}").json()
    assert p["kind"] == "table_data" and "Karate" in p["text"] and "非论文原文" in p["text"]
    # 表格里的数据可以被全文检索到（问答能用上）
    assert any(h["ord"] == a["derived_ord"] for h in api.get(f"/papers/{pid}/search", params={"q": "Dolphins"}).json().get("items", []))


def test_table_with_poor_text_layer_match_is_not_written_back(journal):
    api, pid, _, _ = journal
    api.fake.table_raw = {"columns": ["A", "B"], "rows": [["foo", "111"], ["bar", "222"], ["baz", "333"]]}
    with api.ctx.db.session() as conn:
        n = conn.execute("SELECT MAX(ord) FROM passages WHERE paper_id = ?", (pid,)).fetchone()[0]
        conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0, y0, x1, y1) VALUES (?,?,?,?,?,?,?,?,?)",
                     (pid, n + 1, 2, "figure_text", "completely different words here", 100, 400, 300, 440))
        conn.execute("INSERT INTO figures(paper_id, page, x0, y0, x1, y1, kind, label) VALUES (?,?,?,?,?,?,?,?)",
                     (pid, 2, 90, 390, 320, 450, "table", "Table 9"))
        tid = conn.execute("SELECT id FROM figures WHERE kind = 'table'").fetchone()["id"]
    a = _run_analysis(api, pid, tid)
    assert a["status"] == "ready" and a["derived_ord"] is None and a["data"]["verified_ratio"] < 0.5


# ---------------- API：图的追问（防编造） ----------------
def test_figure_question_unanswerable_says_cannot_see(journal):
    api, pid, _, fig = journal
    d = api.post(f"/papers/{pid}/figures/{fig['id']}/ask", {"question": "FINDER 在 ER 图上的运行时间是多少秒？"}).json()
    assert d["answerable"] is False and d["answer"].startswith("图中看不出")
    assert len(_image_messages(api.fake)) == 1
    assert api.post(f"/papers/{pid}/figures/{fig['id']}/ask", {"question": "  "}).status_code == 400


def test_figure_question_numbers_need_text_layer_or_are_marked_estimated(journal):
    api, pid, _, fig = journal
    api.fake.qa_reply = {"answerable": True, "answer": "FINDER 的 ANC 约 0.52，比 HDA 低约 31.5%。", "seen": "折线图",
                         "numbers": [{"value": "0.52", "how": "printed"}]}
    d = api.post(f"/papers/{pid}/figures/{fig['id']}/ask", {"question": "ANC 多少？"}).json()
    assert d["numbers"] == [{"value": "0.52", "how": "estimated"}]  # 声称“印在图上”，但文字层里没有：降级
    assert any("读图估计" in w for w in d["warnings"]) and any("31.5" in w for w in d["warnings"])


def _analysis(kind, panels=None):
    return {"status": "ready", "kind": kind, "data": {"panels": panels or []}}


def test_premise_problem_uses_the_read_result_to_reject_impossible_questions():
    from paperdesk.vision.figure_qa import premise_problem

    panels = [{"label": "a", "chart_type": "heatmap"}, {"label": "b", "chart_type": "bar"}]
    q = "第一个子图里最右端数据点的纵坐标精确值是多少（给出小数）？"
    assert "热力图" in premise_problem(q, _analysis("chart", panels))  # 热力图没有“数据点”
    assert "热力图" in premise_problem("子图 (a) 的纵坐标是多少", _analysis("chart", panels))
    assert premise_problem("子图 b 最右端数据点的纵坐标是多少", _analysis("chart", panels)) is None  # 柱状图可以问
    assert "只识别出 2 个子图" in premise_problem("第三个子图的纵坐标是多少", _analysis("chart", panels))
    assert "表格" in premise_problem("最右端数据点的纵坐标是多少", _analysis("table"))
    # 没问坐标/数据点、没读过图、没指明子图：不拦截（交给模型自己判断前提）
    assert premise_problem("第一个子图里哪个方法的颜色最深？", _analysis("chart", panels)) is None
    assert premise_problem(q, {"status": "none", "kind": None, "data": None}) is None
    assert premise_problem("最右端数据点的纵坐标是多少", _analysis("chart", panels)) is None


def test_mark_estimates_adds_about_and_a_note():
    from paperdesk.vision.figure_qa import mark_estimates

    est = [{"value": "0.17", "how": "estimated"}]
    out = mark_estimates("最右端的值是0.17", est)
    assert "约 0.17" in out and "估计" in out
    assert mark_estimates("最右端的值约为0.17（读图估计）", est) == "最右端的值约为0.17（读图估计）"  # 已经带“约”和说明：原样
    assert mark_estimates("0.00", [{"value": "0.00", "how": "estimated"}]).startswith("约 0.00")
    assert mark_estimates("值为 0.17，读图估计", est).count("约") == 1 and "（这是读图估计值" not in mark_estimates("值为 0.17，读图估计", est)
    assert mark_estimates("图上印的是 0.52", [{"value": "0.52", "how": "printed"}]) == "图上印的是 0.52"  # 图上印出的不加“约”
    assert "约 " not in mark_estimates("1.17", est)  # 不会把别的数字的一部分（1.17 里的 .17）当成 0.17


def test_figure_question_about_a_heatmap_point_is_refused_without_calling_the_model(journal):
    api, pid, _, fig = journal
    api.fake.chart_raw = {"panels": [{
        "label": "a", "chart_type": "heatmap", "title": "ANC", "x_axis": {"label": "", "unit": "", "range": ""},
        "y_axis": {"label": "", "unit": "", "range": ""}, "series": ["FINDER"], "observations": ["FINDER 颜色最深"], "values": []}]}
    assert _run_analysis(api, pid, fig["id"], kind="chart")["status"] == "ready"
    n = len(api.fake.requests)
    d = api.post(f"/papers/{pid}/figures/{fig['id']}/ask",
                 {"question": "第一个子图里最右端数据点的纵坐标精确值是多少（给出小数）？"}).json()
    assert d["answerable"] is False and d["answer"].startswith("图中看不出") and "热力图" in d["answer"]
    assert d["numbers"] == [] and d["premise"] == "mismatch"
    assert len(api.fake.requests) == n  # 没有调用模型、也没有发送截图


def test_figure_question_model_reported_false_premise_is_not_answered(journal):
    api, pid, _, fig = journal
    api.fake.qa_reply = {"premise_ok": False, "answerable": True, "answer": "第一个子图是热力图，没有数据点。0.00", "seen": "热力图",
                         "numbers": [{"value": "0.00", "how": "estimated"}]}
    d = api.post(f"/papers/{pid}/figures/{fig['id']}/ask", {"question": "最右端数据点的纵坐标精确值是多少？"}).json()
    assert d["answerable"] is False and d["answer"].startswith("图中看不出") and d["premise"] == "mismatch"


def test_figure_question_estimated_numbers_always_carry_about_and_a_note(journal):
    api, pid, _, fig = journal
    api.fake.qa_reply = {"answerable": True, "answer": "最右端的条形高度是0.17", "seen": "柱状图",
                         "numbers": [{"value": "0.17", "how": "estimated"}]}
    d = api.post(f"/papers/{pid}/figures/{fig['id']}/ask", {"question": "最右端条形的高度？"}).json()
    assert d["answerable"] is True and "约 0.17" in d["answer"] and "估计" in d["answer"]


def test_vision_status_lists_processed_items(journal):
    api, pid, eq, fig = journal
    api.post(f"/papers/{pid}/equations/{eq['id']}/latex", {})
    _run_analysis(api, pid, fig["id"], kind="framework")
    st = api.get(f"/papers/{pid}/vision/status").json()
    assert st["equations"][0]["equation_id"] == eq["id"] and st["equations"][0]["status"] == "ok"
    assert st["figures"][0]["status"] == "ready" and st["figures"][0]["kind"] == "framework"
    # 已有结果且不 force：不重复创建任务
    again = api.post(f"/papers/{pid}/figures/{fig['id']}/analyze", {}).json()
    assert again == {"job_id": None, "status": "ready"}


def test_secret_never_appears_in_vision_logs(journal):
    api, pid, eq, fig = journal
    api.post(f"/papers/{pid}/equations/{eq['id']}/latex", {})
    _run_analysis(api, pid, fig["id"], kind="framework")
    blob = json.dumps(api.get("/privacy/requests").json()) + json.dumps(api.get("/jobs").json())
    assert "sk-test-secret" not in blob


# ---------------- API：整篇公式批量识别（打开文字阅读器时自动触发） ----------------
def test_batch_equation_recognition_runs_in_background_and_is_idempotent(journal):
    api, pid, eq, _ = journal
    st = api.get(f"/papers/{pid}/equations-latex").json()
    assert st["total"] >= 1 and st["done"] == 0 and not st["complete"] and st["items"] == []
    job_id = api.post(f"/papers/{pid}/equations-latex").json()["job_id"]
    assert job_id
    job = api.wait_job(job_id)
    assert job["status"] == "completed" and job["result"]["failed"] == 0
    st = api.get(f"/papers/{pid}/equations-latex").json()
    assert st["complete"] and st["done"] == st["total"] and all("\frac" in i["latex"] or i["latex"] for i in st["items"])
    # 已经识别完：不再创建任务，也不再发送截图
    n = len(api.fake.requests)
    assert api.post(f"/papers/{pid}/equations-latex").json()["job_id"] is None
    assert len(api.fake.requests) == n
    # 发送截图记入隐私日志
    assert any(i["task_type"] == "vision_formula" for i in api.get("/privacy/requests").json()["items"])


def test_batch_equation_recognition_reports_missing_key(vapi, tmp_path):
    item = vapi.import_pdf(pdfgen_journal.build_journal_paper(tmp_path / "nokey.pdf"))
    pid = item["paper_id"]  # 没有配置 AI
    job_id = vapi.post(f"/papers/{pid}/equations-latex").json()["job_id"]
    job = vapi.wait_job(job_id)
    assert job["status"] == "failed" and job["error"]


# ---------------- 表格：从图注推出位置，整篇批量识别 ----------------
def _tp(ord_, page, text, y, kind="paragraph"):
    return {"ord": ord_, "page": page, "kind": kind, "text": text, "bbox": (72.0, y, 500.0, y + 11.0)}


def test_detect_tables_from_captions():
    from paperdesk.vision import table_batch as T

    ps = [
        _tp(0, 1, "The introduction paragraph is long enough to be prose. " * 5 + "It ends here.", 50),
        _tp(1, 1, "Table S2: Ablation study between the methods.", 100),
        _tp(2, 1, "(A) Results on problem one", 130),
        _tp(3, 1, "Method Crime Digg Enron", 160),
        _tp(4, 1, "FINDER 8.19 5.90 2.03", 190),
        _tp(5, 1, "No-GraphSAGE 8.87 12.52 33.04", 220),
        _tp(6, 1, "After the table, a long sentence of prose follows and it keeps going well past the limit. " * 3 + "Done.", 260),
        _tp(7, 1, "Table 3: Another caption below its table.", 400),  # 下一张表的图注：当前表在这里结束
        _tp(8, 2, "Fig. 4: A figure caption on the next page.", 50),
    ]
    found = T.detect_tables(ps)
    assert [t["label"] for t in found] == ["Table S2"]  # Table 3 后面没有行文字：不算表格
    t = found[0]
    assert t["body_ords"] == [2, 3, 4, 5] and t["page"] == 1
    assert t["bbox"][1] < 130 and t["bbox"][3] > 231  # 区域覆盖整块行文字并留出边距


def test_detect_tables_caption_below_and_in_text_mentions_ignored():
    from paperdesk.vision import table_batch as T

    ps = [
        _tp(0, 3, "Dataset Nodes Edges", 100),
        _tp(1, 3, "Crime 829 1473", 130),
        _tp(2, 3, "Digg 29652 84781", 160),
        _tp(3, 3, "Table 1. Basic statistics of the networks.", 200),  # 图注在表格下面
        _tp(4, 3, "Table 1 shows that the networks differ a lot in size.", 240),  # 正文里的提及不是图注
    ]
    found = T.detect_tables(ps)
    assert len(found) == 1 and found[0]["label"] == "Table 1" and found[0]["body_ords"] == [0, 1, 2]
    assert T.detect_tables([_tp(0, 1, "Table 1 shows the data.", 10), _tp(1, 1, "a b c", 30), _tp(2, 1, "d e f", 50)]) == []


def test_parse_delimited_only_when_columns_are_consistent():
    from paperdesk.vision import table_batch as T

    ok = T.parse_delimited(["Name\tValue", "a\t1", "b\t2"])
    assert ok and ok["columns"] == ["Name", "Value"] and ok["rows"] == [["a", "1"], ["b", "2"]]
    assert T.parse_delimited(["Name Value", "a 1", "b 2"]) is None  # 只有空格：分不出列，交给视觉模型
    assert T.parse_delimited(["a | b", "1 | 2 | 3"]) is None


def _add_table(api, pid, rows, caption="Table 9: Test statistics of the networks.", page=1):
    with api.ctx.db.session() as conn:
        top = conn.execute("SELECT COALESCE(MAX(ord), 0) + 1 FROM passages WHERE paper_id = ?", (pid,)).fetchone()[0]
        conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0, y0, x1, y1) VALUES (?,?,?,?,?,72,100,500,111)",
                     (pid, top, page, "paragraph", caption))
        for i, r in enumerate(rows, 1):
            conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0, y0, x1, y1) VALUES (?,?,?,?,?,72,?,500,?)",
                         (pid, top + i, page, "paragraph", r, 130 + 30 * i, 141 + 30 * i))
    return top


def test_tables_are_recognized_in_background_and_cached(journal):
    api, pid, _eq, _fig = journal
    cap = _add_table(api, pid, ["Network Nodes Edges", "Crime 829 1473", "Digg 29652 84781"])
    st = api.get(f"/papers/{pid}/tables-analysis").json()
    item = next(i for i in st["items"] if i["caption_ord"] == cap)
    assert item["label"] == "Table 9" and item["status"] == "none" and item["body_ords"] == [cap + 1, cap + 2, cap + 3]
    assert api.get(f"/papers/{pid}/tables/{cap}/image").content.startswith(b"\x89PNG")  # 对照原图（本地渲染）
    job = api.wait_job(api.post(f"/papers/{pid}/tables-analysis").json()["job_id"])
    assert job["status"] == "completed" and job["result"]["failed"] == 0
    st = api.get(f"/papers/{pid}/tables-analysis").json()
    item = next(i for i in st["items"] if i["caption_ord"] == cap)
    assert item["status"] == "ready" and item["source"] == "vision" and item["data"]["rows"] and item["data"]["columns"]
    assert "unverified_cells" in item["data"]  # 与文字层核对不上的单元格（索引）一并返回
    assert any(i["task_type"] == "vision_table" and i["includes"]["images"] for i in api.get("/privacy/requests").json()["items"])
    # 已识别：不再创建任务，也不再发送截图
    n = len(api.fake.requests)
    assert api.post(f"/papers/{pid}/tables-analysis").json()["job_id"] is None and len(api.fake.requests) == n


def test_delimited_table_text_is_rendered_without_calling_the_model(journal):
    api, pid, _eq, _fig = journal
    cap = _add_table(api, pid, ["Name\tValue", "alpha\t1", "beta\t2"], caption="Table 8: Delimited table.")
    n = len(api.fake.requests)
    job = api.wait_job(api.post(f"/papers/{pid}/tables-analysis").json()["job_id"])
    assert job["status"] == "completed"
    item = next(i for i in api.get(f"/papers/{pid}/tables-analysis").json()["items"] if i["caption_ord"] == cap)
    assert item["source"] == "text" and item["data"]["rows"] == [["alpha", "1"], ["beta", "2"]]
    assert not [r for r in api.fake.requests[n:] if "表格转录器" in str(r["body"].get("messages"))]


def test_table_recognition_without_vision_setup_gives_a_readable_error(vapi, tmp_path):
    item = vapi.import_pdf(pdfgen_journal.build_journal_paper(tmp_path / "t.pdf"))
    pid = item["paper_id"]
    _add_table(vapi, pid, ["Network Nodes Edges", "Crime 829 1473", "Digg 29652 84781"])  # 没有配置 AI
    r = vapi.post(f"/papers/{pid}/tables-analysis")
    assert r.status_code >= 400 and r.json()["error"]["message"]
    assert vapi.get(f"/papers/{pid}/tables-analysis").json()["items"][0]["status"] == "none"  # 没有留下“识别中”的残影


def test_table_recognition_can_be_limited_to_chosen_tables(journal):
    api, pid, _eq, _fig = journal
    a = _add_table(api, pid, ["Network Nodes Edges", "Crime 829 1473", "Digg 29652 84781"], caption="Table 6: First table.")
    b = _add_table(api, pid, ["Method ANC Time", "FINDER 8.19 12", "HDA 9.10 40"], caption="Table 7: Second table.", page=2)
    job = api.wait_job(api.post(f"/papers/{pid}/tables-analysis", {"ords": [b]}).json()["job_id"])
    assert job["status"] == "completed"
    items = {i["caption_ord"]: i["status"] for i in api.get(f"/papers/{pid}/tables-analysis").json()["items"]}
    assert items[b] == "ready" and items[a] == "none"  # 只识别了被选中的那一张
    assert api.post(f"/papers/{pid}/tables-analysis", {"ords": [b]}).json()["job_id"] is None  # 已识别：不重复
    again = api.post(f"/papers/{pid}/tables-analysis", {"ords": [b], "force": True}).json()["job_id"]
    assert again and api.wait_job(again)["status"] == "completed"  # force 才重新识别


def test_bare_passage_numbers_become_page_words_but_citation_markers_stay():
    from paperdesk.vision.figures import bare_refs_to_pages

    out = bare_refs_to_pages("论文P36指出缓冲区 [P36]，另见 P99 与 【P36】", {36: 4})
    assert "论文第 4 页指出缓冲区" in out
    assert "[P36]" in out and "【P36】" in out  # 带括号的是引用标记，界面把它渲染成可点击的引用
    assert "P99" in out  # 不是真实存在的段落编号：保持原样，不编造页码


# ---------------- 表格：位置感知核对 + 类型一致性（真实回归：相邻行的数填错了位置） ----------------
def _w(x, y, text):
    return (float(x), float(y), float(x) + 6.0 * len(text), float(y) + 9.0, text)


def _s3_words(diameter=("10", "12", "11")):
    """补充材料 Table S3 的缩影：行标签在最左，三列数值；文字层每个词都有坐标。"""
    xs = (260, 350, 440)
    out = [_w(72, 140, "Statistics"), *[_w(x, 140, h) for x, h in zip(xs, ("Crime", "Digg", "Enron"))]]
    rows = [("Nodes num (N)", ("829", "29652", "33696")), ("diameter", diameter), ("average degree", ("3.55", "5.72", "10.73"))]
    for i, (label, vals) in enumerate(rows):
        y = 170 + 30 * i
        for j, tok in enumerate(label.split()):
            out.append(_w(72 + 40 * j, y, tok))
        out += [_w(x, y, v) for x, v in zip(xs, vals)]
    return out


S3_COLUMNS = ["Statistics", "Crime", "Digg", "Enron"]


def test_position_check_flags_a_value_copied_from_the_neighbouring_row():
    from paperdesk.vision import table_check as K

    words = _s3_words()
    good = [["Nodes num (N)", "829", "29,652", "33,696"], ["diameter", "10", "12", "11"], ["average degree", "3.55", "5.72", "10.73"]]
    ok = K.positional_check(words, S3_COLUMNS, good)
    assert ok["misplaced"] == [] and ok["located_rows"] == 3 and ok["checked"] == 9
    bad = [r[:] for r in good]
    bad[1][2] = "5.72"  # 直径填成了同表同列“平均度”的值：5.72 在文字层里有，但不在 diameter 这一行
    res = K.positional_check(words, S3_COLUMNS, bad)
    assert res["misplaced"] == [[1, 2]]


def test_position_check_flags_a_value_in_the_wrong_column_of_the_right_row():
    from paperdesk.vision import table_check as K

    swapped = [["Nodes num (N)", "829", "29,652", "33,696"], ["diameter", "10", "11", "12"], ["average degree", "3.55", "5.72", "10.73"]]
    res = K.positional_check(_s3_words(), S3_COLUMNS, swapped)  # 同一行里 12 和 11 互换了列
    assert sorted(res["misplaced"]) == [[1, 2], [1, 3]]


def test_position_check_handles_overprinted_bold_numbers_and_unlocated_rows():
    from paperdesk.vision import table_check as K

    words = _s3_words(("10", "12", "11"))
    words = [w if w[4] != "12" else (w[0], w[1], w[2] + 12, w[3], "1212") for w in words]  # 粗体叠印：12 被写了两遍
    good = [["Nodes num (N)", "829", "29,652", "33,696"], ["diameter", "10", "12", "11"], ["average degree", "3.55", "5.72", "10.73"]]
    assert K.positional_check(words, S3_COLUMNS, good)["misplaced"] == []
    # 行标签在文字层里找不到：这一行不做位置核对（退回“值在文字层里出现过没有”的原有核对），不误报
    odd = [["zzz unknown", "10", "12", "11"]]
    r = K.positional_check(words, S3_COLUMNS, odd)
    assert r["misplaced"] == [] and r["located_rows"] == 0


def test_consistency_warnings_are_gentle_and_never_change_values():
    from paperdesk.vision import table_check as K

    cols = ["Statistics", "Crime", "Digg", "Enron", "Epinions"]
    rows = [["diameter", "10", "5.72", "11", "9"], ["average degree", "3.55", "5.72", "10.73", "7.20"]]
    before = [r[:] for r in rows]
    w = K.consistency_warnings(cols, rows)
    assert any("diameter" in m and "5.72" in m and "整数" in m for m in w)
    assert rows == before and len(w) <= 6
    dup = K.consistency_warnings(list("abcdefg"), [["x", "5.72", "5.72", "1.10", "2.20", "3.30"]])  # 一行 ≥5 个数值，同一个小数出现两次
    assert any("出现了 2 次" in m for m in dup)
    assert K.consistency_warnings(list("abc"), [["x", "5.72", "5.72"]]) == []  # 数值太少（超参数表之类）：不告警
    pct = K.consistency_warnings(["m", "a"], [[f"r{i}", f"{i}0%"] for i in range(1, 6)] + [["r6", "60"]])
    assert any("百分号" in m and "60" in m for m in pct)
    assert K.consistency_warnings(["m", "a"], [["x", "1.5"], ["y", "2.5"], ["z", "3.5"]]) == []  # 小数列里出现小数：自洽，不告警


def _table_pdf(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 60), "A tiny paper about networks for the table regression test.", fontsize=12)
    page.insert_text((72, 100), "Table 3: Basic statistics of the networks.", fontsize=10)
    for w in _s3_words():
        page.insert_text((w[0], w[3] - 2), w[4], fontsize=10)
    path = tmp_path / "s3.pdf"
    doc.save(path)
    doc.close()
    return path


def _s3_paper(vapi, tmp_path):
    item = vapi.import_pdf(_table_pdf(tmp_path))
    pid = item["paper_id"]
    vapi.configure_ai()
    rows = ["Statistics Crime Digg Enron", "Nodes num (N) 829 29652 33696", "diameter 10 12 11", "average degree 3.55 5.72 10.73"]
    with vapi.ctx.db.session() as conn:  # 用确定的段落取代解析器的切分：行文字的位置与 PDF 里写的位置一致
        conn.execute("DELETE FROM passages WHERE paper_id = ?", (pid,))
        conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0, y0, x1, y1) VALUES (?,?,?,?,?,72,91,330,103)",
                     (pid, 0, 1, "paragraph", "Table 3: Basic statistics of the networks."))
        for i, r in enumerate(rows, 1):
            y = 140 + 30 * (i - 1)
            conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0, y0, x1, y1) VALUES (?,?,?,?,?,72,?,480,?)", (pid, i, 1, "paragraph", r, y - 1, y + 10))
    return pid


def _recognize_s3(vapi, pid, diameter_digg):
    vapi.fake.table_raw = {"title": "Table 3", "columns": S3_COLUMNS,
                           "rows": [["Nodes num (N)", "829", "29,652", "33,696"], ["diameter", "10", diameter_digg, "11"],
                                    ["average degree", "3.55", "5.72", "10.73"]], "notes": ""}
    # method=vision：强制走视觉模型（auto 时文字层能还原出整齐行列，不会调用模型，见下一个测试）
    job = vapi.wait_job(vapi.post(f"/papers/{pid}/tables-analysis", {"force": True, "method": "vision"}).json()["job_id"])
    assert job["status"] == "completed", job
    return next(i for i in vapi.get(f"/papers/{pid}/tables-analysis").json()["items"] if i["label"] == "Table 3")["data"]


def test_s3_regression_misplaced_digg_diameter_is_flagged_not_verified(vapi, tmp_path):
    pid = _s3_paper(vapi, tmp_path)
    good = _recognize_s3(vapi, pid, "12")
    assert good["misplaced_cells"] == [] and good["verified_ratio"] == 1.0
    bad = _recognize_s3(vapi, pid, "5.72")  # 模型把“平均度 5.72”填进了 Digg 的 diameter
    assert bad["misplaced_cells"] == [[1, 2]]
    assert bad["unverified_cells"] == []  # 5.72 在文字层里有：和“文字层里找不到”区分开
    assert bad["verified_ratio"] is not None and bad["verified_ratio"] < 1.0
    assert any("位置存疑" in w for w in bad["warnings"]) and any("diameter" in w and "整数" in w for w in bad["warnings"])


def test_numeric_table_is_rebuilt_from_the_text_layer_without_calling_the_model(vapi, tmp_path):
    pid = _s3_paper(vapi, tmp_path)
    n = len(vapi.fake.requests)
    vapi.post(f"/papers/{pid}/tables-analysis", {})  # auto：先文字建表、再文字层坐标还原，还原不了才用视觉模型
    item = next(i for i in vapi.get(f"/papers/{pid}/tables-analysis").json()["items"] if i["label"] == "Table 3")
    assert item["status"] == "ready" and item["source"] == "layout" and item["kind"] == "table"
    rows = {r[0]: r[1:] for r in item["data"]["rows"]}
    assert rows["diameter"] == ["10", "12", "11"] and rows["average degree"] == ["3.55", "5.72", "10.73"]  # 文字层里印的是什么就是什么，不是模型猜的
    assert item["data"]["misplaced_cells"] == [] and item["data"]["verified_ratio"] == 1.0
    assert not [r for r in vapi.fake.requests[n:] if "表格转录器" in str(r["body"].get("messages"))]  # 没有调用视觉模型


# ---------------- 真实 S3 / S4 的回归：位置核对在真实版面上、类型一致性不误报 ----------------
S3_REAL_ROWS = [
    ["Nodes num (N)", "829", "4,165", "29,652", "33,696", "62,561", "75,877", "63,392", "1,134,890", "1,624,991"],
    ["Edges num (E)", "1,473", "13,087", "84,781", "180,811", "147,878", "405,739", "816,831", "2,987,624", "15,473,043"],
    ["maximum degree", "25", "286", "310", "1,383", "95", "3,044", "1,098", "28,754", "27,224"],
    ["average degree", "3.55", "6.28", "5.72", "10.73", "4.73", "10.69", "25.77", "5.27", "19.04"],
    ["diameter", "10", "11", "5.72", "11", "11", "15", "15", "24", "24"],
    ["mean shortest path length", "5.04", "4.16", "4.68", "4.03", "5.96", "4.40", "4.31", "5.55", "5.19"],
    ["clustering coefficient", "0.0058", "0.0444", "0.0054", "0.5092", "0.0055", "0.1378", "0.2218", "0.0808", "0.1892"],
]
S3_REAL_COLS = ["Statistics", "Crime", "HI-II-14", "Digg", "Enron", "Gnutella31", "Epinions", "Facebook", "Youtube", "Flickr"]
S4_REAL_ROWS = [
    ["replay memory size", "5 × 10^5", "Capacity of the experience replay buffer"],
    ["learning rate", "1 × 10^-4", "The learning rate used by Adam optimizer"],
    ["embedding dimension", "32,64,128", "dimensions of node embedding vectors"],
    ["update time", "10^3", "the frequency with which target network is updated"],
    ["layer iterations", "3,4", "number of neighbor-aggregation iterations"],
    ["maximum episodes", "10^6", "maximum episodes for the training procedure"],
    ["discount factor", "0.99", "discount factor gamma used in Q-learning"],
    ["Q-learning steps", "3,4,5", "number of steps for multi-step Q-learning"],
    ["mini-batch size", "16,32,64", "number of mini-batch training samples"],
    ["initial exploration", "1", "initial value of epsilon in epsilon-greedy exploration"],
    ["final exploration", "5 × 10^-2", "final value of epsilon in epsilon-greedy exploration"],
    ["exploration steps", "10^4", "the number of steps over which epsilon is annealed"],
]


def test_real_s3_diameter_decimal_gets_a_neutral_warning_not_a_blame():
    from paperdesk.vision import table_check as K

    w = K.consistency_warnings(S3_REAL_COLS, S3_REAL_ROWS)
    assert len(w) == 1 and "diameter" in w[0] and "5.72" in w[0] and "整数" in w[0]
    assert "也可能原文如此" in w[0]  # 真实 PDF 的这一格印的就是 5.72：可能是识别错误，也可能是原文笔误，不能一口咬定是模型填错


def test_real_s4_value_column_does_not_trigger_integer_consistency_warnings():
    from paperdesk.vision import table_check as K

    # 超参数表的 Value 列本来就混着整数、小数（0.99）和带指数的写法：不告警
    assert K.consistency_warnings(["Hyperparameter", "Value", "Description"], S4_REAL_ROWS) == []
    # 即使列里全是整数、只有一个小数，通用列名（Value / Result / Score / Parameter）也不做整数一致性告警
    col = [[f"r{i}", str(i * 10)] for i in range(1, 7)] + [["r7", "0.99"], ["r8", "10"]]
    for head in ("Value", "Result", "Score", "Parameter"):
        assert K.consistency_warnings(["name", head], col) == []


def test_integer_row_rule_needs_a_counting_label_or_a_copied_value():
    from paperdesk.vision import table_check as K

    cols = ["Statistics"] + [f"d{i}" for i in range(1, 8)]
    # 计数类行标签（maximum degree）里出现小数：告警；平均值类（average degree）里出现小数：正常
    assert K.consistency_warnings(cols, [["maximum degree", "25", "286", "3.5", "95", "88", "10", "7"]])
    assert K.consistency_warnings(cols, [["average degree", "3.55", "6.28", "5.72", "10.73", "4.73", "10.69", "25.77"]]) == []
    # 标签不明确：只有“≥5 个数值、≥90% 整数、只有 1 个小数、且该小数与表里另一处的值完全相同”才告警
    ints = ["10", "11", "5.72", "11", "12", "13", "14", "15", "16", "17"]  # 10 个数值：9 个整数 + 1 个小数（90%）
    rows = [["other", *ints], ["average", "1.5", "2.5", "5.72", "3.5", "4.5", "5.5", "6.5"]]
    w = K.consistency_warnings(cols, rows)
    assert any("「5.72」是小数" in m and "与「average」" in m for m in w)
    assert K.consistency_warnings(cols, [["other", *[("5.71" if v == "5.72" else v) for v in ints]]]) == []  # 这个小数没有出现在表里别处：不告警


def test_position_check_matches_ligatures_in_row_labels():
    from paperdesk.vision import table_check as K

    xs = (260, 350, 440)
    words = [_w(72, 140, "clustering"), _w(130, 140, "coeﬃcient"), *[_w(x, 140, v) for x, v in zip(xs, ("0.0058", "0.0444", "0.0054"))],
             _w(72, 170, "assortativity"), *[_w(x, 170, v) for x, v in zip(xs, ("-0.1645", "-0.2016", "0.0027"))]]
    rows = [["clustering coefficient", "0.0058", "0.0444", "0.0054"], ["assortativity", "-0.1645", "-0.2016", "0.0027"]]
    res = K.positional_check(words, ["Statistics", "Crime", "Digg", "Enron"], rows)
    assert res["located_rows"] == 2 and res["misplaced"] == []  # 文字层里的 ﬃ 连字和模型写的 ffi 对得上


def test_a_value_the_pdf_itself_prints_in_that_cell_is_not_called_misplaced():
    from paperdesk.vision import table_check as K

    # 真实 Table S3：PDF 在 diameter / Digg 这一格印的就是 5.72（与上一行 average degree 的值相同，疑似原文笔误）。
    # 模型照抄是忠实的：位置核对不能标它“位置存疑”（值就在它该在的位置）；只有类型一致性给一句中性的提示。
    words = _s3_words(("10", "5.72", "11"))
    rows = [["Nodes num (N)", "829", "29,652", "33,696"], ["diameter", "10", "5.72", "11"], ["average degree", "3.55", "5.72", "10.73"]]
    assert K.positional_check(words, S3_COLUMNS, rows)["misplaced"] == []
    assert any("diameter" in m and "5.72" in m for m in K.consistency_warnings(S3_COLUMNS, rows))


# ---------------- 处理进度：公式和表格没处理好之前，不能说“已完成” ----------------
def test_processing_is_not_done_while_formulas_are_unrecognized(journal):
    api, pid, _eq, _fig = journal
    p = api.get(f"/papers/{pid}/processing").json()
    assert p["steps"]["formulas"]["state"] == "pending" and p["steps"]["formulas"]["total"] >= 1
    assert p["overall"] in ("waiting", "processing") and "已完成" not in p["label"]
    # 文库列表里每篇论文也带 processing 摘要，界面据此显示“处理中/待处理”，而不是只看“分析”一项
    item = next(i for i in api.get("/papers").json()["items"] if i["id"] == pid)
    assert item["processing"]["overall"] in ("waiting", "processing") and item["processing"]["steps"]["formulas"]["state"] == "pending"


def test_processing_becomes_done_only_after_everything_is_processed(journal):
    api, pid, _eq, _fig = journal
    api.put("/settings/ai", {"auto_analyze": False})
    started = api.post(f"/papers/{pid}/processing").json()["started"]
    assert "formulas" in started  # 公式识别被启动
    for jid in started.values():
        assert api.wait_job(jid)["status"] == "completed"
    p = api.get(f"/papers/{pid}/processing").json()
    assert p["steps"]["formulas"]["state"] == "done"
    assert p["overall"] == "done" and p["label"] == "已完成", p
    assert api.post(f"/papers/{pid}/processing").json()["started"] == {}  # 幂等：都做完了不再启动


def test_processing_says_why_when_no_vision_model_is_configured(journal):
    api, pid, _eq, _fig = journal
    api.put("/settings/ai", {"vision_model": "", "auto_analyze": False})
    p = api.get(f"/papers/{pid}/processing").json()
    f = p["steps"]["formulas"]
    assert f["state"] == "skipped" and "未配置视觉模型" in f["reason"] and "仍是截图" in f["reason"]
    assert p["overall"] != "done" and any("公式" in r for r in p["reasons"])
    assert api.post(f"/papers/{pid}/processing").json()["started"].get("formulas") is None  # 没配置就不启动，也不假装完成


def test_import_starts_formula_and_table_processing_automatically(vapi, tmp_path):
    vapi.configure_ai()
    vapi.put("/settings/ai", {"auto_analyze": True})
    item = vapi.import_pdf(pdfgen_journal.build_journal_paper(tmp_path / "auto.pdf"))
    pid = item["paper_id"]
    import time

    deadline = time.time() + 30
    state = None
    while time.time() < deadline:
        state = vapi.get(f"/papers/{pid}/processing").json()
        if state["steps"]["formulas"]["state"] == "done":
            break
        time.sleep(0.3)
    assert state and state["steps"]["formulas"]["state"] == "done", state  # 不用打开阅读器，导入后公式就已经在识别


def test_figure_group_box_grows_to_include_axis_labels_and_panel_letters(api):
    """图的外框只包着图本身时，截图会裁掉纵轴刻度和子图字母；紧挨着的“图内文字”要并进外框，但不能扩进图注或下面那张图。"""
    from paperdesk.vision import groups

    with api.ctx.db.session() as conn:
        conn.execute("INSERT INTO papers(id, title, status, imported_at) VALUES ('pg','t','ready','2026-01-01T00:00:00Z')")
        for ord_, kind, text, box in [
            (0, "caption", "Fig. 2 | Process.", (43, 300, 555, 312)),
            (1, "figure_text", "0.8 0.6 0.4", (60, 120, 80, 200)),  # 纵轴刻度，在图框左边
            (2, "figure_text", "b a", (100, 52, 130, 62)),  # 子图字母，在图框上方
            (3, "figure_text", "far away label", (60, 400, 120, 410)),  # 图注下面：别的图的
        ]:
            conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0, y0, x1, y1) VALUES ('pg',?,?,?,?,?,?,?,?)", (ord_, 1, kind, text, *box))
        conn.execute("INSERT INTO figures(paper_id, page, x0, y0, x1, y1, kind, label, caption_ord) VALUES ('pg',1,90,70,550,290,'figure','Fig. 2',0)")
    g = groups.figure_groups(api.ctx, "pg")[0]
    x0, y0, x1, y1 = g["bbox"]
    assert x0 <= 60 and y0 <= 52  # 刻度和子图字母都进来了
    assert y1 <= 300  # 没有扩进图注


# ---------------- 在右侧对话里解析图（卡片，不弹窗） ----------------
def test_figure_explain_creates_a_conversation_card_with_the_honest_figure_answer(journal):
    api, pid, _, fig = journal
    api.fake.qa_reply = {"answerable": True, "answer": "FINDER 的 ANC 约 0.52，比 HDA 低。", "seen": "折线图",
                         "numbers": [{"value": "0.52", "how": "printed"}]}
    ev = api.sse("/ai/explain-figure", {"paper_id": pid, "figure_id": fig["id"]})
    names = [e[0] for e in ev]
    assert names[0] == "stage" and "meta" in names and "delta" in names and names[-1] == "done"
    card = ev[-1][1]["card"]
    assert card["object_type"] == "figure" and card["status"] == "complete" and card["conversation_id"]
    assert card["question"].startswith("请解析这张图") and card["selection"]["figure_id"] == fig["id"]
    f = card["answer"]["figure"]
    assert f["seen"] == "折线图" and f["id"] == fig["id"]
    assert f["numbers"] == [{"value": "0.52", "how": "estimated"}]  # 声称印在图上但文字层里没有：照样降级
    assert card["verification"]["status"] == "partial" and any("读图估计" in n for n in card["verification"]["notes"])
    assert len(_image_messages(api.fake)) == 1  # 图只发给视觉模型一次
    cards = api.get(f"/papers/{pid}/cards?conversation_id={card['conversation_id']}").json()["items"]
    assert [c["id"] for c in cards] == [card["id"]]  # 在当前对话里，右侧面板能看到


def test_figure_explain_follow_up_uses_the_given_question_and_unanswerable_is_stated(journal):
    api, pid, _, fig = journal
    first = api.sse("/ai/explain-figure", {"paper_id": pid, "figure_id": fig["id"]})[-1][1]["card"]
    ev = api.sse("/ai/explain-figure", {"paper_id": pid, "figure_id": fig["id"], "conversation_id": first["conversation_id"],
                                          "question": "FINDER 在 ER 图上的运行时间是多少秒？"})
    card = ev[-1][1]["card"]
    assert card["conversation_id"] == first["conversation_id"] and card["question"].startswith("FINDER 在 ER")
    assert card["answer"]["direct"].startswith("图中看不出") and card["answer"]["figure"]["answerable"] is False


def test_figure_explain_error_leaves_no_card(journal):
    api, pid, _, fig = journal
    ev = api.sse("/ai/explain-figure", {"paper_id": pid, "figure_id": 999999})
    assert ev[-1][0] == "error"
    assert api.get(f"/papers/{pid}/cards").json()["items"] == []


def test_equation_card_belongs_to_a_conversation_so_it_does_not_vanish_when_done(journal):
    api, pid, eq, _ = journal
    conv = api.post(f"/papers/{pid}/conversations", {}).json()
    card = api.sse("/ai/explain-equation", {"paper_id": pid, "equation_id": eq["id"], "conversation_id": conv["id"]})[-1][1]["card"]
    assert card["conversation_id"] == conv["id"]
    again = api.sse("/ai/explain-equation", {"paper_id": pid, "equation_id": eq["id"]})[-1][1]["card"]
    assert again["conversation_id"]  # 没指定对话也归到最近的一个，不是空的


# ---------------- 行内公式的视觉模型兜底 ----------------
def test_inline_math_plausibility_gate_rejects_made_up_answers():
    from paperdesk.vision.inline_math_ai import plausible

    ok, _ = plausible("ti = √hbi", "t_i = \sqrt{\frac{h}{b_i}}")
    assert ok
    assert plausible("φk(·", "\phi_k(\cdot")[0] is True
    assert plausible("ti = √hbi", "q_{9}")[0] is False  # 完全不相干
    assert plausible("ti = √hbi", "t_i = \sqrt{h b_i} + x y z w")[0] is False  # 多出原文里没有的内容
    assert plausible("x", "")[0] is False and plausible("ab", "\frac{a")[0] is False  # 空、括号不配对


def test_inline_math_candidates_are_recognised_applied_and_cached(journal):
    from paperdesk.vision import inline_math_ai as ima

    api, pid, _eq, _fig = journal
    with api.ctx.db.session() as conn:
        row = conn.execute("SELECT ord FROM passages WHERE paper_id = ? ORDER BY ord LIMIT 1", (pid,)).fetchone()
        cands = [{"src": "T = {t0, . . . , tK}", "tex": None, "page": 1, "bbox": [72, 100, 200, 112]},
                 {"src": "BAD x1 y2", "tex": None, "page": 1, "bbox": [72, 120, 160, 132]}]
        conn.execute("UPDATE passages SET math_json = ? WHERE paper_id = ? AND ord = ?", (json.dumps(cands, ensure_ascii=False), pid, row["ord"]))
    job_id = ima.enqueue(api.ctx, pid)
    assert job_id
    job = api.wait_job(job_id)
    assert job["status"] == "completed" and job["result"]["recognized"] == 1 and job["result"]["rejected"] == 1, job
    with api.ctx.db.session() as conn:
        arr = json.loads(conn.execute("SELECT math_json FROM passages WHERE paper_id = ? AND ord = ?", (pid, row["ord"])).fetchone()["math_json"])
    assert arr[0]["tex"] and arr[0]["ai"] is True and not arr[1].get("tex")  # 核对不过的不采用
    assert any(i["task_type"] == "vision_inline_math" for i in api.get("/privacy/requests").json()["items"])
    n = api.fake.inline_calls
    assert ima.enqueue(api.ctx, pid) is None and api.fake.inline_calls == n  # 已有结论（含被拒绝的）：不再调用模型


def test_a_whole_formula_candidate_with_fallback_parts_shows_the_parts_until_it_is_recognised(journal):
    """整条认不全的公式记成候选，认得全的小簇放在 parts 里：识别出来之前读取方看到小簇，识别出来后换成整条并去掉 parts。"""
    from paperdesk.parsing.inline_math import flatten_runs
    from paperdesk.vision import inline_math_ai as ima

    api, pid, _eq, _fig = journal
    whole = {"src": "T = {t0, . . . , tK}", "tex": None, "page": 1, "bbox": [72, 100, 200, 112],
             "parts": [{"src": "T", "tex": "T"}, {"src": "t0", "tex": "t_{0}"}]}
    with api.ctx.db.session() as conn:
        row = conn.execute("SELECT ord FROM passages WHERE paper_id = ? ORDER BY ord LIMIT 1", (pid,)).fetchone()
        conn.execute("UPDATE passages SET math_json = ? WHERE paper_id = ? AND ord = ?", (json.dumps([whole], ensure_ascii=False), pid, row["ord"]))
    assert [r["src"] for r in flatten_runs([whole])] == ["T", "t0"]
    job = api.wait_job(ima.enqueue(api.ctx, pid))
    assert job["status"] == "completed" and job["result"]["applied"] == 1, job
    with api.ctx.db.session() as conn:
        arr = json.loads(conn.execute("SELECT math_json FROM passages WHERE paper_id = ? AND ord = ?", (pid, row["ord"])).fetchone()["math_json"])
    assert len(arr) == 1 and arr[0]["tex"] and arr[0]["ai"] is True and "parts" not in arr[0]
    assert flatten_runs(arr) == arr


def test_inline_math_reply_parser_survives_merged_objects_and_escapes():
    from paperdesk.vision.inline_math_ai import _parse_items

    # 真实网关上出现过：两项写进同一个对象，重复的键会让 json.loads 悄悄丢掉第一项
    bad = '{"items":[{"n":1,"latex":"BSmathcal{T}^* = BS{t_0,BSldots,t_{K-1}BS},","n":2,"latex":"p_k^2"}]}'.replace("BS", chr(92)*2)
    got = _parse_items(bad)
    assert got[1] == chr(92) + "mathcal{T}^* = " + chr(92) + "{t_0," + chr(92) + "ldots,t_{K-1}" + chr(92) + "}," and got[2] == "p_k^2"
    assert _parse_items('{"items":[{"n":1,"latex":""},{"n":2,"latex":"x_1"}]}') == {1: "", 2: "x_1"}  # 正常格式照常
    assert _parse_items("抱歉，我看不清") == {}


def test_inline_math_trailing_sentence_punctuation_is_not_part_of_the_formula():
    from paperdesk.vision.inline_math_ai import tidy

    assert tidy("T = \{t_0\},", "T = {t0, tK}").replace(chr(92), "") == "T = {t_0}"  # 句末的逗号在公式之外
    assert tidy("\eta(\alpha,", "η(α,").endswith(",")  # 原文自己以逗号结尾：保留
    assert tidy("Y = x.", "Y = x") == "Y = x"


def test_inline_math_two_models_agree_or_get_arbitrated(monkeypatch):
    import asyncio

    from paperdesk.vision import inline_math_ai as ima

    calls = []

    async def fake_arbitrate(service, provider, model, paper_id, png, a, b):
        calls.append((a, b))
        return "B", "A 少了下标"

    monkeypatch.setattr(ima.formula, "_arbitrate", fake_arbitrate)
    run = lambda answers: asyncio.run(ima.decide(None, None, "m1", "p", b"png", "h = Ns f 1", answers))  # noqa: E731
    one, _ = run([("m1", "h = \frac{s_f}{N-1}"), ("m2", "h=\frac{s_f}{N - 1}")])  # 归一化后一致：直接采用，不裁决
    assert one and calls == []
    two, why = run([("m1", "h = \frac{sf}{N-1}"), ("m2", "h = \frac{s_f}{N-1}")])  # 不一致：多数不算数，交给裁决
    assert two == "h = \frac{s_f}{N-1}" and calls and "裁决" in why
    only, _ = run([("m1", "h = \frac{sf}{N-1}"), ("m2", "")])  # 另一个模型没答：和只用一个模型一样
    assert only == "h = \frac{sf}{N-1}"
    none, _ = run([("m1", "q_{9}"), ("m2", "")])  # 都对不上原文：不采用
    assert none is None


def test_inline_math_job_asks_both_vision_models_in_parallel(journal):
    from paperdesk.vision import inline_math_ai as ima

    api, pid, _eq, _fig = journal
    assert api.put("/settings/ai", {"vision_model": "vis-a", "vision_model_2": "vis-b"}).status_code == 200
    with api.ctx.db.session() as conn:
        row = conn.execute("SELECT ord FROM passages WHERE paper_id = ? ORDER BY ord LIMIT 1", (pid,)).fetchone()
        cands = [{"src": "T = {t0, tK}", "tex": None, "page": 1, "bbox": [72, 100, 200, 112]}]
        conn.execute("UPDATE passages SET math_json = ? WHERE paper_id = ? AND ord = ?", (json.dumps(cands, ensure_ascii=False), pid, row["ord"]))
    job = api.wait_job(ima.enqueue(api.ctx, pid))
    assert job["status"] == "completed" and job["result"]["models"] == ["vis-a", "vis-b"] and job["result"]["recognized"] == 1, job
    assert api.fake.inline_calls == 2  # 两个模型各问一次（回答一致，不需要裁决）
    logged = [i for i in api.get("/privacy/requests").json()["items"] if i["task_type"] == "vision_inline_math"]
    assert {i["model"] for i in logged} == {"vis-a", "vis-b"}


# ---------------- 本地公式模型（整行图 → 公式片段 → 对应回候选） ----------------
def test_local_math_segments_are_split_out_of_the_line_transcription():
    from paperdesk.vision import math_local

    text = r"Here $\mu$ is the momentum and $$E[x_{l}^{2}]=1$$ plus \(a_{i}\)."
    assert math_local.segments(text) == [r"\mu", r"E[x_{l}^{2}]=1", r"a_{i}"]
    assert math_local.segments("没有公式") == []


def test_match_segment_picks_the_formula_that_fits_the_text_layer_and_rejects_the_rest():
    from paperdesk.vision.inline_math_ai import match_segment

    segs = [r"\mu", r"E[x_{l}^{2}]", r"a_{i}", r"\begin{array}{c} a_i \ b \end{array}"]
    assert match_segment("a i", segs) == "a_{i}"  # 骨架 ai 最像
    assert match_segment("E[x l 2]", segs) == "E[x_{l}^{2}]"
    assert match_segment("q 9 z", segs) is None  # 没有一个对得上：不采用
    assert match_segment("a i", [r"\begin{array}{c} a_i \ b \end{array}"]) is None  # 多行数组不是行内公式
    assert match_segment("η(α,", [r"\eta(\alpha,"]) == r"\eta(\alpha,"  # 原文自己以逗号结尾：保留


def _set_candidates(api, pid, cands):
    with api.ctx.db.session() as conn:
        row = conn.execute("SELECT ord FROM passages WHERE paper_id = ? ORDER BY ord LIMIT 1", (pid,)).fetchone()
        conn.execute("UPDATE passages SET math_json = ? WHERE paper_id = ? AND ord = ?", (json.dumps(cands, ensure_ascii=False), pid, row["ord"]))
    return row["ord"]


def test_local_engine_recognises_candidates_from_whole_line_images_without_calling_the_vision_model(journal):
    from paperdesk.vision import inline_math_ai as ima
    from paperdesk.vision import math_local

    api, pid, _eq, _fig = journal
    seen: list[int] = []

    def engine(pngs):
        seen.append(len(pngs))
        assert all(p[:4] == b"\x89PNG" for p in pngs)  # 送进去的是整行的 PNG
        return [r"We update $a_{i}$ with $E[x_{l}^{2}]$ at each step." for _ in pngs]

    math_local.set_engine(engine)
    ord_ = _set_candidates(api, pid, [
        {"src": "a i", "tex": None, "page": 1, "bbox": [90, 100, 100, 112], "line": [72, 98, 500, 114]},
        {"src": "E[x l 2]", "tex": None, "page": 1, "bbox": [200, 100, 240, 112], "line": [72, 98, 500, 114]},  # 同一行：只识别一次
        {"src": "zz 99", "tex": None, "page": 1, "bbox": [300, 100, 320, 112], "line": [72, 98, 500, 114]},  # 对不上
    ])
    calls_before = getattr(api.fake, "inline_calls", 0)
    job = api.wait_job(ima.enqueue(api.ctx, pid))
    assert job["status"] == "completed" and job["result"]["local"] == 2, job
    assert seen == [1]  # 同一行的三个候选共用一张整行图
    with api.ctx.db.session() as conn:
        arr = json.loads(conn.execute("SELECT math_json FROM passages WHERE paper_id = ? AND ord = ?", (pid, ord_)).fetchone()["math_json"])
        model = conn.execute("SELECT DISTINCT model FROM inline_math_ai WHERE paper_id = ?", (pid,)).fetchall()
    assert arr[0]["tex"] == "a_{i}" and arr[1]["tex"] == "E[x_{l}^{2}]" and arr[0]["ai"] is True
    models = {r["model"] for r in model}
    assert "texify" in models and len(models) == 2  # 前两个是本地认出来的；第三个本地对不上，视觉模型配好了所以再让它试了一次
    with api.ctx.db.session() as conn:
        third = conn.execute("SELECT model FROM inline_math_ai WHERE paper_id = ? AND model != 'texify'", (pid,)).fetchall()
    assert len(third) == 1 and getattr(api.fake, "inline_calls", 0) > calls_before


def test_local_engine_failure_falls_back_to_the_vision_model(journal):
    from paperdesk.vision import inline_math_ai as ima
    from paperdesk.vision import math_local

    api, pid, _eq, _fig = journal

    def boom(pngs):
        raise RuntimeError("model exploded")

    math_local.set_engine(boom)
    _set_candidates(api, pid, [{"src": "T = {t0, . . . , tK}", "tex": None, "page": 1, "bbox": [72, 100, 200, 112], "line": [72, 98, 500, 114]}])
    job = api.wait_job(ima.enqueue(api.ctx, pid))
    assert job["status"] == "completed" and job["result"]["recognized"] == 1 and job["result"]["local"] == 0, job


def test_local_only_mode_works_without_any_api_key_and_retries_with_the_vision_model_once_configured(tmp_path):
    from paperdesk.vision import inline_math_ai as ima
    from paperdesk.vision import math_local

    from conftest import make_api

    api, fake = make_api(tmp_path)
    api.fake = fake
    with api.c:
        import pdfgen

        pid = api.import_pdf(pdfgen.build_paper(tmp_path / "p.pdf"))["paper_id"]
        math_local.set_engine(lambda pngs: [r"only $a_{i}$ here" for _ in pngs])
        ord_ = _set_candidates(api, pid, [{"src": "a i", "tex": None, "page": 1, "bbox": [90, 100, 100, 112], "line": [72, 98, 500, 114]},
                                           {"src": "zz 99", "tex": None, "page": 1, "bbox": [300, 100, 320, 112], "line": [72, 98, 500, 114]}])
        job = api.wait_job(ima.enqueue(api.ctx, pid))  # 没有 Key、没有视觉模型
        assert job["status"] == "completed" and job["result"]["local"] == 1, job
        assert ima.enqueue(api.ctx, pid) is None  # 没认出来的第二个也没有别的引擎可试：不重复跑
        api.configure_ai(vision_model="vis-a")
        assert ima.pending(api.ctx, pid) == 1  # 配好视觉模型之后，本地没认出来的那个会再试一次
        assert ima.enqueue(api.ctx, pid) is not None


def test_import_candidates_carry_the_whole_line_position(tmp_path):
    """解析时记下候选公式所在的整行（本地模型要整行才认得准）。"""
    import pdfgen

    from paperdesk.parsing.pdf_parser import parse_pdf

    d = parse_pdf(pdfgen.build_paper(tmp_path / "p.pdf"))
    cands = [m for p in d.passages for m in p.math if not m.get("tex")]
    assert all(len(m["line"]) == 4 and m["line"][0] <= m["bbox"][0] and m["line"][2] >= m["bbox"][2] - 1 for m in cands if m.get("line"))


def test_import_starts_local_inline_math_recognition_even_without_any_ai_configuration(tmp_path):
    """回归：导入后的自动步骤原来要求“AI 已配置”，纯本地模式（没填 Key）里行内公式识别永远不会自己启动。"""
    import time

    import pdfgen
    from conftest import make_api
    from paperdesk.vision import math_local

    calls = []
    math_local.set_engine(lambda pngs: (calls.append(len(pngs)), [r"$a_{i}$" for _ in pngs])[1])
    api, _fake = make_api(tmp_path)
    with api.c:
        pid = api.import_pdf(pdfgen.build_paper(tmp_path / "p.pdf"))["paper_id"]
        deadline = time.time() + 10
        while time.time() < deadline and not any(j["type"] == "inline_math_ai" for j in api.get("/jobs").json()["items"]):
            # 论文里没有行内公式候选时本来就不会有这个任务：这里只验证“没配 AI 也走到了 start_auto”，所以主动补一个候选再触发
            with api.ctx.db.session() as conn:
                row = conn.execute("SELECT ord FROM passages WHERE paper_id = ? ORDER BY ord LIMIT 1", (pid,)).fetchone()
                conn.execute("UPDATE passages SET math_json = ? WHERE paper_id = ? AND ord = ?",
                             (json.dumps([{"src": "a i", "tex": None, "page": 1, "bbox": [90, 100, 100, 112], "line": [72, 98, 500, 114]}]), pid, row["ord"]))
            assert api.post(f"/papers/{pid}/processing").status_code == 200
            time.sleep(0.3)
        jobs = [j for j in api.get("/jobs").json()["items"] if j["type"] == "inline_math_ai"]
        assert jobs, "没配 AI、装了本地公式模型：行内公式识别应该自己启动"
        assert api.wait_job(jobs[0]["id"])["status"] == "completed" and calls


def test_local_math_a_line_that_is_one_bare_formula_counts_as_a_segment():
    from paperdesk.vision import math_local

    assert math_local.segments(r"\eta(\alpha,0)=\tau_{1}(\alpha),\ \forall\alpha\in[0,1]") == [r"\eta(\alpha,0)=\tau_{1}(\alpha),\ \forall\alpha\in[0,1]"]
    assert math_local.segments("**Y = {y_0, ..., y_M}**") == ["Y = {y_0, ..., y_M}"]
    assert math_local.segments("This is an ordinary English sentence with no formula at all.") == []  # 普通句子不是公式
    assert math_local.segments("Here we have plain words and an equation x=1 inside") == []  # 英文单词太多：不当整行公式
