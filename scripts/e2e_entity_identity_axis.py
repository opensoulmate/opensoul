#!/usr/bin/env python3
"""e2e probe: entity CRUD 面 user_id 轴 bind_identity 收口（F3b-⑦）修前/修后双向硬证。

用法：python3 scripts/e2e_entity_identity_axis.py --pre|--post
  --pre  : 旧代码在跑——断言缺口存在（认证自报他人档案 200 直写、坏 token 200 无鉴权轴）
  --post : 新代码在跑——断言缺口已闭（403 user mismatch / 401 / 派生键真命中）
两模式共同断言：匿名契约零变化（缺省=md5('default') 档、自报保留）、404/删除契约、
timeline 事件行 delta=0（TR-② 纪律）。取证后清痕 remaining=0。
"""

import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
CTX = ssl.create_default_context()
RESULTS = []
CLEANUPS = []


def call(method, path, body=None, token=None, user_id=None):
    url = BASE + path
    qs = []
    if user_id is not None:
        qs.append("user_id=" + urllib.parse.quote(str(user_id)))
    if qs:
        url += ("&" if "?" in url else "?") + "&".join(qs)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30, context=CTX) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, (json.loads(raw) if raw else {})
        except Exception:
            return e.code, {"raw": raw}


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS" if ok else "FAIL"), name, detail)


def expect(name, got_status, want_status, detail=""):
    check(name, got_status == want_status, f"got {got_status} want {want_status} {detail}")


def timeline_total():
    st, body = call("GET", "/api/timeline/stats")
    return body.get("total_events") if st == 200 else None


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    assert mode in ("--pre", "--post"), "need --pre or --post"
    stamp = str(int(time.time()))[-6:]
    ent_name = f"e2e-ent-identity-{stamp}"

    t0 = timeline_total()
    st, _ = call("GET", "/api/health")
    expect("health 200", st, 200)

    # ---------- 匿名契约（两模式共同，零变化面） ----------
    st, body = call("POST", "/api/entity/", {"name": ent_name + "-anon", "entity_type": "concept"})
    check("anon default create 200", st == 200 and "id" in body, str(body)[:80])
    anon_id = body.get("id")
    if anon_id:
        CLEANUPS.append(("anon-default", anon_id, None))
        st, detail = call("GET", f"/api/entity/{anon_id}")
        check("anon default get 200", st == 200, "")
        st, _ = call("DELETE", f"/api/entity/{anon_id}")
        check("anon default delete 200", st == 200, "")
        CLEANUPS.pop()

    st, body = call(
        "POST", "/api/entity/", {"name": ent_name + "-self", "entity_type": "concept"}, user_id="e2e-self-profile"
    )
    check("anon self-report create 200", st == 200 and "id" in body, str(body)[:80])
    self_id = body.get("id")
    if self_id:
        st, _ = call("DELETE", f"/api/entity/{self_id}", user_id="e2e-self-profile")
        check("anon self-report delete 200", st == 200, "")

    # ---------- 真身份（enterprise register→login→token） ----------
    users = {}
    for tag in ("a", "b"):
        uname = f"e2e-entid-{tag}-{stamp}"
        st, body = call("POST", "/api/enterprise/auth/register", {"username": uname, "password": "E2e-Pass-1"})
        check(f"register {tag} ok", st in (200, 201), f"{st} {str(body)[:60]}")
        st, body = call("POST", "/api/enterprise/auth/login", {"username": uname, "password": "E2e-Pass-1"})
        tok = body.get("token") or body.get("access_token")
        check(f"login {tag} 200+token", st == 200 and bool(tok), str(body)[:60])
        users[tag] = {"username": uname, "token": tok}

    tok_a, tok_b = users["a"]["token"], users["b"]["token"]

    # ---------- 冒读/冒写面（核心缺口） ----------
    victim_key = f"e2e-victim-{stamp}"
    st, body = call(
        "POST", "/api/entity/", {"name": ent_name + "-x", "entity_type": "concept"}, token=tok_a, user_id=victim_key
    )
    if mode == "--pre":
        check("GAP cross-user create 200 (修前直写)", st == 200, str(body)[:80])
        x_id = body.get("id")
        if x_id:
            st, _ = call("DELETE", f"/api/entity/{x_id}", user_id=victim_key)
            check("pre gap row cleaned", st == 200, "")
    else:
        expect("cross-user create 403", st, 403, str(body)[:80])
        check("403 detail user mismatch", "user mismatch" in str(body.get("detail", "")), str(body)[:80])

    st, body = call("GET", "/api/entity/", token=tok_a, user_id=victim_key)
    if mode == "--pre":
        check("GAP cross-user list 200 (修前冒读)", st == 200, "")
    else:
        expect("cross-user list 403", st, 403, "")

    st2, _ = call("GET", "/api/entity/", token="garbage-token")
    if mode == "--pre":
        check("GAP bad token 200 (修前无鉴权轴)", st2 == 200, str(st2))
    else:
        expect("bad token 401", st2, 401, "")

    # ---------- 派生键真命中（--post） ----------
    if mode == "--post":
        st, body = call("POST", "/api/entity/", {"name": ent_name + "-drv", "entity_type": "concept"}, token=tok_a)
        check("derived create 200", st == 200 and "id" in body, str(body)[:80])
        drv_id = body.get("id")
        if drv_id:
            CLEANUPS.append(("tok-a", drv_id, tok_a))
            st, lst = call("GET", "/api/entity/", token=tok_a, user_id="default")
            ids = [e.get("id") for e in lst] if isinstance(lst, list) else []
            check("derived visible under user_id=default+token", drv_id in ids, str(ids)[:80])
            st, lst_b = call("GET", "/api/entity/", token=tok_b, user_id="default")
            ids_b = [e.get("id") for e in lst_b] if isinstance(lst_b, list) else []
            check("B token list excludes A entity", drv_id not in ids_b, str(ids_b)[:80])
            st, body = call("DELETE", f"/api/entity/{drv_id}", token=tok_b, user_id="default")
            check("cross-user delete 404 row survives", st == 404, str(body)[:60])
            st, detail = call("GET", f"/api/entity/{drv_id}", token=tok_a, user_id="default")
            check("row survived B delete attempt", st == 200, "")
            st, body = call("DELETE", f"/api/entity/{drv_id}", token=tok_a, user_id="e2e-elsewhere")
            expect("conflict delete 403", st, 403, str(body)[:60])
            st, _ = call("DELETE", f"/api/entity/{drv_id}", token=tok_a, user_id="default")
            check("owner delete 200", st == 200, "")
            CLEANUPS.pop()

    # ---------- 清痕 ----------
    remaining = 0
    for tag, eid, tok in list(CLEANUPS):
        st, _ = call("DELETE", f"/api/entity/{eid}", token=tok)
        if st != 200:
            remaining += 1
        CLEANUPS.remove((tag, eid, tok))

    # 探针自建 enterprise 用户清痕（users 行；审计行按审计语义保留=②c 先例）
    try:
        import os
        import sqlite3

        ent_db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "enterprise.db")
        with sqlite3.connect(ent_db) as c:
            c.execute("DELETE FROM users WHERE username LIKE 'e2e-entid-%'")
            c.commit()
            leftover = c.execute(
                "SELECT COUNT(*) FROM users WHERE username LIKE 'e2e-entid-%'"
            ).fetchone()[0]
            remaining += int(leftover)
    except Exception as exc:  # 如实报，禁静默
        print("WARN enterprise user cleanup:", exc)
        remaining += 1

    t1 = timeline_total()
    check("timeline delta=0 (零事件行面 TR-②)", t0 == t1, f"{t0} -> {t1}")
    check("cleanup remaining=0", remaining == 0, str(remaining))

    fails = [r for r in RESULTS if not r[1]]
    print(f"\n== {mode} {len(RESULTS) - len(fails)}/{len(RESULTS)} ==")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
