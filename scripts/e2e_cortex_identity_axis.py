#!/usr/bin/env python3
"""e2e probe: cortex-enhanced 面 user_id 轴 bind_identity 收口（F3b-⑧）修前/修后双向硬证。

用法：python3 scripts/e2e_cortex_identity_axis.py --pre|--post [--base URL]
  --pre  : 旧代码在跑——断言缺口存在（认证自报他人档案 200 冒读、坏 token 200 无鉴权轴）
  --post : 新代码在跑——断言缺口已闭（403 user mismatch / 401 / 派生键真命中行）
两模式共同断言：匿名契约零变化（缺省/自报保留、响应形状逐键）、timeline 事件行
delta=0（TR-② 纪律——探针只打只读面，/graphrag/build 写面+push_event 由单测覆盖）。
--post 额外：真数据行派生键真命中（SQL 种子→API 读→SQL 清痕 remaining=0）。
"""

import json
import os
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8090")
CTX = ssl.create_default_context()
RESULTS = []
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def md5_uuid_str(s: str) -> str:
    import hashlib
    import uuid

    h = hashlib.md5(s.encode()).hexdigest()
    return str(uuid.UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    assert mode in ("--pre", "--post"), "need --pre or --post"
    stamp = str(int(time.time()))[-6:]

    t0 = timeline_total()
    st, _ = call("GET", "/api/health")
    expect("health 200", st, 200)

    # ---------- 匿名契约（两模式共同，零变化面） ----------
    st, body = call("GET", "/api/cortex/recommend/recent")
    check(
        "anon default recent shape",
        st == 200 and set(body.keys()) == {"entries"} and isinstance(body["entries"], list),
        str(body)[:80],
    )
    st, body = call("GET", "/api/cortex/quality/report")
    check("anon default quality report 200", st == 200 and isinstance(body, dict), str(body)[:80])
    st, body = call("GET", "/api/cortex/recommend/trending", user_id="e2e-self-profile")
    check(
        "anon self-report preserved 200",
        st == 200 and set(body.keys()) == {"entries"},
        str(body)[:80],
    )
    st, body = call("GET", "/api/cortex/recommend/k-x")
    check(
        "recommend shape unchanged",
        st == 200 and set(body.keys()) == {"knowledge_id", "recommendations", "count"},
        str(body)[:80],
    )

    # ---------- 真身份（enterprise register→login→token） ----------
    users = {}
    for tag in ("a", "b"):
        uname = f"e2e-cortexid-{tag}-{stamp}"
        st, body = call(
            "POST", "/api/enterprise/auth/register", {"username": uname, "password": "E2e-Pass-1"}
        )
        check(f"register {tag} ok", st in (200, 201), f"{st} {str(body)[:60]}")
        st, body = call(
            "POST", "/api/enterprise/auth/login", {"username": uname, "password": "E2e-Pass-1"}
        )
        tok = body.get("token") or body.get("access_token")
        check(f"login {tag} 200+token", st == 200 and bool(tok), str(body)[:60])
        users[tag] = {"username": uname, "token": tok}

    tok_a, tok_b = users["a"]["token"], users["b"]["token"]

    # ---------- 冒读面（核心缺口） ----------
    victim_key = f"e2e-victim-{stamp}"
    st, body = call("GET", "/api/cortex/recommend/recent", token=tok_a, user_id=victim_key)
    if mode == "--pre":
        check("GAP cross-user recent 200 (修前冒读)", st == 200, str(body)[:80])
    else:
        expect("cross-user recent 403", st, 403, str(body)[:80])
        check("403 detail user mismatch", "user mismatch" in str(body.get("detail", "")), str(body)[:80])

    st, body = call("GET", "/api/cortex/quality/report", token=tok_a, user_id=victim_key)
    if mode == "--pre":
        check("GAP cross-user quality 200 (修前冒读)", st == 200, str(body)[:80])
    else:
        expect("cross-user quality 403", st, 403, "")

    st2, _ = call("GET", "/api/cortex/recommend/recent", token="garbage-token")
    if mode == "--pre":
        check("GAP bad token 200 (修前无鉴权轴)", st2 == 200, str(st2))
    else:
        expect("bad token 401", st2, 401, "")

    # ---------- 派生键真命中（--post，真数据行） ----------
    if mode == "--post":
        ent_db = os.path.join(REPO, "data", "enterprise.db")
        os_db = os.path.join(REPO, "data", "opensoul.db")
        key_rows = {}
        with sqlite3.connect(ent_db) as c:
            for tag in ("a", "b"):
                row = c.execute(
                    "SELECT id FROM users WHERE username = ?", (users[tag]["username"],)
                ).fetchone()
                key_rows[tag] = md5_uuid_str(str(row[0]))
        key_default = md5_uuid_str("default")
        with sqlite3.connect(os_db) as c:
            for tag, key in (("a", key_rows["a"]), ("d", key_default)):
                c.execute(
                    "INSERT INTO knowledge (id, user_id, title, content) VALUES (?,?,?,?)",
                    (f"e2e-kb-{tag}-{stamp}", key, f"e2e kb {tag}", f"e2e content {tag}"),
                )
            c.commit()
        try:
            st, body = call("GET", "/api/cortex/recommend/recent", token=tok_a)
            ids = [e.get("id") for e in body.get("entries", [])]
            check("derived key hits own row", f"e2e-kb-a-{stamp}" in ids, str(ids)[:100])
            check("derived key excludes default row", f"e2e-kb-d-{stamp}" not in ids, str(ids)[:100])
            st, body = call("GET", "/api/cortex/recommend/recent")
            ids = [e.get("id") for e in body.get("entries", [])]
            check("anon default hits default row", f"e2e-kb-d-{stamp}" in ids, str(ids)[:100])
            check("anon default excludes A row", f"e2e-kb-a-{stamp}" not in ids, str(ids)[:100])
            st, body = call("GET", "/api/cortex/recommend/recent", token=tok_b)
            ids = [e.get("id") for e in body.get("entries", [])]
            check("B excludes A row", f"e2e-kb-a-{stamp}" not in ids, str(ids)[:100])
        finally:
            with sqlite3.connect(os_db) as c:
                c.execute("DELETE FROM knowledge WHERE id LIKE ?", (f"e2e-kb-%-{stamp}",))
                c.commit()
                leftover = c.execute(
                    "SELECT COUNT(*) FROM knowledge WHERE id LIKE ?", (f"e2e-kb-%-{stamp}",)
                ).fetchone()[0]
                check("knowledge seed cleanup remaining=0", leftover == 0, str(leftover))

    # ---------- 清痕（探针自建 enterprise 用户） ----------
    remaining = 0
    try:
        ent_db = os.path.join(REPO, "data", "enterprise.db")
        with sqlite3.connect(ent_db) as c:
            c.execute("DELETE FROM users WHERE username LIKE 'e2e-cortexid-%'")
            c.commit()
            leftover = c.execute(
                "SELECT COUNT(*) FROM users WHERE username LIKE 'e2e-cortexid-%'"
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
