#!/usr/bin/env python3
"""DEC-⑦-㉓ mirror 沙箱管理面（SandboxManager）行租户键加轴 live e2e 探针。

真栈（urllib）打 live :8090：nest 建真租户 x2 -> enterprise register->login->token
真身份 -> A/B/匿名建沙箱行归属 -> list 三面互斥+加性键 -> by-ID 归属闸电池
（跨租户读/写/exec/fork 全与 not-found 同契约零副作用）-> fork 归克隆者 ->
stats 聚合作用域 -> cleanup 作用域（只清自己）-> 坏 token 401 电池 ->
匿名契约 -> 清痕 remaining=0。

用法：python3 scripts/e2e_mirror_sandbox_axis.py [--base http://127.0.0.1:8090]
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
        with urllib.request.urlopen(req, timeout=60) as r:
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

    # ── 真租户 x2 + enterprise 真身份 ─────────────────────────────────
    tag = "e2e-mt23-" + uuid.uuid4().hex[:8]
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

    # ── 写点归属：A/B/匿名各建一箱 ────────────────────────────────────
    sids = {}
    for who, tok in (("a", users["a"]), ("b", users["b"]), ("d", None)):
        st, r = call("POST", "/api/mirror/sandboxes", {"name": f"{tag}-{who}"}, token=tok)
        check(f"create {who}", st == 200 and (r or {}).get("sandbox_id"), r)
        sids[who] = (r or {}).get("sandbox_id", "")

    # 行归属加性键（own get）
    for who, tok in (("a", users["a"]), ("b", users["b"]), ("d", None)):
        st, r = call("GET", f"/api/mirror/sandboxes/{sids[who]}", token=tok)
        expect = tenants.get(who) or "default"
        check(
            f"get own 200 + tenant_id={who}",
            st == 200 and (r or {}).get("tenant_id") == expect,
            ((r or {}).get("tenant_id"), expect),
        )

    # instantiate 产物行归属
    st, r = call("POST", "/api/mirror/templates", {"name": f"{tag}-tpl"}, token=users["a"])
    tid = (r or {}).get("template_id", "")
    st, r = call(
        "POST",
        f"/api/mirror/templates/{tid}/instantiate",
        {"template_id": tid, "name": f"{tag}-inst"},
        token=users["a"],
    )
    inst_sid = (r or {}).get("sandbox_id", "")
    st, r = call("GET", f"/api/mirror/sandboxes/{inst_sid}", token=users["a"])
    check(
        "instantiate product row tenant",
        st == 200 and (r or {}).get("tenant_id") == tenants["a"],
        (r or {}).get("tenant_id"),
    )

    # list 三面互斥 + 加性键
    lists = {}
    for who, tok in (("a", users["a"]), ("b", users["b"]), ("d", None)):
        st, r = call("GET", "/api/mirror/sandboxes", token=tok)
        rows = (r or {}).get("sandboxes", [])
        lists[who] = {x["sandbox_id"]: x for x in rows}
        check(f"list {who} 200", st == 200, st)
    check("A sees own row", sids["a"] in lists["a"], None)
    check("A not see B row", sids["b"] not in lists["a"] and inst_sid in lists["a"], None)
    check("B not see A row", sids["a"] not in lists["b"] and inst_sid not in lists["b"], None)
    check(
        "anon default face",
        sids["d"] in lists["d"] and sids["a"] not in lists["d"] and sids["b"] not in lists["d"],
        None,
    )
    if sids["a"] in lists["a"]:
        check(
            "additive key list",
            lists["a"][sids["a"]].get("tenant_id") == tenants["a"],
            lists["a"][sids["a"]].get("tenant_id"),
        )

    # stats 聚合作用域（与 list 同源对账：total==可见行数）
    for who, tok in (("a", users["a"]), ("b", users["b"]), ("d", None)):
        st, r = call("GET", "/api/mirror/stats", token=tok)
        total = (r or {}).get("total_sandboxes")
        check(
            f"stats {who} == list scope",
            st == 200 and total == len(lists[who]),
            (total, len(lists[who])),
        )
    st, h = call("GET", "/api/mirror/health")
    h_total = (h or {}).get("total_sandboxes")
    check(
        "health global >= all faces",
        st == 200 and h_total >= max(len(x) for x in lists.values()),
        h_total,
    )

    # ── by-ID 归属闸电池（B 冒 A，全与 not-found 同契约零副作用）──────
    hb, ha = users["b"], users["a"]
    sid_a = sids["a"]
    st_miss, r_miss = call("GET", "/api/mirror/sandboxes/sandbox-nope-nope", token=hb)
    st_g, r_g = call("GET", f"/api/mirror/sandboxes/{sid_a}", token=hb)
    check("get cross 404", st_g == 404, st_g)
    check("get cross==missing contract", r_g == r_miss, (r_g, r_miss))

    x_actions = [
        ("POST", f"/api/mirror/sandboxes/{sid_a}/pause", None, 404),
        ("POST", f"/api/mirror/sandboxes/{sid_a}/resume", None, 400),
        ("POST", f"/api/mirror/sandboxes/{sid_a}/log", {"action": "x"}, 404),
        ("POST", f"/api/mirror/sandboxes/{sid_a}/variables", {"key": "k", "value": "v"}, 404),
        ("POST", f"/api/mirror/sandboxes/{sid_a}/snapshot", {"name": "snap-x"}, 404),
        ("POST", f"/api/mirror/sandboxes/{sid_a}/network-policy", {"policy": "allow"}, 404),
    ]
    for method, path, body, want in x_actions:
        st_x, _ = call(method, path, body, token=hb)
        check(f"cross {path.rsplit('/', 1)[-1]} =={want}", st_x == want, st_x)

    st_xv, r_xv = call("GET", f"/api/mirror/sandboxes/{sid_a}/variables", token=hb)
    st_mv, r_mv = call("GET", "/api/mirror/sandboxes/sandbox-nope-nope/variables", token=hb)
    check("variables cross 404==missing", st_xv == 404 and r_xv == r_mv, (st_xv, r_xv, r_mv))
    st_xl, r_xl = call("GET", f"/api/mirror/sandboxes/{sid_a}/logs", token=hb)
    st_ml, r_ml = call("GET", "/api/mirror/sandboxes/sandbox-nope-nope/logs", token=hb)
    check("logs cross 404==missing", st_xl == 404 and r_xl == r_ml, (st_xl, r_xl, r_ml))

    # exec/fork 跨租户：零执行零建行
    st_xe, _ = call("POST", f"/api/mirror/sandboxes/{sid_a}/exec", {"command": "echo hi"}, token=hb)
    check("exec cross 404", st_xe == 404, st_xe)
    st_snap, r_snap = call(
        "POST", f"/api/mirror/sandboxes/{sid_a}/snapshot", {"name": "snap-ok"}, token=ha
    )
    check("snapshot own 200", st_snap == 200 and (r_snap or {}).get("snapshot_id"), r_snap)
    snap_id = (r_snap or {}).get("snapshot_id", "")
    # 行数对账基线（inst 行已在 lists["a"] 内，禁重复计数——探针作者算术自纠）
    n_before = len(lists["a"]) + len(lists["b"]) + len(lists["d"])
    st_xf, _ = call(
        "POST", f"/api/mirror/sandboxes/{sid_a}/fork", {"snapshot_id": snap_id}, token=hb
    )
    check("fork cross 404", st_xf == 404, st_xf)
    st_nl, r_nl = call("GET", "/api/mirror/sandboxes", token=ha)
    st_nl2, r_nl2 = call("GET", "/api/mirror/sandboxes", token=hb)
    st_nl3, r_nl3 = call("GET", "/api/mirror/sandboxes", token=None)
    n_after = len((r_nl or {}).get("sandboxes", [])) + len((r_nl2 or {}).get("sandboxes", [])) + len(
        (r_nl3 or {}).get("sandboxes", [])
    )
    check("fork cross zero child", n_after == n_before, (n_before, n_after))

    # 行哨兵一字不动（变量/日志/状态/网络策略）
    st_sent, r_sent = call("GET", f"/api/mirror/sandboxes/{sid_a}", token=ha)
    check(
        "row sentinel untouched",
        st_sent == 200
        and (r_sent or {}).get("variables") == {}
        and (r_sent or {}).get("status") == "active"
        and (r_sent or {}).get("snapshot_count") == 1,  # own snapshot 1 次
        r_sent,
    )

    # fork 归克隆者（A 自己 fork）
    st_fk, r_fk = call(
        "POST", f"/api/mirror/sandboxes/{sid_a}/fork", {"snapshot_id": snap_id}, token=ha
    )
    child_sid = (r_fk or {}).get("sandbox_id", "")
    st_fc, r_fc = call("GET", f"/api/mirror/sandboxes/{child_sid}", token=ha)
    check(
        "fork child belongs to cloner",
        st_fk == 200
        and st_fc == 200
        and (r_fc or {}).get("tenant_id") == tenants["a"]
        and tag in ((r_fc or {}).get("name") or ""),
        (st_fk, (r_fc or {}).get("tenant_id")),
    )

    # 同租户正对照（exec 真跑+真留痕）
    st_e, r_e = call("POST", f"/api/mirror/sandboxes/{sid_a}/exec", {"command": "echo hi"}, token=ha)
    check("exec own 200", st_e == 200 and (r_e or {}).get("exit_code") == 0, r_e)
    st_lg, r_lg = call("GET", f"/api/mirror/sandboxes/{sid_a}/logs", token=ha)
    check(
        "logs own non-empty",
        st_lg == 200 and len((r_lg or {}).get("logs", [])) > 0,
        len((r_lg or {}).get("logs", [])),
    )

    # ── cleanup 作用域（只清自己）─────────────────────────────────────
    exp = {}
    for who, tok in (("a", users["a"]), ("b", users["b"]), ("d", None)):
        st, r = call(
            "POST",
            "/api/mirror/sandboxes",
            {"name": f"{tag}-exp-{who}", "ttl_seconds": 0},
            token=tok,
        )
        exp[who] = (r or {}).get("sandbox_id", "")
    time.sleep(0.05)
    st_cl, r_cl = call("POST", "/api/mirror/cleanup", token=users["a"])
    check("cleanup A scoped==1", st_cl == 200 and (r_cl or {}).get("cleaned") == 1, r_cl)
    st_ca, _ = call("GET", f"/api/mirror/sandboxes/{exp['a']}", token=users["a"])
    st_cb, _ = call("GET", f"/api/mirror/sandboxes/{exp['b']}", token=users["b"])
    st_cd, _ = call("GET", f"/api/mirror/sandboxes/{exp['d']}", token=None)
    check("cleanup only own gone", st_ca == 404 and st_cb == 200 and st_cd == 200, (st_ca, st_cb, st_cd))

    # ── 坏 token 401 电池（先于读/写零副作用）─────────────────────────
    battery = [
        ("GET", f"/api/mirror/sandboxes/{sid_a}", None),
        ("DELETE", f"/api/mirror/sandboxes/{sid_a}", None),
        ("POST", f"/api/mirror/sandboxes/{sid_a}/pause", None),
        ("POST", f"/api/mirror/sandboxes/{sid_a}/log", {"action": "x"}),
        ("GET", f"/api/mirror/sandboxes/{sid_a}/logs", None),
        ("POST", f"/api/mirror/sandboxes/{sid_a}/variables", {"key": "k", "value": "v"}),
        ("GET", f"/api/mirror/sandboxes/{sid_a}/variables", None),
        ("POST", f"/api/mirror/sandboxes/{sid_a}/snapshot", {"name": "s"}),
        ("POST", f"/api/mirror/sandboxes/{sid_a}/exec", {"command": "echo hi"}),
        ("POST", f"/api/mirror/sandboxes/{sid_a}/network-policy", {"policy": "allow"}),
        ("POST", f"/api/mirror/sandboxes/{sid_a}/fork", {"snapshot_id": snap_id}),
        ("GET", "/api/mirror/sandboxes", None),
        ("POST", "/api/mirror/cleanup", None),
    ]
    all401 = True
    for method, path, body in battery:
        st_b, _ = call(method, path, body, token=bad)
        if st_b != 401:
            all401 = False
            check(f"bad token {method} {path}", False, st_b)
    check("bad token battery all 401", all401, None)
    st_bs, r_bs = call("GET", f"/api/mirror/sandboxes/{sid_a}", token=users["a"])
    check(
        "bad token zero side effect",
        st_bs == 200 and (r_bs or {}).get("variables") == {} and (r_bs or {}).get("status") == "active",
        r_bs,
    )

    # 匿名契约（default 面照旧）
    st_ad, _ = call("GET", f"/api/mirror/sandboxes/{sids['d']}", token=None)
    st_dd, _ = call("DELETE", f"/api/mirror/sandboxes/{sids['d']}", token=None)
    check("anon contract 200", st_ad == 200 and st_dd == 200, (st_ad, st_dd))

    # ── 清痕（容缺：before 跑旧码跨租户真删/真建，行可缺）────────────
    for who in ("a", "b", "d"):
        tok = users.get(who)
        st_l, r_l = call("GET", "/api/mirror/sandboxes", token=tok)
        for sb in (r_l or {}).get("sandboxes", []):
            if tag in (sb.get("name") or ""):
                call("DELETE", f"/api/mirror/sandboxes/{sb['sandbox_id']}", token=tok)
    call("DELETE", f"/api/mirror/templates/{tid}", token=users["a"])
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
    for who in ("a", "b", "d"):
        tok = users.get(who)
        st_l, r_l = call("GET", "/api/mirror/sandboxes", token=tok)
        remaining += sum(1 for sb in (r_l or {}).get("sandboxes", []) if tag in (sb.get("name") or ""))
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
