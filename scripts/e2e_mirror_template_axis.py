#!/usr/bin/env python3
"""DEC-⑦-㉒b mirror sandbox 模板存储面行租户键加轴 live e2e 探针。

真栈（urllib）打 live :8090：nest 建真租户 x2 -> enterprise register->login->token
真身份 -> A/B/匿名建模板行归属 -> list 三面互斥+builtin 共享目录+加性键 -> by-ID
读闸/instantiate 闸（跨租户 404 与 not-found 同契约零副作用）-> 写闸 delete
（跨租户/builtin 恒拒行存活）-> stats 聚合作用域 -> 坏 token 401 电池 ->
清痕 remaining=0（含探针自愈 builtin 种子行恢复）。

用法：python3 scripts/e2e_mirror_template_axis.py [--base http://127.0.0.1:8090]
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

BUILTIN_SEED = [
    ("stpl-workflow-test", "工作流测试沙箱", "workflow"),
    ("stpl-agent-debug", "Agent调试沙箱", "agent"),
    ("stpl-connector-test", "连接器测试沙箱", "connector"),
    ("stpl-knowledge-import", "知识导入沙箱", "workflow"),
    ("stpl-prompt-experiment", "Prompt实验沙箱", "agent"),
    ("stpl-api-stress", "API压测沙箱", "connector"),
]


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

    # production migration readback (sandbox_templates.tenant_id column + backfill + index)
    tpl_db = Path.home() / "opensoul" / "data" / "mirror" / "templates.db"
    mig = {}
    if tpl_db.exists():
        with sqlite3.connect(tpl_db) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(sandbox_templates)")}
            mig["has_col"] = "tenant_id" in cols
            if mig["has_col"]:
                mig["empty_key"] = c.execute(
                    "SELECT count(*) FROM sandbox_templates WHERE tenant_id IS NULL OR TRIM(tenant_id)=''"
                ).fetchone()[0]
            mig["idx"] = bool(
                c.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_sboxtpl_tenant'"
                ).fetchone()
            )
    check(
        "prod migration col+backfill+index",
        mig.get("has_col") and mig.get("empty_key") == 0 and mig.get("idx"),
        mig,
    )

    # ── 真租户 x2 + enterprise 真身份 ─────────────────────────────────
    tag = "e2e-mt22b-" + uuid.uuid4().hex[:8]
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
            "/api/mirror/templates",
            {"name": f"{tag}-{who}"},
            token=tok,
        )
        check(f"create {who}", st == 200 and (r or {}).get("template_id"), r)
        tpl_ids[who] = (r or {}).get("template_id", "")

    # list 三面互斥 + 加性键 + builtin 共享
    lists = {}
    for who, tok in (("a", users["a"]), ("b", users["b"]), ("d", None)):
        st, r = call("GET", "/api/mirror/templates", token=tok)
        rows = (r or {}).get("templates", [])
        lists[who] = {x["template_id"]: x for x in rows}
        check(f"list {who} 200", st == 200, st)
    check("A sees own row", tpl_ids["a"] in lists["a"], None)
    check("A not see B row", tpl_ids["b"] not in lists["a"], None)
    check("B not see A row", tpl_ids["a"] not in lists["b"], None)
    check(
        "anon default face",
        tpl_ids["d"] in lists["d"] and tpl_ids["a"] not in lists["d"] and tpl_ids["b"] not in lists["d"],
        None,
    )
    check(
        "builtin shared all three",
        "stpl-workflow-test" in lists["a"]
        and "stpl-workflow-test" in lists["b"]
        and "stpl-workflow-test" in lists["d"],
        None,
    )
    if tpl_ids["a"] in lists["a"]:
        check(
            "additive keys list",
            lists["a"][tpl_ids["a"]].get("tenant_id") == tenants["a"]
            and lists["a"][tpl_ids["a"]].get("builtin") is False,
            lists["a"][tpl_ids["a"]],
        )
        check(
            "builtin key true",
            lists["a"]["stpl-workflow-test"].get("builtin") is True,
            None,
        )

    # by-ID 读闸
    st_own, r_own = call("GET", f"/api/mirror/templates/{tpl_ids['a']}", token=users["a"])
    st_cross, r_cross = call("GET", f"/api/mirror/templates/{tpl_ids['a']}", token=users["b"])
    st_miss, r_miss = call("GET", "/api/mirror/templates/stpl-nope-nope", token=users["b"])
    check(
        "get own 200 + tenant_id key",
        st_own == 200 and (r_own or {}).get("tenant_id") == tenants["a"],
        r_own,
    )
    check("get cross 404", st_cross == 404, st_cross)
    check("get cross==missing contract", r_cross == r_miss, (r_cross, r_miss))
    st_bi, _ = call("GET", "/api/mirror/templates/stpl-agent-debug", token=users["b"])
    check("get builtin any tenant 200", st_bi == 200, st_bi)

    # stats 聚合作用域（与 list 同源对账：total==可见行数）
    for who, tok in (("a", users["a"]), ("b", users["b"]), ("d", None)):
        st, r = call("GET", "/api/mirror/stats", token=tok)
        total = ((r or {}).get("templates") or {}).get("total_templates")
        check(
            f"stats {who} == list scope",
            st == 200 and total == len(lists[who]),
            (total, len(lists[who])),
        )
    st, h = call("GET", "/api/mirror/health")
    h_total = ((h or {}).get("templates") or {}).get("total_templates")
    check("health global >= all faces", st == 200 and h_total >= max(len(x) for x in lists.values()), h_total)

    # 坏 token 401 电池（先于读/写零副作用）
    codes = []
    st, _ = call("GET", "/api/mirror/templates", token=bad)
    codes.append(st)
    st, _ = call("GET", f"/api/mirror/templates/{tpl_ids['a']}", token=bad)
    codes.append(st)
    st, _ = call("POST", "/api/mirror/templates", {"name": "bad-" + tag}, token=bad)
    codes.append(st)
    st, _ = call("GET", "/api/mirror/stats", token=bad)
    codes.append(st)
    check("bad token 401 read battery", codes == [401, 401, 401, 401], codes)

    # instantiate 闸：跨租户 404 零副作用（usage 记账不进）/own 200/builtin 共享可用
    usage_a_before = None
    if tpl_db.exists():
        with sqlite3.connect(tpl_db) as c:
            usage_a_before = c.execute(
                "SELECT usage_count FROM sandbox_templates WHERE template_id=?",
                (tpl_ids["a"],),
            ).fetchone()
    usage_before = None
    if tpl_db.exists():
        with sqlite3.connect(tpl_db) as c:
            usage_before = c.execute(
                "SELECT usage_count FROM sandbox_templates WHERE template_id='stpl-prompt-experiment'"
            ).fetchone()
    st_ix, _ = call(
        "POST",
        f"/api/mirror/templates/{tpl_ids['a']}/instantiate",
        {"template_id": tpl_ids["a"], "name": f"{tag}-inst-cross"},
        token=users["b"],
    )
    usage_a_mid = None
    if tpl_db.exists():
        with sqlite3.connect(tpl_db) as c:
            usage_a_mid = c.execute(
                "SELECT usage_count FROM sandbox_templates WHERE template_id=?",
                (tpl_ids["a"],),
            ).fetchone()
    st_ic, r_ic = call(
        "POST",
        f"/api/mirror/templates/{tpl_ids['a']}/instantiate",
        {"template_id": tpl_ids["a"], "name": f"{tag}-inst-own"},
        token=users["a"],
    )
    check("instantiate cross 404 own 200", st_ix == 404 and st_ic == 200, (st_ix, st_ic))
    if st_ic == 200 and (r_ic or {}).get("sandbox_id"):
        st_del, _ = call("DELETE", f"/api/mirror/sandboxes/{r_ic['sandbox_id']}", token=users["a"])
        check("instantiate own sandbox cleaned", st_del == 200, st_del)
    st_ib, r_ib = call(
        "POST",
        "/api/mirror/templates/stpl-prompt-experiment/instantiate",
        {"template_id": "stpl-prompt-experiment", "name": f"{tag}-inst-builtin"},
        token=users["b"],
    )
    usage_after = None
    if tpl_db.exists():
        with sqlite3.connect(tpl_db) as c:
            usage_after = c.execute(
                "SELECT usage_count FROM sandbox_templates WHERE template_id='stpl-prompt-experiment'"
            ).fetchone()
    check("instantiate builtin any tenant 200", st_ib == 200, st_ib)
    check(
        "instantiate cross zero usage side effect",
        usage_a_before is not None and usage_a_mid == usage_a_before,
        (usage_a_before, usage_a_mid),
    )
    check(
        "instantiate builtin usage bumped",
        usage_before is not None and usage_after is not None and usage_after[0] == usage_before[0] + 1,
        (usage_before, usage_after),
    )

    # ── 写闸 delete（破坏面靠后，防 before 探针自伤遮蔽前序证据）────────
    st_d, _ = call("DELETE", f"/api/mirror/templates/{tpl_ids['b']}", token=users["a"])
    st_da, _ = call("GET", f"/api/mirror/templates/{tpl_ids['b']}", token=users["b"])
    check("delete cross 404 row alive", st_d == 404 and st_da == 200, (st_d, st_da))
    st_db, _ = call("DELETE", "/api/mirror/templates/stpl-api-stress", token=users["a"])
    st_dbb, _ = call("GET", "/api/mirror/templates/stpl-api-stress", token=users["b"])
    check("delete builtin rejected alive", st_db == 404 and st_dbb == 200, (st_db, st_dbb))
    st_dbad, _ = call("DELETE", f"/api/mirror/templates/{tpl_ids['a']}", token=bad)
    check("delete bad token 401", st_dbad == 401, st_dbad)
    st_d2, _ = call("DELETE", f"/api/mirror/templates/{tpl_ids['a']}", token=users["a"])
    st_d2c, _ = call("GET", f"/api/mirror/templates/{tpl_ids['a']}", token=users["a"])
    check("delete own 200 gone", st_d2 == 200 and st_d2c == 404, (st_d2, st_d2c))

    # ── 清痕（容缺：before 探针自伤（旧码真删）行可缺，断终态不苛求状态码）──
    for who in ("b", "d"):
        tok = users.get(who)
        call("DELETE", f"/api/mirror/templates/{tpl_ids[who]}", token=tok)
    # builtin 种子行自愈恢复（before 跑旧码 delete builtin 真删种子行的探针自伤）
    if tpl_db.exists():
        now = time.time()
        with sqlite3.connect(tpl_db) as c:
            for tid, name, cat in BUILTIN_SEED:
                c.execute(
                    "INSERT OR IGNORE INTO sandbox_templates "
                    "(template_id, name, description, icon, config, variables, tags, category, created_at) "
                    "VALUES (?, ?, '', '🧪', '{}', '{}', '[]', ?, ?)",
                    (tid, name, cat, now),
                )
            c.commit()
        with sqlite3.connect(tpl_db) as c:
            missing = [
                tid
                for (tid,) in c.execute(
                    "SELECT template_id FROM sandbox_templates WHERE template_id LIKE 'stpl-%'"
                ).fetchall()
            ]
        check(
            "builtin seed restored",
            all(t in missing for t, _, _ in BUILTIN_SEED),
            sorted(missing),
        )
        # builtin usage 记账归位（取证用）
        if usage_before is not None:
            with sqlite3.connect(tpl_db) as c:
                c.execute(
                    "UPDATE sandbox_templates SET usage_count=? WHERE template_id='stpl-prompt-experiment'",
                    (usage_before[0],),
                )
                c.commit()

    time.sleep(0.2)
    ent_db = Path.home() / "opensoul" / "data" / "enterprise.db"
    nest_db = Path.home() / "opensoul" / "data" / "nest.db"
    tl_db = Path.home() / "opensoul" / "data" / "timeline.db"
    # 沙箱残留（instantiate 产物）按 tag 名清
    st_sb, r_sb = call("GET", "/api/mirror/sandboxes", token=users["a"])
    for sb in (r_sb or {}).get("sandboxes", []):
        if tag in (sb.get("name") or ""):
            call("DELETE", f"/api/mirror/sandboxes/{sb['sandbox_id']}", token=users["a"])
    st_sb2, r_sb2 = call("GET", "/api/mirror/sandboxes", token=users["b"])
    for sb in (r_sb2 or {}).get("sandboxes", []):
        if tag in (sb.get("name") or ""):
            call("DELETE", f"/api/mirror/sandboxes/{sb['sandbox_id']}", token=users["b"])
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
    if tpl_db.exists():
        with sqlite3.connect(tpl_db) as c:
            remaining += c.execute(
                "SELECT count(*) FROM sandbox_templates WHERE name LIKE ?", (f"%{tag}%",)
            ).fetchone()[0]
    with sqlite3.connect(ent_db) as c:
        remaining += c.execute(
            "SELECT count(*) FROM users WHERE username LIKE ?", (f"{tag}-%",)
        ).fetchone()[0]
    with sqlite3.connect(nest_db) as c:
        remaining += c.execute("SELECT count(*) FROM tenants WHERE name LIKE ?", (f"%{tag}%",)).fetchone()[0]
        remaining += c.execute(
            "SELECT count(*) FROM access_log WHERE tenant_id LIKE ?", (f"%{tag}%",)
        ).fetchone()[0]
    with sqlite3.connect(tl_db) as c:
        remaining += c.execute("SELECT count(*) FROM events WHERE summary LIKE ?", (f"%{tag}%",)).fetchone()[0]
    check("cleanup remaining=0", remaining == 0, remaining)

    fails = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n== {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ==")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
