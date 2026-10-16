#!/usr/bin/env python3
"""G1-⑤c-③ live e2e 探针：agent_reports 表租户加轴（行归属=agents 行租户单一权威）。

用法：
  python3 scripts/e2e_agent_report_tenant_axis.py --pre   # 修前探针（期望 tenant_id 列缺失=缺口实证）
  python3 scripts/e2e_agent_report_tenant_axis.py          # 修后全绿

真实栈 urllib → :8090；探针自带清痕（users/agents/agent_reports/agent_registry
按标记行精确删+回读 remaining=0）。测试行绝不留生产库。
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
MARK = "e2e9c3"

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

    # ── 播种两个真租户用户 + 一个 default 租户用户（bind_identity 派生真源）──
    uid_a, uid_b, uid_d = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    tok_a, tok_b, tok_d = make_token(uid_a), make_token(uid_b), make_token(uid_d)
    for uid, name, tenant in (
        (uid_a, f"{MARK}-a", f"tenant-{MARK}-a"),
        (uid_b, f"{MARK}-b", f"tenant-{MARK}-b"),
        (uid_d, f"{MARK}-d", "default"),
    ):
        exec_sql(
            "INSERT INTO users (id, username, password_hash, role, tenant_id) "
            "VALUES (?, ?, 'x', 'admin', ?)",
            (uid, name, tenant),
        )
    agent_ids: list[str] = []
    try:
        # P0 health
        st, _ = call("GET", "/api/health")
        check(st == 200, "P0 health 200", str(st))

        # P1 租户 A 上报（--pre：列缺失缺口实证；修后：行落 tenant-a）
        st, body = call(
            "POST",
            "/api/agent/register",
            {"name": f"{MARK}-node-a", "agent_type": "generic"},
            {"Authorization": f"Bearer {tok_a}"},
        )
        check(st == 200, "P1 register A 200", f"{st} {body[:120]}")
        a = json.loads(body)
        agent_ids.append(a["agent_id"])
        st, body = call(
            "POST",
            "/api/agent/report",
            {"agent_id": a["agent_id"], "report_type": f"{MARK}-metrics", "data": {"src": "a"}},
            {"X-Agent-Token": a["token"]},
        )
        check(st == 200, "P1 report A 200", f"{st} {body[:80]}")

        cols = {r["name"] for r in q("PRAGMA table_info(agent_reports)")}
        if pre:
            # 修前缺口两形态任一即实证：①列缺失（G1-⑤c-② 留档原形态）
            # ②列已在场（全量 pytest 侧 _ensure_schema 自愈连带补列）但旧代码
            # INSERT 不带租户键→行恒 'default' 而 agent 行租户=tenant-a=行归属缺失。
            rows = q(
                "SELECT tenant_id FROM agent_reports WHERE report_type = ?",
                (f"{MARK}-metrics",),
            )
            agent_row = q(
                "SELECT tenant_id FROM agents WHERE id = ?", (a["agent_id"],)
            )
            gap = ("tenant_id" not in cols) or (
                bool(rows)
                and rows[0].get("tenant_id") == "default"
                and bool(agent_row)
                and agent_row[0]["tenant_id"] == f"tenant-{MARK}-a"
            )
            check(
                gap,
                "P1-pre 行归属缺口实证（列缺失 或 行恒 default 而 agent 行=tenant-a）",
                f"cols_tenant={'tenant_id' in cols} row={rows} agent={agent_row}",
            )
        else:
            check("tenant_id" in cols, "P2 tenant_id 列在场")
            rows = q(
                "SELECT tenant_id FROM agent_reports WHERE report_type = ?",
                (f"{MARK}-metrics",),
            )
            check(
                bool(rows) and rows[0]["tenant_id"] == f"tenant-{MARK}-a",
                "P2 行落派生租户 tenant-a",
                str(rows),
            )

            # P3 租户 B 上报互不串
            st, body = call(
                "POST",
                "/api/agent/register",
                {"name": f"{MARK}-node-b", "agent_type": "generic"},
                {"Authorization": f"Bearer {tok_b}"},
            )
            b = json.loads(body)
            agent_ids.append(b["agent_id"])
            st, _ = call(
                "POST",
                "/api/agent/report",
                {"agent_id": b["agent_id"], "report_type": f"{MARK}-metrics", "data": {"src": "b"}},
                {"X-Agent-Token": b["token"]},
            )
            rows = q(
                "SELECT agent_id, tenant_id FROM agent_reports WHERE report_type = ?",
                (f"{MARK}-metrics",),
            )
            by_agent = {r["agent_id"]: r["tenant_id"] for r in rows}
            check(
                by_agent.get(b["agent_id"]) == f"tenant-{MARK}-b"
                and by_agent.get(a["agent_id"]) == f"tenant-{MARK}-a",
                "P3 双租户行互不串",
                str(by_agent),
            )

            # P4 'default' 租户契约
            st, body = call(
                "POST",
                "/api/agent/register",
                {"name": f"{MARK}-node-d", "agent_type": "generic"},
                {"Authorization": f"Bearer {tok_d}"},
            )
            d = json.loads(body)
            agent_ids.append(d["agent_id"])
            st, body2 = call(
                "POST",
                "/api/agent/report",
                {"agent_id": d["agent_id"], "report_type": f"{MARK}-default", "data": {}},
                {"X-Agent-Token": d["token"]},
            )
            rows = q(
                "SELECT tenant_id FROM agent_reports WHERE report_type = ?",
                (f"{MARK}-default",),
            )
            check(
                bool(rows) and rows[0]["tenant_id"] == "default",
                "P4 default 租户行 'default' 契约",
                str(rows),
            )
            # 响应契约逐键不变
            check(
                set(json.loads(body2).keys()) == {"status", "agent_id"},
                "P4 响应形状逐键不变",
                body2[:80],
            )

            # P5 claim 持证：矛盾 body agent_id 403 零落库
            before = q("SELECT COUNT(*) AS n FROM agent_reports")[0]["n"]
            st, _ = call(
                "POST",
                "/api/agent/report",
                {"agent_id": b["agent_id"], "report_type": f"{MARK}-forged", "data": {}},
                {"X-Agent-Token": a["token"]},
            )
            after = q("SELECT COUNT(*) AS n FROM agent_reports")[0]["n"]
            check(st == 403 and after == before, "P5 claim 矛盾 403 零落库", f"{st} {before}->{after}")

            # P6 迁移回填：存量行 'default'
            rows = q(
                "SELECT tenant_id FROM agent_reports WHERE tenant_id != 'default' "
                "AND report_type NOT LIKE ?",
                (f"{MARK}-%",),
            )
            check(rows == [], "P6 存量行回填 'default' 零例外", str(rows[:3]))

            # P7 租户索引真建
            idx = q(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='idx_agent_reports_tenant'"
            )
            check(bool(idx), "P7 idx_agent_reports_tenant 在场", str(idx))
    finally:
        # ── 清痕：按标记精确删（users/agents/agent_reports/agent_registry）──
        aids = agent_ids
        if aids:
            marks = ",".join("?" * len(aids))
            exec_sql(f"DELETE FROM agent_reports WHERE agent_id IN ({marks})", aids)
            exec_sql(f"DELETE FROM agent_registry WHERE agent_id IN ({marks})", aids)
            exec_sql(f"DELETE FROM agents WHERE id IN ({marks})", aids)
        exec_sql("DELETE FROM users WHERE username LIKE ?", (f"{MARK}-%",))
        remaining = {
            "users": q("SELECT COUNT(*) AS n FROM users WHERE username LIKE ?", (f"{MARK}-%",))[0]["n"],
            "agents": q("SELECT COUNT(*) AS n FROM agents WHERE name LIKE ?", (f"{MARK}-%",))[0]["n"],
            "reports": q(
                "SELECT COUNT(*) AS n FROM agent_reports WHERE report_type LIKE ?", (f"{MARK}-%",)
            )[0]["n"],
        }
        print("cleanup remaining:", remaining)

    failed = [n for ok, n in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
