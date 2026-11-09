"""代码讲解的取材：结构摘录、不连续行号的核实、论文段落（已确认的关联优先、编号核实）、第三方拷贝目录的识别。"""
from __future__ import annotations

from test_code_ai import cenv, explain, sym  # noqa: F401

from paperdesk.code import context as cx
from paperdesk.code import explain as ex

LONG = "\n".join(
    ["# header comment", "import math", ""]
    + [f"def helper_{i}(x):\n    # step {i}\n    y = x + {i}\n    z = y * 2\n    return z\n" for i in range(15)]
    + ["def core(x, r):", "    a = x @ x", "    b = a * r", "    scale = 2.0 / r", "    result = b * scale", "    return result"]
)


def _files(n_vendor: int, own_manifest: bool) -> list[dict]:
    files = [{"path": f"vendor/pkg/mod{i}.py", "lines": 10, "role": "util", "language": "python"} for i in range(n_vendor)]
    if own_manifest:
        files.append({"path": "vendor/setup.py", "lines": 5, "role": "dependency", "language": "python"})
    files += [{"path": "core/layers.py", "lines": 50, "role": "model", "language": "python"}, {"path": "setup.py", "lines": 5, "role": "dependency", "language": "python"}]
    return files


def test_vendored_dirs_need_both_many_files_and_their_own_manifest():
    assert cx.vendored_dirs(_files(200, True)) == {"vendor"}
    assert cx.vendored_dirs(_files(200, False)) == set()  # 文件多但没有自带依赖文件：可能就是作者自己的代码
    assert cx.vendored_dirs(_files(20, True)) == set()  # 自带依赖文件但文件不多：是普通子项目


def test_outline_shows_every_definition_then_grows_the_functions_within_the_budget(cenv):
    api = cenv
    api.configure_ai()
    from conftest import TOKEN  # noqa: F401
    import tempfile
    from pathlib import Path
    from coderepo import write_repo

    d = Path(tempfile.mkdtemp())
    write_repo(d, {"long.py": LONG})
    r = api.post(f"/papers/{api.pid}/code/import", {"path": str(d)}).json()
    assert api.wait_job(r["job_id"])["status"] == "completed"
    sn = cx.outline_snippet(api.ctx, r["repo_id"], "long.py", "C1", max_chars=2300, whole_if=10)
    assert sn is not None and sn.role == "outline" and sn.shown
    text = sn.text
    assert "⋮" in text and "def helper_0(x):" in text and "def helper_14(x):" in text and "def core(x, r):" in text  # 每个定义都在
    assert "result = b * scale" in text  # 最后那个函数被一轮一轮补到了正文
    assert len(text) <= 3100
    # 核实：被省略的行不算发给过模型
    omitted = next(n for n in range(1, 200) if n not in sn.shown and sn.line_start < n < sn.line_end)
    assert not sn.covers(omitted, omitted) and sn.covers(sn.shown[0], sn.shown[0]) and sn.covers(sn.shown[1] - 1, sn.shown[1] + 1)


def test_citing_omitted_lines_or_unknown_paper_passages_is_not_verified(cenv):
    api = cenv
    snippet = ex.Snippet("C1", "train.py", 1, 100, "   1| x", "outline", 8, [1, 2, 3, 50, 51])
    papers = [{"ord": 5, "page": 2, "section": "4.1 Method", "source": "lexical", "text": "We freeze W0 and inject BA."}]
    md = "## 直接回答\n它冻结原权重 [C1:2-3]，又在别处加了低秩项 [C1:10-12]，对应论文的做法 [P5]，另一处 [P99]。\n## 依据\n- [C1:49-52] 覆盖到 50、51 行\n- [P5] 论文里的描述\n"
    _rewritten, answer, refs, ver = ex.verify_answer(md, [snippet], api.ctx, api.rid, papers)
    by = {(r["kind"], r.get("ord"), r["line_start"]): r for r in refs}
    assert by[("code", None, 2)]["verified"] and by[("code", None, 49)]["verified"]  # 49-52 里有真正给出的 50、51
    omitted = [r for r in refs if r["kind"] == "code" and not r["verified"]]
    assert len(omitted) == 1 and "省略" in omitted[0]["reason"]  # [C1:10-12] 落在被省略的部分
    p5, p99 = by[("paper", 5, 0)], by[("paper", 99, 0)]
    assert p5["verified"] and p5["page"] == 2 and p5["section"] == "4.1 Method" and "W0" in p5["snippet"]
    assert not p99["verified"]
    assert ver["status"] == "partial" and len(ver["invalid_citations"]) == 2
    assert answer["paper_context"] == [{"ord": 5, "page": 2, "section": "4.1 Method", "source": "lexical", "text": "We freeze W0 and inject BA."}]


def test_confirmed_links_come_first_and_each_passage_says_why_it_was_picked(cenv):
    api = cenv
    api.configure_ai()
    with api.ctx.db.session() as conn:
        ords = [r["ord"] for r in conn.execute("SELECT ord FROM passages WHERE paper_id = ? AND kind = 'paragraph' ORDER BY ord", (api.pid,)).fetchall()]
    assert len(ords) >= 3
    s = sym(api, "run_training", "train.py")
    link = api.post(f"/code/repos/{api.rid}/links", {"module_name": "Training loop", "symbol_id": s["id"], "passage_ords": [ords[2]]}).json()
    assert link["status"] == "confirmed"
    repo = {"id": api.rid, "paper_id": api.pid}
    got = cx.paper_passages(api.ctx, repo, {"kind": "function"}, [{"id": s["id"], "path": "train.py", "name": "run_training", "qualname": "run_training", "doc": "", "line_start": s["line_start"], "line_end": s["line_end"]}], "")
    assert got and got[0]["ord"] == ords[2] and got[0]["source"] == "confirmed"
    assert all(g["source"] in ("confirmed", "suggested", "semantic", "lexical") for g in got) and len(got) <= 3
    block = cx.paper_block(got)
    assert f"[P{ords[2]}]" in block and "用户已确认" in block


def test_explain_sends_paper_passages_and_returns_them_as_context(cenv):
    api = cenv
    api.configure_ai()
    with api.ctx.db.session() as conn:
        ords = [r["ord"] for r in conn.execute("SELECT ord FROM passages WHERE paper_id = ? AND kind = 'paragraph' ORDER BY ord", (api.pid,)).fetchall()]
    s = sym(api, "run_training", "train.py")
    api.post(f"/code/repos/{api.rid}/links", {"module_name": "Training loop", "symbol_id": s["id"], "passage_ords": [ords[1]]})
    ev = explain(api, kind="function", symbol_id=s["id"], question="这个函数做什么？")
    meta, card = ev[0][1], ev[-1][1]["card"]
    assert meta["context"]["includes"]["paper_passages"] is True and meta["context"]["papers"][0]["source"] == "confirmed"
    sent = api.fake.requests[-1]["body"]["messages"][1]["content"]
    assert "【论文相关段落" in sent and f"[P{ords[1]}]" in sent
    assert card["answer"]["paper_context"][0]["ord"] == ords[1]
    assert all(e["verified"] for e in card["evidence"])


def test_multi_citations_are_split():
    from paperdesk.code.explain import split_multi_cites as f

    assert f("见 [C1:148,152] 与 [C1:149, C2:23-24]") == "见 [C1:148][C1:152] 与 [C1:149][C2:23-24]"
    assert f("[a/b.py:3-4, P12]") == "[a/b.py:3-4][P12]"
    assert f("[1, 2] 和 [注意, 这里]") == "[1, 2] 和 [注意, 这里]"


def test_tokenizer_matches_acronym_names_and_word_forms():
    from paperdesk.code.associate import ident_tokens, stem, surface, tokens

    # 论文里的 “LoRA” 要能对上代码里的 lora_A、LoRALayer、ConvLoRA；碎片 lo / ra 不该留下
    assert "lora" in ident_tokens("LoRA") and "lo" not in ident_tokens("LoRA")
    assert {"lora", "layer"} <= set(ident_tokens("LoRALayer"))
    assert "lora" in ident_tokens("lora_A") and "lora" in ident_tokens("ConvLoRA") and "conv" in ident_tokens("ConvLoRA")
    assert ident_tokens("merge_weights") == ["merge", "weights"]
    assert stem("scale") == stem("scaling") == stem("scales")
    assert stem("update") == stem("updating")
    assert "self" not in tokens("self.weight") and surface(stem("parameters")) in ("parameter", "parameters", "paramet")


def test_local_matching_uses_function_bodies_and_whole_section_text():
    """名字和注释里没有的词（lora_A、scaling）只出现在函数体里：本地匹配也要能靠论文正文里的这些词找到它。"""
    from collections import Counter

    from paperdesk.code.associate import SymbolIndex, module_query, tokens

    bodies = {1: "class Linear:\n  def forward(self, x):\n    return x @ self.lora_A.T @ self.lora_B.T * self.scaling"}
    names = {1: "Linear"}
    for i in range(2, 22):  # 一批无关的符号作背景
        names[i] = f"helper{i}"
        bodies[i] = f"def helper{i}(x):\n    return x + {i}"
    idx = SymbolIndex.__new__(SymbolIndex)  # 只测打分，不碰数据库
    idx.syms = {i: {"id": i, "path": "pkg/layers.py", "qualname": names[i]} for i in bodies}
    idx.docs = {i: Counter(tokens(names[i]) * 3 + tokens(bodies[i])) for i in bodies}
    idx.n = len(idx.docs)
    idx.avg = sum(sum(c.values()) for c in idx.docs.values()) / idx.n
    idx.df = Counter(t for c in idx.docs.values() for t in c)
    idx.vendored, idx.test_files, idx.title_terms = set(), set(), set()
    text = "We scale the update by alpha over r, with matrices lora_A and lora_B trained while the pretrained weight stays frozen."
    ranked = idx.score(module_query({"name": "Low-rank update", "keywords": [], "description": ""}, idx, text), 3)
    assert ranked and ranked[0][0] == 1
    assert {"lora", "scale"} & set(ranked[0][2]) or {"lora", "scaling"} & set(ranked[0][2])


def test_vendored_single_header_and_tiny_symbols_are_not_paired_with_the_paper():
    from paperdesk.code.associate import eligible_symbols

    class Idx:
        vendored: set = set()
        test_files: set = set()
        syms = {i: {"id": i, "path": "src/json.hpp", "kind": "function", "name": f"f{i}", "qualname": f"f{i}", "line_start": 1, "line_end": 20} for i in range(300)}

    Idx.syms[1000] = {"id": 1000, "path": "src/planner.cpp", "kind": "function", "name": "plan", "qualname": "plan", "line_start": 1, "line_end": 30}
    Idx.syms[1001] = {"id": 1001, "path": "src/planner.cpp", "kind": "method", "name": "get", "qualname": "A.get", "line_start": 40, "line_end": 41}  # 两行的小函数
    Idx.syms[1002] = {"id": 1002, "path": "third_party/x/y.cpp", "kind": "function", "name": "z", "qualname": "z", "line_start": 1, "line_end": 30}
    assert [s["id"] for s in eligible_symbols(Idx)] == [1000]
