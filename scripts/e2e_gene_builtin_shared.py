#!/usr/bin/env python3
"""DEC-⑩-① builtin starter 目录跨租户共享语义 live e2e 探针。

真栈（urllib）打 live :8090：nest 建真租户×2 → enterprise register→login→token
真身份 → A/B 建模板行 → 共享目录语义双向对照（修前严格过滤/系统视图漏网 vs
修后共享只读目录+用户行零泄露）+ 写面零变化（delete builtin 恒拒/劫持闸 403）
→ 清痕 remaining=0。

用法：python3 scripts/e2e_gene_builtin_shared.py [--base http://127.0.0.1:8090]
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
    tag = "e2e-sh10-" + uuid.uuid4().hex[:8]
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

    # ── A/B 模板行（写点归属=派生租户）──────────────────────────────
    marker = "e2esh-" + uuid.uuid4().hex[:6]
    tpl_ids = {}
    for key, who in (("a1", "a"), ("b1", "b")):
        tid = f"{marker}-{key}"
        st, r = call(
            "POST",
            "/api/gene/templates",
            {
                "template_id": tid,
                "name": f"{marker} probe {key}",
                "category": "skill",
                "description": f"{marker} body {key}",
                "tags": [marker],
                "config": {"k": "v-" + tid},
            },
            token=users[who],
        )
        check(f"{who} create {key}", st == 200, r)
        tpl_ids[key] = tid

    # ── 共享目录语义核心（修前/修后双向对照点）────────────────────────
    st, la = call("GET", "/api/gene/templates", token=users["a"])
    ids_a = {t["template_id"] for t in (la or {}).get("templates", [])}
    check("A list has builtin catalog", "agent-rag-assistant" in ids_a, sorted(ids_a)[:5])
    check("A list has own row", tpl_ids["a1"] in ids_a, None)
    check("A list zero B row (user-row leak closed)", tpl_ids["b1"] not in ids_a, None)
    check("A list additive key", (la or {}).get("tenant_id") == tenants["a"], (la or {}).get("tenant_id"))

    st, ga = call("GET", "/api/gene/templates/agent-rag-assistant", token=users["a"])
    check("A get builtin by-ID 200", st == 200 and (ga or {}).get("builtin") is True, st)
    st, gb = call("GET", f"/api/gene/templates/{tpl_ids['b1']}", token=users["a"])
    check("A get B row=404 same contract", st == 404, st)

    st, ia = call(
        "POST",
        "/api/gene/templates/agent-rag-assistant/instantiate",
        {"variables": {}},
        token=users["a"],
    )
    check("A instantiate builtin ok", st == 200 and (ia or {}).get("success") is True, st)

    clone_id = f"{marker}-clone"
    st, cl = call(
        "POST",
        "/api/gene/templates/agent-rag-assistant/clone",
        {"new_id": clone_id, "new_name": f"{marker} clone"},
        token=users["a"],
    )
    check("A clone builtin ok", st == 200 and (cl or {}).get("template_id") == clone_id, (st, cl))
    st, gc = call("GET", f"/api/gene/templates/{clone_id}", token=users["a"])
    check("clone row visible to A", st == 200, st)

    st, sa = call("GET", "/api/gene/stats", token=users["a"])
    check("A stats builtin_count>0", (sa or {}).get("builtin_count", 0) > 0, (sa or {}).get("builtin_count"))
    check(
        "A stats user rows=2 (own+clone, B zero)",
        (sa or {}).get("user_count") == 2,
        (sa or {}).get("user_count"),
    )

    st, se = call("GET", "/api/gene/templates/search/RAG", token=users["a"])
    se_ids = {t["template_id"] for t in (se or {}).get("templates", [])}
    check("A search sees builtin starter", "agent-rag-assistant" in se_ids, sorted(se_ids)[:5])

    # ── 匿名 default 面（用户行零泄露）───────────────────────────────
    st, an = call("GET", "/api/gene/templates")
    an_ids = {t["template_id"] for t in (an or {}).get("templates", [])}
    check("anon has builtin", "agent-rag-assistant" in an_ids, None)
    check("anon zero A/B rows", tpl_ids["a1"] not in an_ids and tpl_ids["b1"] not in an_ids, None)
    check("anon additive key default", (an or {}).get("tenant_id") == "default", (an or {}).get("tenant_id"))

    st, bad = call("GET", "/api/gene/templates", token="not-a-token")
    check("bad token 401", st == 401, st)

    # ── 写面零变化（共享≠可写）──────────────────────────────────────
    st, r = call("DELETE", "/api/gene/templates/agent-rag-assistant", token=users["a"])
    check("delete builtin refused (A)", st == 400, st)
    st, r = call("DELETE", "/api/gene/templates/agent-rag-assistant")
    check("delete builtin refused (anon)", st == 400, st)
    st, r = call(
        "POST",
        "/api/gene/templates",
        {"template_id": "agent-rag-assistant", "name": "x", "category": "skill"},
        token=users["a"],
    )
    check("create hijack builtin id=403", st == 403, (st, r))
    st, r = call(
        "POST",
        "/api/gene/import",
        {"templates": [{"template_id": "agent-rag-assistant", "name": "x", "category": "skill"}], "overwrite": True},
        token=users["a"],
    )
    imp_ok = (r or {}).get("imported") == 1
    check("import overwrite builtin denied", not imp_ok, r)
    st, gv = call("GET", "/api/gene/templates/agent-rag-assistant")
    check("builtin row identity unchanged", (gv or {}).get("builtin") is True, gv)

    # ── 清痕 ────────────────────────────────────────────────────────
    for tid in (tpl_ids["a1"], clone_id):
        st, r = call("DELETE", f"/api/gene/templates/{tid}", token=users["a"])
        check(f"cleanup del {tid}", st == 200, (st, r))
    st, r = call("DELETE", f"/api/gene/templates/{tpl_ids['b1']}", token=users["b"])
    check(f"cleanup del {tpl_ids['b1']}", st == 200, (st, r))

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
    for tid in list(tpl_ids.values()) + [clone_id]:
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
