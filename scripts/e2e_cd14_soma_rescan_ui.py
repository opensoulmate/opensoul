#!/usr/bin/env python3
"""CD-⑭ 修复候选④ live UI e2e：soma 页「重新扫描」真点击流——方法漂移修复后重扫走真源 GET。

真用户路径（测试铁律②：复现按钮/点击流）：
  /admin/soma.html → 页面加载（fetchScan）→ 点「重新扫描」按钮 → 数据真刷新。

P1 soma 页真渲染（工具栏+「重新扫描」按钮真点击流可达）。
P2 点「重新扫描」恰 1 个新请求打 /api/soma/discovery/scan 且方法==GET
   （修前：POST 恒 405 空转 + catch 兜底 GET 两连发=恰 2 个新请求含 POST）。
P3 点击请求的真源响应 scan_time == 页头「扫描耗时 {n}ms」逐值对账（UI 真消费
   本次响应，非陈旧/假渲染）。
P4 页面零 'undefined' 假渲染、零错误横幅（修前 POST 失败若无兜底即红条）。

真源零改动面：live POST /api/soma/discovery/scan 仍 405（修在 UI 侧）。
全真数据零桩：扫描端点只读不落库，无清痕面。
用法：python3 scripts/e2e_cd14_soma_rescan_ui.py
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
PORT = 9473

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


def call(method: str, path: str):
    req = urllib.request.Request(BASE + path, method=method)
    with urllib.request.urlopen(req, timeout=20) as r:
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


SCAN_URL = "/api/soma/discovery/scan"

CLICK_FN = """
(() => {
  const want = %s;
  const els = [...document.querySelectorAll('button, [role=tab], a')];
  const el = els.find(e => e.textContent.trim() === want && e.offsetParent !== null);
  if (!el) return false;
  el.click();
  return true;
})()
"""


def scan_requests(cdp: CDP) -> list[dict]:
    return [
        e["params"]["request"]
        for e in cdp.events
        if e.get("method") == "Network.requestWillBeSent"
        and SCAN_URL in e.get("params", {}).get("request", {}).get("url", "")
    ]


def main() -> int:
    token = make_user_token()

    # ── 真源零改动面（live）──
    expected = call("GET", SCAN_URL)
    for k in ("scan_time", "processes", "cli_tools", "services", "adapters"):
        check(k in expected, f"setup: true-source key {k}", str(expected.get(k))[:40])
    try:
        call("POST", SCAN_URL)
        check(False, "setup: POST /scan still 405 (truth unchanged)", "got 2xx")
    except urllib.error.HTTPError as e:
        check(e.code == 405, "setup: POST /scan still 405 (truth unchanged)", str(e.code))

    user_data = tempfile.mkdtemp(prefix="p_cd14_")
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

        cdp.navigate(f"{BASE}/admin/soma.html", 4.0)

        # ── P1 页真渲染 + 「重新扫描」按钮可达 ──
        check(bool(cdp.wait_js(CLICK_FN % "'重新扫描'", 15.0)), "P1 soma: click 重新扫描 button")
        cdp.pump(1.0)
        body_text = cdp.js("document.body.innerText") or ""
        check("扫描耗时" in body_text, "P1 soma: toolbar 扫描耗时 renders")

        # 页面加载期 fetchScan 已打 1 发 GET；点击后应恰再 1 发 GET
        before = scan_requests(cdp)
        check(bool(before), "P2 soma: initial fetchScan request observed", str(len(before)))

        # ── P2 点击「重新扫描」→ 恰 1 个新请求且方法==GET ──
        # （按钮在 P1 已点一次——先吃掉那次点击的请求，再点一次取证）
        cdp.pump(1.0)
        mid_click = len(scan_requests(cdp))
        check(bool(cdp.js(CLICK_FN % "'重新扫描'")), "P2 soma: click 重新扫描 (probe round)")
        cdp.pump(3.0)
        after = scan_requests(cdp)
        new_reqs = after[mid_click:]
        check(len(new_reqs) == 1, "P2 soma: exactly 1 new request per click",
              f"new={len(new_reqs)} (pre-repair: 2=POST 405 + fallback GET)")
        for r in new_reqs:
            check(r.get("method") == "GET", "P2 soma: request method == GET", str(r.get("method")))

        # ── P3 点击请求真源响应 scan_time == 页头「扫描耗时 {n}ms」逐值对账 ──
        # 取最后一发（探针轮点击的响应）
        rids = [
            e["params"]["requestId"]
            for e in cdp.events
            if e.get("method") == "Network.responseReceived"
            and SCAN_URL in e.get("params", {}).get("response", {}).get("url", "")
        ]
        body_val = None
        for rid in reversed(rids):
            res = cdp.result(cdp.send("Network.getResponseBody", {"requestId": rid}), 8.0)
            if res.get("body"):
                try:
                    body_val = json.loads(res["body"])
                    break
                except Exception:
                    continue
        check(body_val is not None, "P3 soma: click response body retrieved via CDP")
        if body_val:
            n = round(body_val["scan_time"] * 1000)
            text = cdp.js("document.body.innerText") or ""
            check(f"扫描耗时 {n}ms" in text, "P3 soma: 扫描耗时 == true-source scan_time",
                  f"expect 扫描耗时 {n}ms")
            for k in ("processes", "cli_tools", "services", "adapters"):
                check(k in body_val, f"P3 soma: response key {k}", str(len(body_val[k]))
                      if isinstance(body_val[k], list) else str(body_val[k]))

        # ── P4 零假渲染零错误横幅 ──
        text = cdp.js("document.body.innerText") or ""
        check("undefined" not in text, "P4 soma: zero 'undefined' fabricated rendering")
        err_banner = cdp.js(
            "(() => [...document.querySelectorAll('div')]"
            ".some(d => typeof d.className === 'string' && d.className.includes('border-red-500/30')))()"
        )
        check(not err_banner, "P4 soma: zero error banner (rescan succeeded)")

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
