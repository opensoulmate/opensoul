#!/usr/bin/env python3
"""G1-⑤ MultiAgentCoordinator 注册接线 live e2e 探针。

真栈（:8090 真 uvicorn）验证：
- P1 修前态：/api/brain/agents 记录（修前恒空集=G0 审计⑤）
- P2 注册桥：/api/collab/register → /api/brain/agents 真活清单（agent_type 映射）
- P3 WS 生命周期桥：连接=active、协议心跳事件刷 last_heartbeat、断开=offline
- P4 租户派生（可选：--tenant-token TOKEN 走 bind_identity）
- P5 清痕：collab agents 行 + 协调行按 agent_id 精确清，remaining=0

用法：python3 scripts/e2e_g15_coord_registration.py [--pre]
（--pre：只测 P1 修前态，不注册不写入）
"""

import argparse
import asyncio
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://localhost:8090"
HOME = "/home/climbing"
OS_DB = f"{HOME}/opensoul/data/opensoul.db"
COLLAB_DB = f"{HOME}/opensoul/data/agent_collaboration.db"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(("PASS" if ok else "FAIL"), name, detail)
    return bool(ok)


def http(method, path, body=None, headers=None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def db_rows(db, sql, params=()):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def db_exec(db, sql, params=()):
    conn = sqlite3.connect(db)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


async def ws_lifecycle(agent_id, token):
    """真 websockets 客户端：连接→心跳事件→断开（真 uvicorn 生产收尾路径）。"""
    import websockets

    url = f"ws://localhost:8090/api/collab/ws/agent/{agent_id}?token={token}"
    async with websockets.connect(url) as ws:
        # 连接=活跃
        row = None
        for _ in range(40):
            rows = db_rows(
                OS_DB,
                "SELECT status, last_heartbeat FROM agent_registry WHERE agent_id = ?",
                (agent_id,),
            )
            if rows and rows[0]["status"] == "active":
                row = rows[0]
                break
            await asyncio.sleep(0.1)
        check("P3.1 WS connect -> coord active", row is not None, str(row))
        before = row["last_heartbeat"] if row else 0
        await asyncio.sleep(0.05)
        await ws.send(json.dumps({"event": "heartbeat"}))
        after = before
        for _ in range(40):
            rows = db_rows(
                OS_DB,
                "SELECT last_heartbeat FROM agent_registry WHERE agent_id = ?",
                (agent_id,),
            )
            after = rows[0]["last_heartbeat"] if rows else 0
            if after > before:
                break
            await asyncio.sleep(0.1)
        check("P3.2 heartbeat event refreshes", after > before, f"{before} -> {after}")
        await ws.send(json.dumps({"event": "ping"}))
        pong = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        check("P3.3 ping/pong regression", pong == {"event": "pong"}, str(pong))
    # 断开=offline（真 uvicorn teardown 同步收尾）
    row = None
    for _ in range(40):
        rows = db_rows(
            OS_DB,
            "SELECT status FROM agent_registry WHERE agent_id = ?",
            (agent_id,),
        )
        if rows and rows[0]["status"] == "offline":
            row = rows[0]
            break
        await asyncio.sleep(0.1)
    check("P3.4 WS disconnect -> coord offline", row is not None, str(row))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre", action="store_true")
    ap.add_argument("--tenant-token", default="")
    args = ap.parse_args()

    status, health = http("GET", "/api/health")
    check("P0 health", status == 200 and health.get("status") == "ok", str(health)[:80])

    status, agents = http("GET", "/api/brain/agents")
    pre_ids = [a.get("agent_id") for a in agents.get("agents", [])]
    print("P1 pre /api/brain/agents:", status, pre_ids)
    check("P1 brain/agents readable", status == 200, str(pre_ids))

    if args.pre:
        failed = [n for n, ok in RESULTS if not ok]
        print("PRE SUMMARY", f"{len(RESULTS) - len(failed)}/{len(RESULTS)}", failed)
        sys.exit(1 if failed else 0)

    agent_id = f"e2e-g15-{uuid.uuid4().hex[:8]}"
    headers = {}
    if args.tenant_token:
        headers["Authorization"] = "Bearer " + args.tenant_token
    status, reg = http(
        "POST",
        "/api/collab/register",
        {"agent_id": agent_id, "name": "e2e g15", "role": "e2e-runner", "capabilities": ["probe"]},
        headers,
    )
    check(
        "P2.1 register contract keys",
        status == 200 and set(reg.keys()) == {"agent_id", "token", "status", "tenant_id"},
        str(reg)[:120],
    )
    token = reg.get("token", "")

    status, agents = http("GET", "/api/brain/agents", headers=headers)
    items = [a for a in agents.get("agents", []) if a.get("agent_id") == agent_id]
    check(
        "P2.2 register lands coord row (brain/agents visible)",
        len(items) == 1 and items[0].get("agent_type") == "e2e-runner",
        str(items)[:160],
    )
    rows = db_rows(
        OS_DB,
        "SELECT tenant_id, agent_type, status FROM agent_registry WHERE agent_id = ?",
        (agent_id,),
    )
    expect_tenant = "default"
    if args.tenant_token:
        expect_tenant = rows[0]["tenant_id"] if rows else ""
    check(
        "P2.3 coord row tenant key",
        len(rows) == 1 and rows[0]["tenant_id"] == expect_tenant,
        str(rows),
    )

    asyncio.run(ws_lifecycle(agent_id, token))

    # P5 清痕（按 agent_id 精确清，禁扫删）
    db_exec(COLLAB_DB, "DELETE FROM agent_capabilities WHERE agent_id = ?", (agent_id,))
    db_exec(COLLAB_DB, "DELETE FROM agents WHERE agent_id = ?", (agent_id,))
    db_exec(OS_DB, "DELETE FROM agent_registry WHERE agent_id = ?", (agent_id,))
    remain = db_rows(
        OS_DB, "SELECT COUNT(*) AS n FROM agent_registry WHERE agent_id = ?", (agent_id,)
    )
    remain2 = db_rows(
        COLLAB_DB, "SELECT COUNT(*) AS n FROM agents WHERE agent_id = ?", (agent_id,)
    )
    check(
        "P5 cleanup remaining=0",
        remain[0]["n"] == 0 and remain2[0]["n"] == 0,
        f"{remain} {remain2}",
    )

    failed = [n for n, ok in RESULTS if not ok]
    print("SUMMARY", f"{len(RESULTS) - len(failed)}/{len(RESULTS)}", "FAIL:", failed)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
