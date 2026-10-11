"""11.2 _run_soulmate_turn 挂死可见性：进度心跳 + 执行时长可查询 + 可显式取消（ACP WS 回合通道）。

红线：同样**不加到点 SIGKILL**——timeout 只是等待预算（耗尽如实报未完成，禁把超时
当成功）；显式取消 = 停止本次执行等待并摘观察窗（会话生命周期归系统，不杀远端生成）。
全部走真实 websockets 服务端（非 mock），协议对齐 live :8092/ws/acp 线协议。
"""

import asyncio
import json

import pytest
import websockets

import src.api.ai_groups as ai_groups
from src.api import ws_chat


def make_handler(scenario: str):
    """fake ACP 服务端：initialize/session/new/session/prompt 三段线协议。

    scenario:
      stream — prompt 后流式 3 块再收尾（正常回合）
      hang   — prompt 后发 1 块即挂住（等待显式取消）
      silent — prompt 后零输出（等待预算耗尽形态）
    """

    async def handler(ws):
        try:
            async for raw in ws:
                msg = json.loads(raw)
                method = msg.get("method")
                mid = msg.get("id")
                if method == "initialize":
                    await ws.send(
                        json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": 1}})
                    )
                elif method == "session/new":
                    await ws.send(
                        json.dumps(
                            {"jsonrpc": "2.0", "id": mid, "result": {"sessionId": "om-fake-turn"}}
                        )
                    )
                elif method == "session/prompt":
                    if scenario == "silent":
                        await ws.wait_closed()
                        return
                    for i in range(1 if scenario == "hang" else 3):
                        await ws.send(
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "method": "session/update",
                                    "params": {
                                        "update": {
                                            "sessionUpdate": "agent_message_chunk",
                                            "content": {"text": f"chunk-{i} "},
                                        }
                                    },
                                }
                            )
                        )
                        await asyncio.sleep(0.2)
                    if scenario == "hang":
                        await ws.wait_closed()
                        return
                    await ws.send(
                        json.dumps(
                            {"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}}
                        )
                    )
        except Exception:
            pass  # 客户端显式取消关连接等，fake 服务端不冒泡

    return handler


@pytest.fixture()
def fake_acp(monkeypatch):
    """起 fake ACP 服务端并把 ai_groups.SOULMATE_WS 指过去。"""

    async def _start(scenario: str):
        server = await websockets.serve(make_handler(scenario), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(ai_groups, "SOULMATE_WS", f"ws://127.0.0.1:{port}")
        return server

    return _start


def test_soulmate_turn_heartbeat_and_queryable_runtime(fake_acp):
    """慢回合可见进度（心跳事件）+ 执行时长可查询（运行中快照带 elapsed/last_event/块数）。"""

    async def main():
        server = await fake_acp("stream")
        events = []
        run_id = "run-smt-heartbeat"

        async def on_event(etype, line):
            events.append((etype, line))

        task = asyncio.create_task(
            ai_groups._run_soulmate_turn(
                "探针目标",
                timeout=15,
                on_event=on_event,
                run_id=run_id,
                task_ref="g-smt/t-smt",
                heartbeat_s=0.15,
            )
        )
        await asyncio.sleep(0.5)
        mid = ws_chat.proxy_run_snapshot(run_id)
        assert mid is not None
        assert mid["status"] == "running"
        assert mid["channel"] == "acp_ws"
        assert mid["elapsed_s"] > 0
        assert mid["task_ref"] == "g-smt/t-smt"
        assert "chunk-" in (mid["last_event"] or "")
        resp, source, ok = await asyncio.wait_for(task, 10)
        server.close()
        await server.wait_closed()
        return resp, source, ok, events

    resp, source, ok, events = asyncio.run(main())
    assert ok is True and source == "soulmate-agent"
    assert "chunk-0" in resp and "chunk-2" in resp
    heartbeats = [line for etype, line in events if etype == "heartbeat"]
    assert len(heartbeats) >= 2, f"心跳事件不足: {events}"
    assert any("SoulMate回合进行中" in h and "已用" in h for h in heartbeats)
    done = ws_chat.proxy_run_snapshot("run-smt-heartbeat")
    assert done["status"] == "done"
    assert done["lines"] == 3


def test_soulmate_turn_explicit_cancel(fake_acp):
    """显式取消：摘观察窗停止本次执行等待，返回如实报'已被显式取消'（禁冒充正常完成）。"""

    async def main():
        server = await fake_acp("hang")
        run_id = "run-smt-cancel"
        task = asyncio.create_task(
            ai_groups._run_soulmate_turn("长回合目标", timeout=30, run_id=run_id, heartbeat_s=0.2)
        )
        await asyncio.sleep(0.5)
        res = await ws_chat.cancel_proxy_run(run_id)
        resp, source, ok = await asyncio.wait_for(task, 5)
        server.close()
        await server.wait_closed()
        return res, resp, source, ok

    res, resp, source, ok = asyncio.run(main())
    assert res["ok"] is True
    assert res["mode"] == "acp_ws_detach" and res["signal"] is None
    assert res["force"] is False
    assert source == "cancelled" and ok is False
    assert "显式取消" in resp
    snap = ws_chat.proxy_run_snapshot("run-smt-cancel")
    assert snap["status"] == "cancelled"
    assert snap["cancel_requested"] is True


def test_soulmate_turn_timeout_is_not_success(fake_acp):
    """等待预算耗尽：如实报回合未完成（禁把超时当成功），已收部分保留可见。"""

    async def main():
        server = await fake_acp("silent")
        run_id = "run-smt-timeout"
        resp, source, ok = await asyncio.wait_for(
            ai_groups._run_soulmate_turn("沉默回合", timeout=1.2, run_id=run_id, heartbeat_s=0.3),
            10,
        )
        server.close()
        await server.wait_closed()
        return resp, source, ok

    resp, source, ok = asyncio.run(main())
    assert ok is False, "超时被当成功（禁）"
    assert "等待超时" in resp and "回合未完成" in resp
    snap = ws_chat.proxy_run_snapshot("run-smt-timeout")
    assert snap["status"] == "timeout"


def test_soulmate_turn_normal_turn_not_mis_killed(fake_acp):
    """正常回合不被误杀：无到点自动杀，完整跑完收尾。"""

    async def main():
        server = await fake_acp("stream")
        resp, source, ok = await asyncio.wait_for(
            ai_groups._run_soulmate_turn("正常回合", timeout=15, run_id="run-smt-ok"), 15
        )
        server.close()
        await server.wait_closed()
        return resp, source, ok

    resp, source, ok = asyncio.run(main())
    assert ok is True
    assert "chunk-2" in resp
    assert ws_chat.proxy_run_snapshot("run-smt-ok")["status"] == "done"


def test_soulmate_ai_groups_endpoints_query_and_cancel(fake_acp, tmp_path, monkeypatch):
    """ai_groups 任务级端点接线：exec-status 可查 acp 回合、exec-cancel 真取消 soulmate 回合。"""
    monkeypatch.setattr(ai_groups, "DB_PATH", tmp_path / "ai_groups.db")
    ai_groups.init_db()
    gid, tid = "g-smt-api", "t-smt-api"
    now = "2026-10-11T08:30:00"
    conn = ai_groups.get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO ai_groups (id,name,description,status,created_at) VALUES (?,?,?,?,?)",
            (gid, "smt", "", "active", now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO ai_group_tasks (id,group_id,goal,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (tid, gid, "慢回合探针", "executing", now, now),
        )
        conn.commit()
    finally:
        conn.close()

    async def main():
        server = await fake_acp("hang")
        task = asyncio.create_task(
            ai_groups._run_soulmate_turn(
                "慢回合目标",
                timeout=30,
                run_id="run-smt-api",
                task_ref=f"{gid}/{tid}",
                heartbeat_s=0.2,
            )
        )
        await asyncio.sleep(0.5)
        status = ai_groups.get_task_exec_status(gid, tid)
        res = await ai_groups.cancel_task_exec(gid, tid, ai_groups.CancelExecRequest(force=False))
        resp, source, ok = await asyncio.wait_for(task, 5)
        after = ai_groups.get_task_exec_status(gid, tid)
        server.close()
        await server.wait_closed()
        return status, res, resp, source, ok, after

    status, res, resp, source, ok, after = asyncio.run(main())
    assert status["task_status"] == "executing"
    assert status["run"] is not None and status["run"]["status"] == "running"
    assert status["run"]["channel"] == "acp_ws"
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
    assert any(e == "cancel-requested" and "acp_ws_detach" in c for e, c in evs), evs


def test_no_auto_kill_in_soulmate_turn_source():
    """源码契约：_run_soulmate_turn 里不存在任何到点自动杀（killpg/SIGKILL）。"""
    import inspect

    src = inspect.getsource(ai_groups._run_soulmate_turn)
    assert "killpg" not in src, "ACP WS 回合通道不得有 killpg"
    assert "SIGKILL" not in src and "SIGTERM" not in src, "回合通道不得发终止信号"
    assert "register_proxy_run" in src, "必须进执行登记表（可见性前提）"
