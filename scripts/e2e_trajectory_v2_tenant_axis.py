#!/usr/bin/env python3
"""DEC-⑦-⑳ live e2e 探针：trajectory-v2 整表行租户键加轴（真租户双向硬证）。

跑法：python3 scripts/e2e_trajectory_v2_tenant_axis.py（服务 :8090 已部署本拍代码）。
自清痕：trajectory_v2_events 探针行、enterprise 用户、nest 租户全清，remaining=0
才算过（生产表修前 0 行=非活面，取证后回零）。
"""

import json
import sqlite3
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8090"
MARK = "e2e-trajv2-" + str(int(time.time()))[-6:]
SECRET = "alpha-secret-e2e-payload"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, str(detail)))
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""))


def jreq(method, path, token=None, body=None):
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
            raw = resp.read()
            code = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        code = e.code
    try:
        return code, json.loads(raw)
    except Exception:
        return code, {}


def main():
    code, health = jreq("GET", "/api/trajectory-v2/health")
    check("v2 health 200", code == 200 and health.get("component") == "trajectory-v2")

    # 生产表迁移面（只读 PRAGMA）：tenant_id 列+idx_traj_v2_tenant 补列之后建。
    # 注意：迁移在 _ensure_table（首个数据请求）时执行——检查必须排在数据请求
    # 之后（首版探针排在 health 之后=迁移未跑假 FAIL，探针自伤非代码问题）。
    db = "/home/climbing/opensoul/data/opensoul.db"

    # 真租户×2 + enterprise register→login→token
    tenants, tokens = {}, {}
    for tag in ("a", "b"):
        code, t = jreq("POST", "/api/nest/tenants", body={"name": f"{MARK}-{tag}"})
        assert code == 200, f"tenant create {code} {t}"
        tenants[tag] = t["tenant_id"]
        uname = f"{MARK}-user-{tag}"
        code, r = jreq(
            "POST",
            "/api/enterprise/auth/register",
            body={"username": uname, "password": "e2e-pass-1", "tenant_id": t["tenant_id"]},
        )
        assert code == 200, f"register {code} {r}"
        code, r = jreq(
            "POST", "/api/enterprise/auth/login", body={"username": uname, "password": "e2e-pass-1"}
        )
        assert code == 200, f"login {code} {r}"
        tokens[tag] = r["access_token"]
    check("真租户×2+token", len(tokens) == 2, str(tenants))

    # 写点行归属：A×2 / B×1 / 匿名×1
    sid_a, sid_b, sid_d = f"{MARK}-sess-a", f"{MARK}-sess-b", f"{MARK}-sess-d"
    code, ea1 = jreq("POST", "/api/trajectory-v2/events", tokens["a"],
                     body={"session_id": sid_a, "event_type": "step", "content": SECRET + "-a1"})
    code2, ea2 = jreq("POST", "/api/trajectory-v2/events", tokens["a"],
                      body={"session_id": sid_a, "event_type": "step", "content": "a2"})
    code3, eb = jreq("POST", "/api/trajectory-v2/events", tokens["b"],
                     body={"session_id": sid_b, "event_type": "step", "content": SECRET + "-b"})
    code4, ed = jreq("POST", "/api/trajectory-v2/events",
                     body={"session_id": sid_d, "event_type": "step", "content": "delta"})
    check("写点 4 行全 200", code == code2 == code3 == code4 == 200)
    check("A 行归属=派生租户", ea1.get("tenant_id") == tenants["a"], ea1.get("tenant_id"))
    check("匿名行=default 契约", ed.get("tenant_id") == "default", ed.get("tenant_id"))

    # 请求体自报被忽略
    code, es = jreq("POST", "/api/trajectory-v2/events", tokens["a"],
                    body={"session_id": sid_a, "event_type": "step", "content": "s",
                          "tenant_id": "attacker-tenant"})
    check("自报 tenant_id 被忽略", code == 200 and es.get("tenant_id") == tenants["a"],
          es.get("tenant_id"))

    # 生产表迁移面已随首个数据请求自愈（只读 PRAGMA 取证）
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cols = [r[1] for r in con.execute("PRAGMA table_info(trajectory_v2_events)")]
    idx = [r[1] for r in con.execute("PRAGMA index_list(trajectory_v2_events)")]
    con.close()
    check("生产表 tenant_id 列+索引", "tenant_id" in cols and "idx_traj_v2_tenant" in idx, idx)

    # /sessions 三面互斥 + total 只计可见
    code_a, ra = jreq("GET", "/api/trajectory-v2/sessions", tokens["a"])
    code_b, rb = jreq("GET", "/api/trajectory-v2/sessions", tokens["b"])
    code_d, rd = jreq("GET", "/api/trajectory-v2/sessions")
    sa = {s["session_id"] for s in ra.get("sessions", [])}
    sb = {s["session_id"] for s in rb.get("sessions", [])}
    sd = {s["session_id"] for s in rd.get("sessions", [])}
    check("A 只见己行", sa == {sid_a}, str(sa))
    check("B 只见己行", sb == {sid_b}, str(sb))
    check("匿名只见 default 行", sd == {sid_d}, str(sd))
    check("total 只计可见", ra.get("total") == 1 and rb.get("total") == 1 and rd.get("total") == 1,
          (ra.get("total"), rb.get("total"), rd.get("total")))
    row_a = (ra.get("sessions") or [{}])[0]
    check("聚合行加性键", row_a.get("tenant_id") == tenants["a"], row_a.get("tenant_id"))

    # by-ID 读面：A 200 真内容+加性键；B/匿名 404 与 not-found 同契约零泄露
    code, ga = jreq("GET", f"/api/trajectory-v2/sessions/{sid_a}", tokens["a"])
    check("A get 200 真内容+加性键",
          code == 200 and SECRET in json.dumps(ga)
          and all(e.get("tenant_id") == tenants["a"] for e in ga.get("events", [])))
    code_r, ra_rep = jreq("GET", f"/api/trajectory-v2/sessions/{sid_a}/replay", tokens["a"])
    check("A replay 200 步序+加性键",
          code_r == 200 and [s["content"] for s in ra_rep.get("steps", [])][:2] == [SECRET + "-a1", "a2"]
          and all(s.get("tenant_id") == tenants["a"] for s in ra_rep.get("steps", [])))

    same, zero = True, True
    for p in (f"/api/trajectory-v2/sessions/{sid_a}",
              f"/api/trajectory-v2/sessions/{sid_a}/replay"):
        for tok in (tokens["b"], None):
            code_x, rx = jreq("GET", p, tok)
            code_m, rm = jreq("GET", p.replace(sid_a, "no-such"), tok)
            same = same and code_x == code_m == 404 and rx.get("detail") == rm.get("detail")
            zero = zero and SECRET not in json.dumps(rx) and MARK not in json.dumps(rx)
    check("B/匿名冒读全 404 与 not-found 同契约", same)
    check("零内容泄露", zero)

    # 坏 token 401 零副作用
    sid_bad = f"{MARK}-sess-bad"
    code, _ = jreq("POST", "/api/trajectory-v2/events", "bad.token",
                   body={"session_id": sid_bad, "event_type": "step", "content": "x"})
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    n_bad = con.execute(
        "SELECT COUNT(*) FROM trajectory_v2_events WHERE session_id = ?", (sid_bad,)
    ).fetchone()[0]
    con.close()
    check("坏 token POST 401+零写入", code == 401 and n_bad == 0, f"rows={n_bad}")
    code, _ = jreq("GET", "/api/trajectory-v2/sessions", "bad.token")
    check("坏 token GET 401", code == 401)

    # 清痕：探针行+enterprise 用户+nest 租户，remaining=0
    con = sqlite3.connect(db)
    con.execute("DELETE FROM trajectory_v2_events WHERE session_id LIKE ?", (MARK + "%",))
    con.commit()
    left_rows = con.execute(
        "SELECT COUNT(*) FROM trajectory_v2_events WHERE session_id LIKE ?", (MARK + "%",)
    ).fetchone()[0]
    total_rows = con.execute("SELECT COUNT(*) FROM trajectory_v2_events").fetchone()[0]
    con.close()
    check("探针行清痕 remaining=0", left_rows == 0 and total_rows == 0,
          f"mark={left_rows} total={total_rows}")

    ent_db = "/home/climbing/opensoul/data/enterprise.db"
    con = sqlite3.connect(ent_db)
    for tag in ("a", "b"):
        con.execute("DELETE FROM users WHERE username = ?", (f"{MARK}-user-{tag}",))
    con.commit()
    left_users = con.execute(
        "SELECT COUNT(*) FROM users WHERE username LIKE ?", (MARK + "%",)
    ).fetchone()[0]
    con.close()
    check("enterprise 用户清痕", left_users == 0, f"remaining={left_users}")

    for tag in ("a", "b"):
        code, _ = jreq("DELETE", f"/api/nest/tenants/{tenants[tag]}")
        check(f"租户 {tag} 清痕", code in (200, 204, 404), str(code))

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n=== {len(RESULTS) - len(failed)}/{len(RESULTS)} PASS ===")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
