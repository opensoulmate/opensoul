#!/usr/bin/env python3
"""DEC-⑦-⑥ live e2e 探针：trajectory 面加轴。

覆盖：生产迁移回读（补列+存量回填+索引）/真租户×2 行归属/事件父行继承/
劫持闸（ensure/end 403 行零改写）/归属闸（delete 404 行存活）/fork 归克隆者/
list·total·unified·search 三面隔离/匿名契约/坏 token 401/清痕 remaining=0。
"""
import json
import os
import sqlite3
import sys
import urllib.request
import urllib.error
import uuid

BASE = "http://127.0.0.1:8090"
OS_DB = "/home/climbing/opensoul/data/opensoul.db"
ENT_DB = "/home/climbing/opensoul/data/enterprise.db"
RESULTS = []


def req(method, path, body=None, token=None):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS" if ok else "FAIL"), name, detail)


def sql(q, args=()):
    with sqlite3.connect(OS_DB) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(q, args)]


def row_tenant(table, key_col, key):
    rows = sql(f"SELECT tenant_id FROM {table} WHERE {key_col}=?", (key,))
    return rows[0]["tenant_id"] if rows else None


def main():
    tag = "s-e2e-" + uuid.uuid4().hex[:8]
    created = []

    # 0. health
    s, d = req("GET", "/api/trajectory/health")
    check("health", s == 200, f"{s}")

    # 1. 生产迁移回读（服务重启后）
    sess_cols = [r["name"] for r in sql("PRAGMA table_info(trajectory_sessions)")]
    ev_cols = [r["name"] for r in sql("PRAGMA table_info(trajectory_events)")]
    check("sessions.tenant_id 补列", "tenant_id" in sess_cols, "")
    check("events.tenant_id 补列", "tenant_id" in ev_cols, "")
    n_sess_bad = sql("SELECT COUNT(*) c FROM trajectory_sessions WHERE tenant_id IS NULL OR tenant_id=''")[0]["c"]
    n_ev_bad = sql("SELECT COUNT(*) c FROM trajectory_events WHERE tenant_id IS NULL OR tenant_id=''")[0]["c"]
    n_sess = sql("SELECT COUNT(*) c FROM trajectory_sessions")[0]["c"]
    n_ev = sql("SELECT COUNT(*) c FROM trajectory_events")[0]["c"]
    check(
        "存量行全回填 'default'",
        n_sess_bad == 0 and n_ev_bad == 0,
        f"sessions={n_sess} events={n_ev} empty_key={n_sess_bad}/{n_ev_bad}",
    )
    idx = {r["name"] for r in sql("PRAGMA index_list(trajectory_sessions)")}
    idx_ev = {r["name"] for r in sql("PRAGMA index_list(trajectory_events)")}
    check(
        "租户索引真建（补列之后）",
        "idx_traj_sessions_tenant" in idx and "idx_traj_events_tenant" in idx_ev,
        "",
    )

    # 2. 匿名契约（存量 default 行可见、写点='default'）
    s, d = req("GET", "/api/trajectory/sessions?limit=200")
    anon_ids = [x["id"] for x in d.get("sessions", [])]
    check("list anonymous 200", s == 200, f"total={d.get('total')}")
    check(
        "list anonymous 行全 'default'+加性键",
        all(x.get("tenant_id") == "default" for x in d.get("sessions", [])),
        str([x.get("tenant_id") for x in d.get("sessions", [])][:5]),
    )
    sid_anon = tag + "-anon"
    s, d = req("POST", "/api/trajectory/sessions", {"session_id": sid_anon, "agent_id": "e2e", "task_description": "e2e anon probe"})
    created.append((sid_anon, None))
    check("create anonymous 'default'", s == 200 and d.get("tenant_id") == "default", f"{s} {d.get('tenant_id')}")

    # 3. 真租户×2（nest 建租户 + enterprise register→login→token）
    toks = {}
    created_tenants = []
    unames = []
    for tname in ("tenant-traj-a-" + uuid.uuid4().hex[:6], "tenant-traj-b-" + uuid.uuid4().hex[:6]):
        s, d = req("POST", "/api/nest/tenants", {"name": tname, "tier": "free"})
        real_tid = d.get("tenant_id")
        check(f"nest create {tname[:20]}…", s in (200, 201) and bool(real_tid), f"{s} {real_tid}")
        created_tenants.append(real_tid)
        uname = "e2etraj_" + uuid.uuid4().hex[:8]
        s2, d2 = req("POST", "/api/enterprise/auth/register", {"username": uname, "password": "E2ePass!234", "tenant_id": real_tid})
        s3, d3 = req("POST", "/api/enterprise/auth/login", {"username": uname, "password": "E2ePass!234"})
        tok = d3.get("token") or d3.get("access_token")
        check(f"register+login {(real_tid or '')[:16]}…", s2 in (200, 201) and s3 == 200 and bool(tok), f"{s2}/{s3}")
        toks[real_tid] = tok
        unames.append(uname)
    ta, tb = list(toks.keys())

    # 4. 行归属 + 事件父行继承
    sid_a = tag + "-a"
    s, d = req("POST", "/api/trajectory/sessions", {"session_id": sid_a, "agent_id": "e2e", "task_description": "e2e alpha probe"}, token=toks[ta])
    created.append((sid_a, toks[ta]))
    check("create A 行=派生租户", s == 200 and d.get("tenant_id") == ta, f"{s} {d.get('tenant_id')} vs {ta}")
    check("create A 行键回读", row_tenant("trajectory_sessions", "id", sid_a) == ta, str(row_tenant("trajectory_sessions", "id", sid_a)))
    s, d = req("POST", f"/api/trajectory/sessions/{sid_a}/events", {"event_type": "tool_call", "agent_id": "e2e", "content": "probe ev-inherit"}, token=None)
    ev_id = d.get("id")
    check("事件匿名写方随父行继承", s == 200 and d.get("tenant_id") == ta, f"{s} {d.get('tenant_id')}")
    check("事件行键回读", row_tenant("trajectory_events", "id", ev_id) == ta, str(row_tenant("trajectory_events", "id", ev_id)))

    # 5. 劫持闸：B ensure/end A 的 session → 403 行零改写
    before = sql("SELECT * FROM trajectory_sessions WHERE id=?", (sid_a,))[0]
    s, d = req("POST", "/api/trajectory/sessions", {"session_id": sid_a, "agent_id": "x", "task_description": "stolen"}, token=toks[tb])
    after = sql("SELECT * FROM trajectory_sessions WHERE id=?", (sid_a,))[0]
    check("ensure hijack 403", s == 403 and "another tenant" in str(d.get("detail", "")), f"{s} {str(d.get('detail'))[:60]}")
    check("ensure 行零改写", after == before, "")
    s, d = req("POST", f"/api/trajectory/sessions/{sid_a}/end", token=toks[tb])
    after2 = sql("SELECT * FROM trajectory_sessions WHERE id=?", (sid_a,))[0]
    check("end hijack 403 行零改写", s == 403 and after2 == before, f"{s}")

    # 6. 归属闸：B delete A → 404 行存活；A delete → 200 级联
    s, d = req("DELETE", f"/api/trajectory/sessions/{sid_a}", token=toks[tb])
    alive = bool(sql("SELECT 1 FROM trajectory_sessions WHERE id=?", (sid_a,)))
    ev_alive = bool(sql("SELECT 1 FROM trajectory_events WHERE session_id=?", (sid_a,)))
    check("delete 跨租户 404 零副作用", s == 404 and alive and ev_alive, f"{s}")
    s, d = req("DELETE", f"/api/trajectory/sessions/{sid_a}", token=toks[ta])
    gone = not sql("SELECT 1 FROM trajectory_sessions WHERE id=?", (sid_a,))
    ev_gone = not sql("SELECT 1 FROM trajectory_events WHERE session_id=?", (sid_a,))
    check("owner delete 级联", s == 200 and gone and ev_gone, f"{s}")
    created = [c for c in created if c[0] != sid_a]

    # 7. fork 归克隆者
    sid_src = tag + "-src"
    s, d = req("POST", "/api/trajectory/sessions", {"session_id": sid_src, "agent_id": "e2e", "task_description": "e2e fork src probe"}, token=toks[ta])
    created.append((sid_src, toks[ta]))
    s2, d2 = req("POST", f"/api/trajectory/sessions/{sid_src}/events", {"event_type": "tool_call", "agent_id": "e2e", "content": "probe fork-ev"}, token=toks[ta])
    s, d = req("POST", f"/api/trajectory/sessions/{sid_src}/fork", {"fork_point_event_id": d2.get("id"), "new_agent_id": "cloner"}, token=toks[tb])
    fork_id = d.get("id")
    check("fork 新行归克隆者(B)", s == 200 and d.get("tenant_id") == tb, f"{s} {d.get('tenant_id')}")
    fork_ev_keys = [r["tenant_id"] for r in sql("SELECT DISTINCT tenant_id FROM trajectory_events WHERE session_id=?", (fork_id,))]
    check("fork 复制事件随新行继承", fork_ev_keys == [tb], str(fork_ev_keys))
    if fork_id:
        req("DELETE", f"/api/trajectory/sessions/{fork_id}", token=toks[tb])

    # 8. list/total 三面隔离
    sid_b = tag + "-b"
    s, d = req("POST", "/api/trajectory/sessions", {"session_id": sid_b, "agent_id": "e2e", "task_description": "e2e beta probe"}, token=toks[tb])
    created.append((sid_b, toks[tb]))
    s, d = req("GET", "/api/trajectory/sessions?limit=200", token=toks[ta])
    a_ids = [x["id"] for x in d.get("sessions", [])]
    check("list A 只见己行", s == 200 and set(a_ids) == {sid_src} and d.get("total") == 1, f"{a_ids} total={d.get('total')}")
    s, d = req("GET", "/api/trajectory/sessions?limit=200", token=toks[tb])
    b_ids = [x["id"] for x in d.get("sessions", [])]
    check("list B 只见己行", set(b_ids) == {sid_b} and d.get("total") == 1, f"{b_ids} total={d.get('total')}")
    s, d = req("GET", "/api/trajectory/sessions?limit=200")
    d_ids = [x["id"] for x in d.get("sessions", [])]
    check("list anon 只见 default 行", sid_anon in d_ids and sid_a not in d_ids and sid_b not in d_ids and sid_src not in d_ids, f"{[i for i in d_ids if i.startswith('s-e2e')]}")

    # 9. unified trajectory 子搜索三面隔离
    s, d = req("GET", "/api/search/unified?q=fork-ev&sources=trajectory&limit=10", token=toks[ta])
    a_hits = {m.get("session_id") for m in d.get("by_source", {}).get("trajectory", [])}
    check("unified A 见己行", sid_src in a_hits, str(a_hits))
    s, d = req("GET", "/api/search/unified?q=fork-ev&sources=trajectory&limit=10", token=toks[tb])
    b_hits = {m.get("session_id") for m in d.get("by_source", {}).get("trajectory", [])}
    check("unified B 不见 A 行", sid_src not in b_hits, str(b_hits))
    s, d = req("GET", "/api/search/unified?q=fork-ev&sources=trajectory&limit=10")
    anon_hits = {m.get("session_id") for m in d.get("by_source", {}).get("trajectory", [])}
    check("unified anon 不见 A 行", sid_src not in anon_hits and sid_b not in anon_hits, str(anon_hits))

    # 10. /api/trajectory/search + /search/sessions 过滤
    s, d = req("GET", "/api/trajectory/search?keyword=probe&limit=200", token=toks[ta])
    ev_sids = {e.get("session_id") for e in d.get("events", [])}
    check("search 端点 A 无外租户事件", sid_b not in ev_sids and sid_anon not in ev_sids, str(sorted(x for x in ev_sids if x and x.startswith('s-e2e'))))
    s, d = req("GET", "/api/trajectory/search/sessions?keyword=probe&limit=200", token=toks[ta])
    g_sids = {g.get("session_id") for g in d.get("sessions", [])}
    check("search/sessions 端点 A 无外租户分组", sid_b not in g_sids and sid_anon not in g_sids, str(sorted(x for x in g_sids if x and x.startswith('s-e2e'))))

    # 11. 坏 token 401
    s, d = req("GET", "/api/trajectory/sessions", token="not-a-real-token")
    check("bad token 401", s == 401, f"{s}")

    # 12. 清痕
    for sid, tok in created:
        req("DELETE", f"/api/trajectory/sessions/{sid}", token=tok)
    remaining = [r["id"] for r in sql("SELECT id FROM trajectory_sessions WHERE id LIKE 's-e2e-%'")]
    rem_ev = sql(
        "SELECT COUNT(*) c FROM trajectory_events WHERE content IN ('probe ev-inherit','probe fork-ev')"
    )[0]["c"]
    for tid in created_tenants:
        req("DELETE", "/api/nest/tenants/" + tid)
    if os.path.exists(ENT_DB):
        with sqlite3.connect(ENT_DB) as c:
            for uname in unames:
                c.execute("DELETE FROM users WHERE username=?", (uname,))
            c.execute("DELETE FROM audit_log WHERE user_id NOT IN (SELECT id FROM users)")
            c.commit()
    check("cleanup sessions remaining=0", not remaining, str(remaining))
    check("cleanup probe events remaining=0", rem_ev == 0, str(rem_ev))

    fails = [r for r in RESULTS if not r[1]]
    print(f"\n== E2E {len(RESULTS)-len(fails)}/{len(RESULTS)} ==")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
