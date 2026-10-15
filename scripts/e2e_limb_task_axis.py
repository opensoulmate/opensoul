#!/usr/bin/env python3
"""DEC-⑦-㉔ limb 任务面行租户键加轴 live e2e 探针。

真栈（urllib）打 live :8090：nest 建真租户×2 → enterprise register→login→token
真身份 → A/B/匿名建任务行归属（含自报丢弃）→ list/history 恒过滤（过滤先于
截断）→ by-ID 归属闸四面（get/execute/cancel/delete 跨租户与缺行同契约+行零
破坏）→ instantiate 产物行归属（双职）→ stats 任务计数聚合作用域+/health 系统
视图 → 清痕 remaining=0。

用法：python3 scripts/e2e_limb_task_axis.py [--base http://127.0.0.1:8090]
修前后各跑一次同探针（before=旧代码现场、after=重启新代码），对照取证。
"""

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8090"
RESULTS = []
MARKER = "e2e-lta-"


def call(method, path, body=None, token=None, params=None):
    url = BASE + path
    if params:
        qs = urllib.parse.urlencode(params)
        url += ("&" if "?" in path else "?") + qs
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode()
            return r.status, (json.loads(txt) if txt else None)
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt) if txt else None
        except Exception:
            return e.code, {"raw": txt[:200]}


def check(name: str, ok: object, detail: object = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (" | " + str(detail) if detail else ""))


def marker_tasks(tasks):
    return sorted(
        r.get("name", "") for r in tasks if str(r.get("name", "")).startswith(MARKER)
    )


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base

    st, health = call("GET", "/api/health")
    check("health", st == 200, health)
    st, lh0 = call("GET", "/api/limb/health")
    G0 = (lh0 or {}).get("total_tasks", -1)
    check("limb health baseline", st == 200 and G0 >= 0, lh0)

    # ── 坏 token 401×7 + 零副作用（先于一切写点）────────────────────
    codes = []
    for method, path, body in (
        ("POST", "/api/limb/tasks", {"name": MARKER + "badtok", "actions": []}),
        ("GET", "/api/limb/tasks", None),
        ("GET", "/api/limb/tasks/task-nope", None),
        ("POST", "/api/limb/tasks/task-nope/execute", None),
        ("POST", "/api/limb/tasks/task-nope/cancel", None),
        ("DELETE", "/api/limb/tasks/task-nope", None),
        ("GET", "/api/limb/history", None),
    ):
        st, _ = call(method, path, body, token="not-a-token")
        codes.append(st)
    check("bad token 401 x7", codes == [401] * 7, codes)
    st, lhb = call("GET", "/api/limb/health")
    check("bad token zero side effect", (lhb or {}).get("total_tasks") == G0, (G0, lhb))

    # ── 真租户×2 + enterprise 真身份 ─────────────────────────────────
    tag = MARKER + uuid.uuid4().hex[:8]
    tenants = {}
    users = {}
    for who in ("a", "b"):
        name = f"tenant-{tag}-{who}"
        st, r = call("POST", "/api/nest/tenants", {"tenant_id": name, "name": name})
        real_tenant = (r or {}).get("tenant_id", "")
        check(f"nest create {who}", st == 200 and bool(real_tenant), r)
        tenants[who] = real_tenant
        st, r = call(
            "POST",
            "/api/enterprise/auth/register",
            {"username": f"{tag}-{who}", "password": "pw-" + tag, "tenant_id": real_tenant},
        )
        check(f"ent register {who}", st in (200, 201), r)
        st, r = call(
            "POST",
            "/api/enterprise/auth/login",
            {"username": f"{tag}-{who}", "password": "pw-" + tag},
        )
        check(f"ent login {who}", st == 200 and r and r.get("access_token"), r)
        users[who] = (r or {}).get("access_token", "")

    # ── create 写点归属：A×2（a2 含自报）/B×1/匿名×1 ─────────────────
    tids = {}
    plan = [("a1", "a"), ("a2", "a"), ("b1", "b"), ("d1", None)]
    for key, who in plan:
        body = {"name": f"{tag}-{key}", "actions": []}
        if key == "a2":
            body["tenant_id"] = "attacker-tenant"  # 自报必须不进落行
        st, r = call("POST", "/api/limb/tasks", body, token=users[who] if who else None)
        check(f"create {key}", st == 200 and r and r.get("task_id"), r)
        tids[key] = (r or {}).get("task_id", "")
        want = tenants[who] if who else "default"
        check(
            f"create {key} derived tenant",
            (r or {}).get("tenant_id") == want,
            (r or {}).get("tenant_id"),
        )

    # ── /tasks 三向互斥 + 加性键 + 过滤先于截断 ───────────────────────
    st, ra = call("GET", "/api/limb/tasks", token=users["a"])
    check(
        "list A sees own 2 only",
        marker_tasks((ra or {}).get("tasks", [])) == sorted([f"{tag}-a1", f"{tag}-a2"]),
        marker_tasks((ra or {}).get("tasks", [])),
    )
    check(
        "list A additive key",
        all(
            r.get("tenant_id") == tenants["a"]
            for r in (ra or {}).get("tasks", [])
            if str(r.get("name", "")).startswith(MARKER)
        ),
        "",
    )
    st, rb = call("GET", "/api/limb/tasks", token=users["b"])
    check("list B sees own 1 only", marker_tasks((rb or {}).get("tasks", [])) == [f"{tag}-b1"], marker_tasks((rb or {}).get("tasks", [])))
    st, rn = call("GET", "/api/limb/tasks")
    check("list anon default face", marker_tasks((rn or {}).get("tasks", [])) == [f"{tag}-d1"], marker_tasks((rn or {}).get("tasks", [])))
    st, rl = call("GET", "/api/limb/tasks", params={"limit": 1}, token=users["a"])
    check(
        "list filter before truncation",
        [r.get("name") for r in (rl or {}).get("tasks", [])] == [f"{tag}-a2"],
        (rl or {}).get("tasks", []),
    )

    # ── by-ID 归属闸四面（跨租户与缺行同契约+行零破坏）────────────────
    st, ga = call("GET", f"/api/limb/tasks/{tids['a1']}", token=users["a"])
    check("get own 200 + additive key", st == 200 and ga and ga.get("tenant_id") == tenants["a"], ga)
    stx, gx = call("GET", f"/api/limb/tasks/{tids['a1']}", token=users["b"])
    stm, gm = call("GET", "/api/limb/tasks/task-nope", token=users["b"])
    check("get cross 404 same contract", stx == 404 and stm == 404 and gx == gm, (gx, gm))
    stx, ex = call("POST", f"/api/limb/tasks/{tids['a1']}/execute", token=users["b"])
    check("execute cross 404", stx == 404, ex)
    st, ga2 = call("GET", f"/api/limb/tasks/{tids['a1']}", token=users["a"])
    check("execute cross row untouched", (ga2 or {}).get("status") == "queued", ga2)
    stx, cx = call("POST", f"/api/limb/tasks/{tids['a1']}/cancel", token=users["b"])
    stm, cm = call("POST", "/api/limb/tasks/task-nope/cancel", token=users["b"])
    check("cancel cross same contract", stx == 400 and stm == 400 and cx == cm, (cx, cm))
    st, ga3 = call("GET", f"/api/limb/tasks/{tids['a1']}", token=users["a"])
    check("cancel cross row untouched", (ga3 or {}).get("status") == "queued", ga3)
    stx, dx = call("DELETE", f"/api/limb/tasks/{tids['a1']}", token=users["b"])
    stm, dm = call("DELETE", "/api/limb/tasks/task-nope", token=users["b"])
    check("delete cross same contract", stx == 400 and stm == 400 and dx == dm, (dx, dm))
    st, ga4 = call("GET", f"/api/limb/tasks/{tids['a1']}", token=users["a"])
    check("delete cross row survives", st == 200, ga4)

    # ── instantiate 产物行归属（双职）────────────────────────────────
    st, tpl = call(
        "POST",
        "/api/limb/templates",
        {"name": tag + "-tpl", "category": "custom", "actions": []},
        token=users["a"],
    )
    tpl_id = (tpl or {}).get("template_id", "")
    check("create tpl A", st == 200 and bool(tpl_id), tpl)
    st, io = call(
        "POST",
        f"/api/limb/templates/{tpl_id}/instantiate", {"name": tag + "-prod"}, token=users["a"]
    )
    check(
        "instantiate product inherits instantiator",
        st == 200 and (io or {}).get("tenant_id") == tenants["a"],
        io,
    )
    prod_id = (io or {}).get("task_id", "")
    st, lt0 = call("GET", "/api/limb/health")
    stx, ix = call("POST", f"/api/limb/templates/{tpl_id}/instantiate", {}, token=users["b"])
    stm, im = call("POST", "/api/limb/templates/tpl-nope/instantiate", {}, token=users["b"])
    st, lt1 = call("GET", "/api/limb/health")
    check(
        "instantiate cross 404 same contract + zero product",
        stx == 404 and stm == 404 and ix == im and (lt1 or {}).get("total_tasks") == (lt0 or {}).get("total_tasks"),
        (ix, im),
    )

    # ── own 写面正对照 + history 恒过滤/过滤先于截断 ──────────────────
    st, e1 = call("POST", f"/api/limb/tasks/{tids['a1']}/execute", token=users["a"])
    check("own execute 200 completed", st == 200 and (e1 or {}).get("status") == "completed", e1)
    st, c1 = call("POST", f"/api/limb/tasks/{tids['a2']}/cancel", token=users["a"])
    check("own cancel 200", st == 200, c1)
    st, e2 = call("POST", f"/api/limb/tasks/{tids['b1']}/execute", token=users["b"])
    check("own execute B 200", st == 200 and (e2 or {}).get("status") == "completed", e2)
    st, e3 = call("POST", f"/api/limb/tasks/{tids['d1']}/execute")
    check("own execute anon 200", st == 200 and (e3 or {}).get("status") == "completed", e3)

    st, ha = call("GET", "/api/limb/history", token=users["a"])
    hist_a = [h for h in (ha or {}).get("history", []) if str(h.get("name", "")).startswith(MARKER)]
    check(
        "history A scoped + additive key",
        [h["name"] for h in hist_a] == [f"{tag}-a1"]
        and all(h.get("tenant_id") == tenants["a"] for h in hist_a),
        hist_a,
    )
    st, hb = call("GET", "/api/limb/history", token=users["b"])
    hist_b = [h for h in (hb or {}).get("history", []) if str(h.get("name", "")).startswith(MARKER)]
    check("history B scoped", [h["name"] for h in hist_b] == [f"{tag}-b1"], hist_b)
    st, hn = call("GET", "/api/limb/history")
    hist_n = [h for h in (hn or {}).get("history", []) if str(h.get("name", "")).startswith(MARKER)]
    check("history anon default face", [h["name"] for h in hist_n] == [f"{tag}-d1"], hist_n)
    st, hl = call("GET", "/api/limb/history", params={"limit": 1}, token=users["a"])
    check(
        "history filter before truncation",
        [h["name"] for h in (hl or {}).get("history", [])] == [f"{tag}-a1"],
        (hl or {}).get("history", []),
    )

    # ── stats 任务计数聚合作用域 + /health 系统视图 ───────────────────
    st, sa = call("GET", "/api/limb/stats", token=users["a"])
    st, sb = call("GET", "/api/limb/stats", token=users["b"])
    st, sn = call("GET", "/api/limb/stats")
    st, lh2 = call("GET", "/api/limb/health")
    a_tasks = [t for t in (sa or {}).get("tasks", [])] if isinstance((sa or {}).get("tasks"), list) else []
    check(
        "stats A task keys scoped",
        (sa or {}).get("total_tasks") == 3  # a1 completed + a2 cancelled + prod
        and (sa or {}).get("by_status", {}).get("completed") == 1
        and (sa or {}).get("total_executed") == 1
        and (sa or {}).get("total_succeeded") == 1,
        sa,
    )
    check("stats B task keys scoped", (sb or {}).get("total_tasks") == 1 and (sb or {}).get("total_executed") == 1, sb)
    check("stats anon task keys scoped", (sn or {}).get("total_tasks") == 1 and (sn or {}).get("total_executed") == 1, sn)
    check(
        "health system view global",
        (lh2 or {}).get("total_tasks") == G0 + 5 and (lh2 or {}).get("total_executed") == 3,
        lh2,
    )

    # ── own delete 正对照 ────────────────────────────────────────────
    st, dd = call("DELETE", f"/api/limb/tasks/{tids['a2']}", token=users["a"])
    check("own delete 200", st == 200, dd)

    # ── 清痕 ────────────────────────────────────────────────────────
    for key, tid in tids.items():
        if not tid:
            continue
        tok = users["a"] if key.startswith("a") else (users["b"] if key.startswith("b") else None)
        call("DELETE", f"/api/limb/tasks/{tid}", token=tok)
    if prod_id:
        call("DELETE", f"/api/limb/tasks/{prod_id}", token=users["a"])
    if tpl_id:
        call("DELETE", f"/api/limb/templates/{tpl_id}", token=users["a"])
    for tok in (users["a"], users["b"], None):
        st, sweep = call("GET", "/api/limb/tasks", params={"limit": 500}, token=tok)
        for row in (sweep or {}).get("tasks", []):
            if str(row.get("name", "")).startswith(MARKER):
                call("DELETE", "/api/limb/tasks/" + row["task_id"], token=tok)

    time.sleep(0.2)
    ent_db = Path.home() / "opensoul" / "data" / "enterprise.db"
    nest_db = Path.home() / "opensoul" / "data" / "nest.db"
    for who in ("a", "b"):
        with sqlite3.connect(ent_db) as c:
            c.execute("DELETE FROM users WHERE username=?", (f"{tag}-{who}",))
            c.commit()
    with sqlite3.connect(nest_db) as c:
        for who in ("a", "b"):
            c.execute("DELETE FROM access_log WHERE tenant_id=?", (tenants[who],))
        c.commit()
    with sqlite3.connect(nest_db) as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(tenants)")]
        key = "tenant_id" if "tenant_id" in cols else cols[0]
        for who in ("a", "b"):
            c.execute(f"DELETE FROM tenants WHERE {key}=?", (tenants[who],))
        c.commit()

    remaining = []
    for tok in (users["a"], users["b"], None):
        st, sweep = call("GET", "/api/limb/tasks", params={"limit": 500}, token=tok)
        remaining += marker_tasks((sweep or {}).get("tasks", []))
        st, swt = call("GET", "/api/limb/templates", token=tok)
        remaining += [
            r.get("name")
            for r in (swt or {}).get("templates", [])
            if str(r.get("name", "")).startswith(MARKER)
        ]
    st, lh3 = call("GET", "/api/limb/health")
    check(
        "remaining=0",
        not remaining and (lh3 or {}).get("total_tasks") == G0,
        (remaining, (lh3 or {}).get("total_tasks"), G0),
    )

    ok = sum(1 for _, o, _ in RESULTS if o)
    print(f"\n== {ok}/{len(RESULTS)} ==")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
