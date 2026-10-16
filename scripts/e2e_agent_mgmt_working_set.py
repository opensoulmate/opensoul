#!/usr/bin/env python3
"""G1-⑤c-④ live e2e 探针：心跳桥工作集声明（管理面 current_task/current_files
声明透传协调真源，G1-⑤b 声明语义同款）。

用法：
  python3 scripts/e2e_agent_mgmt_working_set.py --pre   # 修前探针（期望声明被静默吞=缺口实证）
  python3 scripts/e2e_agent_mgmt_working_set.py          # 修后全绿

真实栈 urllib → :8090；探针自带清痕（users/agents/agent_registry 按标记行精确删
+回读 remaining=0）。测试行绝不留生产库。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
DB = "/home/climbing/opensoul/data/opensoul.db"
MARK = "e2e9d1"

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


def call(method: str, path: str, body=None, headers=None):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def q(sql, params=()):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def exec_sql(sql, params=()):
    con = sqlite3.connect(DB)
    try:
        con.execute(sql, params)
        con.commit()
    finally:
        con.close()


def make_token(user_id: str) -> str:
    sys.path.insert(0, "/home/climbing/opensoul")
    from src.services.auth import create_access_token

    return create_access_token(user_id=uuid.UUID(user_id), role="admin")


def main() -> int:
    pre = "--pre" in sys.argv

    uid = str(uuid.uuid4())
    tok = make_token(uid)
    exec_sql(
        "INSERT INTO users (id, username, password_hash, role, tenant_id) "
        "VALUES (?, ?, 'x', 'admin', 'default')",
        (uid, f"{MARK}-admin"),
    )
    agents: list[dict] = []
    try:
        # P0 health
        st, _ = call("GET", "/api/health")
        check(st == 200, "P0 health 200", str(st))

        # 注册 A/B 两节点（管理面）
        for name in (f"{MARK}-node-a", f"{MARK}-node-b"):
            st, body = call(
                "POST",
                "/api/agent/register",
                {"name": name, "agent_type": "generic"},
                {"Authorization": f"Bearer {tok}"},
            )
            check(st == 200, f"P0 register {name[-1]} 200", f"{st} {body[:100]}")
            agents.append(json.loads(body))
        a, b = agents[0], agents[1]

        # P1 声明透传：heartbeat 携 current_task/current_files → 协调行落声明
        st, body = call(
            "POST",
            "/api/agent/heartbeat",
            {
                "agent_id": a["agent_id"],
                "status": "active",
                "current_task": f"{MARK}-task-a",
                "current_files": [f"{MARK}/shared.py", f"{MARK}/only_a.py"],
            },
            {"X-Agent-Token": a["token"]},
        )
        check(st == 200, "P1 heartbeat declaration 200", f"{st} {body[:80]}")
        rows = q("SELECT current_task, current_files FROM agent_registry WHERE agent_id = ?", (a["agent_id"],))
        if pre:
            # 修前缺口实证：桥不带工作集→声明被 pydantic 静默吞/桥不透传，
            # 协调行恒 NULL/'[]'（管理面声明无从落地）
            gap = (not rows) or (
                rows[0]["current_task"] in (None, "") and rows[0]["current_files"] in (None, "[]")
            )
            check(gap, "P1 --pre 声明被吞=缺口实证", str(rows)[:120])
        else:
            ok = bool(rows) and rows[0]["current_task"] == f"{MARK}-task-a" and rows[0]["current_files"] == json.dumps(
                [f"{MARK}/shared.py", f"{MARK}/only_a.py"], ensure_ascii=False
            )
            check(ok, "P1 协调行落声明逐字节", str(rows)[:160])

        # P2 keep-alive 不洗声明（缺省=None 保留现值）
        st, _ = call(
            "POST",
            "/api/agent/heartbeat",
            {"agent_id": a["agent_id"], "status": "active"},
            {"X-Agent-Token": a["token"]},
        )
        rows = q("SELECT current_task, current_files FROM agent_registry WHERE agent_id = ?", (a["agent_id"],))
        ok2 = st == 200 and bool(rows) and rows[0]["current_task"] == f"{MARK}-task-a" and rows[0]["current_files"] == json.dumps(
            [f"{MARK}/shared.py", f"{MARK}/only_a.py"], ensure_ascii=False
        )
        check(ok2, "P2 keep-alive 保留声明", f"{st} {str(rows)[:120]}")

        # P3 /api/brain/agents 协作视图真消费（声明可见）
        st, body = call("GET", "/api/brain/agents")
        agents_view = json.loads(body).get("agents", []) if st == 200 else []
        mine = [x for x in agents_view if x.get("agent_id") == a["agent_id"]]
        ok3 = st == 200 and bool(mine) and mine[0].get("current_task") == f"{MARK}-task-a"
        check(ok3, "P3 /api/brain/agents 声明可见", f"{st} {str(mine)[:140]}")

        # P4 B 声明重叠工作集（冲突视图数据真源双行在场）
        st, _ = call(
            "POST",
            "/api/agent/heartbeat",
            {
                "agent_id": b["agent_id"],
                "status": "active",
                "current_task": f"{MARK}-task-b",
                "current_files": [f"{MARK}/shared.py"],
            },
            {"X-Agent-Token": b["token"]},
        )
        rows = q("SELECT current_files FROM agent_registry WHERE agent_id = ?", (b["agent_id"],))
        ok4 = st == 200 and bool(rows) and rows[0]["current_files"] == json.dumps([f"{MARK}/shared.py"], ensure_ascii=False)
        check(ok4, "P4 B 声明重叠工作集落行", f"{st} {str(rows)[:100]}")

        # P5 offline 清空（未显式声明）——WS 断开收尾同语义
        st, _ = call(
            "POST",
            "/api/agent/heartbeat",
            {"agent_id": b["agent_id"], "status": "offline"},
            {"X-Agent-Token": b["token"]},
        )
        rows = q("SELECT current_task, current_files FROM agent_registry WHERE agent_id = ?", (b["agent_id"],))
        ok5 = st == 200 and bool(rows) and rows[0]["current_task"] == "" and rows[0]["current_files"] == "[]"
        check(ok5, "P5 offline 清空工作集", f"{st} {str(rows)[:100]}")

        # P6 显式声明恒优先（offline+显式=按显式更新，禁静默吞）
        st, _ = call(
            "POST",
            "/api/agent/heartbeat",
            {
                "agent_id": b["agent_id"],
                "status": "offline",
                "current_task": f"{MARK}-final",
                "current_files": [f"{MARK}/final.py"],
            },
            {"X-Agent-Token": b["token"]},
        )
        rows = q("SELECT current_task, current_files FROM agent_registry WHERE agent_id = ?", (b["agent_id"],))
        ok6 = st == 200 and bool(rows) and rows[0]["current_task"] == f"{MARK}-final" and rows[0]["current_files"] == json.dumps(
            [f"{MARK}/final.py"], ensure_ascii=False
        )
        check(ok6, "P6 显式声明优先于 offline", f"{st} {str(rows)[:100]}")

        # P7 非法载荷 422 零副作用（禁静默吞声明）
        before = q("SELECT current_task, current_files FROM agent_registry WHERE agent_id = ?", (b["agent_id"],))
        st, body = call(
            "POST",
            "/api/agent/heartbeat",
            {"agent_id": b["agent_id"], "status": "active", "current_files": ["ok", 3]},
            {"X-Agent-Token": b["token"]},
        )
        after = q("SELECT current_task, current_files FROM agent_registry WHERE agent_id = ?", (b["agent_id"],))
        if pre:
            # 修前：额外字段被 pydantic 静默忽略→200 且声明没生效（吞声明）
            check(st == 200, "P7 --pre 非法载荷被静默吞=缺口实证", f"{st} {body[:80]}")
        else:
            ok7 = st == 422 and before == after
            check(ok7, "P7 非法载荷 422 零副作用", f"{st} before==after:{before == after}")

        # P8 claim 持证零回归（冒写 403 零副作用）
        before = q("SELECT current_task, current_files FROM agent_registry WHERE agent_id = ?", (a["agent_id"],))
        st, body = call(
            "POST",
            "/api/agent/heartbeat",
            {"agent_id": a["agent_id"], "current_task": "forged", "current_files": ["forged.py"]},
            {"X-Agent-Token": b["token"]},
        )
        after = q("SELECT current_task, current_files FROM agent_registry WHERE agent_id = ?", (a["agent_id"],))
        ok8 = st == 403 and before == after
        check(ok8, "P8 claim 冒写 403 零副作用", f"{st} before==after:{before == after}")

        # P9 响应契约逐键不变
        st, body = call(
            "POST",
            "/api/agent/heartbeat",
            {"agent_id": a["agent_id"], "status": "active", "current_task": f"{MARK}-t2"},
            {"X-Agent-Token": a["token"]},
        )
        keys = set(json.loads(body).keys()) if st == 200 else set()
        check(st == 200 and keys == {"agent_id", "status", "timestamp"}, "P9 响应契约逐键", str(sorted(keys)))
    finally:
        # 清痕：delete 节点（级联协调行）+ 用户，回读 remaining=0
        for ag in agents:
            call("DELETE", f"/api/agent/nodes/{ag['agent_id']}", headers={"Authorization": f"Bearer {tok}"})
        exec_sql("DELETE FROM agent_registry WHERE agent_id IN (%s)" % ",".join("?" * len(agents)), tuple(ag["agent_id"] for ag in agents)) if agents else None
        exec_sql("DELETE FROM agents WHERE id IN (%s)" % ",".join("?" * len(agents)), tuple(ag["agent_id"] for ag in agents)) if agents else None
        exec_sql("DELETE FROM users WHERE id = ?", (uid,))
        remaining = 0
        if agents:
            ph = ",".join("?" * len(agents))
            remaining += len(q(f"SELECT 1 FROM agents WHERE id IN ({ph})", tuple(ag["agent_id"] for ag in agents)))
            remaining += len(q(f"SELECT 1 FROM agent_registry WHERE agent_id IN ({ph})", tuple(ag["agent_id"] for ag in agents)))
        remaining += len(q("SELECT 1 FROM users WHERE id = ?", (uid,)))
        check(remaining == 0, "CLEANUP remaining=0", str(remaining))

    failed = [n for ok, n in RESULTS if not ok]
    print(f"\n== {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
