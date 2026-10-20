#!/usr/bin/env python3
"""F3b-⑯ live e2e 探针：knowledge_requests 面身份轴收口（user 派生 + admin 门）。

用法：
  python3 scripts/e2e_knowledge_requests_admin_gate.py --pre   # 修前探针（缺口坐实）
  python3 scripts/e2e_knowledge_requests_admin_gate.py          # 修后全绿

真实栈 urllib → :8090；探针自带清痕（users/knowledge_requests/knowledge 按 MARK
精确删 + 回读 remaining=0）。测试行绝不留生产库。
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
MARK = "e2ekr9f1"

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

    return create_access_token(user_id=uuid.UUID(user_id), role="user")


def main() -> int:
    pre = "--pre" in sys.argv

    uid_a = str(uuid.uuid4())
    uid_b = str(uuid.uuid4())
    uid_admin = str(uuid.uuid4())
    users = [(uid_a, f"{MARK}-a", "user"), (uid_b, f"{MARK}-b", "user"), (uid_admin, f"{MARK}-admin", "admin")]
    for uid, name, role in users:
        exec_sql(
            "INSERT INTO users (id, username, password_hash, role, tenant_id) "
            "VALUES (?, ?, 'x', ?, 'default')",
            (uid, name, role),
        )
    tok_a = {"Authorization": f"Bearer {make_token(uid_a)}"}
    tok_b = {"Authorization": f"Bearer {make_token(uid_b)}"}
    tok_admin = {"Authorization": f"Bearer {make_token(uid_admin)}"}

    try:
        # P0 health
        st, _ = call("GET", "/api/knowledge-requests/health")
        check(st == 200, "P0 health 200", str(st))

        # P1 A 提交申请（行归属）
        st, body = call(
            "POST",
            "/api/knowledge-requests/",
            {"kb_name": f"{MARK}-kb1", "kb_description": "probe"},
            tok_a,
        )
        check(st == 200, "P1 create 200", f"{st}")
        req_a = json.loads(body) if st == 200 else {}
        check(
            req_a.get("user_id") == uid_a,
            "P1 row attribution = derived archive key",
            f"user_id={req_a.get('user_id')} want={uid_a}",
        )

        # P2 B 审批 A 的申请（修前 200 冒写面 / 修后 403 管理员门）
        st, body = call(
            "POST",
            f"/api/knowledge-requests/{req_a.get('id')}/review",
            {"status": "approved", "review_note": f"{MARK}-review"},
            tok_b,
        )
        check(
            st == (200 if pre else 403),
            f"P2 non-admin review -> {200 if pre else 403} ({'GAP' if pre else 'gate'})",
            f"got {st} {body[:120]}",
        )
        krows = q(
            "SELECT * FROM knowledge WHERE user_id = ? AND title = ? AND source = 'auto-created'",
            (uid_a, f"{MARK}-kb1"),
        )
        check(
            len(krows) == (1 if pre else 0),
            f"P2 approve-side knowledge rows == {1 if pre else 0} ({'GAP 冒写' if pre else 'zero side effect'})",
            f"got {len(krows)}",
        )
        row_a = q("SELECT * FROM knowledge_requests WHERE id = ?", (req_a.get("id"),))
        if not pre:
            check(
                bool(row_a) and row_a[0]["status"] == "pending" and row_a[0]["reviewer_id"] is None,
                "P2 row untouched (pending/reviewer NULL)",
                str(row_a),
            )

        # P3 A 自审自批（独立申请——不复用已被裁决的 req_a；修前 200 / 修后 403）
        st, body = call(
            "POST",
            "/api/knowledge-requests/",
            {"kb_name": f"{MARK}-kb2", "kb_description": "probe"},
            tok_a,
        )
        req_a2 = json.loads(body) if st == 200 else {}
        st, body = call(
            "POST",
            f"/api/knowledge-requests/{req_a2.get('id')}/review",
            {"status": "rejected"},
            tok_a,
        )
        check(
            st == (200 if pre else 403),
            f"P3 self review -> {200 if pre else 403} ({'GAP' if pre else 'gate'})",
            f"got {st}",
        )

        # P4 B 列表（修前全量泄露 / 修后只看自己的）
        st, body = call("GET", "/api/knowledge-requests/", None, tok_b)
        names = [x["kb_name"] for x in json.loads(body)] if st == 200 else []
        leaked = f"{MARK}-kb1" in names
        check(st == 200, "P4 list 200", str(st))
        check(
            leaked if pre else (not leaked),
            f"P4 B list {'leaks A row (GAP)' if pre else 'own only'}",
            f"names={names}",
        )

        # P5 B 自报 user_id=A（修前 200 冒读 / 修后 403）
        st, body = call(
            "GET", f"/api/knowledge-requests/?user_id={uid_a}", None, tok_b
        )
        check(
            st == (200 if pre else 403),
            f"P5 self-report foreign -> {200 if pre else 403} ({'GAP' if pre else '403 mismatch'})",
            f"got {st} {body[:100]}",
        )

        # P6 B 读 A 的申请（修前 200 / 修后 404 同缺行契约）
        st, body = call("GET", f"/api/knowledge-requests/{req_a.get('id')}", None, tok_b)
        check(
            st == (200 if pre else 404),
            f"P6 get foreign -> {200 if pre else 404} ({'GAP' if pre else 'not-found contract'})",
            f"got {st}",
        )

        # P7 admin 面：看全部 + 过滤 + 裁决（两代都该通=admin 面契约不变）
        st, body = call("GET", "/api/knowledge-requests/", None, tok_admin)
        names = [x["kb_name"] for x in json.loads(body)] if st == 200 else []
        check(
            st == 200 and f"{MARK}-kb1" in names,
            "P7 admin list sees all",
            f"{st} {names}",
        )
        st, body = call(
            "GET", f"/api/knowledge-requests/?user_id={uid_b}", None, tok_admin
        )
        names = [x["kb_name"] for x in json.loads(body)] if st == 200 else []
        check(
            st == 200 and f"{MARK}-kb1" not in names,
            "P7 admin filter by user_id excludes others",
            f"{st} {names}",
        )
        # P7 admin 裁决面（独立申请 req3——admin 面两代契约不变）
        st, body = call(
            "POST",
            "/api/knowledge-requests/",
            {"kb_name": f"{MARK}-kb3", "kb_description": "probe"},
            tok_a,
        )
        req_a3 = json.loads(body) if st == 200 else {}
        st, body = call(
            "POST",
            f"/api/knowledge-requests/{req_a3.get('id')}/review",
            {"status": "approved", "review_note": f"{MARK}-ok"},
            tok_admin,
        )
        check(st == 200, "P7 admin review 200", f"{st} {body[:100]}")
        krows = q(
            "SELECT * FROM knowledge WHERE user_id = ? AND title = ? AND source = 'auto-created'",
            (uid_a, f"{MARK}-kb3"),
        )
        check(
            len(krows) == 1 and krows[0]["user_id"] == uid_a,
            "P7 admin approve -> knowledge row under requester archive key",
            f"rows={[(r['user_id'], r['title']) for r in krows]}",
        )
        row_a3 = q("SELECT * FROM knowledge_requests WHERE id = ?", (req_a3.get("id"),))
        check(
            bool(row_a3) and row_a3[0]["reviewer_id"] == uid_admin,
            "P7 reviewer_id = admin derived id",
            str(bool(row_a3) and row_a3[0]["reviewer_id"]),
        )

        # P8 认证契约不变：无 token 401 / 坏 token 401
        st, _ = call("GET", "/api/knowledge-requests/")
        check(st == 401, "P8 no-token list 401", str(st))
        st, _ = call(
            "GET", "/api/knowledge-requests/", None, {"Authorization": "Bearer not-a-jwt"}
        )
        check(st == 401, "P8 bad-token list 401", str(st))

    finally:
        # ── 清痕：按 MARK 精确删 + 回读 remaining=0 ──
        exec_sql("DELETE FROM knowledge WHERE title LIKE ?", (f"{MARK}%",))
        exec_sql("DELETE FROM knowledge_requests WHERE user_name LIKE ?", (f"{MARK}%",))
        exec_sql("DELETE FROM users WHERE username LIKE ?", (f"{MARK}%",))
        rem = {
            "knowledge": len(q("SELECT 1 FROM knowledge WHERE title LIKE ?", (f"{MARK}%",))),
            "requests": len(q("SELECT 1 FROM knowledge_requests WHERE user_name LIKE ?", (f"{MARK}%",))),
            "users": len(q("SELECT 1 FROM users WHERE username LIKE ?", (f"{MARK}%",))),
        }
        ok_clean = all(v == 0 for v in rem.values())
        check(ok_clean, "cleanup remaining=0", str(rem))

    passed = sum(1 for ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{'--pre' if pre else '--post'}: {passed}/{total}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
