#!/usr/bin/env python3
"""DEC-㉗a-② live render e2e：logs 页最近事件卡回落形收口（摘要缺席='—'，禁 fabricate 0）。

P0 setup（只读真源）：live /api/events/summary 恰 6 键含 recent_count（int，0<=recent<=total）。
P1 正常路径：真浏览器渲染卡值==页面自身 /api/events/summary 真响应回读 recent_count
   （CD-⑪/CD-⑱ 反自伤纪律：对账对象=页面真响应，非事后另取）。
P2 缺席路径（修前面=假 0）：init script 拦截 /api/events/summary fetch 拒绝
   （复现 summary 缺席的真实用户可见故障形态）→ 卡值=='—'（修前 {summary?.recent_count || 0}
   渲染 '0'=fabricate 0 硬反证）。
P3 'undefined' 零出现（假渲染面清零）。
P4 诚实面：timeline 事件行面增量=0（TR-② 纪律：探针只读零写入；有增量须可见不瞒报）。

用法：python3 scripts/e2e_dec27a_fallback_render.py
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
PORT = 9481

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


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


CARD_VALUE_JS = """
(() => {
  const spans = [...document.querySelectorAll('span')];
  const label = spans.find(s => s.textContent.trim() === '\u6700\u8fd1\u4e8b\u4ef6');
  if (!label) return null;
  const card = label.closest('div.rounded-lg') || label.parentElement.parentElement;
  const v = card.querySelector('.text-2xl');
  return v ? v.textContent.trim() : null;
})()
"""

BLOCK_SUMMARY_INIT = """
(() => {
  const orig = window.fetch;
  window.fetch = function(input, init) {
    const url = typeof input === 'string' ? input : ((input && input.url) || '');
    if (url.indexOf('/api/events/summary') >= 0) {
      return Promise.reject(new TypeError('probe-block-summary'));
    }
    return orig.apply(this, arguments);
  };
})();
"""


def main() -> int:
    token = make_user_token()
    tl_before = timeline_count()

    req = urllib.request.Request(BASE + "/api/events/summary", method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=15) as r:
        summary = json.loads(r.read().decode())
    check(
        set(summary)
        == {"total_events", "by_organ", "by_type", "most_active_organ", "recent_count", "collected_at"},
        "P0 setup: live /api/events/summary 恰 6 键含 recent_count",
        str(sorted(summary)),
    )
    check(isinstance(summary.get("recent_count"), int), "P0 setup: recent_count 为 int", str(summary.get("recent_count")))

    user_data = tempfile.mkdtemp(prefix="cdp_fb_")
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

        # ── P1 正常路径：卡值==页面自身真响应 recent_count ──
        cdp.navigate(f"{BASE}/admin/logs.html", 3.5)
        ready = cdp.wait_js(CARD_VALUE_JS, 20.0)
        check(ready is not None, "setup: logs page rendered（最近事件卡可见）", str(ready))
        page_rc = None
        for ev in cdp.events:
            if ev.get("method") == "Network.responseReceived":
                u = ev["params"]["response"]["url"]
                if "/api/events/summary" in u:
                    try:
                        page_rc = json.loads(cdp.body(ev["params"]["requestId"])).get("recent_count")
                    except Exception:
                        pass
        check(page_rc is not None, "P1: 页面自身 /api/events/summary 响应回读", str(page_rc))
        check(
            ready == str(page_rc),
            "P1: 卡值==页面真响应回读 recent_count（真值照显）",
            f"card={ready!r} page={page_rc!r}",
        )

        # ── P2 缺席路径：summary fetch 被拦 → 卡值=='—'（修前='0' fabricate 0）──
        cdp2 = CDP(ws_url)
        cdp2.send("Page.enable")
        cdp2.send("Network.enable")
        cdp2.send("Network.setCacheDisabled", {"cacheDisabled": True})
        cdp2.add_init_script(
            f"try {{ localStorage.setItem('soul_token', {json.dumps(token)}); }} catch (e) {{}}"
        )
        cdp2.add_init_script(BLOCK_SUMMARY_INIT)
        cdp2.navigate(f"{BASE}/admin/logs.html", 3.5)
        val2 = cdp2.wait_js(CARD_VALUE_JS, 20.0)
        check(val2 == "\u2014", "P2: summary 缺席渲染 '—'（修前 '0'=fabricate 0 硬反证）", f"card={val2!r}")

        body_text = cdp2.js("document.body.innerText") or ""
        check("undefined" not in body_text, "P3: 'undefined' 零出现", "")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    tl_after = timeline_count()
    check(
        tl_after == tl_before,
        "P4: timeline 事件行面增量=0（只读探针）",
        f"before={tl_before} after={tl_after}",
    )

    passed = sum(1 for ok, _ in RESULTS if ok)
    print(f"TOTAL: {passed}/{len(RESULTS)}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
