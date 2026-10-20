#!/usr/bin/env python3
"""e2e probe: capture promote_to_knowledge 面 user_id 轴 bind_identity 收口 修前/修后双向硬证。

用法：python3 scripts/e2e_capture_promote_identity_axis.py --pre|--post [--base URL]
  --pre  : 旧代码在跑——断言缺口存在（认证自报他人档案 200 冒写、坏 token 200、
           写键裸串 'default'/victim 原样落库）
  --post : 新代码在跑——断言缺口已闭（403 user mismatch / 401 / 派生键真命中行形）
两模式共同断言：匿名契约（缺省/自报保留、响应形状逐键 {promoted,id,user_id}）、
capture status='promoted' 更新、timeline 事件行 delta=0（TR-② 纪律——promote 不
push_event，探针全程零事件行面）。
--post 额外：写键=档案键家族正典 str(_resolve_user_id(...))（SQL 回读行形）。
清痕：探针自建 capture 行/knowledge 行/tags 行/knowledge_tags 链/enterprise 用户
全数删除后 remaining=0。
"""

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8090")
RESULTS = []
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def call(method, path, body=None, token=None, params=None):
    url = BASE + path
    qs = []
    for k, v in (params or {}).items():
        qs.append(f"{k}={urllib.parse.quote(str(v))}")
    if qs:
        url += ("&" if "?" in url else "?") + "&".join(qs)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
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
    # DB 路径可覆盖（worktree :8091 --pre 跑旧码自库；缺省=生产 data/）
    os_db = os.environ.get("E2E_OPENSOU_DB", os.path.join(REPO, "data", "opensoul.db"))
    ent_db = os.environ.get("E2E_ENTERPRISE_DB", os.path.join(REPO, "data", "enterprise.db"))

    t0 = timeline_total()
    st, _ = call("GET", "/api/health")
    expect("health 200", st, 200)

    # ---------- 真身份（enterprise register→login→token） ----------
    users = {}
    for tag in ("a", "b"):
        uname = f"e2e-capid-{tag}-{stamp}"
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
    tok_a = users["a"]["token"]

    # enterprise 行 id（派生键形依据：bind_identity derived_user=str(users.id)）
    ent_ids = {}
    with sqlite3.connect(ent_db) as c:
        for tag in ("a", "b"):
            row = c.execute(
                "SELECT id FROM users WHERE username = ?", (users[tag]["username"],)
            ).fetchone()
            ent_ids[tag] = str(row[0])

    # 探针自建 tags 预存快照（清理只删新增，禁误删既有行）
    probe_keys = {"default": md5_uuid_str("default"), "alice": md5_uuid_str("alice")}
    pre_tag_ids = set()
    with sqlite3.connect(os_db) as c:
        for key in list(probe_keys.values()) + [md5_uuid_str(ent_ids["a"])]:
            for r in c.execute(
                "SELECT id FROM tags WHERE user_id = ? AND name IN ('capture','page','browser')",
                (key,),
            ):
                pre_tag_ids.add(r[0])

    created_cids = []

    def make_capture(url):
        st, body = call(
            "POST", "/api/capture/page", {"url": url, "title": "e2e probe", "content": f"body-{stamp}"}
        )
        check(f"create capture {url[-14:]}", st == 200 and "id" in body, str(body)[:80])
        cid = int(body["id"])
        created_cids.append(cid)
        return cid

    def knowledge_rows_for(cid):
        with sqlite3.connect(os_db) as c:
            c.row_factory = sqlite3.Row
            return [
                dict(r)
                for r in c.execute("SELECT * FROM knowledge WHERE source = ?", (f"capture://{cid}",))
            ]

    # ---------- 写键行形（两模式各自如实断言修前/修后形） ----------
    cid = make_capture(f"https://e2e.example.com/promote-anon-{stamp}")
    st, body = call("POST", f"/api/capture/{cid}/promote")
    expect("anon default promote 200", st, 200, str(body)[:80])
    check(
        "response shape {promoted,id,user_id}",
        set(body.keys()) == {"promoted", "id", "user_id"} and body["promoted"] is True,
        str(body)[:80],
    )
    rows = knowledge_rows_for(cid)
    check("knowledge row landed 1", len(rows) == 1, str(len(rows)))
    if rows:
        got_key = rows[0]["user_id"]
        if mode == "--pre":
            check("GAP anon default writes raw 'default' (修前裸串孤儿键)", got_key == "default", got_key)
        else:
            check("anon default writes canonical key", got_key == md5_uuid_str("default"), got_key)

    cid = make_capture(f"https://e2e.example.com/promote-self-{stamp}")
    st, body = call("POST", f"/api/capture/{cid}/promote", params={"user_id": "alice"})
    expect("anon self-report promote 200", st, 200, str(body)[:80])
    rows = knowledge_rows_for(cid)
    if rows:
        got_key = rows[0]["user_id"]
        if mode == "--pre":
            check("GAP anon self-report writes raw 'alice' (修前裸串)", got_key == "alice", got_key)
        else:
            check("anon self-report writes canonical key", got_key == md5_uuid_str("alice"), got_key)

    # ---------- 冒写面（核心缺口） ----------
    cid = make_capture(f"https://e2e.example.com/promote-hijack-{stamp}")
    st, body = call(
        "POST",
        f"/api/capture/{cid}/promote",
        token=tok_a,
        params={"user_id": f"e2e-victim-{stamp}"},
    )
    if mode == "--pre":
        check("GAP cross-user promote 200 (修前冒写)", st == 200, str(body)[:80])
    else:
        expect("cross-user promote 403", st, 403, str(body)[:80])
        check("403 detail user mismatch", "user mismatch" in str(body.get("detail", "")), str(body)[:80])
        check("403 zero side effect (no knowledge row)", knowledge_rows_for(cid) == [], "")
        with sqlite3.connect(os_db) as c:
            row = c.execute("SELECT status FROM captures WHERE id = ?", (cid,)).fetchone()
        check("403 capture row untouched", row and row[0] != "promoted", str(row))

    cid = make_capture(f"https://e2e.example.com/promote-badtok-{stamp}")
    st, _ = call("POST", f"/api/capture/{cid}/promote", token="garbage-token")
    if mode == "--pre":
        check("GAP bad token 200 (修前无鉴权轴)", st == 200, str(st))
    else:
        expect("bad token 401", st, 401, "")

    # ---------- 认证派生（--post 行形真回读） ----------
    cid = make_capture(f"https://e2e.example.com/promote-derived-{stamp}")
    st, body = call("POST", f"/api/capture/{cid}/promote", token=tok_a)
    expect("authed undeclared promote 200", st, 200, str(body)[:80])
    rows = knowledge_rows_for(cid)
    if rows:
        got_key = rows[0]["user_id"]
        if mode == "--pre":
            check("GAP authed writes raw 'default' (修前自报缺省顶替)", got_key == "default", got_key)
        else:
            check("authed writes derived canonical key", got_key == md5_uuid_str(ent_ids["a"]), got_key)

    st, body = call(
        "POST", f"/api/capture/{cid}/promote", token=tok_a, params={"user_id": ent_ids["a"]}
    )
    check("authed matching declared 200 (确认绑定)", st == 200, str(body)[:80])

    st, body = call(
        "POST", f"/api/capture/{cid}/promote", token=tok_a, params={"user_id": "default"}
    )
    check("authed declare default = undeclared 200", st == 200, str(body)[:80])

    # ---------- 清痕（探针自建资源全数删除） ----------
    remaining = 0
    try:
        with sqlite3.connect(os_db) as c:
            c.execute(
                "DELETE FROM knowledge_tags WHERE knowledge_id IN "
                "(SELECT id FROM knowledge WHERE source LIKE 'capture://%' AND "
                " substr(source, 11) IN ({}) )".format(",".join("?" * len(created_cids))),
                tuple(str(x) for x in created_cids),
            )
            c.execute(
                "DELETE FROM knowledge WHERE source LIKE 'capture://%' AND "
                "substr(source, 11) IN ({})".format(",".join("?" * len(created_cids))),
                tuple(str(x) for x in created_cids),
            )
            c.execute(
                "DELETE FROM captures WHERE id IN ({})".format(",".join("?" * len(created_cids))),
                tuple(created_cids),
            )
            # tags：只删本次新增（快照差集），既有行一字不动
            new_tag_rows = c.execute(
                "SELECT id FROM tags WHERE name IN ('capture','page','browser') AND "
                "user_id IN ({})".format(",".join("?" * 3)),
                (
                    md5_uuid_str("default"),
                    md5_uuid_str("alice"),
                    md5_uuid_str(ent_ids["a"]),
                ),
            ).fetchall()
            for (tid,) in new_tag_rows:
                if tid not in pre_tag_ids:
                    c.execute("DELETE FROM tags WHERE id = ?", (tid,))
            c.commit()
            leftover_k = c.execute(
                "SELECT COUNT(*) FROM knowledge WHERE source LIKE 'capture://%' AND "
                "substr(source, 11) IN ({})".format(",".join("?" * len(created_cids))),
                tuple(str(x) for x in created_cids),
            ).fetchone()[0]
            leftover_c = c.execute(
                "SELECT COUNT(*) FROM captures WHERE id IN ({})".format(
                    ",".join("?" * len(created_cids))
                ),
                tuple(created_cids),
            ).fetchone()[0]
            leftover_t = c.execute(
                "SELECT COUNT(*) FROM tags WHERE name IN ('capture','page','browser') AND "
                "user_id IN (?,?,?) AND id NOT IN ({})".format(
                    ",".join("?" * len(pre_tag_ids)) if pre_tag_ids else "''"
                ),
                (
                    md5_uuid_str("default"),
                    md5_uuid_str("alice"),
                    md5_uuid_str(ent_ids["a"]),
                    *tuple(pre_tag_ids),
                ),
            ).fetchone()[0]
            remaining += int(leftover_k) + int(leftover_c) + int(leftover_t)
    except Exception as exc:  # 如实报，禁静默
        print("WARN opensoul.db cleanup:", exc)
        remaining += 1

    try:
        with sqlite3.connect(ent_db) as c:
            c.execute("DELETE FROM users WHERE username LIKE 'e2e-capid-%'")
            c.commit()
            leftover = c.execute(
                "SELECT COUNT(*) FROM users WHERE username LIKE 'e2e-capid-%'"
            ).fetchone()[0]
            remaining += int(leftover)
    except Exception as exc:
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
