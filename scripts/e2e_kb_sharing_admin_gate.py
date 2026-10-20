#!/usr/bin/env python3
"""F3b-⑰ live e2e 探针：kb_sharing 面身份轴收口（user 派生 + admin 门）。

用法：
  python3 scripts/e2e_kb_sharing_admin_gate.py --pre   # 修前探针（缺口坐实）
  python3 scripts/e2e_kb_sharing_admin_gate.py          # 修后全绿

真实栈 urllib → :8090；探针自带清痕（users/kb_sharing_requests/knowledge 按 MARK
精确删 + 回读 remaining=0 + timeline delta=0 含事件行面 TR-② 纪律）。
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
TIMELINE_DB = "/home/climbing/opensoul/data/timeline.db"
MARK = "e2ekbs17"

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


def timeline_count() -> int:
    con = sqlite3.connect(TIMELINE_DB)
    try:
        return con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        con.close()


def make_token(user_id: str, role: str = "user") -> str:
    sys.path.insert(0, "/home/climbing/opensoul")
    from src.services.auth import create_access_token

    return create_access_token(user_id=uuid.UUID(user_id), role=role)


def main() -> int:
    pre = "--pre" in sys.argv
    tl0 = timeline_count()

    uid_a = str(uuid.uuid4())
    uid_b = str(uuid.uuid4())
    uid_admin = str(uuid.uuid4())
    users = [
        (uid_a, f"{MARK}-a", "user"),
        (uid_b, f"{MARK}-b", "user"),
        (uid_admin, f"{MARK}-admin", "admin"),
    ]
    for uid, name, role in users:
        exec_sql(
            "INSERT INTO users (id, username, password_hash, role, tenant_id) "
            "VALUES (?, ?, 'x', ?, 'default')",
            (uid, name, role),
        )
    tok_a = {"Authorization": f"Bearer {make_token(uid_a)}"}
    tok_b = {"Authorization": f"Bearer {make_token(uid_b)}"}
    tok_admin = {"Authorization": f"Bearer {make_token(uid_admin, 'admin')}"}

    try:
        # P0 health
        st, _ = call("GET", "/api/kb-sharing/health")
        check(st == 200, "P0 health 200", str(st))

        # P1 A 提交共享申请（行归属=派生档案键）
        st, body = call(
            "POST",
            "/api/kb-sharing/",
            {"kb_id": f"{MARK}-kbid", "kb_name": f"{MARK}-kb1"},
            tok_a,
        )
        check(st == 200, "P1 create 200", str(st))
        req_a = json.loads(body) if st == 200 else {}
        check(
            req_a.get("user_id") == uid_a,
            "P1 row attribution = derived archive key",
            f"user_id={req_a.get('user_id')} want={uid_a}",
        )
        check(
            req_a.get("user_name") == f"{MARK}-a",
            "P1 user_name from identity",
            f"user_name={req_a.get('user_name')}",
        )

        # A 的知识行（家族正典键形=UUID 形原样）
        exec_sql(
            "INSERT INTO knowledge (id, user_id, title, source) VALUES (?, ?, ?, 'e2e')",
            (f"{MARK}-k1", uid_a, f"{MARK}-kn1"),
        )

        # P2 B（非 admin）审批 A 的申请（修前 200 冒写 / 修后 403 管理员门）
        st, body = call(
            "POST",
            f"/api/kb-sharing/{req_a.get('id')}/review",
            {"status": "approved", "review_note": f"{MARK}-review"},
            tok_b,
        )
        check(
            st == (200 if pre else 403),
            f"P2 non-admin review -> {200 if pre else 403} ({'GAP' if pre else 'gate'})",
            f"got {st} {body[:120]}",
        )
        kn = q("SELECT metadata FROM knowledge WHERE id = ?", (f"{MARK}-k1",))
        marked = bool(kn) and "shared_to_enterprise" in (kn[0]["metadata"] or "")
        check(
            marked == pre,
            f"P2 approve-side knowledge marking == {pre} ({'GAP 冒写' if pre else 'zero side effect'})",
            f"marked={marked}",
        )

        # P3 自审自批（修后 403）
        if not pre:
            st, body = call(
                "POST",
                f"/api/kb-sharing/{req_a.get('id')}/review",
                {"status": "approved"},
                tok_a,
            )
            check(st == 403, "P3 self review 403", f"got {st}")

        # P4 B 列表缺省（修前全量泄露 / 修后只看自己的）
        st, body = call("GET", "/api/kb-sharing/", headers=tok_b)
        rows = json.loads(body) if st == 200 else []
        sees_a = any(r.get("id") == req_a.get("id") for r in rows)
        check(
            sees_a == pre,
            f"P4 list default leak == {pre} ({'GAP 全量' if pre else 'own only'})",
            f"got {len(rows)} rows sees_a={sees_a}",
        )

        # P5 B 自报 A 的键（修后 403 user mismatch）
        st, body = call("GET", "/api/kb-sharing/?user_id=" + uid_a, headers=tok_b)
        check(
            st == (200 if pre else 403),
            f"P5 declared mismatch -> {200 if pre else 403}",
            f"got {st} {body[:100]}",
        )
        if not pre:
            check(
                "user mismatch" in body,
                "P5 detail carries 'user mismatch' real cause",
                body[:120],
            )

        # P6 三哨兵=未声明→取派生（只看自己的）
        if not pre:
            for sentinel in ("", "default", "00000000-0000-0000-0000-000000000000"):
                st, body = call(
                    "GET", "/api/kb-sharing/?user_id=" + sentinel, headers=tok_a
                )
                rows = json.loads(body) if st == 200 else []
                only_own = all(r.get("user_id") == uid_a for r in rows) and any(
                    r.get("id") == req_a.get("id") for r in rows
                )
                check(
                    st == 200 and only_own,
                    f"P6 sentinel '{sentinel[:8]}' -> derived own only",
                    f"got {st} rows={len(rows)}",
                )

        # P7 admin 看全部 + user_id 过滤
        st, body = call("GET", "/api/kb-sharing/", headers=tok_admin)
        rows = json.loads(body) if st == 200 else []
        check(
            st == 200 and any(r.get("id") == req_a.get("id") for r in rows),
            "P7 admin list sees all",
            f"got {st} rows={len(rows)}",
        )
        st, body = call("GET", f"/api/kb-sharing/?user_id={uid_a}", headers=tok_admin)
        rows = json.loads(body) if st == 200 else []
        check(
            st == 200 and bool(rows) and all(r.get("user_id") == uid_a for r in rows),
            "P7 admin user_id filter",
            f"got {st} rows={len(rows)}",
        )

        # P8 admin 批准（独立新申请，与 P2 结果解耦）→A 知识行标记企业共享（shared_by=申请人）
        st8c, body8c = call(
            "POST",
            "/api/kb-sharing/",
            {"kb_id": f"{MARK}-kbid8", "kb_name": f"{MARK}-kb8"},
            tok_a,
        )
        req8 = json.loads(body8c) if st8c == 200 else {}
        st, body = call(
            "POST",
            f"/api/kb-sharing/{req8.get('id')}/review",
            {"status": "approved", "review_note": f"{MARK}-ok"},
            tok_admin,
        )
        check(st == 200, "P8 admin approve 200", f"got {st} {body[:100]}")
        if st == 200:
            body_j = json.loads(body)
            check(
                body_j.get("reviewer_id") == uid_admin,
                "P8 reviewer_id = admin identity",
                f"got {body_j.get('reviewer_id')}",
            )
        kn = q("SELECT metadata FROM knowledge WHERE id = ?", (f"{MARK}-k1",))
        marked = bool(kn) and "shared_to_enterprise" in (kn[0]["metadata"] or "")
        check(marked, "P8 applicant knowledge marked shared", f"marked={marked}")
        shared_by_applicant = bool(kn) and uid_a in (kn[0]["metadata"] or "")
        check(shared_by_applicant, "P8 shared_by = applicant", "")

        # P9 admin 拒第二件→零 knowledge 写
        st2, body2 = call(
            "POST",
            "/api/kb-sharing/",
            {"kb_id": f"{MARK}-kbid2", "kb_name": f"{MARK}-kb2"},
            tok_a,
        )
        req2 = json.loads(body2) if st2 == 200 else {}
        exec_sql(
            "INSERT INTO knowledge (id, user_id, title, source) VALUES (?, ?, ?, 'e2e')",
            (f"{MARK}-k2", uid_a, f"{MARK}-kn2"),
        )
        st, body = call(
            "POST",
            f"/api/kb-sharing/{req2.get('id')}/review",
            {"status": "rejected"},
            tok_admin,
        )
        check(st == 200 and json.loads(body).get("status") == "rejected", "P9 reject 200", str(st))
        kn = q("SELECT metadata FROM knowledge WHERE id = ?", (f"{MARK}-k2",))
        check(
            bool(kn) and "shared_to_enterprise" not in (kn[0]["metadata"] or ""),
            "P9 reject zero knowledge write",
            "",
        )

        # P10 /my 只回自己的
        st, body = call("GET", "/api/kb-sharing/my", headers=tok_b)
        rows = json.loads(body) if st == 200 else []
        check(st == 200 and rows == [], "P10 /my own only (B empty)", f"got {len(rows)}")
        st, body = call("GET", "/api/kb-sharing/my", headers=tok_a)
        rows = json.loads(body) if st == 200 else []
        check(
            st == 200 and all(r.get("user_id") == uid_a for r in rows) and len(rows) >= 2,
            "P10 /my A sees own",
            f"got {len(rows)}",
        )

        # P11 status 过滤
        st, body = call("GET", "/api/kb-sharing/?status=approved", headers=tok_a)
        rows = json.loads(body) if st == 200 else []
        check(
            st == 200 and all(r.get("status") == "approved" for r in rows),
            "P11 status filter",
            f"got {len(rows)}",
        )

        # P12 401 电池
        st, _ = call("GET", "/api/kb-sharing/")
        check(st == 401, "P12 no token 401", str(st))
        st, _ = call(
            "GET", "/api/kb-sharing/", headers={"Authorization": "Bearer not-a-token"}
        )
        check(st == 401, "P12 bad token 401", str(st))

    finally:
        # 清痕：users/kb_sharing_requests/knowledge 按 MARK 精确删
        exec_sql("DELETE FROM users WHERE username LIKE ?", (f"{MARK}%",))
        exec_sql("DELETE FROM kb_sharing_requests WHERE kb_name LIKE ?", (f"{MARK}%",))
        exec_sql("DELETE FROM knowledge WHERE title LIKE ?", (f"{MARK}%",))
        remaining = {
            "users": len(q("SELECT id FROM users WHERE username LIKE ?", (f"{MARK}%",))),
            "requests": len(
                q("SELECT id FROM kb_sharing_requests WHERE kb_name LIKE ?", (f"{MARK}%",))
            ),
            "knowledge": len(q("SELECT id FROM knowledge WHERE title LIKE ?", (f"{MARK}%",))),
        }
        tl_delta = timeline_count() - tl0
        print("CLEANUP remaining=", remaining, "timeline_delta=", tl_delta)

    ok = sum(1 for v, _ in RESULTS if v)
    print(f"RESULT {ok}/{len(RESULTS)}")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
