"""10.3 run_agent_proxy 挂死可见性：进度心跳 + 执行时长可查询 + 可显式取消。

红线：**不加到点 SIGKILL**——正常长回合绝不被自动杀；取消只能显式发起
（SIGTERM→有界等待→force=True 才 SIGKILL）。全部走真实子进程（非 mock）。
"""

import asyncio
import os
import time

import pytest

from src.api import ws_chat

SLOW_PROGRESS_SCRIPT = """
import sys, time
for i in range(6):
    print(f"progress line {i}", flush=True)
    time.sleep(0.2)
print("DONE_MARKER", flush=True)
"""

LONG_RUN_SCRIPT = """
import time
print("START", flush=True)
time.sleep(60)
"""

SIGTERM_IGNORING_SCRIPT = """
import signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
print("IGNORE_READY", flush=True)
time.sleep(60)
"""


@pytest.fixture()
def cli_probe(tmp_path, monkeypatch):
    """把 python3 登记成可执行探针 CLI（run_agent_proxy cmd=binary+CLI_ARGS+text）。"""

    def _install(script: str, key: str = "python3") -> str:
        p = tmp_path / "probe.py"
        p.write_text(script, encoding="utf-8")
        monkeypatch.setitem(ws_chat.CLI_ARGS, key, ["-u", str(p)])
        return str(p)

    return _install


def test_heartbeat_progress_and_queryable_runtime(cli_probe):
    """慢回合可见进度（心跳事件）+ 执行时长可查询（运行中快照带 elapsed/last_event）。"""
    cli_probe(SLOW_PROGRESS_SCRIPT)
    events = []

    async def main():
        run_id = "run-ctl-heartbeat"

        async def on_event(etype, line):
            events.append((etype, line))

        task = asyncio.create_task(
            ws_chat.run_agent_proxy(
                "python3", "probe", on_event=on_event, run_id=run_id, heartbeat_s=0.15
            )
        )
        await asyncio.sleep(0.55)
        mid = ws_chat.proxy_run_snapshot(run_id)
        assert mid is not None
        assert mid["status"] == "running"
        assert mid["elapsed_s"] > 0
        assert mid["pid"] and mid["pid"] > 0
        assert "progress" in (mid["last_event"] or "")
        resp, source, ok = await asyncio.wait_for(task, 10)
        return resp, source, ok

    resp, source, ok = asyncio.run(main())
    assert ok is True and source == "python3"
    assert "DONE_MARKER" in resp
    heartbeats = [line for etype, line in events if etype == "heartbeat"]
    assert len(heartbeats) >= 2, f"心跳事件不足: {events}"
    assert any("执行进行中" in h and "已用" in h for h in heartbeats)
    done = ws_chat.proxy_run_snapshot("run-ctl-heartbeat")
    assert done["status"] == "done"
    assert done["returncode"] == 0


def test_explicit_cancel_stops_long_run(cli_probe):
    """显式取消：SIGTERM 停长回合，返回如实报'已被显式取消'（禁冒充正常完成）。"""
    cli_probe(LONG_RUN_SCRIPT)

    async def main():
        run_id = "run-ctl-cancel"
        task = asyncio.create_task(
            ws_chat.run_agent_proxy("python3", "probe", run_id=run_id, heartbeat_s=0.2)
        )
        await asyncio.sleep(0.5)
        res = await ws_chat.cancel_proxy_run(run_id)
        resp, source, ok = await asyncio.wait_for(task, 5)
        return res, resp, source, ok

    res, resp, source, ok = asyncio.run(main())
    assert res["ok"] is True
    assert res["signal"] == "SIGTERM" and res["force"] is False
    assert res["exited"] is True
    assert source == "cancelled" and ok is False
    assert "显式取消" in resp
    snap = ws_chat.proxy_run_snapshot("run-ctl-cancel")
    assert snap["status"] == "cancelled"
    assert snap["cancel_requested"] is True


def test_normal_long_run_not_mis_killed(cli_probe):
    """正常长回合不被误杀：无到点超时杀，2.5s 回合完整跑完（超心跳间隔/软预算任何点位）。"""
    cli_probe(
        """
import time
for i in range(5):
    print(f"tick {i}", flush=True)
    time.sleep(0.5)
print("FIN_OK", flush=True)
"""
    )

    async def main():
        run_id = "run-ctl-long-ok"
        t0 = time.monotonic()
        resp, source, ok = await asyncio.wait_for(
            ws_chat.run_agent_proxy("python3", "probe", run_id=run_id, heartbeat_s=0.3),
            15,
        )
        return resp, source, ok, time.monotonic() - t0

    resp, source, ok, elapsed = asyncio.run(main())
    assert ok is True
    assert "FIN_OK" in resp
    assert elapsed >= 2.0
    assert ws_chat.proxy_run_snapshot("run-ctl-long-ok")["status"] == "done"


def test_cancel_force_sigkill_for_sigterm_ignoring(cli_probe):
    """两级显式取消：SIGTERM 无效时 force=True 才升级 SIGKILL（仍非自动杀）。"""
    cli_probe(SIGTERM_IGNORING_SCRIPT)

    async def main():
        run_id = "run-ctl-force"
        task = asyncio.create_task(
            ws_chat.run_agent_proxy("python3", "probe", run_id=run_id, heartbeat_s=0.2)
        )
        await asyncio.sleep(0.5)
        res1 = await ws_chat.cancel_proxy_run(run_id, force=False)
        res2 = await ws_chat.cancel_proxy_run(run_id, force=True)
        resp, source, ok = await asyncio.wait_for(task, 5)
        return res1, res2, resp, source, ok

    res1, res2, resp, source, ok = asyncio.run(main())
    assert res1["ok"] is True and res1["exited"] is False  # SIGTERM 被忽略，如实报未退出
    assert res2["ok"] is True and res2["signal"] == "SIGKILL" and res2["exited"] is True
    assert source == "cancelled" and ok is False
    assert "显式取消" in resp


def test_task_ref_lookup_and_listing(cli_probe):
    """task_ref 查找键：运行中命中、结束后 running_only 查不到、快照可列举。"""
    cli_probe(SLOW_PROGRESS_SCRIPT)

    async def main():
        task = asyncio.create_task(
            ws_chat.run_agent_proxy(
                "python3",
                "probe",
                task_ref="g-ctl/t-ctl",
                run_id="run-ctl-ref",
                heartbeat_s=0.2,
            )
        )
        await asyncio.sleep(0.3)
        during = ws_chat.find_proxy_run("g-ctl/t-ctl")
        await asyncio.wait_for(task, 10)
        after_running = ws_chat.find_proxy_run("g-ctl/t-ctl")
        after_any = ws_chat.find_proxy_run("g-ctl/t-ctl", running_only=False)
        return during, after_running, after_any

    during, after_running, after_any = asyncio.run(main())
    assert during is not None and during["run_id"] == "run-ctl-ref"
    assert during["task_ref"] == "g-ctl/t-ctl"
    assert after_running is None
    assert after_any is not None and after_any["status"] == "done"
    listed = ws_chat.list_proxy_runs(task_ref="g-ctl/t-ctl")
    assert [s["run_id"] for s in listed] == ["run-ctl-ref"]


def test_ai_groups_endpoints_query_and_cancel(cli_probe, tmp_path, monkeypatch):
    """ai_groups 任务级端点接线：exec-status 可查在跑执行、exec-cancel 真取消真实子进程。"""
    import src.api.ai_groups as ai_groups

    monkeypatch.setattr(ai_groups, "DB_PATH", tmp_path / "ai_groups.db")
    ai_groups.init_db()
    gid, tid = "g-ctl-api", "t-ctl-api"
    now = "2026-10-11T08:00:00"
    conn = ai_groups.get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO ai_groups (id,name,description,status,created_at) VALUES (?,?,?,?,?)",
            (gid, "ctl", "", "active", now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO ai_group_tasks (id,group_id,goal,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (tid, gid, "慢回合探针", "executing", now, now),
        )
        conn.commit()
    finally:
        conn.close()

    cli_probe(LONG_RUN_SCRIPT)

    async def main():
        run_id = "run-ctl-api"
        task = asyncio.create_task(
            ws_chat.run_agent_proxy(
                "python3",
                "probe",
                task_ref=f"{gid}/{tid}",
                run_id=run_id,
                heartbeat_s=0.2,
            )
        )
        await asyncio.sleep(0.5)
        status = ai_groups.get_task_exec_status(gid, tid)
        res = await ai_groups.cancel_task_exec(gid, tid, ai_groups.CancelExecRequest(force=False))
        resp, source, ok = await asyncio.wait_for(task, 5)
        after = ai_groups.get_task_exec_status(gid, tid)
        return status, res, resp, source, ok, after

    status, res, resp, source, ok, after = asyncio.run(main())
    assert status["task_status"] == "executing"
    assert status["run"] is not None and status["run"]["status"] == "running"
    assert status["run"]["elapsed_s"] > 0
    assert res["ok"] is True and source == "cancelled" and ok is False
    assert after["run"]["status"] == "cancelled"
    conn = ai_groups.get_db()
    try:
        rows = conn.execute(
            "SELECT event_type, content FROM task_execution_events WHERE task_id=? ORDER BY id",
            (tid,),
        ).fetchall()
    finally:
        conn.close()
    evs = [(r["event_type"], r["content"]) for r in rows]
    assert any(e == "cancel-requested" and "SIGTERM" in c for e, c in evs), evs

    # 无可取消对象时显式 404，不造假
    with pytest.raises(Exception) as exc:
        asyncio.run(ai_groups.cancel_task_exec(gid, tid, ai_groups.CancelExecRequest()))
    assert "404" in str(exc.value) or "无可取消对象" in str(exc.value)


def test_execute_impl_cancel_no_retry_and_task_ref_wiring(tmp_path, monkeypatch):
    """_execute_task_impl 接线：task_ref 透传 + 显式取消不自动重试、任务以取消收口。"""
    import src.api.ai_groups as ai_groups

    monkeypatch.setattr(ai_groups, "DB_PATH", tmp_path / "ai_groups.db")
    ai_groups.init_db()
    gid, tid = "g-ctl-impl", "t-ctl-impl"
    now = "2026-10-11T08:00:00"
    conn = ai_groups.get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO ai_groups (id,name,description,status,created_at) VALUES (?,?,?,?,?)",
            (gid, "ctl-impl", "", "active", now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO ai_group_tasks (id,group_id,goal,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (tid, gid, "慢回合", "pending", now, now),
        )
        conn.commit()
    finally:
        conn.close()

    calls = {"n": 0, "kwargs": None}

    async def fake_run_agent_proxy(agent_id, text, on_event=None, **kwargs):
        calls["n"] += 1
        calls["kwargs"] = kwargs
        return ("⚠️ 执行已被显式取消（用时 0.5s）", "cancelled", False)

    monkeypatch.setattr(ai_groups, "run_agent_proxy", fake_run_agent_proxy)
    req = ai_groups.ExecuteTaskRequest(agent_id="hermes", goal="慢回合")
    result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req))

    assert result["status"] == "cancelled" and result["success"] is False
    assert calls["n"] == 1, "显式取消被自动重试（不该发生）"
    assert calls["kwargs"].get("task_ref") == f"{gid}/{tid}"
    conn = ai_groups.get_db()
    try:
        row = conn.execute(
            "SELECT status, result FROM ai_group_tasks WHERE id=?", (tid,)
        ).fetchone()
        rows = conn.execute(
            "SELECT event_type FROM task_execution_events WHERE task_id=?", (tid,)
        ).fetchall()
    finally:
        conn.close()
    assert row["status"] == "failed"
    assert "显式取消" in row["result"]
    assert "cancelled" in [r["event_type"] for r in rows]
    # 取消收口不评分
    conn = ai_groups.get_db()
    try:
        scores = conn.execute(
            "SELECT COUNT(*) AS n FROM agent_scores WHERE task_id=?", (tid,)
        ).fetchone()
    finally:
        conn.close()
    assert scores["n"] == 0


def test_no_auto_kill_path_in_source():
    """源码契约：run_agent_proxy 挂死治理里不存在到点自动杀（wait_for/timeout 杀进程类）。"""
    import inspect

    src = inspect.getsource(ws_chat.run_agent_proxy)
    assert "asyncio.wait_for" not in src, "执行主路径不得有到点超时杀"
    assert "killpg" not in src or "cancel_proxy_run" in inspect.getsource(ws_chat), (
        "killpg 只允许出现在显式取消路径"
    )
    # 取消只由显式入口发起
    assert "def cancel_proxy_run" in inspect.getsource(ws_chat)
