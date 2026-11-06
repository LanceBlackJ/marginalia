"""阅读页的段落和译文要能互相核对“是不是同一版”：重新解析之后段落序号整体变了，旧版段落配新版译文会整体错位。"""
from types import SimpleNamespace

from paperdesk.parsing.reflow import _continues


def test_text_and_translation_carry_the_same_revision_until_passages_change(imported):
    api, pid = imported
    text = api.get(f"/papers/{pid}/text").json()
    trans = api.get(f"/papers/{pid}/translation").json()
    assert text["revision"] and text["revision"] == trans["revision"]
    assert api.get(f"/papers/{pid}/text").json()["revision"] == text["revision"]  # 没变就不变
    with api.ctx.db.session() as conn:  # 模拟重新解析：段落序号整体前移/后移
        conn.execute("UPDATE passages SET ord = ord + 1000 WHERE paper_id = ?", (pid,))
    after = api.get(f"/papers/{pid}/text").json()["revision"]
    assert after != text["revision"] and after == api.get(f"/papers/{pid}/translation").json()["revision"]


def _p(text, page, bbox, kind="paragraph"):
    return SimpleNamespace(kind=kind, text=text, page=page, bbox=bbox)


def test_split_paragraph_rejoins_across_column_even_when_the_next_word_is_capitalised():
    long = "Similarly, HuggingGPT operates as an agent that synergizes ChatGPT's linguistic capabilities with a diverse array of AI models from the Hugging"
    left = _p(long, 10, (57, 530, 300, 640))
    assert _continues(left, _p("Face community, thereby enhancing its proficiency.", 10, (333, 86, 540, 110)))  # 换栏
    assert _continues(left, _p("Face community, thereby enhancing.", 11, (57, 86, 300, 110)))  # 换页
    assert _continues(left, _p("and then the next step", 10, (57, 660, 300, 700)))  # 小写开头照旧
    assert not _continues(left, _p("Face community, thereby enhancing.", 10, (57, 660, 300, 700)))  # 同一栏往下：新段落
    assert not _continues(_p(long + " models.", 10, (57, 530, 300, 640)), _p("Face community, x.", 10, (333, 86, 540, 110)))  # 上一段已收尾
    assert not _continues(_p("Short heading", 10, (57, 530, 300, 540)), _p("Face community, x.", 10, (333, 86, 540, 110)))  # 上一段太短
