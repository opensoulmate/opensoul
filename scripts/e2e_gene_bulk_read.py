#!/usr/bin/env python3
"""DEC-⑦-⑯ gene 管理面 bulk 读加轴 live e2e 探针。

真栈（urllib）打 live :8090：nest 建真租户×2 → enterprise register→login→token
真身份 → A/B 建模板行归属 → 五 bulk 读面（search/categories/tags/export/stats）
按派生租户互斥 + 匿名 default 面 + 顶层加性键 tenant_id + 坏 token 401
→ 清痕 remaining=0。

用法：python3 scripts/e2e_gene_bulk_read.py [--base http://127.0.0.1:8090]
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


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base

    st, health = call("GET", "/api/health")
    check("health", st == 200, health)

    # ── 真租户×2 + enterprise 真身份 ─────────────────────────────────
    tag = "e2e-g16-" + uuid.uuid4().hex[:8]
    tenants = {}
    users = {}
    for who in ("a", "b"):
        name = f"tenant-{tag}-{who}"
        st, r = call("POST", "/api/nest/tenants", {"tenant_id": name, "name": name})
        # 坑记档：nest create 服务端自生成 tenant_id（忽略请求值）——登记键必须用回包真值
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

    # ── 匿名基线（seed 前 builtin 面）────────────────────────────────
    st, an_cat0 = call("GET", "/api/gene/categories")
    st, an_tag0 = call("GET", "/api/gene/tags")
    check("anon baseline", st == 200, (st,))

    # ── A×2 / B×1 模板行（写点归属=派生租户）────────────────────────
    marker = "e2e-bulk-" + uuid.uuid4().hex[:6]
    rows = [
        ("a1", "a", "skill", [marker + "-ta", "bulkshared"]),
        ("a2", "a", "workflow", [marker + "-ta2"]),
        ("b1", "b", "skill", [marker + "-tb", "bulkshared"]),
    ]
    tpl_ids = {}
    for key, who, cat, tags in rows:
        tid = f"{marker}-{key}"
        st, r = call(
            "POST",
            "/api/gene/templates",
            {
                "template_id": tid,
                "name": f"{marker} probe {key}",
                "category": cat,
                "description": f"{marker} body {key}",
                "tags": tags,
                "config": {"k": "v-" + tid},
            },
            token=users[who],
        )
        check(f"{who} create {key}", st == 200, r)
        tpl_ids[key] = tid

    # ── /search 互斥 + 加性键 ────────────────────────────────────────
    st, ra = call("GET", f"/api/gene/templates/search/{marker}", token=users["a"])
    ids_a = [t["template_id"] for t in (ra or {}).get("templates", [])]
    check("search A sees only own", sorted(ids_a) == sorted([tpl_ids["a1"], tpl_ids["a2"]]), ids_a)
    check(
        "search A additive key",
        (ra or {}).get("tenant_id") == tenants["a"]
        and all(t.get("tenant_id") == tenants["a"] for t in (ra or {}).get("templates", [])),
        (ra or {}).get("tenant_id"),
    )
    st, rb = call("GET", f"/api/gene/templates/search/{marker}", token=users["b"])
    ids_b = [t["template_id"] for t in (rb or {}).get("templates", [])]
    check("search B sees only own", ids_b == [tpl_ids["b1"]], ids_b)
    st, ran = call("GET", f"/api/gene/templates/search/{marker}")
    ids_an = [t["template_id"] for t in (ran or {}).get("templates", [])]
    check("search anon default face empty", ids_an == [], ids_an)

    # ── /categories /tags 聚合互斥 ───────────────────────────────────
    st, ca = call("GET", "/api/gene/categories", token=users["a"])
    cats_a = {c["category"]: c["count"] for c in (ca or {}).get("categories", [])}
    check("categories A exact", cats_a == {"skill": 1, "workflow": 1}, cats_a)
    check("categories A additive key", (ca or {}).get("tenant_id") == tenants["a"], (ca or {}).get("tenant_id"))
    st, ta = call("GET", "/api/gene/tags", token=users["a"])
    tags_a = {t["tag"]: t["count"] for t in (ta or {}).get("tags", [])}
    check("tags A exact", tags_a == {marker + "-ta": 1, "bulkshared": 1, marker + "-ta2": 1}, tags_a)
    st, tb = call("GET", "/api/gene/tags", token=users["b"])
    tags_b = {t["tag"]: t["count"] for t in (tb or {}).get("tags", [])}
    check("tags B shared count scoped", tags_b.get("bulkshared") == 1, tags_b)
    st, an_cat = call("GET", "/api/gene/categories")
    st, an_tag = call("GET", "/api/gene/tags")
    check(
        "anon categories/tags baseline unchanged",
        an_cat == an_cat0 and an_tag == an_tag0,
        (an_cat, an_cat0),
    )

    # ── /export bundle ───────────────────────────────────────────────
    st, xa = call("GET", "/api/gene/export", token=users["a"])
    xids_a = {t["template_id"] for t in (xa or {}).get("templates", [])}
    check("export A own rows only", xids_a == {tpl_ids["a1"], tpl_ids["a2"]}, sorted(xids_a))
    check("export A count+key", (xa or {}).get("count") == 2 and (xa or {}).get("tenant_id") == tenants["a"], (xa or {}).get("count"))
    check(
        "export A no cross config",
        all(t.get("config", {}).get("k") != "v-" + tpl_ids["b1"] for t in (xa or {}).get("templates", [])),
        "",
    )
    st, xf = call(
        "GET", "/api/gene/export", token=users["a"], params={"include_builtin": "false"}
    )
    xf_ids = {t["template_id"] for t in (xf or {}).get("templates", [])}
    check("export include_builtin compose", xf_ids == xids_a, sorted(xf_ids))

    # ── /stats 聚合 ─────────────────────────────────────────────────
    st, sa = call("GET", "/api/gene/stats", token=users["a"])
    check("stats A totals", (sa or {}).get("total_templates") == 2 and (sa or {}).get("by_category") == {"skill": 1, "workflow": 1}, (sa or {}).get("total_templates"))
    check("stats A additive key", (sa or {}).get("tenant_id") == tenants["a"], (sa or {}).get("tenant_id"))
    check(
        "stats A most_used scoped",
        all(t.get("tenant_id") == tenants["a"] for t in (sa or {}).get("most_used", [])),
        "",
    )
    st, sb = call("GET", "/api/gene/stats", token=users["b"])
    check("stats B totals", (sb or {}).get("total_templates") == 1, (sb or {}).get("total_templates"))
    st, san = call("GET", "/api/gene/stats")
    check(
        "stats anon no probe rows",
        tpl_ids["a1"] not in {t.get("template_id") for t in (san or {}).get("most_used", [])}
        and (san or {}).get("tenant_id") == "default",
        (san or {}).get("tenant_id"),
    )

    # ── 坏 token 401×5 ──────────────────────────────────────────────
    bad = "not-a-token"
    codes = []
    for path in (
        f"/api/gene/templates/search/{marker}",
        "/api/gene/categories",
        "/api/gene/tags",
        "/api/gene/export",
        "/api/gene/stats",
    ):
        st, _ = call("GET", path, token=bad)
        codes.append(st)
    check("bad token 401 x5", codes == [401, 401, 401, 401, 401], codes)

    # ── /health 系统视图不变 ────────────────────────────────────────
    st, h = call("GET", "/api/gene/health")
    check(
        "health system view global",
        st == 200 and (h or {}).get("total_templates", 0) >= (sa or {}).get("total_templates", 0) + (sb or {}).get("total_templates", 0),
        (h or {}).get("total_templates"),
    )

    # ── 清痕 ────────────────────────────────────────────────────────
    for key, tid in tpl_ids.items():
        who = "a" if key.startswith("a") else "b"
        st, r = call("DELETE", f"/api/gene/templates/{tid}", token=users[who])
        check(f"cleanup del {tid}", st == 200, (st, r))

    time.sleep(0.2)
    ent_db = Path.home() / "opensoul" / "data" / "enterprise.db"
    nest_db = Path.home() / "opensoul" / "data" / "nest.db"
    tpl_dir = Path.home() / ".opensoul" / "templates"
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
    for tid in tpl_ids.values():
        if (tpl_dir / f"{tid}.json").exists():
            remaining += 1
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
