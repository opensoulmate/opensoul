#!/usr/bin/env python3
"""DEC-⑦-㉑ echo 模板存储面行租户键加轴 live e2e 探针。

真栈（urllib）打 live :8090：nest 建真租户 x2 -> enterprise register->login->token
真身份 -> A/B/匿名建模板行归属 -> list 三面互斥+builtin 共享目录 -> by-ID 读闸/
写闸/render 闸（跨租户 404 与 not-found 同契约、builtin 恒拒）-> stats 聚合作用域
-> 坏 token 401 电池 -> 清痕 remaining=0。

用法：python3 scripts/e2e_echo_template_axis.py [--base http://127.0.0.1:8090]
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

    # production migration readback (templates.tenant_id column + backfill + index)
    tpl_db = Path.home() / "opensoul" / "data" / "echo" / "templates.db"
    mig = {}
    if tpl_db.exists():
        with sqlite3.connect(tpl_db) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(templates)")}
            mig["has_col"] = "tenant_id" in cols
            if mig["has_col"]:
                mig["empty_key"] = c.execute(
                    "SELECT count(*) FROM templates WHERE tenant_id IS NULL OR TRIM(tenant_id)=''"
                ).fetchone()[0]
            mig["idx"] = bool(
                c.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_templates_tenant'"
                ).fetchone()
            )
    check("prod migration col+backfill+index", mig.get("has_col") and mig.get("empty_key") == 0 and mig.get("idx"), mig)

    # ── 真租户 x2 + enterprise 真身份 ─────────────────────────────────
    tag = "e2e-et21-" + uuid.uuid4().hex[:8]
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

    bad = "not-a-real-jwt"

    # ── 写点归属：A/B/匿名各建一行 ────────────────────────────────────
    tpl_ids = {}
    for who, tok in (("a", users["a"]), ("b", users["b"]), ("d", None)):
        st, r = call(
            "POST",
            "/api/echo/templates",
            {"name": f"{tag}-{who}", "title_template": "T " + tag, "content_template": "C"},
            token=tok,
        )
        check(f"create {who}", st == 200 and (r or {}).get("template_id"), r)
        tpl_ids[who] = (r or {}).get("template_id", "")

    # list 三面互斥 + 加性键 + builtin 共享
    lists = {}
    for who, tok in (("a", users["a"]), ("b", users["b"]), ("d", None)):
        st, r = call("GET", "/api/echo/templates", token=tok)
        rows = (r or {}).get("templates", [])
        lists[who] = {x["template_id"]: x for x in rows}
        check(f"list {who} 200", st == 200, st)
    check("A sees own row", tpl_ids["a"] in lists["a"], None)
    check("A not see B row", tpl_ids["b"] not in lists["a"], None)
    check("B not see A row", tpl_ids["a"] not in lists["b"], None)
    check("anon default face", tpl_ids["d"] in lists["d"] and tpl_ids["a"] not in lists["d"] and tpl_ids["b"] not in lists["d"], None)
    check(
        "builtin shared all three",
        "tpl-task-complete" in lists["a"] and "tpl-task-complete" in lists["b"] and "tpl-task-complete" in lists["d"],
        None,
    )
    if tpl_ids["a"] in lists["a"]:
        check(
            "additive keys list",
            lists["a"][tpl_ids["a"]].get("tenant_id") == tenants["a"] and lists["a"][tpl_ids["a"]].get("builtin") is False,
            lists["a"][tpl_ids["a"]],
        )
        check(
            "builtin key true",
            lists["a"]["tpl-task-complete"].get("builtin") is True,
            None,
        )

    # by-ID 读闸
    st_own, r_own = call("GET", f"/api/echo/templates/{tpl_ids['a']}", token=users["a"])
    st_cross, r_cross = call("GET", f"/api/echo/templates/{tpl_ids['a']}", token=users["b"])
    st_miss, r_miss = call("GET", "/api/echo/templates/tpl-nope-nope", token=users["b"])
    check("get own 200", st_own == 200 and (r_own or {}).get("tenant_id") == tenants["a"], r_own)
    check("get cross 404", st_cross == 404, st_cross)
    check("get cross==missing contract", r_cross == r_miss, (r_cross, r_miss))
    st_bi, _ = call("GET", "/api/echo/templates/tpl-task-complete", token=users["b"])
    check("get builtin any tenant 200", st_bi == 200, st_bi)

    # 写闸 update
    st_u, _ = call("PATCH", f"/api/echo/templates/{tpl_ids['a']}", {"name": "hacked-" + tag}, token=users["b"])
    st_chk, r_chk = call("GET", f"/api/echo/templates/{tpl_ids['a']}", token=users["a"])
    check("update cross 404 + row unchanged", st_u == 404 and st_chk == 200 and (r_chk or {}).get("name") == f"{tag}-a", (st_u, (r_chk or {}).get("name")))
    st_ub, _ = call("PATCH", "/api/echo/templates/tpl-daily-digest", {"name": "hacked-builtin"}, token=users["a"])
    st_ubb, r_ubb = call("GET", "/api/echo/templates/tpl-daily-digest", token=users["a"])
    check("update builtin rejected unchanged", st_ub == 404 and (r_ubb or {}).get("name") == "每日摘要", (st_ub, (r_ubb or {}).get("name")))
    st_uo, _ = call("PATCH", f"/api/echo/templates/{tpl_ids['a']}", {"description": "own-upd"}, token=users["a"])
    check("update own 200", st_uo == 200, st_uo)

    # 写闸 delete
    st_d, _ = call("DELETE", f"/api/echo/templates/{tpl_ids['b']}", token=users["a"])
    st_da, _ = call("GET", f"/api/echo/templates/{tpl_ids['b']}", token=users["b"])
    check("delete cross 404 row alive", st_d == 404 and st_da == 200, (st_d, st_da))
    st_db, _ = call("DELETE", "/api/echo/templates/tpl-workflow-trigger", token=users["a"])
    st_dbb, _ = call("GET", "/api/echo/templates/tpl-workflow-trigger", token=users["b"])
    check("delete builtin rejected alive", st_db == 404 and st_dbb == 200, (st_db, st_dbb))

    # render 闸（send/preview）
    st_ps, _ = call("POST", f"/api/echo/templates/{tpl_ids['a']}/preview", {"variables": {}}, token=users["b"])
    st_po, _ = call("POST", f"/api/echo/templates/{tpl_ids['a']}/preview", {"variables": {}}, token=users["a"])
    check("preview cross 404 own 200", st_ps == 404 and st_po == 200, (st_ps, st_po))
    st_ss, _ = call("POST", f"/api/echo/templates/{tpl_ids['a']}/send", {"variables": {"v": "1"}}, token=users["b"])
    check("send cross 404", st_ss == 404, st_ss)
    st_so, r_so = call("POST", f"/api/echo/templates/{tpl_ids['a']}/send", {"variables": {"v": "1"}}, token=users["a"])
    check("send own 200", st_so == 200 and (r_so or {}).get("success") is not None, r_so)
    # builtin 共享发送（可见即可用）：用前/用后 usage 计数取证，取证后 SQL 归位（清痕）
    usage_before = None
    if tpl_db.exists():
        with sqlite3.connect(tpl_db) as c:
            row = c.execute(
                "SELECT usage_count, last_used FROM templates WHERE template_id='tpl-system-health'"
            ).fetchone()
            usage_before = row
    st_sb, _ = call(
        "POST",
        "/api/echo/templates/tpl-system-health/send",
        {"variables": {"status": tag, "cpu": "1", "memory": "1", "organs_online": "1", "organs_total": "1", "knowledge_count": "1"}},
        token=users["b"],
    )
    usage_after = None
    if tpl_db.exists() and usage_before is not None:
        with sqlite3.connect(tpl_db) as c:
            usage_after = c.execute(
                "SELECT usage_count, last_used FROM templates WHERE template_id='tpl-system-health'"
            ).fetchone()
    check(
        "send builtin 200 + usage bumped",
        st_sb == 200 and usage_before is not None and usage_after is not None and usage_after[0] == usage_before[0] + 1,
        (st_sb, usage_before, usage_after),
    )

    # stats 聚合作用域（与 list 同源对账：total==可见行数）
    for who, tok in (("a", users["a"]), ("b", users["b"]), ("d", None)):
        st, r = call("GET", "/api/echo/stats", token=tok)
        total = ((r or {}).get("templates") or {}).get("total_templates")
        check(f"stats {who} == list scope", st == 200 and total == len(lists[who]), (total, len(lists[who])))
    st, h = call("GET", "/api/echo/health")
    h_total = ((h or {}).get("templates") or {}).get("total_templates")
    check("health global >= all faces", st == 200 and h_total >= max(len(x) for x in lists.values()), h_total)

    # 坏 token 401 电池（先于读/写零副作用）
    codes = []
    st, _ = call("GET", "/api/echo/templates", token=bad)
    codes.append(st)
    st, _ = call("GET", f"/api/echo/templates/{tpl_ids['a']}", token=bad)
    codes.append(st)
    st, _ = call("PATCH", f"/api/echo/templates/{tpl_ids['a']}", {"name": "x"}, token=bad)
    codes.append(st)
    st, _ = call("DELETE", f"/api/echo/templates/{tpl_ids['a']}", token=bad)
    codes.append(st)
    st, _ = call("POST", f"/api/echo/templates/{tpl_ids['a']}/preview", {"variables": {}}, token=bad)
    codes.append(st)
    st_chk, r_chk = call("GET", f"/api/echo/templates/{tpl_ids['a']}", token=users["a"])
    check("bad token 401 x5 zero side effect", codes == [401, 401, 401, 401, 401] and (r_chk or {}).get("description") == "own-upd", (codes, (r_chk or {}).get("description")))

    # ── 清痕 ────────────────────────────────────────────────────────
    for who in ("a", "b", "d"):
        tok = users.get(who)
        st, r = call("DELETE", f"/api/echo/templates/{tpl_ids[who]}", token=tok)
        check(f"cleanup del {who}", st == 200, (st, r))
    if tpl_db.exists() and usage_before is not None:
        with sqlite3.connect(tpl_db) as c:
            c.execute(
                "UPDATE templates SET usage_count=?, last_used=? WHERE template_id='tpl-system-health'",
                usage_before,
            )
            c.commit()
        with sqlite3.connect(tpl_db) as c:
            restored = c.execute(
                "SELECT usage_count, last_used FROM templates WHERE template_id='tpl-system-health'"
            ).fetchone()
        check("cleanup usage restored", restored == tuple(usage_before), (restored, usage_before))

    time.sleep(0.2)
    ent_db = Path.home() / "opensoul" / "data" / "enterprise.db"
    nest_db = Path.home() / "opensoul" / "data" / "nest.db"
    tl_db = Path.home() / "opensoul" / "data" / "timeline.db"
    for who in ("a", "b"):
        with sqlite3.connect(ent_db) as c:
            c.execute("DELETE FROM users WHERE username=?", (f"{tag}-{who}",))
            c.commit()
    with sqlite3.connect(nest_db) as c:
        rowsn = c.execute("SELECT tenant_id FROM tenants WHERE name LIKE ?", (f"%{tag}%",)).fetchall()
        for (t_id,) in rowsn:
            c.execute("DELETE FROM access_log WHERE tenant_id=?", (t_id,))
        c.execute("DELETE FROM tenants WHERE name LIKE ?", (f"%{tag}%",))
        c.commit()
    with sqlite3.connect(tl_db) as c:
        c.execute("DELETE FROM events WHERE summary LIKE ?", (f"%{tag}%",))
        c.commit()

    remaining = 0
    with sqlite3.connect(tpl_db) as c:
        remaining += c.execute("SELECT count(*) FROM templates WHERE name LIKE ?", (f"%{tag}%",)).fetchone()[0]
    with sqlite3.connect(ent_db) as c:
        remaining += c.execute("SELECT count(*) FROM users WHERE username LIKE ?", (f"{tag}-%",)).fetchone()[0]
    with sqlite3.connect(nest_db) as c:
        remaining += c.execute("SELECT count(*) FROM tenants WHERE name LIKE ?", (f"%{tag}%",)).fetchone()[0]
        remaining += c.execute("SELECT count(*) FROM access_log WHERE tenant_id LIKE ?", (f"%{tag}%",)).fetchone()[0]
    with sqlite3.connect(tl_db) as c:
        remaining += c.execute("SELECT count(*) FROM events WHERE summary LIKE ?", (f"%{tag}%",)).fetchone()[0]
    check("cleanup remaining=0", remaining == 0, remaining)

    fails = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n== {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ==")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
