#!/usr/bin/env python3
"""G1-⑤b live e2e 探针：check_file_conflict·send_message·get_messages 消费面。

用法：
  .venv/bin/python scripts/e2e_coord_consumption.py --pre   # 修前（旧代码进程）对照
  .venv/bin/python scripts/e2e_coord_consumption.py         # 修后全链

真 websockets 客户端 + 真 sqlite 回读（data/opensoul.db 协调注册表/收件箱 +
data/agent_collaboration.db agents 行）。取证后清痕 remaining=0。
"""

import argparse
import asyncio
import json
import sqlite3
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8090"
COORD_DB = "data/opensoul.db"
COLLAB_DB = "data/agent_collaboration.db"

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL"), name, detail if not cond else "")


def api(method, path, body=None, headers=None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def rows(sql, params=(), db=COORD_DB):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def exec_sql(sql, params=(), db=COORD_DB):
    conn = sqlite3.connect(db)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def register(agent_id, role="runner"):
    r = api("POST", "/api/collab/register", {"agent_id": agent_id, "name": agent_id, "role": role})
    return r["token"]


async def ws_session(agent_id, token, steps):
    """steps: list of (send_dict_or_None, expect_predicate_or_None, label)。
    返回 frames 列表。"""
    import websockets

    uri = f"ws://127.0.0.1:8090/api/collab/ws/agent/{agent_id}" + (f"?token={token}" if token else "")
    frames = []
    async with websockets.connect(uri, open_timeout=10) as ws:
        for send, recv_n, label in steps:
            if send is not None:
                await ws.send(json.dumps(send))
            for _ in range(recv_n or 0):
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                frames.append(json.loads(raw))
    return frames


async def pre_probes():
    """修前对照（旧代码进程）：声明被吞、check_conflicts/inbox=Unknown event。"""
    import websockets

    tok = register("e2e-pre-g15b")
    uri = f"ws://127.0.0.1:8090/api/collab/ws/agent/e2e-pre-g15b?token={tok}"
    async with websockets.connect(uri, open_timeout=10) as ws:
        await ws.send(json.dumps({"event": "heartbeat", "current_task": "pre-task", "current_files": ["pre.py"]}))
        await asyncio.sleep(0.8)
        row = rows("SELECT current_task, current_files FROM agent_registry WHERE agent_id='e2e-pre-g15b'")
        declared = bool(row) and json.loads(row[0]["current_files"] or "[]") == ["pre.py"]
        check("pre: heartbeat 声明被吞（current_files 恒空）", not declared, str(row))
        await ws.send(json.dumps({"event": "check_conflicts", "files": ["pre.py"]}))
        f = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        check("pre: check_conflicts=Unknown event（缺口）", f.get("event") == "error" and "Unknown" in f.get("detail", ""), str(f))
        await ws.send(json.dumps({"event": "inbox"}))
        f = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        check("pre: inbox=Unknown event（缺口）", f.get("event") == "error" and "Unknown" in f.get("detail", ""), str(f))
    cleanup("e2e-pre-g15b")


def cleanup(*agents):
    for a in agents:
        exec_sql("DELETE FROM agent_registry WHERE agent_id=?", (a,))
        exec_sql("DELETE FROM agent_coord_messages WHERE from_agent=? OR to_agent=?", (a, a))
        exec_sql("DELETE FROM agents WHERE agent_id=?", (a,), db=COLLAB_DB)


async def post_probes():
    import websockets

    tok_a = register("e2e-g15b-a")
    tok_b = register("e2e-g15b-b")
    tok_c = register("e2e-g15b-c")

    # ① 声明面 + keep-alive 保留
    async with websockets.connect(f"ws://127.0.0.1:8090/api/collab/ws/agent/e2e-g15b-a?token={tok_a}", open_timeout=10) as wa:
        await wa.send(json.dumps({"event": "heartbeat", "current_task": "e2e-task", "current_files": ["e2e.py", "b.py"]}))
        await asyncio.sleep(0.5)
        row = rows("SELECT current_task, current_files FROM agent_registry WHERE agent_id='e2e-g15b-a'")
        check(
            "declare: current_task/current_files 真落协调行",
            bool(row) and json.loads(row[0]["current_files"] or "[]") == ["e2e.py", "b.py"] and row[0]["current_task"] == "e2e-task",
            str(row),
        )
        # keep-alive 保留（无字段心跳不洗声明）
        await wa.send(json.dumps({"event": "heartbeat"}))
        await asyncio.sleep(0.5)
        row = rows("SELECT current_files FROM agent_registry WHERE agent_id='e2e-g15b-a'")
        check("keep-alive 不洗已声明工作集", json.loads(row[0]["current_files"] or "[]") == ["e2e.py", "b.py"], str(row))

        # ② B 冲突检查（真消费）+ 冲突通知先落 A 收件箱（send_message 真消费）
        async with websockets.connect(f"ws://127.0.0.1:8090/api/collab/ws/agent/e2e-g15b-b?token={tok_b}", open_timeout=10) as wb:
            await wb.send(json.dumps({"event": "check_conflicts", "files": ["b.py", "z.py"]}))
            ack = json.loads(await asyncio.wait_for(wb.recv(), timeout=10))
            check("check_conflicts ack 帧", ack.get("event") == "conflict_check", str(ack))
            check(
                "冲突检出（their_task 保真）",
                ack.get("conflicts")
                == [{"agent_id": "e2e-g15b-a", "conflicting_files": ["b.py"], "their_task": "e2e-task"}],
                str(ack),
            )
            # 零重叠零噪音
            await wb.send(json.dumps({"event": "check_conflicts", "files": ["q.py"]}))
            ack2 = json.loads(await asyncio.wait_for(wb.recv(), timeout=10))
            check("零重叠空冲突", ack2.get("conflicts") == [], str(ack2))
            await wb.send(json.dumps({"event": "ping"}))
            f = json.loads(await asyncio.wait_for(wb.recv(), timeout=10))
            check("零噪音（下一帧恰 pong）", f == {"event": "pong"}, str(f))

            # ③ get_messages 真消费：A inbox 拉取+拉取即送达
            await wa.send(json.dumps({"event": "inbox"}))
            inbox = json.loads(await asyncio.wait_for(wa.recv(), timeout=10))
            msgs = inbox.get("messages", [])
            check("inbox 拉取真回冲突通知", inbox.get("event") == "inbox" and len(msgs) == 1, str(inbox))
            if msgs:
                content = json.loads(msgs[0]["content"])
                check("通知内容保真（files）", content.get("files") == ["b.py"], str(content))
            await wa.send(json.dumps({"event": "inbox"}))
            inbox2 = json.loads(await asyncio.wait_for(wa.recv(), timeout=10))
            check("拉取即送达标 read（二次拉取空）", inbox2.get("messages") == [], str(inbox2))

        # ④ 离线积压：B 再查一次冲突→通知落库；A 断开后重连接取（flush）
        async with websockets.connect(f"ws://127.0.0.1:8090/api/collab/ws/agent/e2e-g15b-b?token={tok_b}", open_timeout=10) as wb:
            await wb.send(json.dumps({"event": "check_conflicts", "files": ["b.py"]}))
            await asyncio.wait_for(wb.recv(), timeout=10)
        # A 拉取前先记 unread 行数，随后断开（模拟未拉取即离线）
        unread = rows("SELECT COUNT(*) AS n FROM agent_coord_messages WHERE to_agent='e2e-g15b-a' AND read=0")
        check("离线期通知已落（unread>=1）", unread[0]["n"] >= 1, str(unread))
    await asyncio.sleep(0.5)
    row = rows("SELECT status, current_files FROM agent_registry WHERE agent_id='e2e-g15b-a'")
    check("offline 清空工作集", bool(row) and row[0]["status"] == "offline" and json.loads(row[0]["current_files"] or "[]") == [], str(row))

    # A 重连：连接接取积压（coord_message 帧）
    async with websockets.connect(f"ws://127.0.0.1:8090/api/collab/ws/agent/e2e-g15b-a?token={tok_a}", open_timeout=10) as wa:
        f = json.loads(await asyncio.wait_for(wa.recv(), timeout=10))
        check("连接接取积压 coord_message 帧", f.get("event") == "coord_message" and f.get("from_agent") == "e2e-g15b-b", str(f))
        await asyncio.sleep(0.3)
        unread = rows("SELECT COUNT(*) AS n FROM agent_coord_messages WHERE to_agent='e2e-g15b-a' AND read=0")
        check("接取即送达标 read", unread[0]["n"] == 0, str(unread))

        # ⑤ 非法载荷如实 error 帧
        await wa.send(json.dumps({"event": "heartbeat", "current_files": "bad"}))
        f = json.loads(await asyncio.wait_for(wa.recv(), timeout=10))
        check("非法声明 error 帧", f.get("event") == "error" and "current_files" in f.get("detail", ""), str(f))
        await wa.send(json.dumps({"event": "check_conflicts", "files": []}))
        f = json.loads(await asyncio.wait_for(wa.recv(), timeout=10))
        check("非法 check_conflicts error 帧", f.get("event") == "error" and "files" in f.get("detail", ""), str(f))
        await wa.send(json.dumps({"event": "ping"}))
        f = json.loads(await asyncio.wait_for(wa.recv(), timeout=10))
        check("主循环存活（pong）", f == {"event": "pong"}, str(f))

    # ⑥ 跨租户零泄露：C（tenant 未知=默认）已见 A；再验证 ghost 不 fabricate
    async with websockets.connect("ws://127.0.0.1:8090/api/collab/ws/agent/e2e-g15b-ghost", open_timeout=10) as wg:
        await wg.send(json.dumps({"event": "heartbeat", "current_files": ["ghost.py"]}))
        await wg.send(json.dumps({"event": "check_conflicts", "files": ["ghost.py"]}))
        f = json.loads(await asyncio.wait_for(wg.recv(), timeout=10))
        check("ghost 检查照常服务", f.get("event") == "conflict_check", str(f))
    await asyncio.sleep(0.3)
    check("ghost 绝不 fabricate 协调行", not rows("SELECT 1 FROM agent_registry WHERE agent_id='e2e-g15b-ghost'"))

    cleanup("e2e-g15b-a", "e2e-g15b-b", "e2e-g15b-c")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre", action="store_true")
    args = ap.parse_args()
    with urllib.request.urlopen(BASE + "/api/brain/health", timeout=10) as resp:
        assert resp.status == 200
    if args.pre:
        asyncio.run(pre_probes())
    else:
        asyncio.run(post_probes())
        # 清痕复核
        rem = rows("SELECT COUNT(*) AS n FROM agent_registry WHERE agent_id LIKE 'e2e-%'") + \
              rows("SELECT COUNT(*) AS n FROM agent_coord_messages WHERE from_agent LIKE 'e2e-%' OR to_agent LIKE 'e2e-%'") + \
              rows("SELECT COUNT(*) AS n FROM agents WHERE agent_id LIKE 'e2e-%'", db=COLLAB_DB)
        total = sum(r["n"] for r in rem)
        check("清痕 remaining=0", total == 0, str(rem))
    print(f"\n== RESULT: {len(PASS)} passed, {len(FAIL)} failed ==")
    if FAIL:
        print("FAILED:", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
