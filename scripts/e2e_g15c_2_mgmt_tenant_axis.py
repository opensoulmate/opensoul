#!/usr/bin/env python3
"""G1-⑤c-② 管理面租户轴收口 live e2e 探针。

真栈（:8090 真 uvicorn）验证 agents 表行租户键+身份派生+协调键随行归属：
- P1 修前态（--pre）：行无租户键/协调键钉 'default'/读面全局/跨租户删 200/claim 冒写 200
- P2 行归属：register（admin 真 JWT 派生租户）→ agents 行 tenant_id + 响应加性键
- P3 协调键随行归属：agent_registry 行 tenant_id=行归属租户（修前钉 'default'）
- P4 读面过滤：/nodes /stats 三面互不可见（A 只见 A、B 只见 B）
- P5 claim 持证：A token 冒写 B 行 heartbeat → 403 零副作用
- P6 删除闸：B DELETE A 节点 → 403 带真因 + 行/协调行存活（修前 200 全删）
- P7 清痕：探针 users/agents/协调行精确清，remaining=0

用法：.venv/bin/python scripts/e2e_g15c_2_mgmt_tenant_axis.py [--pre]
（--pre：只测修前缺口态；跑在旧进程上，重启前执行）
"""

import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.request
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


def agent_row_tenant(agent_id):
    """行租户键读取；列不在场（修前态）→ '<no-column>' 如实报。"""
    try:
        rows = db_rows("SELECT tenant_id FROM agents WHERE id = ?", (agent_id,))
    except sqlite3.OperationalError:
        return "<no-column>"
    return rows[0]["tenant_id"] if rows else "<no-row>"


def seed_admin(tag, tenant):
    """临时 admin 用户行（真 JWT + 派生租户绑定；取证后清痕）。"""
    from src.services.auth import create_access_token

    uid = str(uuid4())
    db_exec(
        "INSERT INTO users (id, username, password_hash, role, is_active, tenant_id) "
        "VALUES (?, ?, 'x', 'admin', 1, ?)",
        (uid, f"e2e-g15c2-{tag}-{uid[:8]}", tenant),
    )
    token = create_access_token(user_id=UUID(uid), role="admin")
    return uid, {"Authorization": f"Bearer {token}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre", action="store_true")
    args = ap.parse_args()
    pre = args.pre

    pre_agents = {r["id"] for r in db_rows("SELECT id FROM agents")}
    pre_coord = {r["agent_id"] for r in db_rows("SELECT agent_id FROM agent_registry")}
    pre_users = {r["id"] for r in db_rows("SELECT id FROM users")}

    uid_a, auth_a = seed_admin("a", "tenant-e2e-a")
    uid_b, auth_b = seed_admin("b", "tenant-e2e-b")
    node_a = node_b = tok_a = None
    try:
        # ── 注册两租户节点 ─────────────────────────────────────────
        status, data = http(
            "POST",
            "/api/agent/register",
            {"name": "e2e-g15c2-node-a", "agent_type": "e2e-a", "capabilities": ["file"]},
            auth_a,
        )
        if not check("P0 A register 200", status == 200, f"{status} {data}"):
            return
        node_a, tok_a = data["agent_id"], data["token"]
        status, data = http(
            "POST",
            "/api/agent/register",
            {"name": "e2e-g15c2-node-b", "agent_type": "e2e-b", "capabilities": ["file"]},
            auth_b,
        )
        if not check("P0 B register 200", status == 200, f"{status} {data}"):
            return
        node_b, tok_b = data["agent_id"], data["token"]

        # ── P2 行归属 ────────────────────────────────────────────
        row_tenant = agent_row_tenant(node_a)
        if pre:
            check(
                "P2 --pre 行无租户轴（修前缺口）",
                row_tenant in ("<no-column>", "default"),
                f"row_tenant={row_tenant}",
            )
        else:
            check("P2 行归属=身份派生 tenant-e2e-a", row_tenant == "tenant-e2e-a", f"row_tenant={row_tenant}")
            check("P2 响应加性键 tenant_id", data.get("tenant_id") == "tenant-e2e-b", f"keys={sorted(data)}")

        # ── P3 协调键随行归属 ────────────────────────────────────
        crows = db_rows(
            "SELECT tenant_id, status FROM agent_registry WHERE agent_id = ?", (node_a,)
        )
        ct = crows[0]["tenant_id"] if crows else "<no-row>"
        if pre:
            check("P3 --pre 协调键钉 'default'（修前缺口）", ct == "default", f"ct={ct}")
        else:
            check("P3 协调键=行归属租户", ct == "tenant-e2e-a", f"ct={ct}")

        # ── P4 读面过滤 ─────────────────────────────────────────
        status, rows = http("GET", "/api/agent/nodes", None, auth_a)
        names = sorted(r.get("name", "") for r in rows) if status == 200 else []
        if pre:
            check("P4 --pre /nodes 全局列表（修前泄露）", "e2e-g15c2-node-b" in names, f"names={names}")
        else:
            ok = status == 200 and names == ["e2e-g15c2-node-a"]
            check("P4 /nodes 三面隔离（A 只见 A）", ok, f"{status} names={names}")
            status, s = http("GET", "/api/agent/stats", None, auth_a)
            check(
                "P4 /stats 租户聚合（A total=1）",
                status == 200 and s.get("total_agents") == 1,
                f"{status} {s}",
            )

        # ── P5 claim 持证 ───────────────────────────────────────
        status, hb = http(
            "POST",
            "/api/agent/heartbeat",
            {"agent_id": node_b, "status": "hijacked"},
            {"X-Agent-Token": tok_a},
        )
        brows = db_rows("SELECT status FROM agents WHERE id = ?", (node_b,))
        if pre:
            check("P5 --pre claim 冒写畅通（修前缺口）", status == 200, f"{status} {hb}")
        else:
            ok = status == 403 and "claim mismatch" in str(hb.get("detail", ""))
            check("P5 claim 冒写 403 带真因", ok, f"{status} {hb}")
            ok = brows and brows[0]["status"] != "hijacked"
            check("P5 B 行零改写", ok, f"brows={brows}")

        # ── P6 删除闸（破坏性放最后）────────────────────────────
        status, d = http("DELETE", f"/api/agent/nodes/{node_a}", None, auth_b)
        a_rows = db_rows("SELECT id FROM agents WHERE id = ?", (node_a,))
        c_rows = db_rows("SELECT agent_id FROM agent_registry WHERE agent_id = ?", (node_a,))
        if pre:
            ok = status == 200 and a_rows == [] and c_rows == []
            check("P6 --pre 跨租户删除畅通（修前缺口）", ok, f"{status} a={a_rows} c={c_rows}")
        else:
            ok = status == 403 and "access denied" in str(d.get("detail", ""))
            check("P6 跨租户删除 403 带真因", ok, f"{status} {d}")
            check("P6 行/协调行存活", a_rows != [] and c_rows != [], f"a={a_rows} c={c_rows}")

        # ── P7 清痕 ─────────────────────────────────────────────
        for nid in (node_a, node_b):
            if nid:
                db_exec("DELETE FROM agents WHERE id = ?", (nid,))
                db_exec("DELETE FROM agent_registry WHERE agent_id = ?", (nid,))
        db_exec("DELETE FROM users WHERE id IN (?, ?)", (uid_a, uid_b))
        left_agents = [r["id"] for r in db_rows("SELECT id FROM agents") if r["id"] not in pre_agents]
        left_coord = [
            r["agent_id"]
            for r in db_rows("SELECT agent_id FROM agent_registry")
            if r["agent_id"] not in pre_coord
        ]
        left_users = [
            r["id"]
            for r in db_rows("SELECT id FROM users WHERE username LIKE 'e2e-g15c2-%'")
            if r["id"] not in pre_users
        ]
        ok = left_agents == [] and left_coord == [] and left_users == []
        check("P7 清痕 remaining=0", ok, f"agents={left_agents} coord={left_coord} users={left_users}")
    finally:
        for nid in (node_a, node_b):
            if nid:
                db_exec("DELETE FROM agents WHERE id = ?", (nid,))
                db_exec("DELETE FROM agent_registry WHERE agent_id = ?", (nid,))
        db_exec("DELETE FROM users WHERE id IN (?, ?)", (uid_a, uid_b))

    passed = sum(1 for _, ok in RESULTS if ok)
    print(f"\n== {passed}/{len(RESULTS)} ==")
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
