#!/usr/bin/env python3
"""CD-⑪ 修复候选① live UI e2e：logs 页 stream summary 消费键收编真源 /api/events/health。

P1 页脚「缓冲: N | 探针: N」真值渲染（修前 /stream/summary 打错面恒
   「缓冲: undefined | 探针: undefined」假渲染）。
P2 SSE客户端卡真值渲染（修前 sse_clients 恒 `|| 0` 假 0——断言渲染值==live
   /api/events/health sse_subscribers 真值，0 也不放过键名对账）。
P3 'undefined' 零出现（假渲染面清零）。

全真数据零桩零写入（/api/events/health 只读），无清痕面。
用法：python3 scripts/e2e_cd11_logs_health_keys_ui.py
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
PORT = 9471

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

    def add_init_script(self, source: str) -> None:
        self.send("Page.addScriptToEvaluateOnNewDocument", {"source": source})

    def navigate(self, url: str, wait: float = 3.0) -> None:
        self.send("Page.navigate", {"url": url})
        self.pump(wait)


def main() -> int:
    token = make_user_token()

    # live 真源预期（只读）
    health = call("GET", "/api/events/health", token=token)
    for k in ("buffer_size", "probes_configured", "sse_subscribers"):
        check(k in health, f"setup: /api/events/health true-source key {k}", str(health.get(k)))

    user_data = tempfile.mkdtemp(prefix="cdp_cd11_")
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

        # ── P1 页脚真值渲染（修前「缓冲: undefined | 探针: undefined」）──
        ok_footer = cdp.wait_js(
            "(() => { const t = document.body.innerText; return /缓冲: \\d+ \\| 探针: \\d+/.test(t); })()",
            20.0,
        )
        check(bool(ok_footer), "P1 logs: footer 缓冲/探针 render real numbers (not undefined)")
        footer_m = cdp.js(
            "(() => { const m = document.body.innerText.match(/缓冲: (\\d+) \\| 探针: (\\d+)/); return m ? [m[1], m[2]] : null; })()"
        )
        if footer_m:
            check(str(health["buffer_size"]) == footer_m[0],
                  "P1 logs: 缓冲 value == live /api/events/health buffer_size",
                  f"{footer_m[0]} vs {health['buffer_size']}")
            check(str(health["probes_configured"]) == footer_m[1],
                  "P1 logs: 探针 value == live probes_configured",
                  f"{footer_m[1]} vs {health['probes_configured']}")
        else:
            check(False, "P1 logs: footer values extractable")

        # ── P2 SSE客户端卡真值（修前 sse_clients 恒 `|| 0` 假 0）──
        sse_card = cdp.js(
            "(() => { const el = [...document.querySelectorAll('span')].find(s => s.textContent.trim() === 'SSE客户端'); "
            "return el ? el.closest('div').parentElement.innerText : null; })()"
        )
        check(bool(sse_card), "P2 logs: SSE客户端 card present", str(sse_card)[:60] if sse_card else "none")
        if sse_card:
            m = re.search(r"SSE客户端\s*(\d+)", sse_card)
            check(bool(m), "P2 logs: SSE客户端 card renders a number")
            if m:
                check(m.group(1) == str(health["sse_subscribers"]),
                      "P2 logs: SSE客户端 value == live sse_subscribers",
                      f"{m.group(1)} vs {health['sse_subscribers']}")

        # ── P3 假渲染面清零 ──
        ok_no_undef = cdp.js("!document.body.innerText.includes('undefined')")
        check(ok_no_undef is True, "P3 logs: zero 'undefined' fabricated rendering")

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
