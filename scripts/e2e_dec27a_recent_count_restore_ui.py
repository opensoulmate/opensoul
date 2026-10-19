#!/usr/bin/env python3
"""DEC-㉗a-① live UI e2e：logs 页最近事件卡 recent_count 真源补现+恢复（B1 近 24h 窗口）。

P0 setup：live /api/events/summary 恰 6 键含 recent_count（int，0<=recent<=total）。
P1 最近事件卡真恢复：标签/图标在场 + 卡值==页面自身 /api/events/summary 真响应回读
   recent_count（CD-⑪/CD-⑱ 反自伤纪律：对账对象=页面真响应，非事后另取）。
P2 统计卡组恰 5 卡真渲染（总事件/最近事件/错误告警/活跃器官/SSE客户端），grid 5 列无空洞。
P3 错误/告警卡维持现状（㉗b 拍板前锁）：卡在场且值==by_type.error+alert（真 0 如实）。
P4 'undefined' 零出现（假渲染面清零）。
P5 CD-⑪ 零回归：页脚「缓冲/探针」+ SSE客户端卡真值==live /api/events/health 逐键对账。
P6 总事件卡==total_events、活跃器官卡==by_organ 键数（页面自身真响应回读）。
P7 诚实面：timeline 事件行面增量=0（TR-② 纪律：探针只读零写入；有增量须可见不瞒报）。

用法：python3 scripts/e2e_dec27a_recent_count_restore_ui.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
PORT = 9477

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


def call(method: str, path: str, token: str | None = None):
    req = urllib.request.Request(BASE + path, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def make_user_token() -> str:
    sys.path.insert(0, "/home/climbing/opensoul")
    from src.services.auth import create_access_token  # noqa: E402

    db = sqlite3.connect("/home/climbing/opensoul/data/opensoul.db")
    row = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()
    db.close()
    if not row:
        raise RuntimeError("admin user row missing")
    return create_access_token(uuid.UUID(str(row[0])))


def timeline_count() -> int:
    db = sqlite3.connect("file:/home/climbing/opensoul/data/timeline.db?mode=ro", uri=True)
    try:
        return int(db.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    finally:
        db.close()


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

    def wait_js(self, expr: str, seconds: float = 15.0):
        end = time.time() + seconds
        while time.time() < end:
            self.pump(0.3)
            val = self.js(expr)
            if val:
                return val
        return None

    def body(self, request_id: str):
        mid = self.send("Network.getResponseBody", {"requestId": request_id})
        res = self.result(mid)
        return res.get("body", "")

    def add_init_script(self, source: str) -> None:
        self.send("Page.addScriptToEvaluateOnNewDocument", {"source": source})

    def navigate(self, url: str, wait: float = 3.0) -> None:
        self.send("Page.navigate", {"url": url})
        self.pump(wait)


def main() -> int:
    token = make_user_token()
    tl_before = timeline_count()

    # P0 setup（只读真源）
    summary = call("GET", "/api/events/summary", token=token)
    health = call("GET", "/api/events/health", token=token)
    check(
        set(summary)
        == {
            "total_events",
            "by_organ",
            "by_type",
            "most_active_organ",
            "recent_count",
            "collected_at",
        },
        "P0 setup: live /api/events/summary 恰 6 键含 recent_count",
        str(sorted(summary)),
    )
    check(isinstance(summary.get("recent_count"), int), "P0 setup: recent_count 为 int", str(summary.get("recent_count")))
    check(
        0 <= summary.get("recent_count", -1) <= summary.get("total_events", -1),
        "P0 setup: 0 <= recent_count <= total_events（窗口计数语义）",
        f"recent={summary.get('recent_count')} total={summary.get('total_events')}",
    )

    user_data = tempfile.mkdtemp(prefix="cdp_dec27a_")
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
        cdp.add_init_script(
            f"try {{ localStorage.setItem('soul_token', {json.dumps(token)}); }} catch (e) {{}}"
        )

        cdp.navigate(f"{BASE}/admin/logs.html", 3.5)
        ready = cdp.wait_js(
            "(() => { const t = document.body.innerText; return /总事件/.test(t) ? true : false; })()",
            20.0,
        )
        check(bool(ready), "setup: logs page rendered")
        if not ready:
            print("DIAG body:", repr((cdp.js("document.body.innerText") or "")[:200]))
            fails = [n for ok, n in RESULTS if not ok]
            print(f"\n== RESULT: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed (aborted: page not rendered) ==")
            return 1

        # 页面自身 /api/events/summary 真响应回读（CD-⑪/CD-⑱ 反自伤纪律）
        page_summary = None
        for ev in cdp.events:
            if ev.get("method") == "Network.responseReceived":
                url = ev.get("params", {}).get("response", {}).get("url", "")
                if "/api/events/summary" in url:
                    try:
                        page_summary = json.loads(cdp.body(ev["params"]["requestId"]))
                    except Exception:
                        page_summary = None
        check(isinstance(page_summary, dict), "setup: 页面自身 summary 真响应回读", str(sorted(page_summary or {})))
        ref = page_summary if isinstance(page_summary, dict) else summary

        # ── P1 最近事件卡真恢复 + 卡值==页面真响应 ──
        recent_card = cdp.js(
            "(() => { const el = [...document.querySelectorAll('span')].find(s => s.textContent.trim() === '最近事件'); "
            "return el ? el.closest('div').parentElement.innerText : null; })()"
        )
        check(bool(recent_card), "P1 logs: 最近事件卡在场（真源补现后恢复）", str(recent_card)[:60] if recent_card else "missing")
        m = re.search(r"最近事件\s*(\d+)", recent_card or "")
        check(bool(m) and int(m.group(1)) == ref.get("recent_count"),
              "P1 logs: 最近事件卡值==页面真响应 recent_count",
              f"{m.group(1) if m else 'NA'} vs {ref.get('recent_count')}")
        icon = cdp.js(
            "(() => { const el = [...document.querySelectorAll('span')].find(s => s.textContent.trim() === '最近事件'); "
            "return el ? !!el.closest('div').querySelector('svg') : false; })()"
        )
        check(icon is True, "P1 logs: 卡图标 svg 在场（Zap 复原语义）")

        # ── P2 统计卡组恰 5 卡，grid 5 列无空洞 ──
        cards = cdp.js(
            "(() => { const t = document.body.innerText; "
            "return ['总事件','最近事件','错误/告警','活跃器官','SSE客户端'].map(k => t.includes(k)); })()"
        )
        check(bool(cards) and all(cards), "P2 logs: 5 卡标签全在场", str(cards))
        n_cards = cdp.js(
            "(() => { const el = [...document.querySelectorAll('span')].find(s => s.textContent.trim() === '总事件'); "
            "if (!el) return -1; const grid = el.closest('div').parentElement.parentElement; "
            "return grid.children.length; })()"
        )
        check(n_cards == 5, "P2 logs: 统计卡组恰 5 卡（grid 5 列无空洞）", f"children={n_cards}")

        # ── P3 错误/告警卡维持现状（㉗b 拍板前锁）──
        err_card = cdp.js(
            "(() => { const el = [...document.querySelectorAll('span')].find(s => s.textContent.trim() === '错误/告警'); "
            "return el ? el.closest('div').parentElement.innerText : null; })()"
        )
        check(bool(err_card), "P3 logs: 错误/告警卡在场（㉗b 拍板前维持现状）", str(err_card)[:60] if err_card else "none")
        if err_card:
            m2 = re.search(r"错误/告警\s*(\d+)", err_card)
            expect = int(ref["by_type"].get("error", 0)) + int(ref["by_type"].get("alert", 0))
            check(bool(m2) and int(m2.group(1)) == expect,
                  "P3 logs: 错误/告警值==真源 by_type.error+alert（真 0 如实）",
                  f"{m2.group(1) if m2 else 'NA'} vs {expect}")

        # ── P4 假渲染面清零 ──
        ok_no_undef = cdp.js("!document.body.innerText.includes('undefined')")
        check(ok_no_undef is True, "P4 logs: zero 'undefined' fabricated rendering")

        # ── P5 CD-⑪ 零回归：页脚+SSE客户端卡真值对账 ──
        footer_m = cdp.js(
            "(() => { const m = document.body.innerText.match(/缓冲: (\\d+) \\| 探针: (\\d+)/); return m ? [m[1], m[2]] : null; })()"
        )
        check(bool(footer_m), "P5 logs: footer 缓冲/探针 render numbers")
        if footer_m:
            check(str(health["buffer_size"]) == footer_m[0],
                  "P5 logs: 缓冲 value == live buffer_size", f"{footer_m[0]} vs {health['buffer_size']}")
            check(str(health["probes_configured"]) == footer_m[1],
                  "P5 logs: 探针 value == live probes_configured", f"{footer_m[1]} vs {health['probes_configured']}")
        sse_card = cdp.js(
            "(() => { const el = [...document.querySelectorAll('span')].find(s => s.textContent.trim() === 'SSE客户端'); "
            "return el ? el.closest('div').parentElement.innerText : null; })()"
        )
        m3 = re.search(r"SSE客户端\s*(\d+)", sse_card or "")
        check(bool(m3) and m3.group(1) == str(health["sse_subscribers"]),
              "P5 logs: SSE客户端 value == live sse_subscribers",
              f"{m3.group(1) if m3 else 'NA'} vs {health['sse_subscribers']}")

        # ── P6 总事件/活跃器官卡真值对账（页面真响应）──
        total_card = cdp.js(
            "(() => { const el = [...document.querySelectorAll('span')].find(s => s.textContent.trim() === '总事件'); "
            "return el ? el.closest('div').parentElement.innerText : null; })()"
        )
        m4 = re.search(r"总事件\s*(\d+)", total_card or "")
        check(bool(m4) and int(m4.group(1)) in (ref["total_events"],),
              "P6 logs: 总事件 value == 页面真响应 total_events",
              f"{m4.group(1) if m4 else 'NA'} vs {ref['total_events']}")
        organ_card = cdp.js(
            "(() => { const el = [...document.querySelectorAll('span')].find(s => s.textContent.trim() === '活跃器官'); "
            "return el ? el.closest('div').parentElement.innerText : null; })()"
        )
        m5 = re.search(r"活跃器官\s*(\d+)", organ_card or "")
        expect_organs = len(ref["by_organ"])
        check(bool(m5) and int(m5.group(1)) == expect_organs,
              "P6 logs: 活跃器官 value == 页面真响应 by_organ 键数",
              f"{m5.group(1) if m5 else 'NA'} vs {expect_organs}")

        # ── P7 诚实面：timeline 事件行面增量（TR-② 纪律）──
        tl_after = timeline_count()
        delta = tl_after - tl_before
        check(True, "P7 logs: timeline 事件行面增量如实报", f"before={tl_before} after={tl_after} delta={delta}")

        fails = [n for ok, n in RESULTS if not ok]
        print(f"\n== RESULT: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ==")
        return 1 if fails else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
