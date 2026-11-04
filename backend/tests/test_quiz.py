def test_quiz_generation_validates_questions_and_evidence_and_saves_answers(imported):
    api, pid = imported
    assert api.get(f"/papers/{pid}/quizzes").json() == {"items": []}  # 不点“出题”就不会有任何调用
    assert not api.fake.requests
    made = api.post(f"/papers/{pid}/quizzes", {"count": 8, "kind": "mixed"}).json()
    qs = made["questions"]
    assert [q["type"] for q in qs] == ["choice", "judge"]  # 缺选项、答案越界、编造依据的题都被丢弃
    assert qs[1]["options"] == ["正确", "错误"] and qs[1]["answer"] == 1  # 判断题的选项固定，不信模型写的
    sent = api.fake.requests[-1]["body"]["messages"]
    assert "混合" in str(sent) and "理解" in str(sent)  # 请求里要求混合题型、考理解
    assert qs[0]["answer"] == 2 and qs[0]["options"] == ["选项甲", "选项乙", "选项丙", "选项丁"]
    assert qs[0]["evidence"] and qs[0]["evidence"]["page"] >= 1  # 依据是论文里真有的原文
    assert all(q["evidence"] for q in qs)  # 留下来的每道题都有核对过的原文依据
    assert not any("第二题" in q["question"] for q in qs)
    priv = api.get("/privacy/requests").json()["items"]
    assert any(i["task_type"] == "quiz" and "选择题" in i["summary"] for i in priv)

    saved = api.put(f"/quizzes/{made['id']}/answers", {"answers": {"0": 2, "1": 1, "9": 1, "2": 7, "3": 1}}).json()
    assert saved["answers"] == {"0": 2, "1": 1}  # 越界的题号、选项丢掉（第 2 题是判断题，只有两个选项）
    items = api.get(f"/papers/{pid}/quizzes").json()["items"]
    assert items[0]["id"] == made["id"] and items[0]["answers"] == {"0": 2, "1": 1}
    assert api.delete(f"/quizzes/{made['id']}").json() == {"ok": True}
    assert api.get(f"/papers/{pid}/quizzes").json() == {"items": []}


def test_quiz_count_is_bounded(imported):
    api, pid = imported
    assert api.post(f"/papers/{pid}/quizzes", {"count": 50}).status_code in (400, 422)
    assert api.post(f"/papers/{pid}/quizzes", {"count": 5, "kind": "essay"}).status_code in (400, 422)


def test_evidence_match_tolerates_layout_differences():
    from paperdesk.ai.quiz import _find_evidence

    paras = [{"page": 3, "ord": 7, "text": "Adapter methods intro-\nduce small neural modules between the layers, and only the “adapters” are trained."}]
    hit = _find_evidence("Adapter methods introduce small neural modules between the layers, and only the adapters are trained.", paras)
    assert hit and hit["page"] == 3 and hit["ord"] == 7
    assert _find_evidence("The model was trained on one million medical images from five hospitals.", paras) is None
    assert _find_evidence("too short", paras) is None
