#!/usr/bin/env python3
"""G1-⑤c agent.py 管理面注册接线 live e2e 探针。

真栈（:8090 真 uvicorn）验证管理面生命周期→MultiAgentCoordinator 协调面：
- P1 修前态（--pre）：admin 注册节点后 /api/brain/agents 不含它（修前零接线缺口）
- P2 注册桥：/api/agent/register（admin 真 JWT）→ 协调行落 + /api/brain/agents 真活清单
- P3 心跳桥：/api/agent/heartbeat（X-Agent-Token）→ 协调行 last_heartbeat 真前移
- P4 注销桥：DELETE /api/agent/nodes/{id} → 真源行 + 协调行随删（禁幽灵行）
- P5 清痕：探针 admin 用户行/agents 行/协调行精确清，remaining=0

用法：.venv/bin/python scripts/e2e_g15c_mgmt_coord_registration.py [--pre]
（--pre：只测 P1 修前态；跑在旧进程上，重启前执行）
"""

import argparse
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import json
from pathlib import Path
from uuid import UUID, uuid4

BASE = "http://localhost:8090"
HOME = "/home/climbing"
OS_DB = f"{HOME}/opensoul/data/opensoul.db"

sys.path.insert(0, f"{HOME}/opensoul")

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


def db_rows(sql, params=()):
    conn = sqlite3.connect(OS_DB)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def db_exec(sql, params=()):
    conn = sqlite3.connect(OS_DB)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def seed_admin():
    """临时 admin 用户行（取证后清痕）+ 同源 JWT（create_access_token 与
    require_role 鉴权链同 secret 同算法）。"""
    from src.services.auth import create_access_token

    uid = str(uuid4())
    db_exec(
        "INSERT INTO users (id, username, password_hash, role, is_active) "
        "VALUES (?, ?, 'x', 'admin', 1)",
        (uid, f"e2e-g15c-{uid[:8]}"),
    )
    token = create_access_token(user_id=UUID(uid), role="admin")
    return uid, {"Authorization": f"Bearer {token}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre", action="store_true")
    args = ap.parse_args()

    # 快照修前既有行（取证只对探针 id 精确断言，绝不误伤存量）
    pre_agents = {r["id"] for r in db_rows("SELECT id FROM agents")}
    pre_coord = {r["agent_id"] for r in db_rows("SELECT agent_id FROM agent_registry")}

    uid, auth = seed_admin()
    try:
        status, data = http(
            "POST",
            "/api/agent/register",
            {"name": "e2e-g15c-node", "agent_type": "e2e-collector", "capabilities": ["file"]},
            auth,
        )
        if not check("P0 admin register 200", status == 200, f"{status} {data}"):
            return
        agent_id = data["agent_id"]
        token = data["token"]

        status, agents = http("GET", "/api/brain/agents")
        ids = [a["agent_id"] for a in agents.get("agents", [])]
        if args.pre:
            # 修前缺口：管理面节点不进协作视图
            check(
                "P1 --pre 管理面节点缺席协作视图（修前缺口）",
                agent_id not in ids,
                f"brain agents ids={ids}",
            )
            rows = db_rows("SELECT agent_id FROM agent_registry WHERE agent_id = ?", (agent_id,))
            check("P1 --pre 协调行缺席（修前零接线）", rows == [], f"rows={rows}")
        else:
            check("P2 /api/brain/agents 含管理面节点（真消费闭环）", agent_id in ids, f"ids={ids}")
            rows = db_rows(
                "SELECT tenant_id, agent_type, status FROM agent_registry WHERE agent_id = ?",
                (agent_id,),
            )
            ok = (
                len(rows) == 1
                and rows[0]["tenant_id"] == "default"
                and rows[0]["agent_type"] == "e2e-collector"
                and rows[0]["status"] == "active"
            )
            check("P2 协调行落（default/e2e-collector/active）", ok, f"rows={rows}")

            # P3 心跳桥：拨回远古→真前移
            db_exec("UPDATE agent_registry SET last_heartbeat = 1.0 WHERE agent_id = ?", (agent_id,))
            time.sleep(0.05)
            status, hb = http(
                "POST",
                "/api/agent/heartbeat",
                {"agent_id": agent_id, "status": "idle"},
                {"X-Agent-Token": token},
            )
            rows = db_rows(
                "SELECT last_heartbeat, status FROM agent_registry WHERE agent_id = ?",
                (agent_id,),
            )
            ok = (
                status == 200
                and len(rows) == 1
                and rows[0]["last_heartbeat"] > 1000.0
                and rows[0]["status"] == "idle"
            )
            check("P3 心跳桥真刷新协调行", ok, f"{status} rows={rows}")

            # P4 注销桥：真源行+协调行随删
            status, data = http("DELETE", f"/api/agent/nodes/{agent_id}", None, auth)
            a_rows = db_rows("SELECT id FROM agents WHERE id = ?", (agent_id,))
            c_rows = db_rows("SELECT agent_id FROM agent_registry WHERE agent_id = ?", (agent_id,))
            status2, agents2 = http("GET", "/api/brain/agents")
            ids2 = [a["agent_id"] for a in agents2.get("agents", [])]
            ok = (
                status == 200
                and a_rows == []
                and c_rows == []
                and agent_id not in ids2
            )
            check("P4 注销桥随删（禁幽灵行）", ok, f"{status} a={a_rows} c={c_rows} ids={ids2}")

        # 清痕：探针 admin 行 + 探针 agents/协调行（只按探针 id 精确清，禁误伤
        # 存量；--pre 同样清——修前态只登记了 agents 行，同样要清）。
        db_exec("DELETE FROM users WHERE id = ?", (uid,))
        db_exec("DELETE FROM agents WHERE name = 'e2e-g15c-node'")
        db_exec("DELETE FROM agent_registry WHERE agent_id = ?", (agent_id,))
        left_agents = [
            r["id"] for r in db_rows("SELECT id FROM agents") if r["id"] not in pre_agents
        ]
        left_coord = [
            r["agent_id"]
            for r in db_rows("SELECT agent_id FROM agent_registry")
            if r["agent_id"] not in pre_coord
        ]
        left_users = [r["id"] for r in db_rows("SELECT id FROM users WHERE username LIKE 'e2e-g15c-%'")]
        ok = left_agents == [] and left_coord == [] and left_users == []
        check(
            "P5 清痕 remaining=0",
            ok,
            f"agents={left_agents} coord={left_coord} users={left_users}",
        )
    finally:
        db_exec("DELETE FROM users WHERE id = ?", (uid,))
        db_exec("DELETE FROM agents WHERE name = 'e2e-g15c-node'")

    passed = sum(1 for _, ok in RESULTS if ok)
    print(f"\n== {passed}/{len(RESULTS)} ==")
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
