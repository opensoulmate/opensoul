#!/usr/bin/env python3
"""DEC-⑦-⑫ gene 管理面 by-ID 读面归属闸 live e2e 探针。

真栈（urllib）打 live :8090：nest 建真租户×2 → enterprise register→login→token
真身份 → A 建模板 → 三面（get/instantiate/export）+clone 源闸双向（B 冒读
404/400 与 not-found 同契约 + A 正对照 + 匿名 default 契约 + 坏 token 401）
→ 清痕 remaining=0。

用法：python3 scripts/e2e_gene_byid_gate.py [--base http://127.0.0.1:8090]
"""

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8090"
RESULTS = []


def call(method, path, body=None, token=None, raw_body=None):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if raw_body is not None:
        data = raw_body
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
    tag = "e2e-g12-" + uuid.uuid4().hex[:8]
    tenants = {}
    users = {}
    for who in ("a", "b"):
        name = f"tenant-{tag}-{who}"
        st, r = call("POST", "/api/nest/tenants", {"tenant_id": name, "name": name})
        # 坑记档：nest create 服务端自生成 tenant_id（忽略请求值）——登记键必须
        # 用回包真值，validate_tenant 只认 NestStore 真租户。
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

    tpl = "e2e-tpl-" + uuid.uuid4().hex[:8]
    st, r = call(
        "POST",
        "/api/gene/templates",
        {
            "template_id": tpl,
            "name": "e2e probe",
            "category": "skill",
            "description": "e2e body",
            "config": {"k": "v-e2e"},
            "variables": [{"name": "x", "default": "dx"}],
        },
        token=users["a"],
    )
    check("A create template", st == 200, r)

    # ── get 三向 ────────────────────────────────────────────────────
    st, r = call("GET", f"/api/gene/templates/{tpl}", token=users["a"])
    check("A get 200 + content", st == 200 and (r or {}).get("config", {}).get("k") == "v-e2e", r)
    st_b, r_b = call("GET", f"/api/gene/templates/{tpl}", token=users["b"])
    st_m, r_m = call("GET", "/api/gene/templates/no-such-e2e", token=users["b"])
    check("B get 404 same-contract", st_b == 404 and r_b == r_m == {"detail": "Template not found"}, (st_b, r_b, st_m, r_m))
    st_an, r_an = call("GET", f"/api/gene/templates/{tpl}")
    check("anon get 404 same-contract", st_an == 404 and r_an == r_m, (st_an, r_an))

    # ── instantiate 源闸 ────────────────────────────────────────────
    st, r = call("POST", f"/api/gene/templates/{tpl}/instantiate", {"variables": {}}, token=users["a"])
    check("A instantiate 200", st == 200 and (r or {}).get("success"), r)
    st_b, r_b = call("POST", f"/api/gene/templates/{tpl}/instantiate", {"variables": {}}, token=users["b"])
    st_m, r_m = call("POST", "/api/gene/templates/no-such-e2e/instantiate", {"variables": {}}, token=users["b"])
    check(
        "B instantiate 404 same-contract",
        st_b == 404 and (r_b or {}).get("detail") == f"Template '{tpl}' not found"
        and (r_b or {}).get("detail", "").replace(tpl, "ID") == (r_m or {}).get("detail", "").replace("no-such-e2e", "ID"),
        (st_b, r_b, st_m, r_m),
    )

    # ── export 源闸 ─────────────────────────────────────────────────
    st, r = call("GET", f"/api/gene/templates/{tpl}/export", token=users["a"])
    check("A export 200 + content", st == 200 and (r or {}).get("template", {}).get("config", {}).get("k") == "v-e2e", r)
    st_b, r_b = call("GET", f"/api/gene/templates/{tpl}/export", token=users["b"])
    st_m, r_m = call("GET", "/api/gene/templates/no-such-e2e/export", token=users["b"])
    check("B export 404 same-contract", st_b == 404 and r_b == r_m == {"detail": "Template not found"}, (st_b, r_b))

    # ── clone 源闸 ──────────────────────────────────────────────────
    clone_b = tpl + "-copy-b"
    st_b, r_b = call("POST", f"/api/gene/templates/{tpl}/clone", {"new_id": clone_b}, token=users["b"])
    st_m, r_m = call("POST", "/api/gene/templates/no-such-e2e/clone", {"new_id": "nm"}, token=users["b"])
    check(
        "B clone 400 same-contract",
        st_b == 400 and (r_b or {}).get("detail") == f"Source template '{tpl}' not found"
        and (r_b or {}).get("detail", "").replace(tpl, "ID") == (r_m or {}).get("detail", "").replace("no-such-e2e", "ID"),
        (st_b, r_b, st_m, r_m),
    )
    st_x, r_x = call("GET", f"/api/gene/templates/{clone_b}", token=users["a"])
    st_s, r_s = call("GET", f"/api/gene/templates/{tpl}", token=users["a"])
    check("B clone zero side effect", st_x == 404 and st_s == 200 and (r_s or {}).get("config", {}).get("k") == "v-e2e", (st_x, st_s))
    clone_a = tpl + "-copy-a"
    st, r = call("POST", f"/api/gene/templates/{tpl}/clone", {"new_id": clone_a}, token=users["a"])
    check("A clone 200", st == 200, r)
    st, r = call("GET", f"/api/gene/templates/{clone_a}", token=users["a"])
    check("A clone new row visible own", st == 200, r)
    st, r = call("GET", f"/api/gene/templates/{clone_a}", token=users["b"])
    check("clone new row hidden from B", st == 404, (st, r))

    # ── 匿名 default 契约（builtin）────────────────────────────────
    anon_copy = "builtin-copy-" + uuid.uuid4().hex[:6]
    st, r = call("POST", "/api/gene/templates/agent-rag-assistant/clone", {"new_id": anon_copy})
    check("anon clone builtin 200", st == 200, r)
    st, r = call("GET", "/api/gene/templates/agent-rag-assistant")
    check("anon get builtin 200", st == 200, st)

    # ── 坏 token 401 ────────────────────────────────────────────────
    bad = "not-a-token"
    st1, _ = call("GET", f"/api/gene/templates/{tpl}", token=bad)
    st2, _ = call("POST", f"/api/gene/templates/{tpl}/instantiate", {"variables": {}}, token=bad)
    st3, _ = call("GET", f"/api/gene/templates/{tpl}/export", token=bad)
    check("bad token 401 x3", (st1, st2, st3) == (401, 401, 401), (st1, st2, st3))

    # ── 清痕 ────────────────────────────────────────────────────────
    for tid in (tpl, clone_a, clone_b, anon_copy):
        st, r = call("DELETE", f"/api/gene/templates/{tid}", token=users["a"])
        if st != 200:
            st, r = call("DELETE", f"/api/gene/templates/{tid}", token=users["b"])
        if st != 200:
            st, r = call("DELETE", f"/api/gene/templates/{tid}")
        # clone_b 在闸生效时本就不该存在（400=不存在，恰证明零克隆副作用）
        ok_del = st == 200 or (tid == clone_b and st == 400)
        check(f"cleanup del {tid}", ok_del, (st, r))

    # 直库清 users/tenants 行（API 无删除用户口）+ 回读 remaining（删后计数）
    time.sleep(0.2)
    ent_db = Path.home() / "opensoul" / "data" / "enterprise.db"
    nest_db = Path.home() / "opensoul" / "data" / "nest.db"
    tpl_dir = Path.home() / ".opensoul" / "templates"
    for who in ("a", "b"):
        with sqlite3.connect(ent_db) as c:
            c.execute("DELETE FROM users WHERE username=?", (f"{tag}-{who}",))
            c.commit()
    with sqlite3.connect(nest_db) as c:
        rows = c.execute(
            "SELECT tenant_id FROM tenants WHERE name LIKE ?", (f"%{tag}%",)
        ).fetchall()
        for (t_id,) in rows:
            c.execute("DELETE FROM access_log WHERE tenant_id=?", (t_id,))
        c.execute("DELETE FROM tenants WHERE name LIKE ?", (f"%{tag}%",))
        c.commit()
    remaining = 0
    for tid in (tpl, clone_a, clone_b, anon_copy):
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

    ok = sum(1 for _, good, _ in RESULTS if good)
    print(f"\n== {ok}/{len(RESULTS)} PASS ==")
    sys.exit(0 if ok == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
