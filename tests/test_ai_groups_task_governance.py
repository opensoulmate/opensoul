"""B1 旧任务/子任务状态治理测试：历史pending清理+父任务收口，看板数据可信。

测的是 src/api/ai_groups.py::govern_stale_tasks/_finalize_parent_task 及其真实
接线点（list_tasks读取前惰性清扫、POST .../tasks/govern手动触发、_run_task_chain
链跑完收口父任务）。数据面换tmp（ai_groups.db）隔离，绝不写生产库。
"""

import asyncio
from datetime import datetime, timedelta

import pytest


@pytest.fixture()
def gov_env(tmp_path, monkeypatch):
    import src.api.ai_groups as ai_groups

    monkeypatch.setattr(ai_groups, "DB_PATH", tmp_path / "ai_groups.db")
    ai_groups.init_db()

    old_ts = (datetime.now() - timedelta(days=4)).isoformat()
    fresh_ts = datetime.now().isoformat()

    def add_group(gid="g-gov"):
        conn = ai_groups.get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO ai_groups (id,name,description,status,created_at) VALUES (?,?,?,?,?)",
                (gid, "gov-test", "", "active", old_ts),
            )
            conn.commit()
        finally:
            conn.close()

    def add_task(
        tid,
        parent=None,
        status="pending",
        goal="测试任务",
        created=None,
        updated=None,
        score=0.0,
    ):
        conn = ai_groups.get_db()
        try:
            ts = created or old_ts
            conn.execute(
                "INSERT INTO ai_group_tasks (id, group_id, parent_task_id, goal, status, quality_score, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (tid, "g-gov", parent, goal, status, score, ts, updated or ts),
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

    def govern_events(tid):
        conn = ai_groups.get_db()
        try:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM task_execution_events WHERE task_id=? AND event_type='governed' ORDER BY id",
                    (tid,),
                ).fetchall()
            ]
        finally:
            conn.close()

    add_group()
    return ai_groups, add_task, get_task, govern_events, old_ts, fresh_ts


class TestGovernRules:
    def test_r1_orphan_subtask_skipped_when_parent_finished(self, gov_env):
        """R1：父任务已终结的pending子任务（链永远轮不到跑）→ skipped"""
        ai_groups, add_task, get_task, govern_events, old_ts, _ = gov_env
        add_task("p1", status="scored", created=old_ts)
        add_task("s1", parent="p1", status="pending", created=old_ts)
        conn = ai_groups.get_db()
        try:
            stats = ai_groups.govern_stale_tasks(conn, "g-gov")
            conn.commit()
        finally:
            conn.close()
        assert get_task("s1")["status"] == "skipped"
        assert stats["skipped_parent_finished"] == 1
        evs = govern_events("s1")
        assert len(evs) == 1 and "parent_finished" in evs[0]["content"]

    def test_r2_stale_ttl_governs_old_open_tasks(self, gov_env):
        """R2：超龄未启动pending/planning/discussing → skipped；新鲜行不动"""
        ai_groups, add_task, get_task, _, old_ts, fresh_ts = gov_env
        add_task("old1", status="pending", created=old_ts)
        add_task("old2", status="planning", created=old_ts)
        add_task("fresh1", status="pending", created=fresh_ts)
        conn = ai_groups.get_db()
        try:
            stats = ai_groups.govern_stale_tasks(conn, "g-gov")
            conn.commit()
        finally:
            conn.close()
        assert get_task("old1")["status"] == "skipped"
        assert get_task("old2")["status"] == "skipped"
        assert get_task("fresh1")["status"] == "pending"
        assert stats["skipped_stale"] == 2

    def test_executing_never_killed(self, gov_env):
        """executing/reviewing永不强杀（卡死只提醒不杀任务）——超龄也不动"""
        ai_groups, add_task, get_task, _, old_ts, _ = gov_env
        add_task("run1", status="executing", created=old_ts)
        add_task("rev1", status="reviewing", created=old_ts)
        conn = ai_groups.get_db()
        try:
            ai_groups.govern_stale_tasks(conn, "g-gov")
            conn.commit()
        finally:
            conn.close()
        assert get_task("run1")["status"] == "executing"
        assert get_task("rev1")["status"] == "reviewing"

    def test_r3_parent_finalized_scored_with_avg(self, gov_env):
        """R3：父任务卡planning+子任务全终结 → scored（子任务均分）"""
        ai_groups, add_task, get_task, govern_events, _, fresh_ts = gov_env
        add_task("p1", status="planning", created=fresh_ts)
        add_task("s1", parent="p1", status="scored", created=fresh_ts, score=8.0)
        add_task("s2", parent="p1", status="scored", created=fresh_ts, score=6.0)
        conn = ai_groups.get_db()
        try:
            stats = ai_groups.govern_stale_tasks(conn, "g-gov")
            conn.commit()
        finally:
            conn.close()
        p = get_task("p1")
        assert p["status"] == "scored"
        assert p["quality_score"] == 7.0
        assert stats["parent_finalized"] == 1
        assert any("chain_finalized" in e["content"] for e in govern_events("p1"))

    def test_r3_parent_failed_when_sub_failed(self, gov_env):
        """R3：任一子任务failed → 父任务failed（不谎报scored）"""
        ai_groups, add_task, get_task, _, _, fresh_ts = gov_env
        add_task("p1", status="planning", created=fresh_ts)
        add_task("s1", parent="p1", status="scored", created=fresh_ts, score=8.0)
        add_task("s2", parent="p1", status="failed", created=fresh_ts)
        conn = ai_groups.get_db()
        try:
            ai_groups.govern_stale_tasks(conn, "g-gov")
            conn.commit()
        finally:
            conn.close()
        assert get_task("p1")["status"] == "failed"

    def test_r3_parent_skipped_when_all_subs_skipped(self, gov_env):
        """R3：子任务全被治理skipped → 父任务skipped（不算scored），单次扫描即收敛"""
        ai_groups, add_task, get_task, _, old_ts, fresh_ts = gov_env
        add_task("p1", status="planning", created=fresh_ts)  # 父任务新鲜（链还在窗口内）
        add_task("s1", parent="p1", status="pending", created=old_ts)  # 子任务超龄
        conn = ai_groups.get_db()
        try:
            ai_groups.govern_stale_tasks(conn, "g-gov")
            conn.commit()
        finally:
            conn.close()
        assert get_task("s1")["status"] == "skipped"
        assert get_task("p1")["status"] == "skipped"

    def test_parent_with_open_sub_not_finalized(self, gov_env):
        """R3不误收口：还有子任务没跑完的父任务保持原状态"""
        ai_groups, add_task, get_task, _, _, fresh_ts = gov_env
        add_task("p1", status="planning", created=fresh_ts)
        add_task("s1", parent="p1", status="scored", created=fresh_ts, score=8.0)
        add_task("s2", parent="p1", status="pending", created=fresh_ts)
        conn = ai_groups.get_db()
        try:
            ai_groups.govern_stale_tasks(conn, "g-gov")
            conn.commit()
        finally:
            conn.close()
        assert get_task("p1")["status"] == "planning"
        assert get_task("s2")["status"] == "pending"


class TestGovernWiring:
    def test_list_tasks_lazy_govern(self, gov_env):
        """接线：看板读取（list_tasks）即治理——历史pending不再冒充待处理"""
        ai_groups, add_task, get_task, govern_events, old_ts, _ = gov_env
        add_task("p1", status="scored", created=old_ts)
        add_task("s1", parent="p1", status="pending", created=old_ts)
        tasks = ai_groups.list_tasks("g-gov")
        assert isinstance(tasks, list)
        assert get_task("s1")["status"] == "skipped"
        assert govern_events("s1"), "治理必须留痕禁静默"

    def test_govern_endpoint(self, gov_env):
        """接线：POST /api/ai-groups/{gid}/tasks/govern 返回治理统计（非404）"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        ai_groups, add_task, get_task, _, old_ts, _ = gov_env
        add_task("p1", status="scored", created=old_ts)
        add_task("s1", parent="p1", status="pending", created=old_ts)
        app = FastAPI()
        app.include_router(ai_groups.router)
        with TestClient(app) as c:
            resp = c.post("/api/ai-groups/g-gov/tasks/govern")
            assert resp.status_code == 200
            data = resp.json()
            assert data["skipped_parent_finished"] == 1
        assert get_task("s1")["status"] == "skipped"

    def test_chain_finalizes_parent(self, gov_env, monkeypatch):
        """根因修复：_run_task_chain跑完链后收口父任务（不再永远卡planning）"""
        ai_groups, add_task, get_task, _, _, fresh_ts = gov_env
        add_task("p1", status="planning", created=fresh_ts)
        add_task("s1", parent="p1", status="pending", created=fresh_ts)
        add_task("s2", parent="p1", status="pending", created=fresh_ts)

        async def fake_impl(group_id, task_id, req):
            conn = ai_groups.get_db()
            try:
                conn.execute(
                    "UPDATE ai_group_tasks SET status='scored', quality_score=8.0, updated_at=? WHERE id=?",
                    (datetime.now().isoformat(), task_id),
                )
                conn.commit()
            finally:
                conn.close()
            return {"status": "scored"}

        import src.api.ai_groups as mod

        monkeypatch.setattr(mod, "_execute_task_impl", fake_impl)
        asyncio.run(mod._run_task_chain("g-gov", "p1"))
        p = get_task("p1")
        assert p["status"] == "scored"
        assert p["quality_score"] == 8.0
