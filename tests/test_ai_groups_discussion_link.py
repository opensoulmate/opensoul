"""B2 任务链↔讨论联动测试：任务执行完可发起讨论。

测的是 src/api/ai_groups.py::_start_post_task_discussion 及其真实接线点
（POST /{gid}/tasks/{tid}/discuss-after、_run_task_chain链跑完自动发起、
submit_task.discuss_after参数透传）。讨论任务挂source_task_id回链源任务，
respond/decide/messages既有讨论端点原样可用。数据面换tmp（ai_groups.db）隔离，
绝不写生产库。
"""

import asyncio
from datetime import datetime

import pytest


@pytest.fixture()
def disc_env(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.ai_groups as ai_groups

    monkeypatch.setattr(ai_groups, "DB_PATH", tmp_path / "ai_groups.db")
    ai_groups.init_db()

    app = FastAPI()
    # router自带prefix="/api/ai-groups"（src/api/ai_groups.py:20），这里不能再加prefix
    app.include_router(ai_groups.router)
    client = TestClient(app)

    now = datetime.now().isoformat()
    conn = ai_groups.get_db()
    try:
        conn.execute(
            "INSERT INTO ai_groups (id,name,description,status,created_at) VALUES (?,?,?,?,?)",
            ("g-disc", "disc-test", "", "active", now),
        )
        conn.commit()
    finally:
        conn.close()

    def add_task(tid, parent=None, status="scored", goal="测试任务", score=0.0, source=""):
        conn = ai_groups.get_db()
        try:
            conn.execute(
                "INSERT INTO ai_group_tasks (id, group_id, parent_task_id, goal, status, quality_score, source_task_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (tid, "g-disc", parent, goal, status, score, source, now, now),
            )
            conn.commit()
        finally:
            conn.close()

    def get_task(tid):
        conn = ai_groups.get_db()
        try:
            r = conn.execute("SELECT * FROM ai_group_tasks WHERE id=?", (tid,)).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()

    def task_events(tid, etype=None):
        conn = ai_groups.get_db()
        try:
            sql = "SELECT * FROM task_execution_events WHERE task_id=?"
            args = [tid]
            if etype:
                sql += " AND event_type=?"
                args.append(etype)
            return [dict(r) for r in conn.execute(sql + " ORDER BY id", args).fetchall()]
        finally:
            conn.close()

    return ai_groups, client, add_task, get_task, task_events


class TestDiscussAfterEndpoint:
    def test_creates_linked_discussion_with_summary(self, disc_env):
        """核心：已终结任务→复盘讨论（挂source_task_id+系统消息带结果摘要）"""
        ai_groups, client, add_task, get_task, task_events = disc_env
        add_task("p1", status="scored", goal="修复登录bug", score=8.0)
        add_task("s1", parent="p1", status="scored", goal="规划：方案设计", score=8.0)
        add_task("s2", parent="p1", status="failed", goal="执行：代码实现", score=0.0)

        resp = client.post("/api/ai-groups/g-disc/tasks/p1/discuss-after")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "discussing"
        assert data["source_task_id"] == "p1"
        assert data["existing"] is False
        did = data["discussion_task_id"]

        d = get_task(did)
        assert d is not None
        assert d["status"] == "discussing"
        assert d["source_task_id"] == "p1"
        assert d["parent_task_id"] is None  # 不是子任务，禁被R1孤儿治理误杀
        assert "复盘讨论" in d["goal"]

        # 系统消息带结果摘要：子任务goal→status(score)逐行不截断
        conn = ai_groups.get_db()
        try:
            msgs = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM discussion_messages WHERE task_id=? ORDER BY id", (did,)
                ).fetchall()
            ]
        finally:
            conn.close()
        assert len(msgs) == 1
        content = msgs[0]["content"]
        assert msgs[0]["agent_id"] == "system"
        assert "规划：方案设计 → scored（8.0分）" in content
        assert "执行：代码实现 → failed（0.0分）" in content
        assert "状态=scored" in content

        # 可观测：源任务事件流留痕discussion-started
        evs = task_events("p1", "discussion-started")
        assert len(evs) == 1 and did in evs[0]["content"]

    def test_not_terminal_rejected(self, disc_env):
        """没跑完不许开复盘：planning/pending/executing→409"""
        ai_groups, client, add_task, _, _ = disc_env
        add_task("p2", status="planning", goal="未完成任务")
        resp = client.post("/api/ai-groups/g-disc/tasks/p2/discuss-after")
        assert resp.status_code == 409
        add_task("p3", status="executing", goal="执行中任务")
        resp = client.post("/api/ai-groups/g-disc/tasks/p3/discuss-after")
        assert resp.status_code == 409

    def test_unknown_task_404(self, disc_env):
        ai_groups, client, _, _, _ = disc_env
        resp = client.post("/api/ai-groups/g-disc/tasks/nope/discuss-after")
        assert resp.status_code == 404

    def test_idempotent_no_duplicate_discussion(self, disc_env):
        """幂等：二次发起返回既有讨论，禁重复开讨论"""
        ai_groups, client, add_task, get_task, _ = disc_env
        add_task("p4", status="scored", goal="幂等测试", score=6.0)
        r1 = client.post("/api/ai-groups/g-disc/tasks/p4/discuss-after").json()
        r2 = client.post("/api/ai-groups/g-disc/tasks/p4/discuss-after").json()
        assert r2["existing"] is True
        assert r2["discussion_task_id"] == r1["discussion_task_id"]
        conn = ai_groups.get_db()
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM ai_group_tasks WHERE source_task_id='p4'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert n == 1  # 只有一条讨论任务

    def test_custom_goal(self, disc_env):
        ai_groups, client, add_task, get_task, _ = disc_env
        add_task("p5", status="failed", goal="部署上线", score=0.0)
        resp = client.post(
            "/api/ai-groups/g-disc/tasks/p5/discuss-after", json={"goal": "自定义复盘主题"}
        )
        assert resp.status_code == 200
        assert get_task(resp.json()["discussion_task_id"])["goal"] == "自定义复盘主题"


class TestDiscussionTaskChainLoop:
    def test_existing_discussion_endpoints_work_on_linked_discussion(self, disc_env):
        """联动闭环：复盘讨论可用既有respond/decide端点，decide派生子任务"""
        ai_groups, client, add_task, get_task, _ = disc_env
        add_task("p6", status="scored", goal="联动测试", score=7.0)
        did = client.post("/api/ai-groups/g-disc/tasks/p6/discuss-after").json()[
            "discussion_task_id"
        ]
        r = client.post(
            f"/api/ai-groups/g-disc/discuss/{did}/respond",
            json={"agent_id": "soulmate", "intent": "suggest", "content": "建议补集成测试"},
        )
        assert r.status_code == 200
        r = client.post(
            f"/api/ai-groups/g-disc/discuss/{did}/decide",
            json={"assignments": [{"agent_id": "soulmate", "subgoal": "补集成测试"}]},
        )
        assert r.status_code == 200
        conn = ai_groups.get_db()
        try:
            subs = conn.execute(
                "SELECT * FROM ai_group_tasks WHERE parent_task_id=?", (did,)
            ).fetchall()
        finally:
            conn.close()
        assert len(subs) == 1 and subs[0]["goal"] == "补集成测试"
        msgs = client.get(f"/api/ai-groups/g-disc/discuss/{did}/messages").json()
        assert len(msgs) >= 2  # 系统摘要 + agent回复

    def test_chain_auto_discussion_on_finish(self, disc_env, monkeypatch):
        """B2联动真接线：_run_task_chain跑完链自动发起复盘讨论"""
        ai_groups, add_task, get_task = disc_env[0], disc_env[2], disc_env[3]
        add_task("p7", status="planning", goal="链联动测试")
        add_task("s1", parent="p7", status="pending", goal="规划：需求分析")
        add_task("s2", parent="p7", status="pending", goal="执行：编码")

        async def fake_impl(group_id, task_id, req):
            conn = ai_groups.get_db()
            try:
                conn.execute(
                    "UPDATE ai_group_tasks SET status='scored', quality_score=9.0, updated_at=? WHERE id=?",
                    (datetime.now().isoformat(), task_id),
                )
                conn.commit()
            finally:
                conn.close()
            return {"status": "scored"}

        monkeypatch.setattr(ai_groups, "_execute_task_impl", fake_impl)
        asyncio.run(ai_groups._run_task_chain("g-disc", "p7", discuss_after=True))

        p = get_task("p7")
        assert p["status"] == "scored"  # 父任务照常收口
        conn = ai_groups.get_db()
        try:
            ds = conn.execute("SELECT * FROM ai_group_tasks WHERE source_task_id='p7'").fetchall()
        finally:
            conn.close()
        assert len(ds) == 1 and ds[0]["status"] == "discussing"
        assert ds[0]["goal"].startswith("复盘讨论")

    def test_chain_without_flag_no_discussion(self, disc_env, monkeypatch):
        """默认不开讨论：discuss_after=False不产生讨论任务（不惊扰存量流程）"""
        ai_groups, add_task, _get_task = disc_env[0], disc_env[2], disc_env[3]
        add_task("p8", status="planning", goal="默认无讨论")
        add_task("s1", parent="p8", status="pending", goal="执行：编码")

        async def fake_impl(group_id, task_id, req):
            conn = ai_groups.get_db()
            try:
                conn.execute(
                    "UPDATE ai_group_tasks SET status='scored', quality_score=5.0, updated_at=? WHERE id=?",
                    (datetime.now().isoformat(), task_id),
                )
                conn.commit()
            finally:
                conn.close()
            return {"status": "scored"}

        monkeypatch.setattr(ai_groups, "_execute_task_impl", fake_impl)
        asyncio.run(ai_groups._run_task_chain("g-disc", "p8"))
        conn = ai_groups.get_db()
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM ai_group_tasks WHERE source_task_id='p8'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert n == 0

    def test_submit_task_passes_discuss_after(self, disc_env, monkeypatch):
        """接线验证：submit_task把discuss_after透传给_run_task_chain"""
        ai_groups, client = disc_env[0], disc_env[1]
        captured = {}

        async def fake_chain(group_id, task_id, discuss_after=False):
            captured["discuss_after"] = discuss_after

        monkeypatch.setattr(ai_groups, "_run_task_chain", fake_chain)
        resp = client.post(
            "/api/ai-groups/g-disc/tasks",
            json={"goal": "分析并实现登录模块", "discuss_after": True},
        )
        assert resp.status_code == 200
        assert captured.get("discuss_after") is True
