#!/usr/bin/env python3
"""CD-⑥ 修复候选③ live UI e2e：traces 页 events 端点改指真端点 + 消费键收编（真浏览器 CDP 点击流）。

真数据全程（不打形状桩）：真 POST /api/trajectory/sessions + events 攒真轨迹行
→ 真 GET /api/trajectory/sessions{,/{id}/events} 真源形状 → /admin/traces.html
真渲染真点击流：
P1 会话行真渲染 session.id（修前消费 session_id 恒 undefined=React key 全空）。
P2 会话行事件数=真源 total_events（修前 event_count 恒 undefined）。
P3 展开会话→事件真渲染（真源 /sessions/{id}/events 真端点；修前 data.events
   打在无 events 键的会话详情端点上=恒空列表）：事件内容/类型徽章/时间可见。
P4 Network 域真请求观测：真端点 /sessions/{id}/events 恰 1 次、旧详情端点
   （sessions/{id} 无 /events 尾缀）0 次（请求未观测=FAIL 禁空过假阳性）。
P5 事件时间=真源 created_at（修前 evt.timestamp 恒 undefined）：当期日期渲染。
P6 Agent 徽章=真源 agent_id 单值真渲染。
P7 全页 'undefined' 零出现（修前多处恒 undefined 渲染）。
用法：python3 scripts/e2e_cd6_traces_events_ui.py [--pre|--post]（--pre=换旧
bundle 同探针跑修前缺口硬证，与 --post 双向对照；缺省=--post）。
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
PORT = 9466
DB = "/home/climbing/opensoul/data/opensoul.db"

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


def call(method: str, path: str, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else {}


def make_user_token() -> tuple[str, str]:
    """真 JWT = src.services.auth.create_access_token 服务端同钥签名（既有 e2e 先例）。"""
    sys.path.insert(0, "/home/climbing/opensoul")
    from src.services.auth import create_access_token  # noqa: E402

    db = sqlite3.connect(DB)
    row = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()
    db.close()
    if not row:
        raise RuntimeError("admin user row missing")
    uid = str(row[0])
    return create_access_token(uuid.UUID(uid)), uid


class CDP:
    def __init__(self, ws_url: str):
        import websocket

        self.ws = websocket.create_connection(ws_url, timeout=10)
        self.mid = 0
        self.events: list[dict] = []

    def send(self, method: str, params: dict | None = None):
        self.mid += 1
        self.ws.send(json.dumps({"id": self.mid, "method": method, "params": params or {}}))
        return self.mid

    def result(self, mid: int, deadline: float = 10.0):
        end = time.time() + deadline
        while time.time() < end:
            self.ws.settimeout(max(0.1, end - time.time()))
            try:
                msg = json.loads(self.ws.recv())
            except Exception:
                continue
            if msg.get("id") == mid:
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)
        return {}

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self.ws.settimeout(max(0.1, min(0.5, end - time.time())))
            try:
                msg = json.loads(self.ws.recv())
            except Exception:
                continue
            if "method" in msg:
                self.events.append(msg)

    def js(self, expr: str):
        mid = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        res = self.result(mid)
        return res.get("result", {}).get("value")

    def wait_js(self, expr: str, seconds: float = 20.0):
        end = time.time() + seconds
        while time.time() < end:
            self.pump(0.3)
            val = self.js(expr)
            if val:
                return val
        return None

    def click(self, find_js: str, seconds: float = 12.0) -> bool:
        expr = f"(() => {{ const el = {find_js}; if (el) {{ el.click(); return true; }} return false; }})()"
        end = time.time() + seconds
        while time.time() < end:
            self.pump(0.3)
            if self.js(expr) is True:
                return True
        return False

    def add_init_script(self, source: str) -> None:
        self.send("Page.addScriptToEvaluateOnNewDocument", {"source": source})

    def navigate(self, url: str, wait: float = 3.0) -> None:
        self.send("Page.navigate", {"url": url})
        self.pump(wait)

    def requests(self) -> list[str]:
        urls = []
        for e in self.events:
            if e.get("method") == "Network.requestWillBeSent":
                urls.append(e.get("params", {}).get("request", {}).get("url", ""))
        return urls


def main() -> int:
    label = "--post"
    if len(sys.argv) > 1:
        label = sys.argv[1]
    print(f"== e2e_cd6_traces_events_ui ({label}) ==")
    token, uid = make_user_token()
    seed_ids: list[str] = []

    # ── 真数据：真轨迹行（3 事件会话 + 2 事件会话，内容/agent 可辨识）──
    try:
        s1 = call("POST", "/api/trajectory/sessions", token=token,
                  body={"agent_id": "cd6-agent-alpha", "task_description": "cd6 e2e probe s1"})
        s2 = call("POST", "/api/trajectory/sessions", token=token,
                  body={"agent_id": "cd6-agent-beta", "task_description": "cd6 e2e probe s2"})
        seed_ids = [s1["id"], s2["id"]]
        for sid, n in ((s1["id"], 3), (s2["id"], 2)):
            for i in range(n):
                call("POST", f"/api/trajectory/sessions/{sid}/events", token=token,
                     body={"event_type": "tool_call", "agent_id": "cd6-agent-alpha",
                           "content": f"cd6-probe-event-{sid[:8]}-{i}"})
        check(True, "setup: seeded 2 real trajectory sessions (3+2 events)")
    except Exception as e:
        check(False, "setup: seed trajectory data", str(e))
        return 1

    try:
        # ── API 真源形状（行为桥 live 面）──
        lst = call("GET", "/api/trajectory/sessions?limit=200", token=token)
        mine = [s for s in lst.get("sessions", []) if s.get("id") in seed_ids]
        check(len(mine) == 2, "setup: true-source list has seeded sessions",
              f"{len(mine)}/2 total={lst.get('total')}")
        el = mine[0] if mine else {}
        check(all(k in el for k in ("id", "total_events", "created_at", "ended_at", "agent_id")),
              "setup: true-source session element keys {id,total_events,created_at,ended_at,agent_id}",
              json.dumps(sorted(el.keys())))

        ev = call("GET", f"/api/trajectory/sessions/{seed_ids[0]}/events", token=token)
        check(set(ev) == {"session_id", "events", "count"} and ev.get("count") == 3,
              "setup: true-source events endpoint {session_id,events,count}",
              json.dumps({k: (v if k != "events" else len(v)) for k, v in ev.items()}))
        check("created_at" in (ev.get("events") or [{}])[0],
              "setup: true-source event element has created_at")

        detail = call("GET", f"/api/trajectory/sessions/{seed_ids[0]}", token=token)
        check("events" not in detail,
              "setup: old detail endpoint truly has NO events key (pre-fix root cause)")

        user_data = tempfile.mkdtemp(prefix="p")
        proc = subprocess.Popen(
            ["/usr/bin/chromium", "--headless=new", f"--remote-debugging-port={PORT}",
             "--remote-allow-origins=*", "--no-sandbox", "--disable-gpu",
             f"--user-data-dir={user_data}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            ws_url = None
            for _ in range(40):
                time.sleep(0.5)
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=3) as r:
                        pages = json.loads(r.read().decode())
                    for p in pages:
                        if p.get("type") == "page":
                            ws_url = p["webSocketDebuggerUrl"]
                            break
                except Exception:
                    continue
                if ws_url:
                    break
            check(bool(ws_url), "setup: chromium CDP page target", ws_url or "none")
            if not ws_url:
                return 1

            cdp = CDP(ws_url)
            cdp.send("Page.enable")
            cdp.send("Network.enable")
            cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})  # 防缓存假阴性
            init = (
                "try { localStorage.setItem('soul_token', "
                + json.dumps(token)
                + "); } catch (e) {}"
            )
            cdp.result(cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": init}))

            cdp.navigate(f"{BASE}/admin/traces.html", 4.0)

            def body_text() -> str:
                return cdp.js("document.body.innerText") or ""

            # 鉴权注入竞态兜底（首轮实测 login wall 假阴）：token 缺席→重注入+reload
            if not cdp.js("localStorage.getItem('soul_token')"):
                cdp.result(cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": init}))
                cdp.js(f"localStorage.setItem('soul_token', {json.dumps(token)})")
                cdp.navigate(f"{BASE}/admin/traces.html", 4.0)

            loaded = cdp.wait_js(
                "(() => { const t = document.body.innerText;"
                " return t.includes('总会话轨迹') && !t.includes('加载中...'); })()",
                25.0,
            )
            t = body_text()
            # P0 探针不瞎：登录墙在场=后续断言全部无效（首轮 login wall 假阴逼出）
            if "总会话轨迹" not in t or "登录" in t[:60]:
                check(False, "P0: admin page authenticated (probe not blind on login wall)",
                      repr(t[:120]))
                return 1
            check(True, "P0: admin page authenticated (probe not blind on login wall)")
            if not loaded:
                print("DEBUG wait_js not loaded; body len:", len(t), "snippet:", repr(t[:300]))

            # ── P1 会话行真渲染 session.id ──
            p1_ok = all(sid in t for sid in seed_ids)
            check(p1_ok, "P1: session rows render true-source session.id (pre-fix session_id=undefined)",
                  f"{sum(1 for s in seed_ids if s in t)}/2")

            # ── P2 会话行事件数=真源 total_events ──
            exp_s1 = next((s for s in mine if s["id"] == seed_ids[0]), {})
            p2_ok = f"{exp_s1.get('total_events', -1)} 事件" in t
            check(p2_ok, "P2: session row renders true-source total_events 事件",
                  f"expect {exp_s1.get('total_events')} 事件")

            # ── P3 展开会话→事件真渲染（真端点）──
            clicked = cdp.click(
                "[...document.querySelectorAll('button')]"
                f".find(b => b.textContent.includes({json.dumps(seed_ids[0])}))"
            )
            check(clicked, "P3a: click session row to expand")
            ev_visible = cdp.wait_js(
                f"(() => document.body.innerText.includes('cd6-probe-event-{seed_ids[0][:8]}-0'))()",
                20.0,
            )
            t2 = body_text()
            check(bool(ev_visible), "P3b: expanded events render real event content (pre-fix恒空)",
                  f"seeded contents found: {sum(1 for i in range(3) if f'cd6-probe-event-{seed_ids[0][:8]}-{i}' in t2)}/3")
            check("工具调用" in t2, "P3c: event type badge renders (tool_call→工具调用)")
            check("暂无事件数据" not in t2, "P3d: zero '暂无事件数据' empty state (pre-fix always empty)")

            # ── P4 Network 域真请求观测 ──
            cdp.pump(1.0)
            urls = cdp.requests()
            traj = [u for u in urls if "/api/trajectory/sessions" in u and "/events" not in u.split("?")[0]]
            ev_reqs = [u for u in urls if u.split("?")[0].endswith(f"/api/trajectory/sessions/{seed_ids[0]}/events")]
            detail_reqs = [u for u in urls if u.split("?")[0].endswith(f"/api/trajectory/sessions/{seed_ids[0]}")]
            check(len(ev_reqs) == 1, "P4a: Network observed true endpoint /sessions/{id}/events exactly once",
                  f"n={len(ev_reqs)}")
            check(len(detail_reqs) == 0,
                  "P4b: Network observed ZERO calls to old detail endpoint (no /events tail)",
                  f"n={len(detail_reqs)} urls={detail_reqs[:2]}")
            check(bool(traj), "P4c: trajectory requests observed at all (probe not blind)", f"n={len(traj)}")

            # ── P5 事件时间=真源 created_at（当期日期渲染，非 undefined）──
            now_md = time.strftime("%m/%d")
            check(now_md in t2, "P5: event timestamps render from true-source created_at",
                  f"expect {now_md}")

            # ── P6 Agent 徽章=真源 agent_id 单值 ──
            check("cd6-agent-alpha" in t2 or "cd6-agent-beta" in t,
                  "P6: agent badge renders true-source agent_id")

            # ── P7 全页 'undefined' 零出现 ──
            t3 = body_text()
            check("undefined" not in t3, "P7: zero 'undefined' renders on traces page",
                  f"count={t3.count('undefined')}")
        finally:
            proc.terminate()
    finally:
        # ── 清痕：只删本探针 seeded 行，回读 remaining=0 ──
        try:
            for sid in seed_ids:
                call("DELETE", f"/api/trajectory/sessions/{sid}", token=token)
            lst = call("GET", "/api/trajectory/sessions?limit=200", token=token)
            remaining = [s["id"] for s in lst.get("sessions", []) if s.get("id") in seed_ids]
            check(remaining == [], "cleanup: seeded sessions deleted, remaining=0", str(remaining))
        except Exception as e:
            check(False, "cleanup: delete seeded sessions", str(e))

    failed = [n for ok, n in RESULTS if not ok]
    print(f"\n== {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ({label}) ==")
    if failed:
        print("FAILED:", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
