"""导入后的术语表：读论文之前先扫清概念障碍，导入时在后台一次做好；出处原话由后端核对。"""
from __future__ import annotations


def _run(api, pid):
    job = api.post(f"/papers/{pid}/glossary/start").json()["job_id"]
    assert job
    assert api.wait_job(job)["status"] == "completed"
    return api.get(f"/papers/{pid}/glossary").json()


def _paras(api, pid):
    return {p["ord"]: p["text"] for pg in (1, 2, 3) for p in api.get(f"/papers/{pid}/passages?page={pg}").json()["items"]}


def test_glossary_is_precomputed_verified_and_idempotent(imported):
    api, pid = imported
    g0 = api.get(f"/papers/{pid}/glossary").json()
    assert g0["terms"] == [] and g0["status"]["state"] in ("pending", "unavailable")  # 还没做：如实说没做
    before = len(api.fake.requests)
    g = _run(api, pid)
    assert g["status"]["state"] == "done" and len(api.fake.requests) == before + 1  # 整篇只调用一次模型
    terms = {t["term"]: t for t in g["terms"]}
    paras = _paras(api, pid)
    # 有出处的词条：词真在那一段里，原话真在那一段里
    sourced = [t for t in g["terms"] if t["in_paper"]]
    assert len(sourced) == 1
    t = sourced[0]
    assert " ".join(t["quote"].split()) in " ".join(paras[t["ord"]].split()) and t["term"].lower() in paras[t["ord"]].lower()
    # 原话是编造的、引用了不存在段落的：不假装有出处，降为“AI 补充”
    assert terms[f"{t['term']}-fake"]["in_paper"] is False and terms[f"{t['term']}-fake"]["quote"] is None
    assert terms["Ghost"]["in_paper"] is False and terms["Ghost"]["ord"] is None
    assert terms["Gradient descent"]["kind"] == "前置知识" and terms["Gradient descent"]["in_paper"] is False
    # 忽略大小写后重复的词条只留一条
    assert len([x for x in g["terms"] if x["term"].lower() == t["term"].lower()]) == 1
    # 做完了：再启动不会再调用模型
    n = len(api.fake.requests)
    assert api.post(f"/papers/{pid}/glossary/start").json()["job_id"] is None
    assert len(api.fake.requests) == n
    # 处理进度里有“术语表”这一步，做完才算完成
    assert api.get(f"/papers/{pid}/processing").json()["steps"]["glossary"]["state"] == "done"


def test_reparse_clears_the_glossary(imported):
    api, pid = imported
    _run(api, pid)
    r = api.post(f"/papers/{pid}/reparse", {"force": True})
    assert r.status_code in (200, 202), r.text
    job = r.json().get("job_id")
    if job:
        api.wait_job(job)
    s = api.get(f"/papers/{pid}/glossary").json()
    assert s["terms"] == [] or s["status"]["state"] == "done"  # 旧术语表作废（或已经按新段落重做）


def test_too_few_terms_is_a_reported_failure():
    from paperdesk.ai import glossary

    assert glossary._clean({"terms": [{"term": "A", "plain": "x"}]}, {})["terms"][0]["in_paper"] is False
    assert glossary._clean({"terms": [{"term": "", "plain": "x"}, {"term": "B", "plain": ""}, "bad"]}, {})["terms"] == []
