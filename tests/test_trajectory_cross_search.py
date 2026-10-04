"""跨会话搜索（trajectory /search + /search/sessions）——P0参数哑火修复+分组真值。

覆盖：
① q/keyword参数容错（前端历史误传q→文本过滤被静默忽略的P0 bug回归钉）
② status过滤（"哪个session有error的tool_call"）
③ /search/sessions分组：match_count=GROUP BY真值（与/search交叉核对不许说谎）、
   最近命中样本、snippet截断显式标记、会话元数据JOIN、组序=最近命中降序
④ 组合过滤×limit×空结果
"""

import uuid

MARKER = f"needle-{uuid.uuid4().hex[:10]}"


def _mk_session(client, task):
    resp = client.post(
        "/api/trajectory/sessions",
        json={"agent_id": "test-agent", "task_description": task},
    )
    assert resp.status_code == 200
    return resp.json()["id"]


def _mk_event(client, session_id, *, content, event_type="tool_call", status="ok"):
    resp = client.post(
        f"/api/trajectory/sessions/{session_id}/events",
        json={
            "event_type": event_type,
            "agent_id": "test-agent",
            "content": content,
            "status": status,
        },
    )
    assert resp.status_code == 200
    return resp.json()["id"]


class TestSearchParamTolerance:
    """P0回归钉：q=曾被静默忽略（返回全量），现在q/keyword等效。"""

    def test_keyword_filters(self, client):
        sid = _mk_session(client, f"kw-param {MARKER}-kw")
        _mk_event(client, sid, content=f"hit {MARKER}-kw")
        _mk_event(client, sid, content="nope other words")
        resp = client.get(f"/api/trajectory/search?keyword={MARKER}-kw")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert MARKER in data["events"][0]["content"]
        assert data["filters"]["keyword"] == f"{MARKER}-kw"
        client.delete(f"/api/trajectory/sessions/{sid}")

    def test_q_alias_equal_to_keyword(self, client):
        sid = _mk_session(client, f"q-param {MARKER}-alias")
        _mk_event(client, sid, content=f"hit {MARKER}-alias")
        _mk_event(client, sid, content="unrelated payload")
        r_q = client.get(f"/api/trajectory/search?q={MARKER}-alias")
        r_kw = client.get(f"/api/trajectory/search?keyword={MARKER}-alias")
        assert r_q.status_code == 200 and r_kw.status_code == 200
        assert r_q.json()["count"] == 1
        assert r_q.json()["count"] == r_kw.json()["count"]
        # 回归钉：q=不存在词曾返回全量事件（bug形态），现在必须是0
        r_none = client.get("/api/trajectory/search?q=zzz-no-such-content-probe")
        assert r_none.status_code == 200
        assert r_none.json()["count"] == 0
        client.delete(f"/api/trajectory/sessions/{sid}")

    def test_keyword_wins_over_q(self, client):
        sid = _mk_session(client, f"both {MARKER}-both")
        _mk_event(client, sid, content=f"kw-side {MARKER}-both")
        _mk_event(client, sid, content="q-side other")
        resp = client.get(f"/api/trajectory/search?keyword={MARKER}-both&q=zzz-never")
        assert resp.json()["count"] == 1
        assert resp.json()["filters"]["keyword"] == f"{MARKER}-both"
        client.delete(f"/api/trajectory/sessions/{sid}")


class TestStatusFilter:
    def test_status_error_tool_call(self, client):
        """ "哪个session有error的tool_call"的事件面。"""
        sid = _mk_session(client, f"status {MARKER}-st")
        err_id = _mk_event(client, sid, content=f"boom {MARKER}-st", status="error")
        _mk_event(client, sid, content=f"fine {MARKER}-st", status="ok")
        _mk_event(
            client, sid, content=f"boom text {MARKER}-st", event_type="user_input", status="error"
        )
        resp = client.get(
            f"/api/trajectory/search?keyword={MARKER}-st&event_type=tool_call&status=error"
        )
        data = resp.json()
        assert data["count"] == 1
        assert data["events"][0]["id"] == err_id
        assert data["events"][0]["status"] == "error"
        assert data["filters"]["status"] == "error"
        client.delete(f"/api/trajectory/sessions/{sid}")

    def test_status_ok_only(self, client):
        sid = _mk_session(client, f"ok-only {MARKER}")
        _mk_event(client, sid, content=f"x {MARKER}-ok", status="ok")
        _mk_event(client, sid, content=f"y {MARKER}-ok", status="error")
        resp = client.get(f"/api/trajectory/search?keyword={MARKER}-ok&status=ok")
        assert resp.json()["count"] == 1
        client.delete(f"/api/trajectory/sessions/{sid}")

    def test_status_no_match(self, client):
        sid = _mk_session(client, f"no-match {MARKER}")
        _mk_event(client, sid, content=f"z {MARKER}-nm", status="ok")
        resp = client.get(f"/api/trajectory/search?keyword={MARKER}-nm&status=timeout")
        assert resp.json()["count"] == 0
        client.delete(f"/api/trajectory/sessions/{sid}")


class TestSearchBySession:
    def test_grouped_counts_and_metadata(self, client):
        sid = _mk_session(client, f"grouped-task {MARKER}-g")
        _mk_event(client, sid, content=f"g1 {MARKER}-g", status="error")
        _mk_event(client, sid, content=f"g2 {MARKER}-g", status="error")
        _mk_event(client, sid, content=f"g3 {MARKER}-g", status="ok")
        sid2 = _mk_session(client, f"grouped-task2 {MARKER}-g")
        _mk_event(client, sid2, content=f"g4 {MARKER}-g", status="error")

        resp = client.get(f"/api/trajectory/search/sessions?keyword={MARKER}-g&status=error")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        groups = {g["session_id"]: g for g in data["sessions"]}
        assert groups[sid]["match_count"] == 2
        assert groups[sid2]["match_count"] == 1
        assert groups[sid]["task_description"] == f"grouped-task {MARKER}-g"
        assert groups[sid]["agent_id"] == "test-agent"
        assert groups[sid]["session_status"] in ("running", "completed")
        # 样本=最近一条命中，字段齐全
        sample = groups[sid]["sample"]
        assert sample is not None
        assert sample["status"] == "error"
        assert sample["event_type"] == "tool_call"
        assert sample["snippet"]
        assert sample["sample_truncated"] is False
        assert groups[sid]["first_match_at"] <= groups[sid]["last_match_at"]

        # "指标不许说谎"：分组计数==事件面真实命中数（交叉核对）
        ev_resp = client.get(f"/api/trajectory/search?keyword={MARKER}-g&status=error&limit=200")
        assert ev_resp.json()["count"] == sum(g["match_count"] for g in data["sessions"])

        for s in (sid, sid2):
            client.delete(f"/api/trajectory/sessions/{s}")

    def test_group_order_latest_first(self, client):
        sid_old = _mk_session(client, f"order-old {MARKER}-o")
        _mk_event(client, sid_old, content=f"old {MARKER}-o")
        sid_new = _mk_session(client, f"order-new {MARKER}-o")
        _mk_event(client, sid_new, content=f"new {MARKER}-o")
        resp = client.get(f"/api/trajectory/search/sessions?keyword={MARKER}-o")
        order = [g["session_id"] for g in resp.json()["sessions"]]
        assert order.index(sid_new) < order.index(sid_old)
        for s in (sid_old, sid_new):
            client.delete(f"/api/trajectory/sessions/{s}")

    def test_snippet_truncation_flagged(self, client):
        sid = _mk_session(client, f"trunc {MARKER}-t")
        _mk_event(client, sid, content=f"{MARKER}-t " + "x" * 900)
        resp = client.get(f"/api/trajectory/search/sessions?keyword={MARKER}-t")
        g = resp.json()["sessions"][0]
        assert g["sample"]["sample_truncated"] is True
        assert len(g["sample"]["snippet"]) == 500
        client.delete(f"/api/trajectory/sessions/{sid}")

    def test_limit_caps_groups(self, client):
        sids = []
        for i in range(3):
            sid = _mk_session(client, f"cap {MARKER}-c{i}")
            _mk_event(client, sid, content=f"c{i} {MARKER}-cap")
            sids.append(sid)
        resp = client.get(f"/api/trajectory/search/sessions?keyword={MARKER}-cap&limit=2")
        data = resp.json()
        assert data["count"] == 2
        assert len(data["sessions"]) == 2
        for s in sids:
            client.delete(f"/api/trajectory/sessions/{s}")

    def test_empty_result(self, client):
        resp = client.get("/api/trajectory/search/sessions?keyword=zzz-no-such-probe-xyz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["sessions"] == []

    def test_session_id_filter(self, client):
        sid = _mk_session(client, f"selfilter {MARKER}-s")
        _mk_event(client, sid, content=f"s1 {MARKER}-s")
        sid2 = _mk_session(client, f"selfilter2 {MARKER}-s")
        _mk_event(client, sid2, content=f"s2 {MARKER}-s")
        resp = client.get(f"/api/trajectory/search/sessions?session_id={sid}&keyword={MARKER}-s")
        data = resp.json()
        assert data["count"] == 1
        assert data["sessions"][0]["session_id"] == sid
        for s in (sid, sid2):
            client.delete(f"/api/trajectory/sessions/{s}")

    def test_q_alias_on_grouped(self, client):
        sid = _mk_session(client, f"qgroup {MARKER}-qg")
        _mk_event(client, sid, content=f"qg {MARKER}-qg")
        resp = client.get(f"/api/trajectory/search/sessions?q={MARKER}-qg")
        assert resp.json()["count"] == 1
        client.delete(f"/api/trajectory/sessions/{sid}")
