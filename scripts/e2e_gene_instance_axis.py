#!/usr/bin/env python3
"""DEC-⑦-⑰ gene_templates 实例面行租户键加轴 live e2e 探针。

真栈（urllib）打 live :8090：nest 建真租户×2 → enterprise register→login→token
真身份 → A×2/B×1/匿名×1 实例（instantiate 写点归属）→ /instances 三向互斥+
加性键+禁自报+坏 token 401 → delete 归属闸（跨租户 404 行零破坏）→
/capabilities 父行归属过滤 → /dashboard 聚合作用域 → /health·/adapter-health
系统视图不变 → 清痕 remaining=0。

用法：python3 scripts/e2e_gene_instance_axis.py [--base http://127.0.0.1:8090]
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


def marker_names(rows):
    return sorted(r.get("name", "") for r in rows if r.get("name", "").startswith(MARKER))


MARKER = ""


def main():
    global BASE, MARKER
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base
    MARKER = "e2e-g17-"

    st, health = call("GET", "/api/health")
    check("health", st == 200, health)

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

    # ── 匿名基线（既有实例面）────────────────────────────────────────
    st, an0 = call("GET", "/api/gene/instances")
    base_ids = sorted(i["instance_id"] for i in (an0 or {}).get("instances", []))
    st, ah0 = call("GET", "/api/gene/adapter-health")
    check("adapter-health baseline", st == 200, ah0)

    # ── instantiate 写点归属：A×2 / B×1 / 匿名×1 ─────────────────────
    inst_ids = {}
    plan = [("a1", "a"), ("a2", "a"), ("b1", "b"), ("d1", None)]
    for key, who in plan:
        body = {"name": f"{tag}-{key}", "params": {}, "auto_configure": True}
        if key == "a2":
            body["tenant_id"] = "attacker-tenant"  # 自报必须不进落行
        st, r = call(
            "POST",
            "/api/gene/adapter-templates/git/instantiate",
            body,
            token=users[who] if who else None,
        )
        check(f"instantiate {key}", st == 200 and r and r.get("instance_id"), r)
        inst_ids[key] = (r or {}).get("instance_id", "")
        if who:
            check(
                f"instantiate {key} derived tenant",
                (r or {}).get("tenant_id") == tenants[who],
                (r or {}).get("tenant_id"),
            )
        else:
            check(
                f"instantiate {key} anon default",
                (r or {}).get("tenant_id") == "default",
                (r or {}).get("tenant_id"),
            )

    # ── /instances 三向互斥 + 加性键 + 禁自报 ────────────────────────
    st, ra = call("GET", "/api/gene/instances", token=users["a"])
    check(
        "instances A sees own 2 only",
        marker_names((ra or {}).get("instances", [])) == sorted([f"{tag}-a1", f"{tag}-a2"]),
        marker_names((ra or {}).get("instances", [])),
    )
    check(
        "instances A additive key",
        (ra or {}).get("tenant_id") == tenants["a"]
        and all(i.get("tenant_id") == tenants["a"] for i in (ra or {}).get("instances", [])),
        (ra or {}).get("tenant_id"),
    )
    check(
        "self-report ignored (a2 row derived)",
        all(
            i.get("tenant_id") == tenants["a"]
            for i in (ra or {}).get("instances", [])
            if i.get("name") == f"{tag}-a2"
        ),
        "",
    )
    st, rb = call("GET", "/api/gene/instances", token=users["b"])
    check(
        "instances B sees own 1 only",
        marker_names((rb or {}).get("instances", [])) == [f"{tag}-b1"],
        marker_names((rb or {}).get("instances", [])),
    )
    st, rn = call("GET", "/api/gene/instances")
    check(
        "instances anon default face",
        marker_names((rn or {}).get("instances", [])) == [f"{tag}-d1"]
        and sorted(i["instance_id"] for i in (rn or {}).get("instances", []))
        == sorted(base_ids + [inst_ids["d1"]]),
        marker_names((rn or {}).get("instances", [])),
    )

    # ── 坏 token 401×4 + 零行 ────────────────────────────────────────
    codes = []
    st, _ = call(
        "POST",
        "/api/gene/adapter-templates/git/instantiate",
        {"name": f"{tag}-badtok", "params": {}},
        token="not-a-token",
    )
    codes.append(st)
    for path in ("/api/gene/instances", "/api/gene/capabilities", "/api/gene/dashboard"):
        st, _ = call("GET", path, token="not-a-token")
        codes.append(st)
    check("bad token 401 x4", codes == [401, 401, 401, 401], codes)
    st, anb = call("GET", "/api/gene/instances")
    check(
        "bad token instantiate zero row",
        f"{tag}-badtok" not in marker_names((anb or {}).get("instances", [])),
        marker_names((anb or {}).get("instances", [])),
    )

    # ── delete 归属闸 ───────────────────────────────────────────────
    st, rd = call("DELETE", f"/api/gene/instances/{inst_ids['a1']}", token=users["b"])
    check("cross delete 404", st == 404, (st, rd))
    st, ra2 = call("GET", "/api/gene/instances", token=users["a"])
    check(
        "cross delete row survives",
        inst_ids["a1"] in [i["instance_id"] for i in (ra2 or {}).get("instances", [])],
        "",
    )
    st, rdmiss = call("DELETE", "/api/gene/instances/no-such-id", token=users["b"])
    check(
        "cross delete same contract as not-found",
        st == 404
        and (rd or {}).get("detail") == f"Instance '{inst_ids['a1']}' not found"
        and (rdmiss or {}).get("detail") == "Instance 'no-such-id' not found",
        (rd, rdmiss),
    )
    st, rd2 = call("DELETE", f"/api/gene/instances/{inst_ids['a1']}", token=users["a"])
    check("own delete 200 + caps removed", st == 200 and (rd2 or {}).get("capabilities_removed", 0) >= 0, rd2)

    # ── /capabilities 父行归属过滤 ──────────────────────────────────
    st, ca = call("GET", "/api/gene/capabilities", token=users["a"])
    cap_a_insts = {c.get("adapter_instance_id") for c in (ca or {}).get("capabilities", [])}
    check(
        "capabilities A scoped to own instances",
        inst_ids["a2"] in cap_a_insts
        and inst_ids["b1"] not in cap_a_insts
        and inst_ids["d1"] not in cap_a_insts,
        sorted(x for x in cap_a_insts if x),
    )
    st, cn = call("GET", "/api/gene/capabilities")
    cap_n_insts = {c.get("adapter_instance_id") for c in (cn or {}).get("capabilities", [])}
    check(
        "capabilities anon default face",
        inst_ids["a2"] not in cap_n_insts and inst_ids["b1"] not in cap_n_insts,
        "",
    )

    # ── /dashboard 聚合作用域 ───────────────────────────────────────
    st, da = call("GET", "/api/gene/dashboard", token=users["a"])
    recent_a = [i["instance_id"] for i in (da or {}).get("recent_instances", [])]
    check(
        "dashboard A scoped",
        (da or {}).get("summary", {}).get("total_instances") == 1  # a1 已删，余 a2
        and inst_ids["b1"] not in recent_a
        and inst_ids["d1"] not in recent_a
        and (da or {}).get("tenant_id") == tenants["a"],
        (da or {}).get("summary"),
    )
    st, db = call("GET", "/api/gene/dashboard", token=users["b"])
    check(
        "dashboard B scoped",
        (db or {}).get("summary", {}).get("total_instances") == 1
        and [i["instance_id"] for i in (db or {}).get("recent_instances", [])] == [inst_ids["b1"]],
        (db or {}).get("summary"),
    )

    # ── /health /adapter-health 系统视图不变 ────────────────────────
    st, ah1 = call("GET", "/api/gene/adapter-health")
    check(
        "adapter-health system view global",
        st == 200 and (ah1 or {}).get("instances", 0) == (ah0 or {}).get("instances", 0) + 4 - 1,
        (ah0, ah1),
    )

    # ── 清痕 ────────────────────────────────────────────────────────
    for key, iid in inst_ids.items():
        if key == "a1" or not iid:
            continue  # 已删
        who = {"a": "a", "b": "b"}.get(key[:1])
        st, r = call("DELETE", f"/api/gene/instances/{iid}", token=users[who] if who else None)
        check(f"cleanup del {key}", st == 200, (st, r))

    # 安全网：扫尾一切 marker 残留行（坏 token 面/意外行）
    st, sweep = call("GET", "/api/gene/instances")
    for row in (sweep or {}).get("instances", []):
        if row.get("name", "").startswith(tag):
            call("DELETE", f"/api/gene/instances/{row['instance_id']}")

    time.sleep(0.2)
    ent_db = Path.home() / "opensoul" / "data" / "enterprise.db"
    nest_db = Path.home() / "opensoul" / "data" / "nest.db"
    gtp = Path.home() / ".openmate" / "gene_templates.json"
    for who in ("a", "b"):
        with sqlite3.connect(ent_db) as c:
            c.execute("DELETE FROM users WHERE username=?", (f"{tag}-{who}",))
            c.commit()
    with sqlite3.connect(nest_db) as c:
        rowsn = c.execute(
            "SELECT tenant_id FROM tenants WHERE name LIKE ?", (f"%{tag}%",)
        ).fetchall()
        for (t_id,) in rowsn:
            c.execute("DELETE FROM access_log WHERE tenant_id=?", (t_id,))
        c.execute("DELETE FROM tenants WHERE name LIKE ?", (f"%{tag}%",))
        c.commit()

    remaining = 0
    if gtp.exists():
        remaining += gtp.read_text().count(tag)  # 实例/能力残留字面计数
    st, an1 = call("GET", "/api/gene/instances")
    if sorted(i["instance_id"] for i in (an1 or {}).get("instances", [])) != base_ids:
        remaining += 1  # 匿名基线未回原
    with sqlite3.connect(ent_db) as c:
        remaining += c.execute(
            "SELECT count(*) FROM users WHERE username LIKE ?", (f"{tag}-%",)
        ).fetchone()[0]
    with sqlite3.connect(nest_db) as c:
        remaining += c.execute(
            "SELECT count(*) FROM tenants WHERE name LIKE ?", (f"%{tag}%",)
        ).fetchone()[0]
        remaining += c.execute(
            "SELECT count(*) FROM access_log WHERE tenant_id LIKE ?", (f"%{tag}%",)
        ).fetchone()[0]
    check("cleanup remaining=0", remaining == 0, remaining)

    fails = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n== {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ==")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
