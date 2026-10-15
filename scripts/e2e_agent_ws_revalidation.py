"""F3b-④b live e2e：/ws/agent/{agent_id} token 连接期重校验（生产 :8090 真栈）。

验证面：
1. 注册身份+真 token 开会话 ping/pong（握手面零回归）
2. token 轮换（重注册）后长连接在生产 60s 重校验节拍内被 close 1008
   （修前：照常收发）
3. 有效凭据对照连接同窗存活（禁误杀）
4. 握手拒绝面零回归（坏 token 仍 1008 握手拒绝）
5. 清痕 remaining=0（agents/agent_messages 表 e2e 行全清）
"""

import asyncio
import json
import sqlite3
import time

import httpx
import websockets

BASE = "http://localhost:8090"
WS_BASE = "ws://localhost:8090"
DB = "/home/climbing/opensoul/data/agent_collaboration.db"
SUF = "f4bb" + str(int(time.time()) % 100000)
A = f"e2e-{SUF}-a"
B = f"e2e-{SUF}-b"

results = []


def check(name, ok, detail=""):
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}")


async def recv_close(ws, budget):
    try:
        msg = await asyncio.wait_for(ws.recv(), timeout=budget)
        return {"unexpected_frame": str(msg)[:120]}
    except websockets.exceptions.ConnectionClosed as e:
        return {"code": e.code, "reason": e.reason}
    except asyncio.TimeoutError:
        return {"timeout": True}


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=15) as http:
        r = await http.get("/api/health")
        check("health", r.status_code == 200, f"{r.status_code}")

        r = await http.post("/api/collab/register", json={"agent_id": A, "name": A})
        tok1 = r.json()["token"]
        r = await http.post("/api/collab/register", json={"agent_id": B, "name": B})
        tokB = r.json()["token"]
        check("register_x2", r.status_code == 200 and tok1 and tokB, f"{r.status_code}")

        ws_a = await websockets.connect(f"{WS_BASE}/api/collab/ws/agent/{A}?token={tok1}")
        ws_b = await websockets.connect(f"{WS_BASE}/api/collab/ws/agent/{B}?token={tokB}")
        await ws_a.send(json.dumps({"event": "ping"}))
        pong = json.loads(await asyncio.wait_for(ws_a.recv(), timeout=5))
        check("ws_alive_a", pong == {"event": "pong"}, str(pong))

        # token 轮换（同 agent 重注册=②d 既有语义）
        r = await http.post("/api/collab/register", json={"agent_id": A, "name": A})
        tok2 = r.json()["token"]
        check("token_rotated", tok2 != tok1, f"{r.status_code}")

        t0 = time.time()
        info = await recv_close(ws_a, budget=80.0)
        dt = time.time() - t0
        check("a_closed_after_rotation", info.get("code") == 1008, f"{info} {dt:.1f}s")

        # 有效凭据对照存活（禁误杀）
        await ws_b.send(json.dumps({"event": "ping"}))
        pong = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=5))
        check("b_survives", pong == {"event": "pong"}, str(pong))

        # 握手拒绝面零回归
        try:
            await websockets.connect(f"{WS_BASE}/api/collab/ws/agent/{B}?token=wrong-e2e")
            check("handshake_reject", False, "wrong token accepted")
        except websockets.exceptions.ConnectionClosed as e:
            check("handshake_reject", e.code == 1008, f"code={e.code}")
        except Exception as e:  # InvalidStatus 等握手段拒绝形态
            check("handshake_reject", True, f"{type(e).__name__}: {e}")

        await ws_b.close()

    # 清痕
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute(
            "SELECT agent_id FROM agents WHERE agent_id IN (?, ?)", (A, B)
        ).fetchall()
        conn.execute("DELETE FROM agents WHERE agent_id IN (?, ?)", (A, B))
        conn.execute("DELETE FROM agent_messages WHERE from_agent IN (?, ?) OR to_agent IN (?, ?)", (A, B, A, B))
        conn.commit()
        remain = conn.execute(
            "SELECT COUNT(*) FROM agents WHERE agent_id LIKE ?", (f"e2e-{SUF}-%",)
        ).fetchone()[0]
        check("cleanup", remain == 0, f"deleted={len(rows)} remaining={remain}")
    finally:
        conn.close()

    ok_all = all(x["ok"] for x in results)
    print(json.dumps({"passed": sum(x["ok"] for x in results), "total": len(results), "ok": ok_all}, ensure_ascii=False))
    raise SystemExit(0 if ok_all else 1)


asyncio.run(main())
