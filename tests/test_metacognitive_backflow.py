"""G1-④a 元认知纠错回流测试（blind_spots/calibration → 执行 prompt 纠偏块）。

G0 定性③「reflect/blind_spots/calibration 仅观测口展示（brain.py /api/brain/
metacognition）不驱动任何纠偏动作」收口（第一条纠偏闭环=执行 prompt 面）。
本文件钉住：
1. 格式化纯函数（空零注入、盲区行渲染、top-k、字段缺失如实略禁硬造、
   calibration 仅 miscalibrated 出行且按偏差方向给纠偏、免责块头）
2. 取数语义（真账本写→读闭环、命名空间隔离、异常如实抛禁静默吞）
3. 真实执行路径接线（_execute_task_impl 真注入 → agent 拿到纠偏块；
   控制组零注入逐字一致；元认知层故障不阻断执行但可见；三块并存序）
数据面全 tmp（metacognition_log 走生产写点符号落账），db_pool 换 tmp 走
生产适配器契约（test_episodic_advisory_backflow.py 同款）。
"""

import asyncio
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 新符号走模块属性访问（控制组=旧代码无符号 → AttributeError 落结构面失败，
# 不变面测试不依赖新符号仍可跑=精确命中非自嗨）。
from src.mirror import metacognition as mc
from src.mirror.metacognition import FAILURE_OUTCOME, Metacognition


class _AsyncDB:
    """生产适配器注入（test_episodic_advisory_backflow.py 同款：测试即生产路径）。"""

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


def _seed_ledger(path: Path, agent_id: str, rows):
    """生产写点符号落账（Metacognition._ensure_table+log_decision/log_outcome
    同一真源=think/verify 双写点同款），写→读闭环非裸 SQL 造数。"""
    meta = Metacognition(_AsyncDB(path), "default", agent_id)
    for row in rows:
        if row["kind"] == "outcome":
            asyncio.run(
                meta.log_outcome(
                    decision=row["decision"],
                    outcome=row["outcome"],
                    ref=row.get("ref", ""),
                )
            )
        else:
            asyncio.run(
                meta.log_decision(
                    decision=row["decision"],
                    reasoning=row.get("reasoning", ""),
                    confidence=row["confidence"],
                    ref=row.get("ref", ""),
                )
            )


# ── 1. 格式化纯函数 ──────────────────────────────────────────────────────


class TestFormatMetacognitiveAdvisory:
    def test_empty_zero_injection(self):
        assert mc.format_metacognitive_advisory_for_prompt([], {"calibration": "no_data"}) == ""
        assert mc.format_metacognitive_advisory_for_prompt(None, None) == ""
        assert mc.format_metacognitive_advisory_for_prompt([], {"calibration": "good"}) == ""

    def test_blind_spot_rendered(self):
        block = mc.format_metacognitive_advisory_for_prompt(
            [{"area": "deploy-to-prod", "fail_count": 3}], {"calibration": "no_data"}
        )
        assert block.startswith("【元认知纠偏提醒】")
        assert "deploy-to-prod" in block
        assert "反复失败 3 次" in block
        assert block.strip().endswith(
            "1. 决策领域「deploy-to-prod」历史上反复失败 3 次——执行同类动作时换方法或加强前置检查与结果验证"
        )

    def test_top_k_cap_on_blind_spots(self):
        spots = [{"area": f"area-{i}", "fail_count": 2} for i in range(5)]
        block = mc.format_metacognitive_advisory_for_prompt(
            spots, {"calibration": "no_data"}, top_k=2
        )
        assert "area-0" in block and "area-1" in block
        assert "area-2" not in block

    def test_malformed_items_skipped_not_fabricated(self):
        """字段缺失如实略禁硬造（G1-③b-2 同纪律）。"""
        spots = [
            "not-a-dict",
            {"fail_count": 2},  # 缺 area
            {"area": "   ", "fail_count": 2},  # 空白 area
            {"area": "bad-count", "fail_count": "abc"},  # 非数值计数
            {"area": "zero-count", "fail_count": 0},
            {"area": "真盲区", "fail_count": 2},
        ]
        block = mc.format_metacognitive_advisory_for_prompt(spots, {"calibration": "no_data"})
        assert "真盲区" in block
        assert "bad-count" not in block and "zero-count" not in block
        assert block.count("决策领域") == 1  # 只渲染可渲染行，不硬造

    def test_calibration_overconfident_line(self):
        calib = {
            "calibration": "miscalibrated",
            "avg_confidence": 0.9,
            "actual_success_rate": 0.3,
        }
        block = mc.format_metacognitive_advisory_for_prompt([], calib)
        assert "自评信心偏高" in block
        assert "0.90" in block and "0.30" in block
        assert "加强结果验证" in block

    def test_calibration_underconfident_line(self):
        calib = {
            "calibration": "miscalibrated",
            "avg_confidence": 0.2,
            "actual_success_rate": 0.8,
        }
        block = mc.format_metacognitive_advisory_for_prompt([], calib)
        assert "自评信心偏低" in block
        assert "大胆推进" in block

    def test_calibration_good_or_missing_keys_no_line(self):
        """good/no_data 如实无纠偏可言；缺数值键如实略不猜数字。"""
        assert (
            mc.format_metacognitive_advisory_for_prompt(
                [{"area": "a", "fail_count": 2}], {"calibration": "good", "avg_confidence": 0.9}
            ).count("自评信心")
            == 0
        )
        block = mc.format_metacognitive_advisory_for_prompt(
            [], {"calibration": "miscalibrated", "avg_confidence": 0.9}
        )  # 缺 actual_success_rate
        assert block == ""

    def test_area_truncation_marker(self):
        block = mc.format_metacognitive_advisory_for_prompt(
            [{"area": "x" * 500, "fail_count": 2}], {"calibration": "no_data"}
        )
        assert "…" in block
        assert len(block) < 400

    def test_disclaimer_header(self):
        block = mc.format_metacognitive_advisory_for_prompt(
            [{"area": "a", "fail_count": 2}], {"calibration": "no_data"}
        )
        assert block.startswith("【元认知纠偏提醒】")
        assert "仅供参考" in block and "以任务要求为准" in block

    def test_lines_helper_shared_single_source(self):
        """渲染/计数单一源：_metacognitive_advisory_lines 与 format 同判法。"""
        spots = [{"area": "a", "fail_count": 2}, {"area": "b", "fail_count": 2}]
        lines = mc._metacognitive_advisory_lines(spots, {"calibration": "no_data"})
        assert len(lines) == 2
        assert all(
            line in mc.format_metacognitive_advisory_for_prompt(spots, {"calibration": "no_data"})
            for line in lines
        )


# ── 2. 取数语义（真账本写→读闭环） ──────────────────────────────────────


class TestMetacognitiveAdvisoryFor:
    def test_blind_spot_write_read_closed_loop(self, tmp_path):
        """生产写点符号落账（log_outcome ×2 failure 同 decision）→ 盲区真现身。"""
        path = tmp_path / "meta.db"
        _seed_ledger(
            path,
            "mimo",
            [
                {
                    "kind": "outcome",
                    "decision": "deploy-action",
                    "outcome": FAILURE_OUTCOME,
                    "ref": "t1",
                },
                {
                    "kind": "outcome",
                    "decision": "deploy-action",
                    "outcome": FAILURE_OUTCOME,
                    "ref": "t2",
                },
            ],
        )
        block, n = asyncio.run(mc.metacognitive_advisory_for("mimo", db_pool=_AsyncDB(path)))
        assert n == 1
        assert "deploy-action" in block and "反复失败 2 次" in block

    def test_calibration_write_read_closed_loop(self, tmp_path):
        """决策行（自评 0.9）+成败行（failure 同 ref）→ miscalibrated 偏高纠偏行。"""
        path = tmp_path / "meta.db"
        _seed_ledger(
            path,
            "mimo",
            [
                {"kind": "decision", "decision": "auto", "confidence": 0.9, "ref": "r1"},
                {"kind": "outcome", "decision": "act-a", "outcome": FAILURE_OUTCOME, "ref": "r1"},
                {"kind": "outcome", "decision": "act-b", "outcome": FAILURE_OUTCOME, "ref": "r2"},
            ],
        )
        block, n = asyncio.run(mc.metacognitive_advisory_for("mimo", db_pool=_AsyncDB(path)))
        assert "自评信心偏高" in block
        # 盲区语义锁死：act-a/act-b 各失败 1 次 < 门槛 2 → 不出盲区行（只出校准行）
        assert "act-a" not in block and "act-b" not in block
        assert "决策领域" not in block
        assert n == 1

    def test_empty_ledger_zero_injection(self, tmp_path):
        path = tmp_path / "meta.db"
        _seed_ledger(path, "mimo", [])
        assert asyncio.run(mc.metacognitive_advisory_for("mimo", db_pool=_AsyncDB(path))) == ("", 0)

    def test_namespace_isolation(self, tmp_path):
        """盲区属 (tenant, agent) 命名空间：别的 agent 的盲区不进本 agent 纠偏块。"""
        path = tmp_path / "meta.db"
        _seed_ledger(
            path,
            "other-agent",
            [
                {
                    "kind": "outcome",
                    "decision": "other-action",
                    "outcome": FAILURE_OUTCOME,
                    "ref": "t1",
                },
                {
                    "kind": "outcome",
                    "decision": "other-action",
                    "outcome": FAILURE_OUTCOME,
                    "ref": "t2",
                },
            ],
        )
        assert asyncio.run(mc.metacognitive_advisory_for("mimo", db_pool=_AsyncDB(path))) == ("", 0)

    def test_fault_raises_not_swallowed(self):
        """异常如实抛禁静默吞（降级可见性归消费方，F2b-② 同款纪律）。"""

        class _Broken:
            async def fetch(self, *a, **k):
                raise RuntimeError("元认知账本炸了")

            async def execute(self, *a, **k):
                raise RuntimeError("元认知账本炸了")

        with pytest.raises(RuntimeError):
            asyncio.run(mc.metacognitive_advisory_for("mimo", db_pool=_Broken()))


# ── 3. 真实执行路径接线（_execute_task_impl 真调用真注入）─────────────────


@pytest.fixture()
def metacog_env(tmp_path, monkeypatch):
    """真实 _execute_task_impl + 真取数口（db_pool 换 tmp 生产适配器）+ agent 执行桩。"""
    import src.api.ai_groups as ai_groups
    import src.database.postgres as pg
    from src.api import brain as brain_api
    from src.hippo.memory_store import MemoryStore

    monkeypatch.setattr(ai_groups, "DB_PATH", tmp_path / "ai_groups.db")
    ai_groups.init_db()
    monkeypatch.setattr(brain_api, "store", MemoryStore())

    meta_path = tmp_path / "meta.db"
    monkeypatch.setattr(pg, "db_pool", _AsyncDB(meta_path))

    calls = {"n": 0}

    async def fake_run_agent_proxy(agent_id, text, on_event=None, **kwargs):
        calls["n"] += 1
        calls["goal"] = text
        return ("桩执行结果：任务完成", "stub-agent", True)

    monkeypatch.setattr(ai_groups, "run_agent_proxy", fake_run_agent_proxy)

    def seed(rows, agent_id="mimo"):
        _seed_ledger(meta_path, agent_id, rows)

    def make_task(goal):
        conn = ai_groups.get_db()
        try:
            gid, tid = "g-metacog", "t-metacog"
            now = "2026-10-17T12:00:00"
            conn.execute(
                "INSERT OR REPLACE INTO ai_groups (id,name,description,status,created_at) VALUES (?,?,?,?,?)",
                (gid, "metacog", "", "active", now),
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
        "meta_path": meta_path,
    }


class TestMetacognitiveBackflowWiredIntoExecution:
    def test_injected_into_real_execution_prompt(self, metacog_env):
        """真取数口+真注入：盲区观测 → 执行prompt → agent 拿到纠偏块。"""
        env = metacog_env
        ai_groups = env["ai_groups"]
        goal = "帮我查看当前目录"
        env["seed"](
            [
                {
                    "kind": "outcome",
                    "decision": "deploy-action",
                    "outcome": FAILURE_OUTCOME,
                    "ref": "t1",
                },
                {
                    "kind": "outcome",
                    "decision": "deploy-action",
                    "outcome": FAILURE_OUTCOME,
                    "ref": "t2",
                },
            ]
        )
        gid, tid = env["make_task"](goal)
        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal=goal)
        result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req, confirmed=True))

        assert result["status"] == "scored"
        prompt = env["calls"]["goal"]
        assert prompt.startswith(goal)  # 任务本体要求在前，纠偏块只追加
        assert "元认知纠偏提醒" in prompt and "以任务要求为准" in prompt
        assert "deploy-action" in prompt
        events = env["task_events"](tid)
        assert any(
            t == "metacognitive-context"
            and "元认知纠偏提醒注入执行prompt：1条（metacognition→执行）" in c
            for t, c in events
        ), f"元认知注入事件缺失: {events}"

    def test_no_observation_zero_injection_control(self, metacog_env):
        """控制组：账本无可用观测 → prompt 与 goal 逐字一致、零注入事件。"""
        env = metacog_env
        ai_groups = env["ai_groups"]
        goal = "写一个单元测试用例"
        gid, tid = env["make_task"](goal)
        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal=goal)
        result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req))

        assert result["status"] == "scored"
        assert env["calls"]["goal"] == goal  # 零注入=逐字一致
        types = [t for t, _ in env["task_events"](tid)]
        assert "metacognitive-context" not in types

    def test_metacognitive_fault_does_not_block_execution(self, metacog_env, monkeypatch):
        """元认知层故障不阻断执行，但 metacognitive-context-error 必须可见（禁静默）。"""
        env = metacog_env
        ai_groups = env["ai_groups"]
        goal = "帮我查看当前目录"

        async def broken(agent_id):
            raise RuntimeError("元认知账本炸了")

        monkeypatch.setattr(ai_groups, "_metacognitive_advisory_for", broken, raising=False)
        gid, tid = env["make_task"](goal)
        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal=goal)
        result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req, confirmed=True))

        assert result["status"] == "scored"
        assert env["calls"]["n"] == 1
        assert env["calls"]["goal"] == goal  # 未注入
        events = env["task_events"](tid)
        assert any(
            t == "metacognitive-context-error" and "元认知纠偏回流失败" in c for t, c in events
        ), f"故障必须可见: {events}"

    def test_three_blocks_coexist_ordered(self, metacog_env, monkeypatch):
        """learned + episodic + metacognitive 三块并存，任务本体在前、序稳定。"""
        env = metacog_env
        ai_groups = env["ai_groups"]
        goal = "帮我查看当前目录"

        async def fake_learned(run_goal):
            return ("【历史经验建议】learned-mark", 1)

        async def fake_episodic(run_goal):
            return ("【历史失败提醒】episodic-mark", 1)

        async def fake_metacog(agent_id):
            return ("【元认知纠偏提醒】metacog-mark", 1)

        monkeypatch.setattr(ai_groups, "_learned_advisory_for", fake_learned)
        monkeypatch.setattr(ai_groups, "_episodic_advisory_for", fake_episodic)
        monkeypatch.setattr(ai_groups, "_metacognitive_advisory_for", fake_metacog, raising=False)
        gid, tid = env["make_task"](goal)
        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal=goal)
        result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req, confirmed=True))

        assert result["status"] == "scored"
        prompt = env["calls"]["goal"]
        assert prompt.startswith(goal)
        assert "learned-mark" in prompt and "episodic-mark" in prompt and "metacog-mark" in prompt
        assert (
            prompt.index("learned-mark")
            < prompt.index("episodic-mark")
            < prompt.index("metacog-mark")
        )

    def test_real_seam_reads_production_symbols(self, metacog_env):
        """接缝真走 src.mirror.metacognition 取数口（写了≠接线了）：真账本真回流。"""
        env = metacog_env
        ai_groups = env["ai_groups"]
        env["seed"](
            [
                {
                    "kind": "outcome",
                    "decision": "seam-action",
                    "outcome": FAILURE_OUTCOME,
                    "ref": "t1",
                },
                {
                    "kind": "outcome",
                    "decision": "seam-action",
                    "outcome": FAILURE_OUTCOME,
                    "ref": "t2",
                },
            ]
        )
        block, n = asyncio.run(ai_groups._metacognitive_advisory_for("mimo"))
        assert n == 1
        assert "seam-action" in block
