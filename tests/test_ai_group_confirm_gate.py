"""F4b-⑳ 确认门禁真执行测试：brain think execute_mode=confirm_required 从"被静默忽略
照常执行（门禁形同虚设）"变为真门禁——任务暂停 awaiting_confirm 等人工裁决，问句回群聊，
confirm 端点 approved=true 放行（不再重复要确认）/ false 终止，链式子任务暂停处断链、
放行后自动续跑剩余子任务。

测的是 src/api/ai_groups.py::_execute_task_impl / _run_task_chain / confirm_task 真实
代码路径。只桩 agent 子进程调用（run_agent_proxy）与 brain.think 决策（可控 execute_mode），
数据面换 tmp（ai_groups.db）隔离，群聊推送指向不可达地址防污染真 acp-proxy。
"""

import asyncio
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _think_result(mode: str) -> dict:
    return {
        "intent": {
            "goal": "fix_bug",
            "target_files": [],
            "change_size": "small",
            "modify_scope": "single_file",
        },
        "risk": {
            "risks": [],
            "overall_level": "high" if mode == "confirm_required" else "low",
            "recommendation": "高风险操作",
        },
        "decision": {
            "edit_mode": "patch",
            "execute_mode": mode,
            "confirm_prompt": "需要确认：高风险操作（删除生产数据）"
            if mode == "confirm_required"
            else None,
        },
        "emotion": {"primary": "neutral", "confidence": 0.5, "sentiment": "neutral"},
        "memory_count": 0,
    }


@pytest.fixture()
def confirm_env(tmp_path, monkeypatch):
    """真实执行函数 + 可控brain决策 + tmp数据面 + agent执行桩。"""
    import src.api.ai_groups as ai_groups
    from src.api import brain as brain_api
    from src.hippo.memory_store import MemoryStore

    monkeypatch.setattr(ai_groups, "DB_PATH", tmp_path / "ai_groups.db")
    ai_groups.init_db()
    monkeypatch.setattr(brain_api, "store", MemoryStore())
    real_think = brain_api.think  # 捕获真 think（稍后被决策桩覆盖）
    # 群聊实时推送指向不可达端口：落库照旧（问题可见性真源=discussion_messages），推送不碰真 acp-proxy
    monkeypatch.setattr(ai_groups, "GROUP_MSG_PUSH", "http://127.0.0.1:9/disabled")

    calls = {"n": 0, "goals": []}

    async def fake_run_agent_proxy(agent_id, text, on_event=None, **kwargs):
        calls["n"] += 1
        calls["goals"].append(text)
        return ("桩执行结果：任务完成", "stub-agent", True)

    monkeypatch.setattr(ai_groups, "run_agent_proxy", fake_run_agent_proxy)

    # G1-③a 门禁面创造力取数口桩（不打真 LLM）：本 fixture 测门禁语义，创造力
    # 回流语义由 test_creativity_backflow_gate 覆盖；空产出=零织入零事件（既有
    # 事件断言意图零丢失）。
    async def _no_creativity(goal, constraints):
        return {"count": 0, "items": []}, ""

    monkeypatch.setattr(ai_groups, "_creative_alternatives_for", _no_creativity)

    # brain决策可控：按 goal 子串指定 execute_mode（缺省 auto）
    think_modes: dict = {}

    async def fake_think(req):
        mode = "auto"
        for key, m in think_modes.items():
            if key in (req.user_input or ""):
                mode = m
        return _think_result(mode)

    monkeypatch.setattr(brain_api, "think", fake_think)

    def make_task(goal="修复src/api/echo.py里的超时bug", tid="t-confirm-1", status="pending"):
        conn = ai_groups.get_db()
        try:
            gid = "g-confirm-test"
            # F3b-⑨b-②b-1 顺手收口（时间炸弹修）：原硬编码 "2026-10-12T12:00:00"，
            # 一旦墙钟越过 +24h 父任务行变超龄被 R2 清扫（2026-10-13 12:00 后全量必炸）。
            # fixture 行=建行即"新鲜"语义，取真实 now 还原作者意图。
            now = datetime.now().isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO ai_groups (id,name,description,status,created_at) VALUES (?,?,?,?,?)",
                (gid, "confirm-gate", "", "active", now),
            )
            conn.execute(
                "INSERT OR REPLACE INTO ai_group_tasks (id,group_id,goal,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (tid, gid, goal, status, now, now),
            )
            conn.commit()
        finally:
            conn.close()
        return gid, tid

    def make_chain(goals, sub_statuses=None):
        """父任务(planning)+N子任务（默认全 pending，assigned=mimo）。返回 (gid, pid, [sub_id])"""
        conn = ai_groups.get_db()
        try:
            gid, pid = "g-confirm-test", "t-chain-parent"
            # F3b-⑨b-②b-1 顺手收口（时间炸弹修）：原硬编码 "2026-10-12T12:00:00"，
            # 一旦墙钟越过 +24h 父任务行变超龄被 R2 清扫（2026-10-13 12:00 后全量必炸）。
            # fixture 行=建行即"新鲜"语义，取真实 now 还原作者意图。
            now = datetime.now().isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO ai_groups (id,name,description,status,created_at) VALUES (?,?,?,?,?)",
                (gid, "confirm-gate", "", "active", now),
            )
            conn.execute(
                "INSERT OR REPLACE INTO ai_group_tasks (id,group_id,goal,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (pid, gid, "主任务：修复发布流水线", "planning", now, now),
            )
            sub_ids = []
            for i, goal in enumerate(goals):
                sid = f"t-chain-sub{i + 1}"
                st = (sub_statuses or ["pending"] * len(goals))[i]
                conn.execute(
                    "INSERT OR REPLACE INTO ai_group_tasks (id,group_id,parent_task_id,goal,status,assigned_agent_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (sid, gid, pid, goal, st, "mimo", now, now),
                )
                sub_ids.append(sid)
            conn.commit()
        finally:
            conn.close()
        return gid, pid, sub_ids

    def task_row(tid):
        conn = ai_groups.get_db()
        try:
            r = conn.execute("SELECT * FROM ai_group_tasks WHERE id=?", (tid,)).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()

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

    def chat_messages(tid):
        conn = ai_groups.get_db()
        try:
            rows = conn.execute(
                "SELECT agent_id, content FROM discussion_messages WHERE task_id=? ORDER BY id",
                (tid,),
            ).fetchall()
            return [(r["agent_id"], r["content"]) for r in rows]
        finally:
            conn.close()

    def all_tasks():
        conn = ai_groups.get_db()
        try:
            rows = conn.execute(
                "SELECT id, goal, status, parent_task_id FROM ai_group_tasks ORDER BY rowid"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    async def wait_status(tid, want, timeout=8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = task_row(tid)
            if row and row["status"] in want:
                return row
            await asyncio.sleep(0.05)
        return task_row(tid)

    yield {
        "ai_groups": ai_groups,
        "brain_api": brain_api,
        "real_think": real_think,  # 修前捕获的真 think（触发面测试走真认知管线）
        "calls": calls,
        "think_modes": think_modes,
        "make_task": make_task,
        "make_chain": make_chain,
        "task_row": task_row,
        "task_events": task_events,
        "chat_messages": chat_messages,
        "all_tasks": all_tasks,
        "wait_status": wait_status,
    }


class TestConfirmGateSemantics:
    def test_confirm_required_pauses_and_asks_human(self, confirm_env):
        """confirm_required 真门禁：不调agent、任务暂停 awaiting_confirm、问句回群聊。"""
        env = confirm_env
        ai_groups = env["ai_groups"]
        env["think_modes"]["超时"] = "confirm_required"
        gid, tid = env["make_task"]()
        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal="修复src/api/echo.py里的超时bug")

        result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req))

        assert result["status"] == "confirm_required"
        assert result["success"] is False
        assert "需要确认" in result["confirm_prompt"]
        assert env["calls"]["n"] == 0, "confirm_required 仍执行了 agent（门禁形同虚设复发）"
        row = env["task_row"](tid)
        assert row["status"] == "awaiting_confirm"
        assert "需要确认" in row["result"]
        types = [t for t, _ in env["task_events"](tid)]
        assert "confirm-required" in types, f"暂停不可见: {types}"
        assert "finished" not in types and "failed" not in types
        # 问句回群聊（人机对话面）：问题+裁决口可见
        msgs = env["chat_messages"](tid)
        assert any("等待人工确认" in c and "/confirm" in c for _, c in msgs), (
            f"问句未回群聊: {msgs}"
        )
        assert any(a == "system" for a, _ in msgs)

    def test_confirmed_bypass_executes_without_reprompt(self, confirm_env):
        """confirmed=True（confirm端点放行）：执行且不再重复要确认（防无限确认环）。"""
        env = confirm_env
        ai_groups = env["ai_groups"]
        env["think_modes"]["超时"] = "confirm_required"
        gid, tid = env["make_task"]()
        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal="修复src/api/echo.py里的超时bug")

        result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req, confirmed=True))

        assert env["calls"]["n"] == 1
        assert result["status"] == "scored"
        types = [t for t, _ in env["task_events"](tid)]
        assert "confirm-required" not in types, "已放行仍重复要确认（无限确认环）"
        assert env["task_row"](tid)["status"] == "scored"

    def test_unconfirmed_rerun_still_gated(self, confirm_env):
        """execute/retry 等未确认路径重跑仍被拦（禁旁路口绕过门禁）。"""
        env = confirm_env
        ai_groups = env["ai_groups"]
        env["think_modes"]["超时"] = "confirm_required"
        gid, tid = env["make_task"]()
        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal="修复src/api/echo.py里的超时bug")

        r1 = asyncio.run(ai_groups._execute_task_impl(gid, tid, req))
        r2 = asyncio.run(ai_groups._execute_task_impl(gid, tid, req))

        assert r1["status"] == r2["status"] == "confirm_required"
        assert env["calls"]["n"] == 0
        assert env["task_row"](tid)["status"] == "awaiting_confirm"

    def test_deny_still_fails(self, confirm_env):
        """deny 零回归（门禁块移位后语义不变）：不执行、落 failed、原因可见。"""
        env = confirm_env
        ai_groups = env["ai_groups"]
        env["think_modes"]["删除"] = "deny"
        gid, tid = env["make_task"](goal="删除生产数据")
        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal="删除生产数据")

        result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req))

        assert result["status"] == "denied"
        assert env["calls"]["n"] == 0
        row = env["task_row"](tid)
        assert row["status"] == "failed"

    def test_auto_unchanged(self, confirm_env):
        """auto 零回归：正常执行到 scored。"""
        env = confirm_env
        ai_groups = env["ai_groups"]
        gid, tid = env["make_task"]()
        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal="写个测试")

        result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req))

        assert env["calls"]["n"] == 1
        assert result["status"] == "scored"
        assert env["task_row"](tid)["status"] == "scored"


class TestConfirmEndpoint:
    def test_approve_resumes_execution_without_reprompt(self, confirm_env):
        """confirm approved=true：真放行执行（think 仍报 confirm_required 也不再拦）。"""
        env = confirm_env
        ai_groups = env["ai_groups"]
        env["think_modes"]["超时"] = "confirm_required"
        gid, tid = env["make_task"]()
        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal="修复src/api/echo.py里的超时bug")
        asyncio.run(ai_groups._execute_task_impl(gid, tid, req))
        assert env["task_row"](tid)["status"] == "awaiting_confirm"

        async def scenario():
            resp = await ai_groups.confirm_task(
                gid, tid, ai_groups.ConfirmTaskRequest(approved=True, note="放行"), None
            )
            assert resp.status_code == 202
            return await env["wait_status"](tid, ("scored", "failed"))

        row = asyncio.run(scenario())
        assert row["status"] == "scored", "确认放行后未恢复执行"
        assert env["calls"]["n"] == 1
        types = [t for t, _ in env["task_events"](tid)]
        assert "confirm-approved" in types
        assert types.count("confirm-required") == 1, "放行后重复要确认"

    def test_reject_terminates_with_reason(self, confirm_env):
        """confirm approved=false：任务终止、原因如实落 result、agent 永不执行。"""
        env = confirm_env
        ai_groups = env["ai_groups"]
        env["think_modes"]["超时"] = "confirm_required"
        gid, tid = env["make_task"]()
        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal="修复src/api/echo.py里的超时bug")
        asyncio.run(ai_groups._execute_task_impl(gid, tid, req))

        async def scenario():
            return await ai_groups.confirm_task(
                gid, tid, ai_groups.ConfirmTaskRequest(approved=False, note="太危险了"), None
            )

        resp = asyncio.run(scenario())
        assert resp.status_code == 200
        row = env["task_row"](tid)
        assert row["status"] == "failed"
        assert "太危险了" in row["result"] and "人工拒绝" in row["result"]
        assert env["calls"]["n"] == 0
        types = [t for t, _ in env["task_events"](tid)]
        assert "confirm-rejected" in types

    def test_confirm_wrong_status_409(self, confirm_env):
        """非 awaiting_confirm 任务不可裁决：409 如实，禁静默换状态。"""
        env = confirm_env
        ai_groups = env["ai_groups"]
        gid, tid = env["make_task"]()  # pending

        async def scenario():
            await ai_groups.confirm_task(
                gid, tid, ai_groups.ConfirmTaskRequest(approved=True), None
            )

        with pytest.raises(HTTPException) as ei:
            asyncio.run(scenario())
        assert ei.value.status_code == 409
        assert env["task_row"](tid)["status"] == "pending", "409 仍改了任务状态"

    def test_confirm_cross_tenant_403(self, confirm_env, monkeypatch):
        """by-ID 写走隔离闸门：跨租户裁决 403 禁裁决他租户任务（F3b-③b-1 纪律）。"""
        env = confirm_env
        ai_groups = env["ai_groups"]
        env["think_modes"]["超时"] = "confirm_required"
        gid, tid = env["make_task"]()
        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal="修复src/api/echo.py里的超时bug")
        asyncio.run(ai_groups._execute_task_impl(gid, tid, req))

        monkeypatch.setattr(
            ai_groups, "bind_identity", lambda auth: SimpleNamespace(tenant_id="tenant-b")
        )

        async def scenario():
            await ai_groups.confirm_task(
                gid, tid, ai_groups.ConfirmTaskRequest(approved=True), "Bearer x"
            )

        with pytest.raises(HTTPException) as ei:
            asyncio.run(scenario())
        assert ei.value.status_code == 403
        assert env["task_row"](tid)["status"] == "awaiting_confirm", "跨租户 403 仍改了状态"


class TestChainPauseResume:
    def test_chain_pauses_at_confirm_sub(self, confirm_env):
        """链遇确认门禁断在暂停处：后续子任务不抢跑、父任务不收口、复盘讨论不发起。"""
        env = confirm_env
        ai_groups = env["ai_groups"]
        env["think_modes"]["规划"] = "confirm_required"
        gid, pid, subs = env["make_chain"](["规划：梳理修复方案", "执行：改代码", "验证：跑测试"])

        asyncio.run(ai_groups._run_task_chain(gid, pid, discuss_after=True))

        rows = {r["id"]: r for r in env["all_tasks"]()}
        assert rows[subs[0]]["status"] == "awaiting_confirm"
        assert rows[subs[1]]["status"] == "pending", "后续子任务抢跑了"
        assert rows[subs[2]]["status"] == "pending", "后续子任务抢跑了"
        assert env["calls"]["n"] == 0
        assert rows[pid]["status"] == "planning", "暂停链提前收口父任务"
        parent_types = [t for t, _ in env["task_events"](pid)]
        assert "chain-paused" in parent_types
        assert "chain-finished" not in parent_types
        assert not any(r["status"] == "discussing" for r in rows.values()), "暂停链发起复盘讨论"

    def test_chain_resumes_after_confirm(self, confirm_env):
        """确认放行后链自动续跑剩余子任务：被确认子任务恰执行一次，链收口父任务。"""
        env = confirm_env
        ai_groups = env["ai_groups"]
        env["think_modes"]["规划"] = "confirm_required"
        gid, pid, subs = env["make_chain"](["规划：梳理修复方案", "执行：改代码", "验证：跑测试"])
        asyncio.run(ai_groups._run_task_chain(gid, pid))
        assert env["task_row"](subs[0])["status"] == "awaiting_confirm"

        async def scenario():
            await ai_groups.confirm_task(
                gid, subs[0], ai_groups.ConfirmTaskRequest(approved=True, note="按方案做"), None
            )
            await env["wait_status"](pid, ("scored", "failed", "skipped"))

        asyncio.run(scenario())

        rows = {r["id"]: r for r in env["all_tasks"]()}
        assert rows[subs[0]]["status"] == "scored"
        assert rows[subs[1]]["status"] == "scored"
        assert rows[subs[2]]["status"] == "scored"
        assert rows[pid]["status"] == "scored", "续跑后父任务未收口"
        assert env["calls"]["n"] == 3, (
            f"子任务双执行/漏执行: calls={env['calls']['n']} goals={env['calls']['goals']}"
        )
        parent_types = [t for t, _ in env["task_events"](pid)]
        assert "chain-finished" in parent_types
        # 被确认子任务恰一次 confirm-required（放行后不重复要确认）
        sub0_types = [t for t, _ in env["task_events"](subs[0])]
        assert sub0_types.count("confirm-required") == 1

    def test_chain_multi_round_confirms(self, confirm_env):
        """逐子任务确认语义（live 实证定性）：每个含破坏性意图的子任务各闸一次，
        链逐段暂停逐段续跑，禁一次放行偷跑其余高风险子任务。"""
        env = confirm_env
        ai_groups = env["ai_groups"]
        env["think_modes"]["规划"] = "confirm_required"
        env["think_modes"]["执行"] = "confirm_required"
        gid, pid, subs = env["make_chain"](["规划：梳理修复方案", "执行：改代码", "验证：跑测试"])
        asyncio.run(ai_groups._run_task_chain(gid, pid))
        assert env["task_row"](subs[0])["status"] == "awaiting_confirm"
        assert env["task_row"](subs[1])["status"] == "pending", "后续高风险子任务被偷跑"

        async def scenario():
            await ai_groups.confirm_task(
                gid, subs[0], ai_groups.ConfirmTaskRequest(approved=True, note="第一步放行"), None
            )
            await env["wait_status"](subs[1], ("awaiting_confirm", "scored", "failed"))
            assert env["task_row"](subs[1])["status"] == "awaiting_confirm", "第二段未独立要确认"
            await ai_groups.confirm_task(
                gid, subs[1], ai_groups.ConfirmTaskRequest(approved=True, note="第二步放行"), None
            )
            await env["wait_status"](pid, ("scored", "failed", "skipped"))

        asyncio.run(scenario())
        rows = {r["id"]: r for r in env["all_tasks"]()}
        assert rows[pid]["status"] == "scored"
        assert env["calls"]["n"] == 3, f"calls={env['calls']['n']}"
        for sid in (subs[0], subs[1]):
            assert [t for t, _ in env["task_events"](sid)].count("confirm-required") == 1
        parent_types = [t for t, _ in env["task_events"](pid)]
        assert parent_types.count("chain-paused") == 2, f"每段暂停恰一次: {parent_types}"
        assert "chain-finished" in parent_types


class TestGovernanceContract:
    def test_awaiting_confirm_not_swept_and_parent_open(self, confirm_env):
        """awaiting_confirm 已启动过 think（非"从未启动"）：TTL 治理不强杀、父任务不提前收口。"""
        env = confirm_env
        ai_groups = env["ai_groups"]
        gid, pid, subs = env["make_chain"](
            ["规划：梳理修复方案", "执行：改代码"],
            sub_statuses=["awaiting_confirm", "scored"],
        )
        # 超龄 48h（> TTL 24h）——若被治理强杀应变 skipped
        conn = ai_groups.get_db()
        try:
            conn.execute(
                "UPDATE ai_group_tasks SET updated_at=?, created_at=? WHERE id=?",
                ("2026-10-10T12:00:00", "2026-10-10T12:00:00", subs[0]),
            )
            stats = ai_groups.govern_stale_tasks(conn, gid, tenant_id="default")
            conn.commit()
        finally:
            conn.close()

        assert env["task_row"](subs[0])["status"] == "awaiting_confirm", "TTL 治理强杀了等确认任务"
        assert env["task_row"](pid)["status"] == "planning", "子任务未全终结父任务被提前收口"
        assert stats["skipped_stale"] == 0
        # 契约锁定：awaiting_confirm 非终结态（父任务收口判定依赖此语义）
        assert "awaiting_confirm" not in ai_groups._TASK_TERMINAL_STATUSES


class TestConfirmTriggerFace:
    """触发面（真认知管线，不桩 think）：破坏性意图 → risk≥high → confirm_required 可达。
    修前 risk 只看文件/经验（ai_groups 的 think 不带 repo_root → target_files 恒空 →
    overall 恒 low）→ confirm_required 在执行路径构造上不可达=确认门禁永不触发。"""

    def test_destructive_intent_triggers_confirm_required(self, confirm_env):
        brain_api = confirm_env["brain_api"]

        async def scenario():
            return await confirm_env["real_think"](
                brain_api.ThinkRequest(agent_id="mimo", user_input="清空生产数据库所有表")
            )

        result = asyncio.run(scenario())
        assert result["risk"]["overall_level"] == "high"
        assert any(r["title"] == "破坏性操作意图" for r in result["risk"]["risks"])
        assert result["decision"]["execute_mode"] == "confirm_required"
        assert "破坏性" in result["decision"]["confirm_prompt"]

    def test_benign_intent_stays_auto(self, confirm_env):
        brain_api = confirm_env["brain_api"]

        async def scenario():
            return await confirm_env["real_think"](
                brain_api.ThinkRequest(agent_id="mimo", user_input="修复src/api/echo.py里的超时bug")
            )

        result = asyncio.run(scenario())
        assert result["risk"]["overall_level"] == "low"
        assert result["decision"]["execute_mode"] == "auto"

    def test_real_brain_gate_end_to_end(self, confirm_env, monkeypatch):
        """真 brain→真门禁→真裁决全链（不桩 think）：破坏性目标暂停→人工放行→执行收口。"""
        env = confirm_env
        ai_groups, brain_api = env["ai_groups"], env["brain_api"]
        monkeypatch.setattr(brain_api, "think", env["real_think"])
        gid, tid = env["make_task"](goal="格式化备份盘")
        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal="格式化备份盘")

        result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req))
        assert result["status"] == "confirm_required"
        assert env["calls"]["n"] == 0
        assert env["task_row"](tid)["status"] == "awaiting_confirm"

        async def scenario():
            await ai_groups.confirm_task(
                gid, tid, ai_groups.ConfirmTaskRequest(approved=True, note="已备份，放行"), None
            )
            return await env["wait_status"](tid, ("scored", "failed"))

        row = asyncio.run(scenario())
        assert row["status"] == "scored", "真 brain 放行后未恢复执行"
        assert env["calls"]["n"] == 1
