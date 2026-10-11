"""F4b-④ 群组 SoulMate 逐块推送：session/update chunk 即刻 on_event("output")。

定性（F4 审计⑥）：_run_soulmate_turn 攒 chunk 到回合结束一次性 agent_reply 回群，
执行期间任务时间线只有心跳（黑箱），与 CLI 通道（run_agent_proxy _pump 逐行
on_event→_emit_exec_event→task_execution_events→ws_group 尾随 task_progress WS 帧）
逐行可见不对称。

修复语义：每收一块即刻 on_event("output")，与 CLI stdout 同一分类/同一通道
（群组里即 _on_exec_event→task_execution_events→task_progress WS 帧，逐块实时
可见+可回放）；聊天流仍回合结束一条完整 agent_reply（单条消息语义，禁逐块刷屏）。

覆盖：
1. 逐块真增量（双向门闩硬证明）：fake ACP 发 chunk-i 后必须等客户端真消费到该块的
   output 事件才放行下一块——攒块/伪流式实现门闩永不触发→超时失败（控制组语义
   内建）；每块独立成事件+文本逐字+顺序保持。
2. 通道对称：chunk 事件 etype 与 CLI 通道 _classify_stream_event stdout 分类逐值
   相等；心跳并行不混入块文本；登记表 lines/last_event 语义照旧；终文契约零变化。
3. 真实执行路径接线（_execute_task_impl soulmate 分支=execute/retry/任务链共用）：
   DB 级门闩——fake ACP 发 chunk-i 后等 task_execution_events 真出现该块 output 行
   才发下一块（on_event→_emit_exec_event→落库全链逐块实时硬证明）；output 行全部
   在 finished 之前；聊天流恰好一条 agent_reply+一条 result、GROUP_MSG_PUSH 恰好
   一次=禁逐块刷屏。
4. 旁路纪律：on_event 回调抛异常不阻塞回合（块照收、终文照旧、done 照常）。
"""

import asyncio
import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import aiosqlite
import pytest
import websockets

import src.api.ai_groups as ai_groups
from src.api import ws_chat
from src.database.postgres import _SQLiteConnection
from src.learn.long_term import LongTermLearning


class _AsyncDB:
    """生产适配器注入（test_recommendation_backflow.py 同款：测试即生产路径）。"""

    def __init__(self, path: Path):
        self._path = str(path)

    async def _wrapper(self):
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


def _chunk_frame(text: str) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"text": text},
                }
            },
        }
    )


def _chunk_frame_str_form(text: str) -> str:
    """session/update 的 content 也可能是裸字符串形态（_note_chunk 两分支都要覆盖）。"""
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"update": {"sessionUpdate": "agent_message_chunk", "content": text}},
        }
    )


def make_handler(chunk_texts, gate_fn=None, str_form_indices=(), inter_delay=0.0):
    """fake ACP 服务端：prompt 后逐块流式发 chunk，收尾 id 帧。

    gate_fn(i) 可选门闩（双向硬证明）：第 i 块发出后必须等 gate_fn(i) 返回 True
    才发第 i+1 块——客户端没有逐块消费（攒块/伪流式）则门闩永不触发，本 handler
    关连接按失败收口，测试断言侧必炸（控制组语义内建）。
    """

    async def handler(ws):
        try:
            async for raw in ws:
                msg = json.loads(raw)
                method, mid = msg.get("method"), msg.get("id")
                if method == "initialize":
                    await ws.send(
                        json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": 1}})
                    )
                elif method == "session/new":
                    await ws.send(
                        json.dumps(
                            {"jsonrpc": "2.0", "id": mid, "result": {"sessionId": "om-fake-chunk"}}
                        )
                    )
                elif method == "session/prompt":
                    for i, text in enumerate(chunk_texts):
                        frame = (
                            _chunk_frame_str_form(text)
                            if i in set(str_form_indices)
                            else _chunk_frame(text)
                        )
                        await ws.send(frame)
                        if gate_fn is not None:
                            ok = await gate_fn(i)
                            if not ok:
                                return  # 门闩超时=端点未逐块消费：关连接收口，断言侧必炸
                        if inter_delay:
                            await asyncio.sleep(inter_delay)
                    await ws.send(
                        json.dumps(
                            {"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}}
                        )
                    )
        except Exception:
            pass  # 客户端显式取消关连接等，fake 服务端不冒泡

    return handler


async def _serve_and_turn(
    chunk_texts, turn_kwargs, gate_fn=None, str_form_indices=(), inter_delay=0.0
):
    """起 fake ACP + 跑 _run_soulmate_turn，返回(resp, source, ok, seen)。"""
    server = await websockets.serve(
        make_handler(chunk_texts, gate_fn, str_form_indices, inter_delay), "127.0.0.1", 0
    )
    port = server.sockets[0].getsockname()[1]
    ai_groups.SOULMATE_WS = f"ws://127.0.0.1:{port}"
    seen: list = []

    async def on_event(etype, line):
        seen.append((etype, line, time.monotonic()))

    turn_kwargs.setdefault("on_event", on_event)
    try:
        resp, source, ok = await asyncio.wait_for(ai_groups._run_soulmate_turn(**turn_kwargs), 30)
    finally:
        server.close()
        await server.wait_closed()
    return resp, source, ok, seen


class TestPerChunkPush:
    def test_chunk_events_per_chunk_bidirectional_gate(self, monkeypatch):
        """逐块硬证明：第 i 块的 output 事件被客户端真收到后，fake ACP 才发第 i+1 块。

        修前行为（攒块到回合结束一次性处理）门闩永不触发 → 发完第一块即卡死 → 关连接
        等待超时收口 → ok=False/事件缺失 → 断言失败。控制组语义内建于门闩。
        """
        chunk_texts = ["第一块 ", "第二块 ", "第三块"]
        collected: list = []

        async def main():
            gates = [asyncio.Event() for _ in chunk_texts]

            async def on_event(etype, line):
                if etype != "output":
                    return
                collected.append((line, time.monotonic()))
                gates[len(collected) - 1].set()

            async def gate_fn(i):
                try:
                    await asyncio.wait_for(gates[i].wait(), timeout=12)
                    return True
                except TimeoutError:
                    return False

            server = await websockets.serve(make_handler(chunk_texts, gate_fn), "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            ai_groups.SOULMATE_WS = f"ws://127.0.0.1:{port}"
            try:
                return await asyncio.wait_for(
                    ai_groups._run_soulmate_turn(
                        "逐块探针", timeout=25, on_event=on_event, run_id="run-chunk-gate"
                    ),
                    30,
                )
            finally:
                server.close()
                await server.wait_closed()

        resp, source, ok = asyncio.run(main())
        assert ok is True and source == "soulmate-agent"
        outs = [line for line, _ in collected]
        assert outs == chunk_texts, f"每块独立成事件+逐字+顺序保持: {outs}"
        ts = [t for _, t in collected]
        assert all(b > a for a, b in zip(ts, ts[1:])), "事件到达时间戳须严格递增"
        assert resp == "".join(chunk_texts).strip(), "终文契约零变化"
        assert ws_chat.proxy_run_snapshot("run-chunk-gate")["status"] == "done"

    def test_chunk_event_type_matches_cli_channel(self, monkeypatch):
        """通道对称：chunk 事件 etype 与 CLI 通道 stdout 分类逐值相等（同一分类器），
        心跳并行不混入块文本、登记表 lines/last_event 语义照旧、终文契约零变化。"""
        chunk_texts = ["前段回复", "，中段回复", "，末段"]

        async def main():
            return await _serve_and_turn(
                chunk_texts,
                {
                    "goal": "对称探针",
                    "timeout": 25,
                    "run_id": "run-chunk-parity",
                    "heartbeat_s": 0.15,
                },
                str_form_indices={2},
                inter_delay=0.25,
            )

        resp, source, ok, seen = asyncio.run(main())
        assert ok is True and source == "soulmate-agent"
        outs = [line for et, line, _ in seen if et == "output"]
        assert outs == chunk_texts, f"逐块事件文本逐字+顺序: {outs}"
        # CLI 同径分类等价：同文本在 CLI 通道 _pump 也归 "output"（_classify_stream_event）
        assert all(
            et == ws_chat._classify_stream_event("output", line)
            for et, line, _ in seen
            if et == "output"
        )
        heartbeats = [line for et, line, _ in seen if et == "heartbeat"]
        assert any("SoulMate回合进行中" in h for h in heartbeats), f"心跳缺席: {seen}"
        assert all(h not in chunk_texts for h in heartbeats), "心跳混入块文本"
        snap = ws_chat.proxy_run_snapshot("run-chunk-parity")
        assert snap["status"] == "done"
        assert snap["lines"] == 3, "登记表逐块计数语义照旧"
        assert "末段" in (snap["last_event"] or ""), "last_event=最后一块"
        assert resp == "".join(chunk_texts).strip(), "终文契约零变化"


class TestWiredIntoGroupExecution:
    def test_chunks_land_in_task_execution_events_during_turn(self, tmp_path, monkeypatch):
        """真实执行路径接线（_execute_task_impl soulmate 分支）：DB 级门闩——fake ACP
        发 chunk-i 后等 task_execution_events 真出现该块的 output 行才发下一块
        （on_event→_emit_exec_event→落库全链逐块实时硬证明）；output 行全部在
        finished 之前；聊天流恰好一条 agent_reply+一条 result、GROUP_MSG_PUSH 恰好
        一次（禁逐块刷屏）。"""
        import src.database.postgres as pg
        from src.api import brain as brain_api
        from src.hippo.memory_store import MemoryStore

        db_path = tmp_path / "ai_groups.db"
        monkeypatch.setattr(ai_groups, "DB_PATH", db_path)
        ai_groups.init_db()
        monkeypatch.setattr(brain_api, "store", MemoryStore())
        learn_path = tmp_path / "learn.db"
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(learn_path))
        asyncio.run(LongTermLearning(_AsyncDB(learn_path), "default", "default")._ensure_table())

        received: list = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                received.append(self.rfile.read(length))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *args):
                pass

        push_server = HTTPServer(("127.0.0.1", 0), Handler)
        push_port = push_server.server_address[1]
        push_thread = threading.Thread(target=push_server.serve_forever, daemon=True)
        push_thread.start()
        monkeypatch.setattr(
            ai_groups, "GROUP_MSG_PUSH", f"http://127.0.0.1:{push_port}/internal/group-message"
        )

        chunk_texts = ["流式甲 ", "流式乙 ", "流式丙"]
        gid, tid = "g-chunk-wire", "t-chunk-wire"
        now = "2026-10-12T01:00:00"
        conn = ai_groups.get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO ai_groups (id,name,description,status,created_at) VALUES (?,?,?,?,?)",
                (gid, "chunk-wire", "", "active", now),
            )
            conn.execute(
                "INSERT OR REPLACE INTO ai_group_tasks (id,group_id,goal,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (tid, gid, "逐块推送验证目标", "pending", now, now),
            )
            conn.commit()
        finally:
            conn.close()

        def db_gate(i):
            """DB 级门闩：task_execution_events 里真出现第 i 块的 output 行才放行下一块。"""

            async def _wait():
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    c = sqlite3.connect(str(db_path))
                    try:
                        row = c.execute(
                            "SELECT COUNT(*) FROM task_execution_events WHERE event_type='output' AND content=?",
                            (chunk_texts[i],),
                        ).fetchone()
                    finally:
                        c.close()
                    if row and row[0] >= 1:
                        return True
                    await asyncio.sleep(0.05)
                return False

            return _wait()

        req = ai_groups.ExecuteTaskRequest(agent_id="soulmate", goal="逐块推送验证目标")

        async def main():
            server = await websockets.serve(
                make_handler(chunk_texts, gate_fn=db_gate, inter_delay=0.05), "127.0.0.1", 0
            )
            port = server.sockets[0].getsockname()[1]
            monkeypatch.setattr(ai_groups, "SOULMATE_WS", f"ws://127.0.0.1:{port}")
            try:
                return await asyncio.wait_for(ai_groups._execute_task_impl(gid, tid, req), 60)
            finally:
                server.close()
                await server.wait_closed()

        try:
            result = asyncio.run(main())
        finally:
            push_server.shutdown()

        assert result["status"] == "scored" and result["success"] is True
        conn = ai_groups.get_db()
        try:
            rows = conn.execute(
                "SELECT event_type, content FROM task_execution_events WHERE task_id=? ORDER BY id",
                (tid,),
            ).fetchall()
            msgs = conn.execute(
                "SELECT intent, content FROM discussion_messages WHERE task_id=? ORDER BY rowid",
                (tid,),
            ).fetchall()
        finally:
            conn.close()
        types = [r["event_type"] for r in rows]
        out_rows = [r["content"] for r in rows if r["event_type"] == "output"]
        assert out_rows == chunk_texts, f"逐块落库（DB 门闩已证增量）: {out_rows}"
        assert "finished" in types
        out_indices = [i for i, t in enumerate(types) if t == "output"]
        assert max(out_indices) < types.index("finished"), "output 行须全在 finished 之前"
        intents = [m["intent"] for m in msgs]
        assert intents.count("agent_reply") == 1, f"agent_reply 须恰好一条: {intents}"
        assert intents.count("result") == 1
        assert len(msgs) == 2, f"禁逐块刷屏: {intents}"
        assert len(received) == 1, f"GROUP_MSG_PUSH 恰好一次（合并终稿）: {len(received)}"
        body = json.loads(received[0])
        assert body["content"] == result["response"][:2000]
        assert body["intent"] == "agent_reply"


class TestCallbackBypass:
    def test_on_event_failure_does_not_block_turn(self, monkeypatch):
        """旁路纪律：on_event 抛异常不影响回合本体（块照收、终文照旧、done 照常）。"""
        chunk_texts = ["甲", "乙"]
        calls = {"n": 0}

        def bad_event(etype, line):
            calls["n"] += 1
            raise RuntimeError("回调炸了")

        async def main():
            server = await websockets.serve(make_handler(chunk_texts), "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            ai_groups.SOULMATE_WS = f"ws://127.0.0.1:{port}"
            try:
                return await asyncio.wait_for(
                    ai_groups._run_soulmate_turn(
                        "旁路探针",
                        timeout=25,
                        on_event=bad_event,
                        run_id="run-chunk-bypass",
                    ),
                    30,
                )
            finally:
                server.close()
                await server.wait_closed()

        resp, source, ok = asyncio.run(main())
        assert ok is True and source == "soulmate-agent"
        assert resp == "甲乙", "终文照旧"
        assert calls["n"] >= 2, f"每块都尝试回调（逐块推送未被回调故障掐断）: {calls}"
        assert ws_chat.proxy_run_snapshot("run-chunk-bypass")["status"] == "done"
