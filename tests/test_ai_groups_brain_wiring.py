"""A1认知层接线测试：/api/brain/think|verify 被真实执行流程调用（而非仅存在）。

测的是 src/api/ai_groups.py::_execute_task_impl —— execute/retry/任务链共用的
真实执行函数本体。只桩 agent 子进程调用（run_agent_proxy），brain think/verify
全走真代码（意图/风险/决策/记忆/事件），数据面换 tmp（ai_groups.db）隔离。
"""

import asyncio

import pytest


@pytest.fixture()
def brain_wiring_env(tmp_path, monkeypatch):
    """真实ai_groups执行函数 + 真实brain管线 + tmp数据面 + agent执行桩。"""
    import src.api.ai_groups as ai_groups
    from src.api import brain as brain_api
    from src.hippo.memory_store import MemoryStore

    # 数据面隔离：ai_groups.db换tmp（模块import时init_db已建生产表，幂等无害）
    monkeypatch.setattr(ai_groups, "DB_PATH", tmp_path / "ai_groups.db")
    ai_groups.init_db()

    # brain记忆数据面换tmp实例（think/verify逻辑本体不变，只换存储实例）
    tmp_store = MemoryStore()
    monkeypatch.setattr(brain_api, "store", tmp_store)

    # agent执行桩：只桩子进程调用，brain管线全走真代码
    calls = {"n": 0}

    async def fake_run_agent_proxy(agent_id, text, on_event=None):
        calls["n"] += 1
        calls["agent_id"] = agent_id
        calls["goal"] = text
        return ("桩执行结果：任务完成", "stub-agent", True)

    monkeypatch.setattr(ai_groups, "run_agent_proxy", fake_run_agent_proxy)

    def make_task(goal="修复src/api/echo.py里的超时bug"):
        conn = ai_groups.get_db()
        try:
            gid, tid = "g-brain-test", "t-brain-test"
            now = "2026-10-10T12:00:00"
            conn.execute(
                "INSERT OR REPLACE INTO ai_groups (id,name,description,status,created_at) VALUES (?,?,?,?,?)",
                (gid, "brain-wiring", "", "active", now),
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

    def task_row(tid):
        conn = ai_groups.get_db()
        try:
            r = conn.execute("SELECT * FROM ai_group_tasks WHERE id=?", (tid,)).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()

    yield {
        "ai_groups": ai_groups,
        "brain_api": brain_api,
        "store": tmp_store,
        "calls": calls,
        "make_task": make_task,
        "task_events": task_events,
        "task_row": task_row,
    }


class TestBrainWiredIntoExecution:
    def test_think_verify_called_in_real_path(self, brain_wiring_env):
        """think在执行前、verify在执行后被真实调用：事件+记忆双证据。"""
        env = brain_wiring_env
        ai_groups, _brain_api = env["ai_groups"], env["brain_api"]
        gid, tid = env["make_task"]()
        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal="修复src/api/echo.py里的超时bug")

        result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req))

        # Gate3-①：agent执行桩被调用=走的是真实执行路径
        assert env["calls"]["n"] == 1
        assert result["status"] == "scored"
        assert result["success"] is True

        # Gate3-②：执行过程事件里有brain-think（前）与brain-verify（后），顺序正确
        events = env["task_events"](tid)
        types = [e[0] for e in events]
        assert "brain-think" in types, f"brain-think未接线: {types}"
        assert "brain-verify" in types, f"brain-verify未接线: {types}"
        # 顺序：started → brain-think（执行前）→ … → scored → brain-verify（执行后）
        assert types.index("started") < types.index("brain-think") < types.index("scored")
        assert types.index("brain-verify") > types.index("scored")
        think_event = next(c for t, c in events if t == "brain-think")
        assert "risk=" in think_event and "mode=" in think_event

        # Gate3-③：真实brain代码写了记忆（同session=task_id可回溯think+verify）
        memories = env["store"].search(session_id=tid)
        contents = [m.content for m in memories]
        assert any(c.startswith("[think]") for c in contents), f"think记忆缺失: {contents}"
        assert any(c.startswith("[verify]") for c in contents), f"verify记忆缺失: {contents}"
        verify_mem = next(c for c in contents if c.startswith("[verify]"))
        assert "success=True" in verify_mem

        # 决策真正进入返回值（被消费，不是只打日志）
        assert result["brain_decision"] and "execute_mode" in result["brain_decision"]

    def test_brain_failure_does_not_block_execution(self, brain_wiring_env):
        """认知层自身故障不阻断执行，但brain-error事件必须可见（禁静默）。"""
        env = brain_wiring_env
        ai_groups, brain_api = env["ai_groups"], env["brain_api"]
        gid, tid = env["make_task"]()

        async def broken_think(req):
            raise RuntimeError("cortex炸了")

        brain_api.think, orig = broken_think, brain_api.think
        try:
            req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal="写个测试")
            result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req))
        finally:
            brain_api.think = orig

        assert result["status"] == "scored"
        assert env["calls"]["n"] == 1
        types = [t for t, _ in env["task_events"](tid)]
        assert "brain-error" in types
        assert "brain-think" not in types

    def test_deny_decision_blocks_execution(self, brain_wiring_env):
        """brain决策deny（critical风险）时任务被拒：不调agent、落failed、原因可见。"""
        env = brain_wiring_env
        ai_groups, brain_api = env["ai_groups"], env["brain_api"]
        gid, tid = env["make_task"]()

        async def deny_think(req):
            return {
                "intent": {
                    "goal": "fix_bug",
                    "target_files": [],
                    "change_size": "small",
                    "modify_scope": "single_file",
                },
                "risk": {"risks": [], "overall_level": "critical", "recommendation": "风险过高"},
                "decision": {
                    "edit_mode": "patch",
                    "execute_mode": "deny",
                    "confirm_prompt": "风险过高：删除核心模块",
                },
                "emotion": {"primary": "neutral", "confidence": 0.5, "sentiment": "neutral"},
                "memory_count": 0,
            }

        brain_api.think, orig = deny_think, brain_api.think
        try:
            req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal="删掉整个src目录")
            result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req))
        finally:
            brain_api.think = orig

        assert result["status"] == "denied"
        assert result["success"] is False
        assert env["calls"]["n"] == 0, "deny后仍调用了agent执行"
        row = env["task_row"](tid)
        assert row["status"] == "failed"
        assert "风险" in row["result"]
        types = [t for t, _ in env["task_events"](tid)]
        assert "failed" in types
        assert "finished" not in types
