#!/usr/bin/env python3
"""F3b-⑨b-③-1c-③ live e2e 探针：link_gateway 事件读面（/stream SSE）租户过滤。

跑法：python3 scripts/e2e_link_event_tenant.py（服务 :8090）。
修前/修后同探针：旧代码先跑（跨租户真见=FAIL 修前硬证），重启部署新代码后
再跑（全 PASS）。传输级证据=真 httpx SSE 流：同租户正对照先收到（证明流真
跑），再断言跨租户窗口内零帧。事件可区分=push 事件 source=webhook_id（每
租户独立 webhook_id）。自清痕：租户/用户/timeline 探针行全清，remaining=0。

顺带断言（1c-⑥ 路由遮蔽收口后）：/api/link/gateway/events=真 gateway 读面
（events+total 双键真可达），/api/link/events 归 OpenLink（canonical 归属
回归）——修前 gateway /events 被 OpenLink 同前缀先挂遮蔽=生产死路由。
"""

import json
import queue
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8090"
MARK = "e2e-evt-" + str(int(time.time()))[-6:]
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    label = "INFO " if name.startswith("info") else ("PASS " if ok else "FAIL ")
    print(label + name + (f" | {detail}" if detail else ""))


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


class _Recorder(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


def collect_stream(token=None, header_token=None, budget=5.0, want=()):
    """真 SSE 流收集：满窗口收集（不做正对照提前收窗——零跨断言须满窗才硬），
    返回 (frames, saw_want, status)。"""
    params = {}
    if token:
        params["token"] = token
    headers = {}
    if header_token:
        headers["Authorization"] = "Bearer " + header_token
    frames = []
    deadline = time.time() + budget
    try:
        with httpx.stream(
            "GET",
            BASE + "/api/link/stream",
            params=params,
            headers=headers,
            timeout=httpx.Timeout(5.0, read=max(2.0, deadline - time.time())),
        ) as r:
            if r.status_code != 200:
                return frames, False, r.status_code
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                frames.append(json.loads(line[len("data: "):]))
                if time.time() > deadline:
                    break
    except httpx.ReadTimeout:
        pass
    except Exception as exc:
        return frames, False, type(exc).__name__
    got = {f.get("source") for f in frames}
    return frames, all(w in got for w in want) if want else True, 200


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

    # 出站桩（push 真发 200）
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stub_url = f"http://127.0.0.1:{server.server_address[1]}/hook"

    wh_ids = {}
    try:
        # 每面一个 webhook + 一次 push（事件 source=webhook_id 可区分）
        for tag, tok in (("a", tokens["a"]), ("b", tokens["b"]), ("d", None)):
            code, r = jreq(
                "POST",
                "/api/link/webhooks",
                token=tok,
                body={"name": f"{MARK}-{tag}", "direction": "out", "url": stub_url},
            )
            assert code == 200, f"webhook {tag} {code} {r}"
            wh_ids[tag] = r["webhook_id"]
            code, r = jreq(
                "POST",
                "/api/link/push",
                token=tok,
                body={"webhook_id": wh_ids[tag], "payload": {"marker": f"{MARK}-{tag}"}},
            )
            assert code == 200 and r.get("status") == "ok", f"push {tag} {code} {r}"
        check("三面事件已生成（push ok）", len(wh_ids) == 3, str(wh_ids))

        # 1c-⑥ 路由遮蔽收口：gateway 读面真可达（修前被 OpenLink 遮蔽=死路由）
        code, r = jreq("GET", "/api/link/gateway/events?limit=5")
        check(
            "gateway /events 读面生产可达（events+total 双键）",
            code == 200 and set(r.keys()) == {"events", "total"},
            f"code={code} keys={sorted(r.keys())}",
        )
        code, r2 = jreq("GET", "/api/link/events?limit=5")
        check(
            "/api/link/events 归 OpenLink（canonical 归属回归）",
            "total" not in r2,
            f"code={code} keys={sorted(r2.keys())}",
        )

        # ① 同租户正对照（A 流真收到 A 事件）+ 跨租户零帧
        frames, saw, status = collect_stream(
            token=tokens["a"], budget=6.0, want=(wh_ids["a"],)
        )
        got = {f.get("source") for f in frames}
        check("① A 流收到 A 事件（同租户正对照=流真跑）", saw, f"got={sorted(x for x in got if x)}")
        check(
            "① A 流零跨租户帧（B/D 事件不得出现）",
            wh_ids["b"] not in got and wh_ids["d"] not in got,
            f"got={sorted(x for x in got if x)}",
        )

        # ② B 流同款反向
        frames_b, saw_b, _ = collect_stream(
            token=tokens["b"], budget=6.0, want=(wh_ids["b"],)
        )
        got_b = {f.get("source") for f in frames_b}
        check("② B 流收到 B 事件", saw_b, f"got={sorted(x for x in got_b if x)}")
        check(
            "② B 流零跨租户帧（A/D 事件不得出现）",
            wh_ids["a"] not in got_b and wh_ids["d"] not in got_b,
            f"got={sorted(x for x in got_b if x)}",
        )

        # ③ 匿名流=default 面（只 D）
        frames_d, saw_d, _ = collect_stream(budget=6.0, want=(wh_ids["d"],))
        got_d = {f.get("source") for f in frames_d}
        check("③ 匿名流收到 D 事件（'default' 面契约不变）", saw_d, f"got={sorted(x for x in got_d if x)}")
        check(
            "③ 匿名流零命名租户帧",
            wh_ids["a"] not in got_d and wh_ids["b"] not in got_d,
            f"got={sorted(x for x in got_d if x)}",
        )

        # ④ 双源取参：Authorization 头通道同款
        frames_h, saw_h, _ = collect_stream(
            header_token=tokens["a"], budget=6.0, want=(wh_ids["a"],)
        )
        got_h = {f.get("source") for f in frames_h}
        check("④ 头通道真派生（A 事件收到）", saw_h, f"got={sorted(x for x in got_h if x)}")
        check(
            "④ 头通道零跨租户帧",
            wh_ids["b"] not in got_h and wh_ids["d"] not in got_h,
            f"got={sorted(x for x in got_h if x)}",
        )

        # ⑤ 坏 token 401（身份证明先于开流）
        frames_x, _, status_x = collect_stream(token="not-a-jwt", budget=3.0)
        check("⑤ 坏 token 401 零帧", status_x == 401 and not frames_x, f"status={status_x} frames={len(frames_x)}")

        # ⑥ 帧形不变
        if frames:
            check(
                "⑥ SSE 帧形恒 6 键",
                set(frames[0].keys())
                == {"event_id", "timestamp", "source", "direction", "event_type", "status"},
                str(sorted(frames[0].keys())),
            )
        else:
            check("⑥ SSE 帧形恒 6 键", False, "无帧可验")
    finally:
        server.shutdown()
        server.server_close()

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
            [MARK] + [f"webhook_sent: {w}" for w in wh_ids.values()]
            + ["webhook_registered: gateway"],
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
