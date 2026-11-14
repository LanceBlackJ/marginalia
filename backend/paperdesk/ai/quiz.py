"""读完之后的自测：让 AI 按这篇论文出选择题（只在用户点“出题”时才发请求），答题记录存在本机。

- 出题只依据这篇论文的正文段落（按位置均匀取样，总量有上限），发往云端的字数记入隐私日志；
- 每道题要求给出一句原文依据，后端核对这句话确实出现在论文里；核对不上的题不要（多出一些题、不够再补一轮），实在凑不够才如实少给几道；
- 题目格式不合格（选项数不对、答案越界）的丢弃，一道都没有就报错，不拿残缺的题凑数。
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

from ..context import AppContext
from .quiz_prompt import SYSTEM
from .settings import context_scale, load_settings
from ..db import register_schema
from ..llmjson import ask_json
from ..util import ApiError, utcnow
from ..ai.providers import LLMError

register_schema("quiz", """
CREATE TABLE IF NOT EXISTS quiz_sets (
  id INTEGER PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '',
  questions_json TEXT NOT NULL,
  answers_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_quiz_paper ON quiz_sets(paper_id, created_at);
""")

MAX_CONTEXT_CHARS = 14000
MIN_COUNT, MAX_COUNT = 3, 20
KINDS = ('mixed', 'choice', 'multi', 'judge')
JUDGE_OPTIONS = ['正确', '错误']

def _passages(app: AppContext, paper_id: str) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT p.ord, p.page, p.text, s.title AS section FROM passages p LEFT JOIN sections s ON s.id = p.section_id "
            "WHERE p.paper_id = ? AND p.kind = 'paragraph' ORDER BY p.ord", (paper_id,)
        ).fetchall()
    out = []
    for r in rows:
        t = " ".join((r["text"] or "").split())
        if len(t) >= 120 and (r["section"] or "").strip().lower() not in {"references", "bibliography", "参考文献", "acknowledgements", "acknowledgments"}:
            out.append({"ord": r["ord"], "page": r["page"], "text": t, "section": r["section"] or ""})
    return out


def _sample(paras: list[dict], limit: int = MAX_CONTEXT_CHARS) -> list[dict]:
    """按位置均匀取样，总字数不超过上限：摘要/引言、方法、实验、结论都能覆盖到。"""
    total = sum(len(p["text"]) for p in paras)
    if total <= limit:
        return paras
    step = total / limit
    out, acc, nxt = [], 0.0, 0.0
    for p in paras:
        if acc >= nxt:
            out.append(p)
            nxt += step * len(p["text"])
        acc += len(p["text"])
    return out or paras[:20]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _squash(s: str) -> str:
    """只留字母数字，抹平空格、连字符断行、引号和标点的差异。"""
    return re.sub(r"[\W_]+", "", s.lower())


def _find_evidence(ev: str, paras: list[dict]) -> dict | None:
    """模型摘的依据必须真在原文里：先整句包含，再容忍排版差异（空格、断词连字符、标点），最后允许极少量字符不同。"""
    if len(ev) < 12:
        return None
    needle = _squash(ev)
    if len(needle) < 10:
        return None
    for p in paras:
        if needle in _squash(p["text"]):
            return {"quote": ev, "page": p["page"], "ord": p["ord"]}
    for p in paras:
        hay = _squash(p["text"])
        m = SequenceMatcher(None, hay, needle, autojunk=False).find_longest_match(0, len(hay), 0, len(needle))
        if m.size >= 0.9 * len(needle):
            return {"quote": ev, "page": p["page"], "ord": p["ord"]}
    return None


def too_alike(options: list[str]) -> bool:
    """选项是不是“长得一样”：两两太像（只差一两个词），或者大半选项共用同一个开头（同一个句式套着换词）。"""
    sq = [_squash(o) for o in options]
    for i in range(len(sq)):
        for j in range(i + 1, len(sq)):
            if sq[i] == sq[j] or SequenceMatcher(None, sq[i], sq[j]).ratio() >= 0.8:
                return True
    heads = [x[:4] for x in sq if len(x) >= 8]
    return bool(heads) and max(heads.count(h) for h in set(heads)) > max(2, len(options) // 2)


def _clean(q: dict, paras: list[dict]) -> dict | None:
    t = str(q.get("type") or "choice").lower()
    kind = t if t in ("judge", "multi") else "choice"
    opts = q.get("options")
    ans = q.get("answer")
    if kind == "judge":
        options = list(JUDGE_OPTIONS)  # 判断题的选项固定，不信模型写的
        if not isinstance(ans, int) or ans not in (0, 1):
            return None
    else:
        lo, hi = (4, 7) if kind == "multi" else (4, 4)  # 单选正好 4 个，多选 4 到 7 个
        if not isinstance(opts, list) or not lo <= len(opts) <= hi or not all(isinstance(o, str) and o.strip() for o in opts):
            return None
        if kind == "multi":
            if not isinstance(ans, list) or not all(isinstance(x, int) and not isinstance(x, bool) for x in ans):
                return None
            ans = sorted(set(ans))
            if not 2 <= len(ans) < len(opts) or ans[0] < 0 or ans[-1] >= len(opts):
                return None  # 多选至少 2 个正确、不能全对
        elif not isinstance(ans, int) or isinstance(ans, bool) or not 0 <= ans < len(opts):
            return None
        options = [re.sub(r"^\s*[A-Ha-h][.、．:：)）]\s*", "", o).strip() for o in opts]
        if any(re.fullmatch(r"(以上|上述|其(他|它)).{0,6}(都|均)?(对|正确|错误|不对|皆是)?", o) for o in options) or too_alike(options):
            return None  # “以上都对”之类的凑数选项；选项重复或长得几乎一样
    stem = str(q.get("question") or "").strip()
    if len(stem) < 6:
        return None
    ev = " ".join(str(q.get("evidence") or "").split())
    evidence = _find_evidence(ev, paras)
    return {"type": kind, "question": stem, "options": options, "answer": ans, "explanation": str(q.get("explanation") or "").strip(), "evidence": evidence}


async def generate(app: AppContext, paper_id: str, count: int, kind: str = 'mixed') -> dict:
    count = max(MIN_COUNT, min(MAX_COUNT, count))
    kind = kind if kind in KINDS else 'mixed'
    with app.db.session() as conn:
        paper = conn.execute("SELECT title FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if paper is None:
        raise ApiError("not_found", "论文不存在", 404)
    paras = _passages(app, paper_id)
    if len(paras) < 3:
        raise ApiError("bad_request", "这篇论文的正文太少，出不了题（可能还没解析完，或只有元数据）。")
    ctx = _sample(paras, int(MAX_CONTEXT_CHARS * context_scale(load_settings(app))))
    body = "\n\n".join(f"[{p['section'] or '正文'}] {p['text']}" for p in ctx)
    want = {
        "mixed": f"请出 {count} 道题，三种题型混合：单选题约一半，多选题约四分之一，判断题约四分之一。",
        "choice": f"请出 {count} 道单项选择题（type 全部是 choice，每题正好 4 个选项）。",
        "multi": f"请出 {count} 道多项选择题（type 全部是 multi，每题 4 到 7 个选项，正确的至少 2 个、不能全对）。",
        "judge": f"请出 {count} 道判断题（type 全部是 judge）。",
    }[kind]
    verified: list[dict] = []
    loose: list[dict] = []  # 格式合格但依据没核对上的，只在一道有依据的都没有时才报错用
    seen: set[str] = set()
    last_err: ApiError | None = None
    for _round in range(2):
        need = count - len(verified)
        if need <= 0:
            break
        ask_n = need + max(2, (need + 1) // 2)  # 多要一些，核对不上的会被淘汰
        ask = want.replace(f"{count}", str(ask_n), 1)
        avoid = ""
        if verified:
            avoid = "\n已经出过的题（不要重复）：\n" + "\n".join(f"- {q['question']}" for q in verified)
        user = f"论文标题：{paper['title']}\n{ask}\n每道题的 evidence 必须是论文原文里连续的一句话，逐字照抄，不要改写、不要翻译。{avoid}\n\n论文内容：\n{body}"
        try:
            data = await ask_json(
                app, system=SYSTEM, user=user, task_type="quiz", tier="strong", max_tokens=800 + 900 * ask_n, paper_id=paper_id, retries=1,
                includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": False},
                summary=f"发送了论文《{paper['title'][:40]}》约 {len(user):,} 字的正文，用于出 {ask_n} 道题（选择题/判断题）",
            )
        except LLMError as err:
            last_err = ApiError(err.code, err.message, err.status)
            break
        raw = data.get("questions") if isinstance(data, dict) else None
        for q in raw or []:
            c = _clean(q, paras) if isinstance(q, dict) else None
            if not c or c["question"] in seen:
                continue
            seen.add(c["question"])
            (verified if c["evidence"] else loose).append(c)
    questions = verified[:count]
    if not questions:
        if last_err:
            raise last_err
        raise ApiError("provider_error", "模型给出的题目都找不到原文依据，没有保存。请重试。" if loose else "模型没有给出合格的题目，请重试。")
    with app.db.session() as conn:
        cur = conn.execute("INSERT INTO quiz_sets(paper_id, created_at, model, questions_json) VALUES (?,?,?,?)",
                           (paper_id, utcnow(), "", json.dumps(questions, ensure_ascii=False)))
        qid = cur.lastrowid
    return get_set(app, qid)


def _row(r) -> dict:
    return {"id": r["id"], "paper_id": r["paper_id"], "created_at": r["created_at"], "questions": json.loads(r["questions_json"]),
            "answers": {int(k): v for k, v in json.loads(r["answers_json"] or "{}").items()}}


def get_set(app: AppContext, quiz_id: int) -> dict:
    with app.db.session() as conn:
        r = conn.execute("SELECT * FROM quiz_sets WHERE id = ?", (quiz_id,)).fetchone()
    if r is None:
        raise ApiError("not_found", "这套题不存在", 404)
    return _row(r)


def list_sets(app: AppContext, paper_id: str, limit: int = 20) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute("SELECT * FROM quiz_sets WHERE paper_id = ? ORDER BY id DESC LIMIT ?", (paper_id, limit)).fetchall()
    return [_row(r) for r in rows]


def save_answers(app: AppContext, quiz_id: int, answers: dict[int, int | list[int]]) -> dict:
    """作答：单选和判断题是一个选项下标，多选题是选中的下标列表。越界的丢掉。"""
    qs = get_set(app, quiz_id)
    n = len(qs["questions"])
    clean: dict[int, int | list[int]] = {}
    for k, v in answers.items():
        k = int(k)
        if not 0 <= k < n:
            continue
        n_opt = len(qs["questions"][k]["options"])
        if qs["questions"][k].get("type") == "multi":
            picked = sorted({int(x) for x in v} if isinstance(v, list) else {int(v)})
            if picked and all(0 <= x < n_opt for x in picked):
                clean[k] = picked
        elif not isinstance(v, list) and 0 <= int(v) < n_opt:
            clean[k] = int(v)
    with app.db.session() as conn:
        conn.execute("UPDATE quiz_sets SET answers_json = ? WHERE id = ?", (json.dumps(clean), quiz_id))
    return get_set(app, quiz_id)


def delete_set(app: AppContext, quiz_id: int) -> None:
    with app.db.session() as conn:
        conn.execute("DELETE FROM quiz_sets WHERE id = ?", (quiz_id,))
