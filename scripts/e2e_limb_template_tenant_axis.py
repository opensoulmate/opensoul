#!/usr/bin/env python3
"""DEC-⑦-㉒ limb 模板存储面行租户键加轴 live e2e 探针。

真栈（urllib）打 live :8090：nest 建真租户×2 → enterprise register→login→token
真身份 → create 写点归属（A×2 含自报/B×1/匿名×1）→ /templates 三向互斥+加性键
→ get/instantiate by-ID 归属闸（跨租户 404 与 not-found 同契约+零副作用）→
delete 归属闸（跨租户与 not-found 同契约+行存活）→ builtin 共享只读目录
（跨租户可读可实例化、删除恒拒）→ stats 聚合作用域+/health 系统视图 →
坏 token 401×6 零副作用 → 清痕 remaining=0。

用法：python3 scripts/e2e_limb_template_tenant_axis.py [--base http://127.0.0.1:8090]
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
MARKER = ""


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


def marker_rows(rows):
    return sorted(r.get("name", "") for r in rows if str(r.get("name", "")).startswith(MARKER))


def main():
    global BASE, MARKER
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base
    MARKER = "e2e-ltb-"

    st, health = call("GET", "/api/health")
    check("health", st == 200, health)
    st, lh0 = call("GET", "/api/limb/health")
    G0 = (lh0 or {}).get("templates", -1)
    check("limb health baseline", st == 200 and G0 >= 4, lh0)

    # ── 坏 token 401×6 + 零副作用（先于一切写点）────────────────────
    codes = []
    st, _ = call("GET", "/api/limb/templates", token="not-a-token")
    codes.append(st)
    st, _ = call("GET", "/api/limb/templates/tpl-form-fill", token="not-a-token")
    codes.append(st)
    st, _ = call("POST", "/api/limb/templates", {"name": MARKER + "badtok"}, token="not-a-token")
    codes.append(st)
    st, _ = call("DELETE", "/api/limb/templates/tpl-form-fill", token="not-a-token")
    codes.append(st)
    st, _ = call("POST", "/api/limb/templates/tpl-form-fill/instantiate", {}, token="not-a-token")
    codes.append(st)
    st, _ = call("GET", "/api/limb/stats", token="not-a-token")
    codes.append(st)
    check("bad token 401 x6", codes == [401, 401, 401, 401, 401, 401], codes)
    st, lhb = call("GET", "/api/limb/health")
    check(
        "bad token zero side effect",
        (lhb or {}).get("templates") == G0
        and (lhb or {}).get("total_tasks") == (lh0 or {}).get("total_tasks"),
        (lh0, lhb),
    )

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

    st, sa0 = call("GET", "/api/limb/stats", token=users["a"])
    A0 = (sa0 or {}).get("templates", -1)
    st, sb0 = call("GET", "/api/limb/stats", token=users["b"])
    B0 = (sb0 or {}).get("templates", -1)
    st, sn0 = call("GET", "/api/limb/stats")
    N0 = (sn0 or {}).get("templates", -1)

    # ── create 写点归属：A×2（含自报）/B×1/匿名×1 ───────────────────
    tids = {}
    plan = [("a1", "a"), ("a2", "a"), ("b1", "b"), ("d1", None)]
    for key, who in plan:
        body = {"name": f"{tag}-{key}", "category": "custom", "actions": []}
        if key == "a2":
            body["tenant_id"] = "attacker-tenant"  # 自报必须不进落行
        st, r = call("POST", "/api/limb/templates", body, token=users[who] if who else None)
        check(f"create {key}", st == 200 and r and r.get("template_id"), r)
        tids[key] = (r or {}).get("template_id", "")
        want = tenants[who] if who else "default"
        check(f"create {key} derived tenant", (r or {}).get("tenant_id") == want, (r or {}).get("tenant_id"))

    # ── /templates 三向互斥 + 加性键 ─────────────────────────────────
    st, ra = call("GET", "/api/limb/templates", token=users["a"])
    check("list A sees own 2 only", marker_rows((ra or {}).get("templates", [])) == sorted([f"{tag}-a1", f"{tag}-a2"]), marker_rows((ra or {}).get("templates", [])))
    check(
        "list A builtin shared + additive keys",
        {"tpl-form-fill", "tpl-data-extract", "tpl-login", "tpl-screenshot"}
        <= {t["template_id"] for t in (ra or {}).get("templates", [])}
        and all(t.get("tenant_id") == tenants["a"] for t in (ra or {}).get("templates", []) if t.get("name") == f"{tag}-a1")
        and any(t.get("builtin") is True for t in (ra or {}).get("templates", [])),
        "",
    )
    st, rb = call("GET", "/api/limb/templates", token=users["b"])
    check("list B sees own 1 only", marker_rows((rb or {}).get("templates", [])) == [f"{tag}-b1"], marker_rows((rb or {}).get("templates", [])))
    st, rn = call("GET", "/api/limb/templates")
    check("list anon default face", marker_rows((rn or {}).get("templates", [])) == [f"{tag}-d1"], marker_rows((rn or {}).get("templates", [])))

    # ── get by-ID 归属闸 ─────────────────────────────────────────────
    st, ga = call("GET", f"/api/limb/templates/{tids['a1']}", token=users["a"])
    check("get own 200", st == 200 and ga and ga.get("tenant_id") == tenants["a"], ga)
    stx, gx = call("GET", f"/api/limb/templates/{tids['a1']}", token=users["b"])
    stm, gm = call("GET", "/api/limb/templates/tpl-no-such", token=users["b"])
    check(
        "get cross 404 same contract as missing",
        stx == 404 and stm == 404 and gx.get("detail") == gm.get("detail"),
        (gx, gm),
    )
    st, gb = call("GET", "/api/limb/templates/tpl-login", token=users["b"])
    check("get builtin cross-tenant 200", st == 200 and gb and gb.get("builtin") is True, gb)

    # ── instantiate 源读闸 ───────────────────────────────────────────
    st, lt0 = call("GET", "/api/limb/health")
    T0 = (lt0 or {}).get("total_tasks", -1)
    stx, ix = call("POST", f"/api/limb/templates/{tids['a1']}/instantiate", {}, token=users["b"])
    stm, im = call("POST", "/api/limb/templates/tpl-no-such/instantiate", {}, token=users["b"])
    check(
        "instantiate cross 404 same contract + zero side effect",
        stx == 404 and stm == 404 and ix.get("detail") == im.get("detail"),
        (ix, im),
    )
    st, lt1 = call("GET", "/api/limb/health")
    check("instantiate cross zero task", (lt1 or {}).get("total_tasks") == T0, (T0, lt1))
    st, io = call(
        "POST",
        f"/api/limb/templates/{tids['a1']}/instantiate",
        {"name": f"{tag}-inst"},
        token=users["a"],
    )
    check("instantiate own 200", st == 200 and io and io.get("task_id"), io)
    st, ib = call(
        "POST",
        "/api/limb/templates/tpl-screenshot/instantiate",
        {"name": f"{tag}-inst-b", "variables": {"url": "http://x"}},
        token=users["b"],
    )
    check("instantiate builtin cross-tenant 200", st == 200 and ib and ib.get("task_id"), ib)

    # ── stats 聚合作用域 + /health 系统视图 ──────────────────────────
    st, sa = call("GET", "/api/limb/stats", token=users["a"])
    st, sb = call("GET", "/api/limb/stats", token=users["b"])
    st, sn = call("GET", "/api/limb/stats")
    st, lh1 = call("GET", "/api/limb/health")
    check("stats A scoped", (sa or {}).get("templates") == A0 + 2, (A0, sa))
    check("stats B scoped", (sb or {}).get("templates") == B0 + 1, (B0, sb))
    check("stats anon scoped", (sn or {}).get("templates") == N0 + 1, (N0, sn))
    check("health system view global", (lh1 or {}).get("templates") == G0 + 4, (G0, lh1))

    # ── builtin 回归：跨租户删除恒拒+种子完好 ────────────────────────
    st, rd = call("DELETE", "/api/limb/templates/tpl-form-fill", token=users["a"])
    check("builtin delete denied 400", st == 400, rd)
    st, gb2 = call("GET", "/api/limb/templates/tpl-form-fill", token=users["b"])
    check("builtin survives", st == 200 and gb2 and gb2.get("name") == "表单填写", gb2)

    # ── delete 归属闸（放最后：修前会真删=证据）────────────────────
    stx, dx = call("DELETE", f"/api/limb/templates/{tids['a2']}", token=users["b"])
    stm, dm = call("DELETE", "/api/limb/templates/tpl-no-such", token=users["b"])
    check("delete cross same status as missing", stx == stm and stx == 400, (stx, stm))
    st, ga2 = call("GET", f"/api/limb/templates/{tids['a2']}", token=users["a"])
    check("delete cross row survives", st == 200, ga2)

    # ── 清痕 ────────────────────────────────────────────────────────
    for key, tid in tids.items():
        if not tid:
            continue
        who = {"a": "a", "b": "b"}.get(key[:1])
        st, r = call("DELETE", f"/api/limb/templates/{tid}", token=users[who] if who else None)
        check(f"cleanup del tpl {key}", st in (200, 400, 404), (st, r))
    # 安全网：扫尾一切 marker 残留模板（三面）+ marker 任务
    for tok in (users["a"], users["b"], None):
        st, sweep = call("GET", "/api/limb/templates", token=tok)
        for row in (sweep or {}).get("templates", []):
            if str(row.get("name", "")).startswith(MARKER):
                call("DELETE", "/api/limb/templates/" + row["template_id"], token=tok)
    st, tasks = call("GET", "/api/limb/tasks", params={"limit": 500})
    for row in (tasks or {}).get("tasks", []):
        if str(row.get("name", "")).startswith(MARKER):
            call("DELETE", "/api/limb/tasks/" + row["task_id"])

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

    # remaining=0 回读
    remaining = []
    for tok in (users["a"], users["b"], None):
        st, sweep = call("GET", "/api/limb/templates", token=tok)
        remaining += marker_rows((sweep or {}).get("templates", []))
    st, tasks = call("GET", "/api/limb/tasks", params={"limit": 500})
    remaining += [
        r.get("name") for r in (tasks or {}).get("tasks", []) if str(r.get("name", "")).startswith(MARKER)
    ]
    st, lh2 = call("GET", "/api/limb/health")
    check("remaining=0", not remaining and (lh2 or {}).get("templates") == G0, remaining)

    ok = sum(1 for _, o, _ in RESULTS if o)
    print(f"\n== {ok}/{len(RESULTS)} ==")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
