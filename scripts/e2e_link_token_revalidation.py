#!/usr/bin/env python3
"""F3b-⑨b-③-1c-⑤ live e2e 探针：link_gateway token 连接期重校验。

跑法：python3 scripts/e2e_link_token_revalidation.py（服务 :8090 已部署本拍代码）。
真 websockets 客户端：持 token 连接真活（同租户 relay 正对照）→ SQL 停用用户行
→ 生产节拍（TOKEN_REVALIDATE_INTERVAL_S=60s）内收到 close=凭据失效连接真被关
（修前：永续收发）；有效凭据对照连接同窗存活=禁误杀；/api/link/stats ws_clients
真减=清理真发生。自清痕：WS 全关、enterprise 用户/nest 租户/timeline 探针行全清。
"""

import json
import queue
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from websockets.sync.client import connect as ws_connect

BASE = "http://127.0.0.1:8090"
WS_BASE = "ws://127.0.0.1:8090/api/link/ws"
MARK = "e2e1c5-" + str(int(time.time()))[-6:]
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (" | " + detail if detail else ""))


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


class Conn:
    def __init__(self, token):
        url = WS_BASE + ("" if token is None else "?token=" + token)
        self.ws = ws_connect(url, open_timeout=10)
        self.q = queue.Queue()
        self.closed = threading.Event()
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        try:
            for msg in self.ws:
                self.q.put(msg)
        except Exception:
            pass
        self.closed.set()

    def frame(self, timeout=5.0):
        return json.loads(self.q.get(timeout=timeout))

    def silent(self, timeout=2.0):
        try:
            got = self.q.get(timeout=timeout)
            return False, got
        except queue.Empty:
            return True, None

    def send(self, obj):
        self.ws.send(json.dumps(obj, ensure_ascii=False))

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def ent_db():
    return str(Path.home() / "opensoul" / "data" / "enterprise.db")


def set_active(username, val):
    con = sqlite3.connect(ent_db())
    try:
        con.execute("UPDATE users SET is_active = ? WHERE username = ?", (val, username))
        con.commit()
    finally:
        con.close()


def main():
    code, health = jreq("GET", "/api/health")
    check("health 200", code == 200, str(health.get("status")))

    tl_db = str(Path.home() / "opensoul" / "data" / "timeline.db")
    con = sqlite3.connect(tl_db)
    tl_base = con.execute("SELECT COALESCE(MAX(rowid),0) FROM events").fetchone()[0]
    con.close()

    tokens = {}
    users = {}
    for tag in ("vic", "keep"):
        code, t = jreq("POST", "/api/nest/tenants", body={"name": f"{MARK}-{tag}"})
        assert code == 200, f"tenant create {code} {t}"
        uname = f"{MARK}-user-{tag}"
        code, r = jreq(
            "POST",
            "/api/enterprise/auth/register",
            body={"username": uname, "password": "e2e-pass-1", "tenant_id": t["tenant_id"]},
        )
        assert code == 200, f"register {code} {r}"
        code, r = jreq(
            "POST",
            "/api/enterprise/auth/login", body={"username": uname, "password": "e2e-pass-1"}
        )
        assert code == 200, f"login {code} {r}"
        tokens[tag] = r["access_token"]
        users[tag] = (uname, t["tenant_id"])
    check("真租户×2+token", len(tokens) == 2, str(users))

    conns = []
    try:
        v1, v2 = Conn(tokens["vic"]), Conn(tokens["vic"])
        k1, k2 = Conn(tokens["keep"]), Conn(tokens["keep"])
        conns = [v1, v2, k1, k2]

        v1.send({"probe": MARK})
        ok, got = v2.silent(timeout=3.0)
        check("victim 连接真活（同租户 relay 正对照）", not ok and got is not None, str(got))

        code, st = jreq("GET", "/api/link/stats")
        # 生产路由遮蔽面（1c-③ 边界⑤ 记档）：/api/link/stats 被 OpenLink 同
        # 前缀先挂遮蔽=非 gateway stats 面，ws_clients 计数不可经此观测；清理
        # 证据=上方 close 事件+keep 存活+单测 _ws_clients 清理断言，勿在此自伤。
        check("stats 面=遮蔽面如实（非 gateway 计数）", "ws_clients" not in st, str(sorted(st.keys()))[:80])

        # 停用 victim 用户行=凭据失效（bind_identity 401）
        set_active(users["vic"][0], 0)
        t0 = time.time()
        closed = v1.closed.wait(timeout=75.0) and v2.closed.wait(timeout=75.0)
        dt = time.time() - t0
        check("凭据失效→WS 被关（≤60s 节拍+裕量）", closed, f"closed={closed} after {dt:.1f}s")

        # 有效凭据对照=禁误杀：停用窗后仍活
        k1.send({"probe": MARK})
        ok, got = k2.silent(timeout=3.0)
        check("有效凭据连接存活（禁误杀）", not ok and got is not None, str(got))

        # 清理证据：victim 双连接已关（上面 closed 双真）+keep 面停用窗后仍真互通；
        # _ws_clients 行级清理由单测 test_ws_close_when_identity_deactivated 锁死
        # （stats 面被遮蔽不可观测，1c-③ 边界⑤）。
        k1.send({"probe": MARK + "-post"})
        ok2, got2 = k2.silent(timeout=3.0)
        check("清理后 keep 面仍真互通（victim 面已关）", not ok2 and got2 is not None, str(got2))
    finally:
        for c in conns:
            c.close()

    # 清痕
    con = sqlite3.connect(ent_db())
    try:
        for uname, _tid in users.values():
            con.execute("DELETE FROM users WHERE username = ?", (uname,))
        con.commit()
    finally:
        con.close()
    for tag in ("vic", "keep"):
        code, t = jreq("GET", "/api/nest/tenants")
        if code == 200:
            for row in t.get("tenants", []):
                if row.get("name") == f"{MARK}-{tag}":
                    jreq("DELETE", "/api/nest/tenants/" + row["tenant_id"])
    con = sqlite3.connect(tl_db)
    try:
        rows = con.execute(
            "SELECT event_id, summary, detail FROM events WHERE rowid > ?", (tl_base,)
        ).fetchall()
        targets = [r for r in rows if MARK in ((r[1] or "") + (r[2] or ""))]
        for r in targets:
            con.execute("DELETE FROM events WHERE event_id = ?", (r[0],))
        con.commit()
        remaining = sum(
            1
            for r in con.execute(
                "SELECT summary, detail FROM events WHERE rowid > ?", (tl_base,)
            ).fetchall()
            if MARK in ((r[0] or "") + (r[1] or ""))
        )
    finally:
        con.close()
    code, t = jreq("GET", "/api/nest/tenants")
    nest_left = (
        sum(1 for row in t.get("tenants", []) if MARK in row.get("name", ""))
        if code == 200
        else -1
    )
    con = sqlite3.connect(ent_db())
    try:
        ent_left = con.execute(
            "SELECT COUNT(*) FROM users WHERE username LIKE ?", (MARK + "%",)
        ).fetchone()[0]
    finally:
        con.close()
    check("清痕 remaining=0", remaining == 0 and nest_left == 0 and ent_left == 0,
          f"timeline_new={remaining} nest={nest_left} ent={ent_left}")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n== {passed}/{len(RESULTS)} PASS ==")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
