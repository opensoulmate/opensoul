"""DEC-④ 推荐回流面消费点 —— advisory_review 定期行为（建议默认 B，自决例外记
pending-decisions.md ④）。

覆盖：
1. advisory_task_goal 确定性派生（动态字段不进 goal，防绕过 at-most-once 去重）
2. ai_groups.submit_advisory_task 消费面（上板+回群聊、at-most-once、组行继承租户）
3. proactive_behaviors.advisory_review 编排（无群组空转可观测、top_k、多组、
   单组单条失败不掀翻、推荐读取失败如实抛）
4. 接线面（register_default_watchers 第五行为 + run_watcher 真消费）

数据面全 tmp（ai_groups.db+learned_patterns 同 tmp、GROUP_MSG_PUSH 死端口），
绝不写生产库/绝不真推送群聊。
"""

import asyncio
import json
import sqlite3
import sys
from functools import partial
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api import ai_groups
from src.learn.long_term import LongTermLearning
from src.will import proactive_behaviors as pb_mod
from src.will.proactive import ProactiveEngine


# 容缺取符号（控制组旧代码无此符号时按 AttributeError 炸在行为断言上，而非
# 收集期 ImportError——控制组语义内建，test_a2a_cancel_semantics.py 同款）。
def _missing(name: str):
    def _f(*args, **kwargs):
        raise AttributeError(f"old code has no {name}")

    return _f


advisory_review = getattr(pb_mod, "advisory_review", _missing("advisory_review"))
advisory_task_goal = getattr(pb_mod, "advisory_task_goal", _missing("advisory_task_goal"))
submit_advisory_task = getattr(ai_groups, "submit_advisory_task", _missing("submit_advisory_task"))


class _AsyncDB:
    """生产适配器注入（test_evolve_recommendation_backflow.py 同款：测试即生产路径）。"""

    def __init__(self, path: Path):
        self._path = str(path)

    async def _wrapper(self):
        import aiosqlite

        from src.database.postgres import _SQLiteConnection

        conn = await aiosqlite.connect(self._path)
        conn.row_factory = sqlite3.Row
        return _SQLiteConnection(conn), conn

    async def fetch(self, query, *args):
        wrapper, conn = await self._wrapper()
        try:
            return await wrapper.fetch(query, *args)
        finally:
            await conn.close()

    async def execute(self, query, *args):
        wrapper, conn = await self._wrapper()
        try:
            return await wrapper.execute(query, *args)
        finally:
            await conn.close()

    async def fetchval(self, query, *args):
        wrapper, conn = await self._wrapper()
        try:
            return await wrapper.fetchval(query, *args)
        finally:
            await conn.close()


@pytest.fixture()
def grp_db(tmp_path, monkeypatch):
    """tmp ai_groups.db + 群聊推送死端口（绝不真推送）。"""
    db = tmp_path / "ai_groups.db"
    monkeypatch.setattr(ai_groups, "DB_PATH", db)
    monkeypatch.setattr(ai_groups, "GROUP_MSG_PUSH", "http://127.0.0.1:9/disabled")
    ai_groups.init_db()
    return db


def _add_group(db_path, gid="g1", name="改进组", tenant="default", created="2026-10-14T00:00:00"):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO ai_groups (id,name,description,status,tenant_id,created_at) VALUES (?,?,?,?,?,?)",
        (gid, name, "", "active", tenant, created),
    )
    conn.commit()
    conn.close()


def _seed_recs(db, n=2, prefix="失败模式"):
    """往 tmp learned_patterns 播 n 条推荐（default/default=ADVISORY_SCOPE 同口径）。"""

    async def _run():
        lt = LongTermLearning(db, "default", "default")
        await lt._ensure_table()
        for i in range(n):
            await lt._upsert_pattern(
                "failure",
                f"{prefix}{i}",
                {"common_fixes": [f"修复{i}"]},
                0.9 - i * 0.1,
                10 - i,
            )

    asyncio.run(_run())


def _task_rows(db_path, group_id=None):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if group_id:
        rows = conn.execute(
            "SELECT * FROM ai_group_tasks WHERE group_id=? ORDER BY goal", (group_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM ai_group_tasks ORDER BY group_id, goal").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 1. goal 派生纯函数 ──────────────────────────────────────────────


class TestGoalRenderer:
    def test_dynamic_fields_not_in_goal(self):
        """置信度/样本/pattern_data 变化不得改变 goal（防绕过 at-most-once 去重）。"""
        rec = {
            "type": "failure",
            "key": "查看当前目录",
            "data": {"common_fixes": ["a"]},
            "confidence": 0.9,
            "samples": 5,
        }
        g1 = advisory_task_goal(rec)
        rec2 = {
            "type": "failure",
            "key": "查看当前目录",
            "data": {"common_fixes": ["b"]},
            "confidence": 0.33,
            "samples": 99,
        }
        assert g1 == advisory_task_goal(rec2)
        assert "failure" in g1 and "查看当前目录" in g1
        assert "0.9" not in g1 and "5" not in g1 and "0.33" not in g1

    def test_identity_segment(self):
        assert advisory_task_goal({"type": "strategy", "key": "k"}) == "[改进建议] strategy·k"

    def test_missing_fields_fallback(self):
        assert advisory_task_goal({}) == "[改进建议] ?·?"


# ── 2. submit_advisory_task 消费面 ──────────────────────────────────


class TestSubmitAdvisoryTask:
    def test_group_missing(self, grp_db):
        res = asyncio.run(submit_advisory_task("nope", "[改进建议] x", {"a": 1}))
        assert res == {"created": False, "reason": "group_missing"}

    def test_row_tenant_inherits_group(self, grp_db):
        """行租户键=组行继承（挂 'default' 会让组主租户看板看不到=自我不可见）。"""
        _add_group(grp_db, "g1", tenant="tenant-a")
        res = asyncio.run(submit_advisory_task("g1", "[改进建议] failure·k", {"x": 1}))
        assert res["created"] is True
        rows = _task_rows(grp_db)
        assert len(rows) == 1
        row = rows[0]
        assert row["tenant_id"] == "tenant-a"
        assert row["status"] == "planning"
        assert row["id"] == res["task_id"]
        assert json.loads(row["constraints"]) == {"x": 1}

    def test_at_most_once(self, grp_db):
        _add_group(grp_db, "g1")
        r1 = asyncio.run(submit_advisory_task("g1", "[改进建议] failure·k", {}))
        r2 = asyncio.run(submit_advisory_task("g1", "[改进建议] failure·k", {}))
        assert r1["created"] is True
        assert r2["created"] is False
        assert r2["reason"] == "already_on_board"
        assert r2["task_id"] == r1["task_id"]
        assert len(_task_rows(grp_db)) == 1

    def test_chat_message_with_source(self, grp_db):
        _add_group(grp_db, "g1")
        res = asyncio.run(submit_advisory_task("g1", "[改进建议] failure·k", {"x": 1}))
        conn = sqlite3.connect(str(grp_db))
        conn.row_factory = sqlite3.Row
        msgs = conn.execute("SELECT * FROM discussion_messages").fetchall()
        conn.close()
        assert len(msgs) == 1
        msg = dict(msgs[0])
        assert msg["task_id"] == res["task_id"]
        assert msg["group_id"] == "g1"
        assert "[改进建议] failure·k" in msg["content"]
        assert json.loads(msg["metadata"])["source"] == "advisory_review"


# ── 3. advisory_review 编排 ─────────────────────────────────────────


class TestAdvisoryReview:
    def test_no_groups_zero_side_effect(self, grp_db, tmp_path):
        db = _AsyncDB(tmp_path / "learn.db")
        _seed_recs(db, 2)
        res = asyncio.run(advisory_review(db_pool=db))
        assert res["reason"] == "no_groups"
        assert res["created"] == 0 and res["groups"] == 0
        assert res["recommendations"] == 2  # 推荐读到了，只是没消费面
        assert _task_rows(grp_db) == []  # 硬反证：零任务行

    def test_creates_tasks_on_board(self, grp_db, tmp_path):
        db = _AsyncDB(tmp_path / "learn.db")
        _seed_recs(db, 2)
        _add_group(grp_db, "g1")
        res = asyncio.run(advisory_review(db_pool=db))
        assert res["created"] == 2
        assert res["skipped_existing"] == 0 and res["errors"] == []
        goals = {r["goal"] for r in _task_rows(grp_db)}
        assert goals == {"[改进建议] failure·失败模式0", "[改进建议] failure·失败模式1"}

    def test_top_k_cap(self, grp_db, tmp_path):
        db = _AsyncDB(tmp_path / "learn.db")
        _seed_recs(db, 5)
        _add_group(grp_db, "g1")
        res = asyncio.run(advisory_review(top_k=2, db_pool=db))
        assert res["created"] == 2
        assert len(_task_rows(grp_db)) == 2

    def test_multi_group_each_gets_tasks(self, grp_db, tmp_path):
        db = _AsyncDB(tmp_path / "learn.db")
        _seed_recs(db, 2)
        _add_group(grp_db, "g1", created="2026-10-14T00:00:00")
        _add_group(grp_db, "g2", created="2026-10-14T00:00:01")
        res = asyncio.run(advisory_review(db_pool=db))
        assert res["created"] == 4
        assert len(_task_rows(grp_db, "g1")) == 2
        assert len(_task_rows(grp_db, "g2")) == 2

    def test_second_run_at_most_once(self, grp_db, tmp_path):
        db = _AsyncDB(tmp_path / "learn.db")
        _seed_recs(db, 3)
        _add_group(grp_db, "g1")
        r1 = asyncio.run(advisory_review(db_pool=db))
        r2 = asyncio.run(advisory_review(db_pool=db))
        assert r1["created"] == 3
        assert r2["created"] == 0
        assert r2["skipped_existing"] == 3
        assert len(_task_rows(grp_db)) == 3  # 任务数不变

    def test_single_group_failure_not_blocking(self, grp_db, monkeypatch, tmp_path):
        db = _AsyncDB(tmp_path / "learn.db")
        _seed_recs(db, 1)
        _add_group(grp_db, "g1", created="2026-10-14T00:00:00")
        _add_group(grp_db, "g2", created="2026-10-14T00:00:01")
        real = ai_groups.submit_advisory_task
        calls = []

        async def _wrapper(group_id, goal, detail):
            calls.append(group_id)
            if group_id == "g1":
                raise RuntimeError("boom")
            return await real(group_id, goal, detail)

        monkeypatch.setattr(ai_groups, "submit_advisory_task", _wrapper)
        res = asyncio.run(advisory_review(db_pool=db))
        assert calls == ["g1", "g2"]  # 失败不掀翻其余
        assert res["created"] == 1
        assert len(res["errors"]) == 1 and "boom" in res["errors"][0]
        assert len(_task_rows(grp_db, "g2")) == 1

    def test_recommend_read_failure_raises(self, grp_db):
        """推荐读取失败如实抛（run_watcher 记 ok=False 可见），禁静默空转。"""

        class _BrokenDB:
            async def execute(self, *a, **k):
                return None

            async def fetch(self, *a, **k):
                raise RuntimeError("learn db down")

        with pytest.raises(RuntimeError, match="learn db down"):
            asyncio.run(advisory_review(db_pool=_BrokenDB()))


# ── 4. 接线面 ──────────────────────────────────────────────────────


class TestWiring:
    def test_register_default_watchers_includes_advisory(self):
        registered = {}

        class _Engine:
            def register_watcher(self, name, fn, interval_seconds=None):
                registered[name] = (fn, interval_seconds)

        names = pb_mod.register_default_watchers(_Engine())
        assert "advisory_review" in names
        assert registered["advisory_review"][1] == pb_mod.DEFAULT_INTERVALS["advisory_review"]
        assert len(names) == 5  # 五默认行为全在

    def test_run_watcher_consumes_advisory(self, grp_db, tmp_path):
        """调度面真消费：run_watcher 跑 advisory_review 真上板（写了≠接线了）。"""
        db = _AsyncDB(tmp_path / "learn.db")
        _seed_recs(db, 1)
        _add_group(grp_db, "g1")
        engine = ProactiveEngine(db_path="")  # 纯内存留痕，不碰生产 proactive.db
        engine.register_watcher(
            "advisory_review",
            partial(advisory_review, db_pool=db),
            interval_seconds=60,
        )
        res = asyncio.run(engine.run_watcher("advisory_review"))
        assert res["ok"] is True
        assert res["result"]["created"] == 1
        rows = _task_rows(grp_db)
        assert len(rows) == 1 and rows[0]["goal"] == "[改进建议] failure·失败模式0"
