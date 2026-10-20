#!/usr/bin/env python3
"""F3b-⑳ live e2e 探针：hippo /memories 身份轴收口（记忆行归属=父会话行继承）。

用法：
  python3 scripts/e2e_memory_face_identity.py --pre   # 修前探针（缺口坐实）
  python3 scripts/e2e_memory_face_identity.py --post  # 修后全绿（自带清痕）

真实栈 urllib → :8090；真身份=enterprise register→login→token。断言面：
冒写/冒读/冒改/冒删 404 零副作用、list「用户看自己的」、peek 零副作用
（被拒读取 access_count 不刷）、自动建会话行归属=派生身份、坏 token 401
电池、自报矛盾 403。清痕：记忆/会话 API 删除 + enterprise users 行删除 +
timeline delta=0 含事件行面 TR-② 纪律。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8090"
REPO = os.path.expanduser("~/opensoul")
TIMELINE_DB = os.path.join(REPO, "data", "timeline.db")
MARK = "e2e-mf20"
PRE = "--pre" in sys.argv
POST = "--post" in sys.argv
RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


def call(method: str, path: str, body=None, params=None, token=None):
    if params:
        path = path + ("&" if "?" in path else "?") + urllib.parse.urlencode(params)
    data = None
    hdrs = {}
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if token:
        hdrs["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, (json.loads(raw) if raw else {})
        except Exception:
            return e.code, {"raw": raw[:200]}


def timeline_total():
    try:
        with sqlite3.connect(TIMELINE_DB) as c:
            row = c.execute("SELECT COUNT(*) FROM events").fetchone()
        return row[0]
    except Exception:
        return None


def main():
    if not (PRE or POST):
        print("need --pre or --post")
        return 2
    stamp = str(int(time.time()))[-6:]
    ent_db = os.path.join(REPO, "data", "enterprise.db")
    t0 = timeline_total()

    st, _ = call("GET", "/api/health")
    check(st == 200, "health 200", str(st))

    # ---------- 真身份（enterprise register→login→token） ----------
    users = {}
    for tag in ("a", "b"):
        uname = f"{MARK}-{tag}-{stamp}"
        st, body = call(
            "POST",
            "/api/enterprise/auth/register",
            {"username": uname, "password": "E2e-Pass-1"},
        )
        check(st in (200, 201), f"register {tag}", str(body)[:60])
        st, body = call(
            "POST",
            "/api/enterprise/auth/login",
            {"username": uname, "password": "E2e-Pass-1"},
        )
        tok = body.get("token") or body.get("access_token")
        check(st == 200 and bool(tok), f"login {tag} token", str(body)[:60])
        users[tag] = {"username": uname, "token": tok}
    tok_a, tok_b = users["a"]["token"], users["b"]["token"]
    ent_ids = {}
    with sqlite3.connect(ent_db) as c:
        for tag in ("a", "b"):
            row = c.execute(
                "SELECT id FROM users WHERE username = ?", (users[tag]["username"],)
            ).fetchone()
            ent_ids[tag] = str(row[0])
    uid_a, uid_b = ent_ids["a"], ent_ids["b"]

    sid_a = f"{MARK}-sa-{stamp}"
    sid_anon = f"{MARK}-sanon-{stamp}"
    sid_self = f"{MARK}-sself-{stamp}"
    sid_bad = f"{MARK}-sbad-{stamp}"

    # ---------- A 建会话 + 记忆 ----------
    st, body = call(
        "POST", "/api/hippo/sessions", {"user_id": "", "title": MARK}, token=tok_a
    )
    check(st == 200, "A create session", str(body)[:60])
    # A 的显式 sid 会话（经 create_session 返回的 sid 不可控，用自动建会话拿显式 sid）
    st, body = call(
        "POST",
        "/api/hippo/memories",
        {"content": "A 记忆 e2e", "session_id": sid_a},
        token=tok_a,
    )
    check(st == 200, "A write memory into auto session", str(body)[:60])
    mid_a = body.get("memory_id")

    if PRE:
        # 修前缺口硬证（预期 FAIL 面）：
        st, body = call(
            "POST",
            "/api/hippo/memories",
            {"content": "B 冒写 e2e", "session_id": sid_a},
            token=tok_b,
        )
        check(st == 200, "[gap] B cross-user write lands 200", str(body)[:60])
        st, body = call("GET", "/api/hippo/memories", token=tok_b)
        seen = [m.get("session_id") for m in body.get("memories", [])]
        check(sid_a in seen, "[gap] B list sees A memory (全量泄露)", str(seen)[:80])
        st, body = call("GET", f"/api/hippo/memories/{mid_a}", token=tok_b)
        check(st == 200, "[gap] B cross-user get 200", str(st))
        st, body = call(
            "POST", "/api/hippo/memories", {"content": "x", "session_id": sid_bad},
            token="not-a-real-token",
        )
        check(st == 200, "[gap] bad token write lands 200", str(st))
        st, body = call("GET", f"/api/hippo/sessions/{sid_a}", token=tok_a)
        check(st == 404, "[gap] auto session unowned by derived user (恒 default)", str(st))
    else:
        # 1) 冒写面收口：B 写 A 会话记忆 404 零副作用
        st, body = call(
            "POST",
            "/api/hippo/memories",
            {"content": "B 冒写 e2e", "session_id": sid_a},
            token=tok_b,
        )
        check(st == 404, "B cross-user write 404", f"{st} {str(body)[:60]}")
        st, body = call("GET", "/api/hippo/memories", token=tok_a)
        check(
            body.get("total") == 1 and not any(
                m.get("content") == "B 冒写 e2e" for m in body.get("memories", [])
            ),
            "A list total==1 零冒写行",
            str(body.get("total")),
        )
        # 2) list「用户看自己的」
        st, body = call("GET", "/api/hippo/memories", token=tok_b)
        check(body.get("total") == 0, "B list 不见 A 记忆", str(body.get("total")))
        st, body = call(
            "GET", "/api/hippo/memories", params={"session_id": sid_a}, token=tok_b
        )
        check(st == 404, "B explicit session_id 404（越权=同缺行）", str(st))
        # 3) 冒读/冒改/冒删 404 + 行零改写
        st, body = call("GET", f"/api/hippo/memories/{mid_a}", token=tok_b)
        check(st == 404, "B cross-user get 404", str(st))
        st, body = call(
            "PATCH", f"/api/hippo/memories/{mid_a}", {"content": "B 冒改"}, token=tok_b
        )
        check(st == 404, "B cross-user patch 404", str(st))
        st, body = call("GET", f"/api/hippo/memories/{mid_a}", token=tok_a)
        check(
            st == 200 and body.get("content") == "A 记忆 e2e",
            "content 行零改写",
            str(body.get("content"))[:40],
        )
        st, body = call("DELETE", f"/api/hippo/memories/{mid_a}", token=tok_b)
        check(st == 404, "B cross-user delete 404", str(st))
        st, body = call("GET", f"/api/hippo/memories/{mid_a}", token=tok_a)
        check(st == 200, "行存活（A 仍可读）", str(st))
        # 4) peek 零副作用：A 自己两次读 access_count 递增（读取即活动契约），
        #    B 被拒读取不刷（修前 get() 直刷）
        ac0 = int((body.get("access_count") if isinstance(body, dict) else 0) or 0)
        call("GET", f"/api/hippo/memories/{mid_a}", token=tok_b)
        st, body = call("GET", f"/api/hippo/memories/{mid_a}", token=tok_a)
        ac1 = int((body.get("access_count") if isinstance(body, dict) else 0) or 0)
        check(
            ac1 == ac0 + 1,
            "被拒读取零刷+过闸读取真刷",
            f"ac {ac0}->{ac1}",
        )
        # 5) 自动建会话行归属=派生身份（修前恒 default）
        st, body = call(
            "POST",
            "/api/hippo/memories",
            {"content": "auto 归属", "session_id": f"{MARK}-auto-{stamp}"},
            token=tok_a,
        )
        check(st == 200, "A auto-create write", str(body)[:60])
        mid_auto = body.get("memory_id") if isinstance(body, dict) else None
        st, body = call(
            "GET", f"/api/hippo/sessions/{MARK}-auto-{stamp}", token=tok_a
        )
        check(
            st == 200 and body.get("user_id") == uid_a,
            "auto session 归属=派生身份",
            f"{st} owner={body.get('user_id')} want={uid_a}",
        )
        # 6) 匿名自报契约（本地多档案语义）
        st, body = call(
            "POST",
            "/api/hippo/memories",
            {"content": "匿名自报", "session_id": sid_self},
            params={"user_id": "profile-x"},
        )
        check(st == 200, "anon self-report write", str(body)[:60])
        mid_self = body.get("memory_id") if isinstance(body, dict) else None
        st, body = call("GET", "/api/hippo/memories", params={"user_id": "profile-x"})
        check(body.get("total") == 1, "anon self-report bucket 可见", str(body.get("total")))
        st, body = call("GET", "/api/hippo/memories")
        check(body.get("total") == 0, "anon default bucket 不见 profile-x", str(body.get("total")))
        # 7) 自报矛盾 403 + 坏 token 401 电池
        st, body = call(
            "POST",
            "/api/hippo/memories",
            {"content": "x", "session_id": sid_a},
            params={"user_id": uid_b},
            token=tok_a,
        )
        check(st == 403 and "user mismatch" in str(body), "self-report mismatch 403", str(body)[:80])
        bad_hits = []
        for method, path, p in (
            ("POST", "/api/hippo/memories", {"session_id": sid_bad}),
            ("GET", "/api/hippo/memories", None),
            ("GET", f"/api/hippo/memories/{mid_a}", None),
            ("PATCH", f"/api/hippo/memories/{mid_a}", None),
            ("DELETE", f"/api/hippo/memories/{mid_a}", None),
        ):
            body_arg = {"content": "x", "session_id": sid_bad} if method in ("POST", "PATCH") else None
            st, _ = call(method, path, body_arg, token="not-a-real-token")
            bad_hits.append((method, st))
        check(
            all(stt == 401 for _, stt in bad_hits),
            "bad token 401 电池×5 零副作用",
            str(bad_hits),
        )
        # 8) 清痕：记忆/会话 API 删除
        st, _ = call("DELETE", f"/api/hippo/memories/{mid_a}", token=tok_a)
        check(st == 200, "cleanup delete A memory", str(st))
        if mid_auto:
            call("DELETE", f"/api/hippo/memories/{mid_auto}", token=tok_a)
        if mid_self:
            call(
                "DELETE",
                f"/api/hippo/memories/{mid_self}",
                params={"user_id": "profile-x"},
            )
        st, body = call("GET", "/api/hippo/memories", token=tok_a)
        leftover_a = body.get("total", -1)
        st, body = call("GET", "/api/hippo/memories", params={"user_id": "profile-x"})
        leftover_x = body.get("total", -1)
        call(
            "DELETE", "/api/hippo/sessions/" + sid_self, params={"user_id": "profile-x"}
        )
        for sid, tok in ((sid_a, tok_a), (f"{MARK}-auto-{stamp}", tok_a)):
            call("DELETE", f"/api/hippo/sessions/{sid}", token=tok)
        check(
            leftover_a == 0 and leftover_x == 0,
            "cleanup memories remaining=0",
            f"a={leftover_a} x={leftover_x}",
        )

    # ---------- enterprise users 清痕 ----------
    try:
        with sqlite3.connect(ent_db) as c:
            c.execute("DELETE FROM users WHERE username LIKE ?", (f"{MARK}-%",))
            c.commit()
        with sqlite3.connect(ent_db) as c:
            left = c.execute(
                "SELECT COUNT(*) FROM users WHERE username LIKE ?", (f"{MARK}-%",)
            ).fetchone()[0]
        check(left == 0, "cleanup enterprise users remaining=0", str(left))
    except Exception as exc:
        check(False, "cleanup enterprise users", str(exc))

    t1 = timeline_total()
    check(t1 == t0, "timeline delta=0（事件行面 TR-② 纪律）", f"{t0}->{t1}")

    passed = sum(1 for ok, _ in RESULTS if ok)
    print(f"\n== {passed}/{len(RESULTS)} checks passed ==")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
