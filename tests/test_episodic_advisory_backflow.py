"""DEC-③-① episodic 失败经验回流执行 prompt 测试（pending-decisions ③ 试水形态 B）。

F4b-㉜ 边界③「episodic 经验 prompt 回流」收口（试水形态）：experiences 失败行
error 字段 → 执行 prompt 软建议块。本文件钉住：
1. 格式化纯函数（空零注入、仅 failure+error 字段渲染、top-k、截断如实、免责块头、
   「仅 error 字段」纪律=其余字段绝不进块）
2. 取数语义（相似失败命中、无关零注入宁缺勿滥、异常如实抛禁静默吞）
3. 真实执行路径接线（_execute_task_impl 真注入 → agent 拿到失败教训块；
   控制组零注入逐字一致；episodic 层故障不阻断执行但可见；learned+episodic 双块并存）
数据面全 tmp（experiences 行直插），db_pool 换 tmp 走生产适配器契约。
"""

import asyncio
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.learn.experience import (
    ExperienceMemory,
    _episodic_error_text,
    episodic_advisory_for,
    format_episodic_advisory_for_prompt,
)
from src.models.cognitive import Experience


def _exp(outcome: Any = "failure", error: Any = "boom", intent: Any = "旧任务", fix: Any = "补丁"):
    return Experience(
        action="job:x",
        intent_summary=intent,
        outcome=outcome,
        error=error,
        fix=fix,
    )


class _AsyncDB:
    """生产适配器注入（test_recommendation_backflow.py 同款：测试即生产路径）。"""

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


def _seed_experiences(path: Path, rows):
    """experiences 生产 schema 建表（ExperienceMemory._ensure_table）+ 直插行。"""
    memory = ExperienceMemory(_AsyncDB(path), "default", "default")
    asyncio.run(memory._ensure_table())
    conn = sqlite3.connect(path)
    now = time.time()
    for intent, outcome, error in rows:
        conn.execute(
            "INSERT INTO experiences (tenant_id, agent_id, action, intent_summary, "
            "outcome, error, fix, relevance_score, created_at, metadata) "
            "VALUES ('default','default','job:seed',?,?,?,NULL,1.0,?, '{}')",
            (intent, outcome, error, now),
        )
    conn.commit()
    conn.close()


# ── 1. 格式化纯函数 ──────────────────────────────────────────────────────


class TestFormatEpisodicAdvisory:
    def test_empty_zero_injection(self):
        assert format_episodic_advisory_for_prompt([]) == ""
        assert format_episodic_advisory_for_prompt(None) == ""

    def test_only_failure_error_rendered(self):
        """试水形态 B 纪律：仅 failure 行 error 字段；success/无 error 行不入块。"""
        items = [
            _exp(outcome="success", error="成功的行不进块"),
            _exp(outcome="failure", error=None),
            _exp(outcome="failure", error="   "),
            _exp(outcome="failure", error="真实失败原因"),
        ]
        block = format_episodic_advisory_for_prompt(items)
        assert "真实失败原因" in block
        assert "成功的行不进块" not in block
        # 编号连续只数渲染行
        assert block.strip().endswith("1. 真实失败原因")

    def test_error_field_only_no_other_fields(self):
        """intent_summary/fix 等字段绝不进块（「仅 error 字段」锁死）。"""
        block = format_episodic_advisory_for_prompt(
            [_exp(error="错误原文", intent="旧任务意图原文", fix="修复补丁原文")]
        )
        assert "错误原文" in block
        assert "旧任务意图原文" not in block
        assert "修复补丁原文" not in block

    def test_top_k_and_truncation_marker(self):
        items = [_exp(error=f"err-{i}") for i in range(5)]
        block = format_episodic_advisory_for_prompt(items, top_k=2)
        assert "err-0" in block and "err-1" in block
        assert "err-2" not in block
        long_err = "x" * 500
        block2 = format_episodic_advisory_for_prompt([_exp(error=long_err)])
        assert len(block2) < 300
        assert block2.rstrip().endswith("…")

    def test_disclaimer_header(self):
        block = format_episodic_advisory_for_prompt([_exp(error="e")])
        assert block.startswith("【历史失败提醒】")
        assert "仅供参考" in block and "以任务要求为准" in block

    def test_whitespace_folded_single_line(self):
        block = format_episodic_advisory_for_prompt([_exp(error="行一\n行二\t行三")])
        assert "行一 行二 行三" in block
        assert _episodic_error_text(_exp(error="a\nb")) == "a b"


# ── 2. 取数语义（真数据面） ──────────────────────────────────────────────


class TestEpisodicAdvisoryFor:
    def test_similar_failure_hit(self, tmp_path):
        path = tmp_path / "exp.db"
        _seed_experiences(path, [("帮我查看当前目录", "failure", "Permission denied: /root")])
        block, n = asyncio.run(episodic_advisory_for("帮我查看当前目录", db_pool=_AsyncDB(path)))
        assert n == 1
        assert "Permission denied: /root" in block

    def test_unrelated_zero_injection(self):
        """无关零注入（宁缺勿滥：字面相关门槛外的失败教训不进 prompt）。"""
        with pytest.MonkeyPatch.context() as mp:
            import tempfile

            import src.database.postgres as pg

            tmpdir = Path(tempfile.mkdtemp())
            path = tmpdir / "exp.db"
            _seed_experiences(path, [("部署数据库集群到生产环境", "failure", "磁盘满")])
            mp.setattr(pg, "db_pool", _AsyncDB(path))
            block, n = asyncio.run(episodic_advisory_for("写一个单元测试用例"))
        assert (block, n) == ("", 0)

    def test_fault_raises_not_swallowed(self):
        """异常如实抛禁静默吞（降级可见性归消费方，F2b-② 同款纪律）。"""

        class _Broken:
            async def fetch(self, *a, **k):
                raise RuntimeError("经验库炸了")

            async def execute(self, *a, **k):
                raise RuntimeError("经验库炸了")

        with pytest.raises(RuntimeError):
            asyncio.run(episodic_advisory_for("任意目标", db_pool=_Broken()))


# ── 3. 真实执行路径接线（_execute_task_impl 真调用真注入）─────────────────


@pytest.fixture()
def episodic_env(tmp_path, monkeypatch):
    """真实 _execute_task_impl + 真取数口（db_pool 换 tmp 生产适配器）+ agent 执行桩。"""
    import src.api.ai_groups as ai_groups
    import src.database.postgres as pg
    from src.api import brain as brain_api
    from src.hippo.memory_store import MemoryStore

    monkeypatch.setattr(ai_groups, "DB_PATH", tmp_path / "ai_groups.db")
    ai_groups.init_db()
    monkeypatch.setattr(brain_api, "store", MemoryStore())

    exp_path = tmp_path / "exp.db"
    monkeypatch.setattr(pg, "db_pool", _AsyncDB(exp_path))

    calls = {"n": 0}

    async def fake_run_agent_proxy(agent_id, text, on_event=None, **kwargs):
        calls["n"] += 1
        calls["goal"] = text
        return ("桩执行结果：任务完成", "stub-agent", True)

    monkeypatch.setattr(ai_groups, "run_agent_proxy", fake_run_agent_proxy)

    def seed(rows):
        _seed_experiences(exp_path, rows)

    def make_task(goal):
        conn = ai_groups.get_db()
        try:
            gid, tid = "g-episodic", "t-episodic"
            now = "2026-10-15T12:00:00"
            conn.execute(
                "INSERT OR REPLACE INTO ai_groups (id,name,description,status,created_at) VALUES (?,?,?,?,?)",
                (gid, "episodic", "", "active", now),
            )
            conn.execute(
                "INSERT OR REPLACE INTO ai_group_tasks (id,group_id,goal,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (tid, gid, goal, "pending", now, now),
            )
            conn.commit()
        finally:
            conn.close()
        return gid, tid

    def task_events(tid):
        conn = ai_groups.get_db()
        try:
            rows = conn.execute(
                "SELECT event_type, content FROM task_execution_events WHERE task_id=? ORDER BY id",
                (tid,),
            ).fetchall()
            return [(r["event_type"], r["content"]) for r in rows]
        finally:
            conn.close()

    yield {
        "ai_groups": ai_groups,
        "calls": calls,
        "seed": seed,
        "make_task": make_task,
        "task_events": task_events,
        "exp_path": exp_path,
    }


class TestEpisodicBackflowWiredIntoExecution:
    def test_injected_into_real_execution_prompt(self, episodic_env):
        """真取数口+真注入：失败经验 error → 执行prompt → agent 拿到失败教训块。

        注：命中失败经验会同时抬升风险评估（F4b-㉚ 失败经验风险轴既有设计）→
        确认门禁，本测以 confirmed=True 放行观察 prompt 装配（门禁语义非本切片对象）。
        """
        env = episodic_env
        ai_groups = env["ai_groups"]
        goal = "帮我查看当前目录"
        env["seed"]([("帮我查看当前目录", "failure", "Permission denied: /root")])
        gid, tid = env["make_task"](goal)

        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal=goal)
        result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req, confirmed=True))

        assert result["status"] == "scored"
        prompt = env["calls"]["goal"]
        assert prompt.startswith(goal)  # 任务本体要求在前，建议块只追加
        assert "历史失败提醒" in prompt and "以任务要求为准" in prompt
        assert "Permission denied: /root" in prompt
        events = env["task_events"](tid)
        assert any(
            t == "learned-context" and "历史失败教训注入执行prompt：1条（experiences→执行）" in c
            for t, c in events
        ), f"episodic 注入事件缺失: {events}"

    def test_no_match_zero_injection_control(self, episodic_env):
        """控制组：无相关失败经验 → prompt 与 goal 逐字一致、零注入事件。"""
        env = episodic_env
        ai_groups = env["ai_groups"]
        goal = "写一个单元测试用例"
        env["seed"]([("部署数据库集群到生产环境", "failure", "磁盘满")])
        gid, tid = env["make_task"](goal)

        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal=goal)
        result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req))

        assert result["status"] == "scored"
        assert env["calls"]["goal"] == goal  # 零注入=逐字一致
        types = [t for t, _ in env["task_events"](tid)]
        assert "learned-context" not in types

    def test_episodic_fault_does_not_block_execution(self, episodic_env, monkeypatch):
        """episodic 层故障不阻断执行，但 learned-context-error 必须可见（禁静默）。"""
        env = episodic_env
        ai_groups = env["ai_groups"]
        goal = "帮我查看当前目录"
        env["seed"]([("帮我查看当前目录", "failure", "Permission denied: /root")])

        async def broken(run_goal):
            raise RuntimeError("经验库炸了")

        monkeypatch.setattr(ai_groups, "_episodic_advisory_for", broken)
        gid, tid = env["make_task"](goal)
        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal=goal)
        result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req, confirmed=True))

        assert result["status"] == "scored"
        assert env["calls"]["n"] == 1
        assert env["calls"]["goal"] == goal  # 未注入
        events = env["task_events"](tid)
        assert any(t == "learned-context-error" and "episodic回流失败" in c for t, c in events), (
            f"故障必须可见: {events}"
        )

    def test_learned_and_episodic_blocks_coexist(self, episodic_env):
        """F2b-② learned 块与 DEC-③-① episodic 块并存：两块都在、任务本体在前。"""
        env = episodic_env
        ai_groups = env["ai_groups"]
        goal = "帮我查看当前目录"
        env["seed"]([("帮我查看当前目录", "failure", "Permission denied: /root")])

        async def fake_learned(run_goal):
            return ("【历史经验建议】learned-mark", 1)

        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(ai_groups, "_learned_advisory_for", fake_learned)
            gid, tid = env["make_task"](goal)
            req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal=goal)
            result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req, confirmed=True))
        finally:
            monkeypatch.undo()

        assert result["status"] == "scored"
        prompt = env["calls"]["goal"]
        assert prompt.startswith(goal)
        assert "learned-mark" in prompt and "历史失败提醒" in prompt
        assert prompt.index("learned-mark") < prompt.index("历史失败提醒")
