#!/usr/bin/env python3
"""F3b-⑨b-③-1c 边界② live e2e 探针：link_gateway WS relay 广播消息路由隔离。

跑法：python3 scripts/e2e_link_ws_relay_tenant.py（服务 :8090 已部署本拍代码）。
修前/修后同探针：旧进程先跑（跨租户真收到=FAIL 修前硬证），重启部署新代码后
再跑（全 PASS）。传输级证据=真 websockets 客户端：同租户正对照先收到（证明
广播循环真跑），再断言跨租户 2s 零帧。自清痕：WS 全关、租户/用户/timeline
探针行全清，remaining=0 才算过。
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
MARK = "e2e-relay-" + str(int(time.time()))[-6:]
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


class Conn:
    """真 websockets 后台排空线程（收帧进 queue，负断言=queue 超时零帧）。"""

    def __init__(self, token):
        url = WS_BASE + ("?token=" + token if token else "")
        self.ws = ws_connect(url, open_timeout=10)
        self.q = queue.Queue()
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        try:
            for msg in self.ws:
                self.q.put(msg)
        except Exception:
            pass

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


def tl_cleanup(db_path, baseline_rowid, needles):
    def hit(summary, detail):
        blob = (summary or "") + "|" + (detail or "")
        return any(n in blob for n in needles)

    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT rowid, event_id, summary, detail FROM events WHERE rowid > ?",
            (baseline_rowid,),
        ).fetchall()
        targets = [r for r in rows if hit(r[2], r[3])]
        for r in targets:
            con.execute("DELETE FROM events WHERE event_id = ?", (r[1],))
        con.commit()
        after = con.execute(
            "SELECT summary, detail FROM events WHERE rowid > ?", (baseline_rowid,)
        ).fetchall()
        remaining = sum(1 for r in after if hit(r[0], r[1]))
        return remaining, len(targets), len(after)
    finally:
        con.close()


def main():
    code, health = jreq("GET", "/api/health")
    check("health 200", code == 200, str(health.get("status")))

    tl_db = str(Path.home() / "opensoul" / "data" / "timeline.db")
    con = sqlite3.connect(tl_db)
    tl_base = con.execute("SELECT COALESCE(MAX(rowid),0) FROM events").fetchone()[0]
    con.close()

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

    conns = []
    try:
        a1, a2 = Conn(tokens["a"]), Conn(tokens["a"])
        b1, b2 = Conn(tokens["b"]), Conn(tokens["b"])
        n1, n2 = Conn(None), Conn(None)
        conns = [a1, a2, b1, b2, n1, n2]

        # ① 同租户正对照=广播循环真跑
        m1 = f"{MARK}-same"
        a1.send({"raw": m1})
        frame = a2.frame(timeout=5.0)
        check(
            "① 同租户 relay 真投递（A2 收到 A1）",
            frame.get("from") == "relay" and m1 in json.dumps(frame.get("data", {}), ensure_ascii=False),
            str(frame)[:120],
        )

        # ② 跨租户零投递（正对照已到=同迭代投递已跑，B1/B2 必须零帧）
        ok_b1, got_b1 = b1.silent(2.0)
        ok_b2, got_b2 = b2.silent(2.0)
        check("② 跨租户零投递（B1/B2 收不到 A 的广播）", ok_b1 and ok_b2, f"b1={got_b1} b2={got_b2}")

        # ③ 反向同款：B 广播只走 tenant-b
        m2 = f"{MARK}-reverse"
        b1.send({"raw": m2})
        frame2 = b2.frame(timeout=5.0)
        check(
            "③ 反向同租户正对照（B2 收到 B1）",
            m2 in json.dumps(frame2.get("data", {}), ensure_ascii=False),
            str(frame2)[:120],
        )
        ok_a1, got_a1 = a1.silent(2.0)
        ok_a2b, got_a2b = a2.silent(2.0)
        check("③ 反向跨租户零投递（A1/A2 收不到 B 的广播）", ok_a1 and ok_a2b, f"a1={got_a1} a2={got_a2b}")

        # ④ 匿名面契约不变（'default' 内部照旧互通）
        m3 = f"{MARK}-anon"
        n1.send({"raw": m3})
        frame3 = n2.frame(timeout=5.0)
        check(
            "④ 匿名面内部 relay 契约不变（N2 收到 N1）",
            m3 in json.dumps(frame3.get("data", {}), ensure_ascii=False),
            str(frame3)[:120],
        )
        ok_a1b, got_a1b = a1.silent(2.0)
        check("④ 匿名广播不投命名租户（A1 零收）", ok_a1b, str(got_a1b)[:120])

        # ⑤ 命名租户广播不投匿名
        m4 = f"{MARK}-named"
        a1.send({"raw": m4})
        frame4 = a2.frame(timeout=5.0)
        check("⑤ 命名同租户正对照", m4 in json.dumps(frame4.get("data", {}), ensure_ascii=False), str(frame4)[:120])
        ok_n1, got_n1 = n1.silent(2.0)
        ok_n2, got_n2 = n2.silent(2.0)
        check("⑤ 命名广播不投匿名（N1/N2 零收）", ok_n1 and ok_n2, f"n1={got_n1} n2={got_n2}")

        # ⑥ 帧形不变
        check(
            "⑥ relay 帧形恒 {from,data,timestamp}",
            set(frame.keys()) == {"from", "data", "timestamp"} and frame["from"] == "relay",
            str(sorted(frame.keys())),
        )
    finally:
        for c in conns:
            c.close()

        # ⑦ 坏 token 握手拒绝（1c 语义零变化）
        try:
            bad = Conn("not-a-jwt")
            bad.close()
            check("⑦ 坏 token 握手拒绝", False, "连接竟然成功")
        except Exception as exc:
            check("⑦ 坏 token 握手拒绝", True, type(exc).__name__)

        # 清痕
        for tag in ("a", "b"):
            code, _ = jreq("DELETE", f"/api/nest/tenants/{tenants[tag]}")
            check(f"租户 {tag} 清痕", code in (200, 204, 404), str(code))

        ent_db = str(Path.home() / "opensoul" / "data" / "enterprise.db")
        con = sqlite3.connect(ent_db)
        for tag in ("a", "b"):
            con.execute("DELETE FROM users WHERE username = ?", (f"{MARK}-user-{tag}",))
        con.commit()
        left = con.execute("SELECT COUNT(*) FROM users WHERE username LIKE ?", (MARK + "%",)).fetchone()[0]
        con.close()
        check("enterprise 用户清痕", left == 0, f"remaining={left}")

        remaining, touched, window_rows = tl_cleanup(
            tl_db,
            tl_base,
            [MARK, "ws_message: ws-client", "ws_connected: ws-client", "WebSocket message received"],
        )
        check(
            "timeline 探针行清痕",
            remaining == 0,
            f"touched={touched} remaining={remaining} window={window_rows}",
        )

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n=== {len(RESULTS) - len(failed)}/{len(RESULTS)} PASS ===")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
