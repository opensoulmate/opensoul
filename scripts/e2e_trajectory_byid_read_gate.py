#!/usr/bin/env python3
"""DEC-⑦-⑪ live e2e 探针：trajectory 面 by-ID 读面归属闸（真租户双向硬证）。

跑法：python3 scripts/e2e_trajectory_byid_read_gate.py（服务 :8090 已部署本拍代码）。
自清痕：探针会话/事件、enterprise 用户、nest 租户全清，remaining=0 才算过。
"""

import json
import sqlite3
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8090"
MARK = "e2e-trajbyid-" + str(int(time.time()))[-6:]
SECRET = "alpha-secret-e2e-payload"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""))


def req(method, path, token=None, body=None):
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
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def jreq(method, path, token=None, body=None):
    code, data = req(method, path, token, body)
    try:
        return code, json.loads(data)
    except Exception:
        return code, {}


def main():
    code, health = jreq("GET", "/api/health")
    check("health 200", code == 200, str(health.get("status")))

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

    # A 建会话+事件（行归属=派生租户）
    sid = f"{MARK}-sess-a"
    code, sess = jreq(
        "POST",
        "/api/trajectory/sessions",
        tokens["a"],
        body={"agent_id": "agent-x", "task_description": f"{MARK} alpha task", "session_id": sid},
    )
    assert code == 200, f"create session {code} {sess}"
    check("A 行归属=派生租户", sess.get("tenant_id") == tenants["a"], str(sess.get("tenant_id")))
    code, ev = jreq(
        "POST",
        f"/api/trajectory/sessions/{sid}/events",
        tokens["a"],
        body={"event_type": "tool_call", "agent_id": "agent-x", "content": SECRET},
    )
    assert code == 200, f"record event {code} {ev}"
    eid = ev["id"]

    # A 正对照：五读面全 200 + 内容可见 + 加性键
    code, meta = jreq("GET", f"/api/trajectory/sessions/{sid}", tokens["a"])
    check("A get_session 200+tenant_id 键", code == 200 and meta.get("tenant_id") == tenants["a"])
    code, evs = jreq("GET", f"/api/trajectory/sessions/{sid}/events", tokens["a"])
    check("A get_events 200 真内容", code == 200 and SECRET in json.dumps(evs))
    code, one = jreq("GET", f"/api/trajectory/events/{eid}", tokens["a"])
    check("A get_event 200 真内容", code == 200 and one.get("content") == SECRET)
    code, rep = jreq("GET", f"/api/trajectory/sessions/{sid}/replay", tokens["a"])
    check("A replay 200 真内容", code == 200 and SECRET in json.dumps(rep))
    code, tr = jreq("GET", f"/api/trajectory/sessions/{sid}/trace", tokens["a"])
    check("A trace 200 真内容", code == 200 and SECRET in json.dumps(tr))

    # B 冒读电池：五读面全 404 与 not-found 同契约 + 零内容泄露
    paths = [
        f"/api/trajectory/sessions/{sid}",
        f"/api/trajectory/sessions/{sid}/events",
        f"/api/trajectory/sessions/{sid}/replay",
        f"/api/trajectory/sessions/{sid}/trace",
    ]
    same = True
    zero_leak = True
    for p in paths:
        code_b, rb = jreq("GET", p, tokens["b"])
        code_m, rm = jreq("GET", p.replace(sid, "no-such"), tokens["b"])
        same = same and code_b == code_m == 404 and rb.get("detail") == rm.get("detail")
        zero_leak = zero_leak and SECRET not in json.dumps(rb) and MARK not in json.dumps(rb)
    code_b, rb = jreq("GET", f"/api/trajectory/events/{eid}", tokens["b"])
    code_m, rm = jreq("GET", "/api/trajectory/events/no-such", tokens["b"])
    same = same and code_b == code_m == 404 and rb.get("detail") == rm.get("detail") == "Event not found"
    zero_leak = zero_leak and SECRET not in json.dumps(rb)
    check("B 冒读五面全 404 与 not-found 同契约", same)
    check("B 零内容泄露", zero_leak)

    # 匿名：租户行 404、default 行契约不变
    code, _ = jreq("GET", f"/api/trajectory/sessions/{sid}")
    check("匿名读租户行 404", code == 404)
    sid_d = f"{MARK}-sess-d"
    code, sess_d = jreq(
        "POST",
        "/api/trajectory/sessions",
        body={"agent_id": "agent-x", "task_description": f"{MARK} default task", "session_id": sid_d},
    )
    assert code == 200, f"anon session {code} {sess_d}"
    code, ev_d = jreq(
        "POST",
        f"/api/trajectory/sessions/{sid_d}/events",
        body={"event_type": "tool_call", "agent_id": "agent-x", "content": "delta-e2e"},
    )
    assert code == 200
    code, meta_d = jreq("GET", f"/api/trajectory/sessions/{sid_d}")
    check(
        "匿名 default 行 200 契约不变",
        code == 200 and meta_d.get("tenant_id") == "default",
    )
    code, _ = jreq("GET", f"/api/trajectory/events/{ev_d['id']}")
    check("匿名 default 事件 200 契约不变", code == 200)

    # fork 源读闸：B fork A=与缺源同契约+零克隆副作用；A fork A 正对照
    traj_db = str(__import__("pathlib").Path.home() / "opensoul" / "data" / "opensoul.db")

    def _mark_sessions():
        con = sqlite3.connect(traj_db)
        try:
            return con.execute(
                "SELECT COUNT(*) FROM trajectory_sessions WHERE task_description LIKE ?",
                ("%" + MARK + "%",),
            ).fetchone()[0]
        finally:
            con.close()

    before = _mark_sessions()
    code_b, rb = jreq(
        "POST",
        f"/api/trajectory/sessions/{sid}/fork",
        tokens["b"],
        body={"fork_point_event_id": eid},
    )
    code_m, rm = jreq(
        "POST",
        "/api/trajectory/sessions/no-such/fork",
        tokens["b"],
        body={"fork_point_event_id": ""},
    )
    detail_same = (
        code_b == code_m == 400
        and rb.get("detail", "").replace(sid, "SID") == rm.get("detail", "").replace("no-such", "SID")
    )
    check("B fork A=缺源同契约 400", detail_same, f"{rb.get('detail')}")
    check("B fork 零内容泄露", SECRET not in json.dumps(rb))
    check("B fork 零克隆副作用", _mark_sessions() == before, f"{before}->{_mark_sessions()}")
    code_a, ra = jreq(
        "POST",
        f"/api/trajectory/sessions/{sid}/fork",
        tokens["a"],
        body={"fork_point_event_id": eid},
    )
    check("A fork 己源 200 归克隆者", code_a == 200 and ra.get("tenant_id") == tenants["a"])
    forked_id = ra.get("id") if code_a == 200 else ""

    # 写面契约零变化回归钉（⑦-⑥ 边界②：匿名写方事件随父行继承）
    code, ev_ext = jreq(
        "POST",
        f"/api/trajectory/sessions/{sid}/events",
        body={"event_type": "tool_call", "agent_id": "agent-x", "content": "ext write"},
    )
    check("写面父行继承契约不变", code == 200 and ev_ext.get("tenant_id") == tenants["a"])

    # 坏 token 401
    code, _ = jreq("GET", f"/api/trajectory/sessions/{sid}", "bad.token")
    check("坏 token 401", code == 401)

    # 清痕：探针会话/事件 SQL 清（含 fork 克隆行）+ enterprise 用户 + nest 租户
    traj_db = str(__import__("pathlib").Path.home() / "opensoul" / "data" / "opensoul.db")
    con = sqlite3.connect(traj_db)
    con.execute("DELETE FROM trajectory_events WHERE session_id IN (SELECT id FROM trajectory_sessions WHERE task_description LIKE ?)", ("%" + MARK + "%",))
    if forked_id:
        con.execute("DELETE FROM trajectory_events WHERE session_id = ?", (forked_id,))
        con.execute("DELETE FROM trajectory_sessions WHERE id = ?", (forked_id,))
    con.execute("DELETE FROM trajectory_sessions WHERE task_description LIKE ?", ("%" + MARK + "%",))
    con.commit()
    left_sess = con.execute(
        "SELECT COUNT(*) FROM trajectory_sessions WHERE task_description LIKE ?", (MARK + "%",)
    ).fetchone()[0]
    left_ev = con.execute(
        "SELECT COUNT(*) FROM trajectory_events WHERE content LIKE ? OR content LIKE ?",
        ("%" + SECRET + "%", "%e2e-trajbyid%"),
    ).fetchone()[0]
    con.close()
    check("探针会话/事件清痕", left_sess == 0 and left_ev == 0, f"sess={left_sess} ev={left_ev}")

    ent_db = str(__import__("pathlib").Path.home() / "opensoul" / "data" / "enterprise.db")
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
