#!/usr/bin/env python3
"""F3b-⑱ live e2e 探针：hippo /sessions user_id 轴收口（会话登记另轴）。

用法：
  python3 scripts/e2e_hippo_sessions_identity.py --pre   # 修前探针（缺口坐实）
  python3 scripts/e2e_hippo_sessions_identity.py          # 修后全绿

真实栈 urllib → :8090；探针自带清痕（探针会话 API 删除 + users MARK 行精确删
+ 回读 remaining=0 + timeline delta=0 含事件行面 TR-② 纪律）。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
DB = "/home/climbing/opensoul/data/opensoul.db"
TIMELINE_DB = "/home/climbing/opensoul/data/timeline.db"
MARK = "e2ef3b18"

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


def call(method: str, path: str, body=None, headers=None, params=None):
    if params:
        path = path + ("&" if "?" in path else "?") + urllib.parse.urlencode(params)
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


def jbody(text: str):
    try:
        return json.loads(text)
    except Exception:
        return {}


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
    for uid, name in ((uid_a, f"{MARK}-a"), (uid_b, f"{MARK}-b")):
        exec_sql(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, 'default')",
            (uid, name),
        )
    tok_a = {"Authorization": "Bearer " + make_token(uid_a)}
    tok_b = {"Authorization": "Bearer " + make_token(uid_b)}
    bad = {"Authorization": "Bearer not-a-real-token"}
    sid_keep = f"{MARK}-keep"

    def expect(pre_val, post_val):
        return pre_val if pre else post_val

    # ── P1 create 行归属=认证派生（另轴=users.id 原值） ──
    st, body = call("POST", "/api/hippo/sessions", {"title": f"{MARK} s1"}, headers=tok_a)
    s1 = jbody(body)
    check(st == 200 and s1.get("session_id"), "P1 A 建会话 200", body[:80])
    if not pre:
        check(s1.get("user_id") == uid_a, "P1 行归属=派生原值（非 md5 正典）", str(s1.get("user_id")))

    st2, body2 = call("POST", "/api/hippo/sessions", {"title": f"{MARK} s2"}, headers=tok_b)
    s2 = jbody(body2)
    check(st2 == 200, "P2 B 建会话 200", body2[:60])

    # ── P3 list 缺省=全量泄露 → 「用户看自己的」 ──
    st, body = call("GET", "/api/hippo/sessions", headers=tok_a, params={"limit": 200})
    ids = [s.get("session_id") for s in jbody(body).get("sessions", [])]
    leaked = s2.get("session_id") in ids
    check(st == 200, "P3 list 200", "")
    check(leaked == expect(True, False), f"P3 list 缺省视图 {'泄露 B 会话' if pre else '只见自己'}", f"n={len(ids)} leaked={leaked}")

    # ── P4 create 自报矛盾=冒写他人名义 → 403 ──
    st, body = call(
        "POST", "/api/hippo/sessions", {"title": f"{MARK} s3", "user_id": uid_b}, headers=tok_a
    )
    check(st == expect(200, 403), f"P4 自报矛盾 {'冒写 200' if pre else '403'}", f"got {st}")
    if not pre:
        check("user mismatch" in body, "P4 403 带 user mismatch 真因", body[:80])

    # ── P5/P6/P7 by-ID 零闸 → 归属闸 ──
    st, body = call("GET", f"/api/hippo/sessions/{s1.get('session_id')}", headers=tok_b)
    check(st == expect(200, 404), f"P5 B 冒读 A 会话 {'200' if pre else '404'}", f"got {st}")

    st, body = call(
        "POST", f"/api/hippo/sessions/{s1.get('session_id')}/archive", headers=tok_b
    )
    check(st == expect(200, 404), f"P6 B 冒归档 A 会话 {'200' if pre else '404'}", f"got {st}")
    if not pre:
        st, body = call("GET", f"/api/hippo/sessions/{s1.get('session_id')}", headers=tok_a)
        check(jbody(body).get("status") == "active", "P6 行零改写（仍 active）", body[:80])

    # s4 = 冒删专用一次性会话
    st, body = call("POST", "/api/hippo/sessions", {"title": f"{MARK} s4"}, headers=tok_a)
    s4 = jbody(body).get("session_id", "")
    st, body = call("DELETE", f"/api/hippo/sessions/{s4}", headers=tok_b)
    check(st == expect(200, 404), f"P7 B 冒删 A 会话 {'200' if pre else '404'}", f"got {st}")
    if not pre:
        st, body = call("GET", f"/api/hippo/sessions/{s4}", headers=tok_a)
        check(st == 200, "P7 行存活（冒删零破坏）", f"got {st}")

    # ── P8 坏 token 401 零副作用 ──
    st, body = call("GET", f"/api/hippo/sessions/{s1.get('session_id')}", headers=bad)
    check(st == expect(200, 401), f"P8 坏 token {'200 无鉴权' if pre else '401'}", f"got {st}")

    # ── P9 属主正对照 + 匿名自报契约 ──
    st, body = call(
        "GET",
        f"/api/hippo/sessions/{s1.get('session_id')}",
        headers=tok_a,
        params={"user_id": uid_a},
    )
    check(st == 200, "P9 属主读 200 正对照", f"got {st}")
    st, body = call(
        "POST",
        "/api/hippo/sessions",
        {"title": f"{MARK} anon", "user_id": f"{MARK}-anon"},
    )
    anon = jbody(body)
    check(st == 200, "P9 匿名自报建会话 200 契约不变", f"got {st}")
    st, body = call(
        "GET",
        f"/api/hippo/sessions/{anon.get('session_id')}",
        params={"user_id": f"{MARK}-anon"},
    )
    check(st == 200, "P9 匿名自报读自己会话 200 契约不变", f"got {st}")

    # ── 清痕 ──
    for sid, owner in (
        (s1.get("session_id", ""), uid_a),
        (s2.get("session_id", ""), uid_b),
        (s4, uid_a),
        (anon.get("session_id", ""), f"{MARK}-anon"),
    ):
        if sid:
            call("DELETE", f"/api/hippo/sessions/{sid}", params={"user_id": owner}, headers=tok_a if owner == uid_a else None)
    exec_sql("DELETE FROM users WHERE username LIKE ?", (f"{MARK}%",))
    remaining = q("SELECT COUNT(*) AS n FROM users WHERE username LIKE ?", (f"{MARK}%",))[0]["n"]
    check(remaining == 0, "清痕 users remaining=0", str(remaining))
    st, body = call("GET", "/api/hippo/sessions", params={"limit": 200, "user_id": f"{MARK}-anon"})
    gone = all(MARK not in (s.get("title") or "") for s in jbody(body).get("sessions", []))
    check(gone, "清痕 探针会话不可见", "")
    tl1 = timeline_count()
    check(tl1 - tl0 == 0, "timeline delta=0（零事件行面）", f"delta={tl1 - tl0}")

    failed = [n for ok, n in RESULTS if not ok]
    print(f"\n{'--pre' if pre else '--post'}: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("FAILED:", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
