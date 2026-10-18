#!/usr/bin/env python3
"""CD-⑬ 修复候选③ live UI e2e：hippo 页「模拟衰减」真点击流——方法漂移修复后模拟面板真渲染。

真用户路径（测试铁律②：复现按钮/点击流）：
  /admin/hippo.html → 点「衰减」tab → 点「模拟衰减」按钮 → 对话框点「模拟」→
  「模拟结果」面板渲染。

P1 「模拟衰减」对话框可开（按钮真点击流可达）。
P2 点「模拟」后面板真渲染（修前 apiFetch 缺省 GET 恒 405 → catch → setError，
   simResult 永不置位=面板永不出现）。
P3 面板四键真值==live POST /api/hippo/decay/simulate 真源（留存率%/应归档/应遗忘/策略）。
P4 真请求方法==POST（Network.requestWillBeSent 实证，修前 GET=405 反证面）。
P5 面板零 'undefined' 假渲染。

全真数据零桩：模拟端点只读不落库，无清痕面。
用法：python3 scripts/e2e_cd13_hippo_simulate_ui.py
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
PORT = 9472

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


def main() -> int:
    token = make_user_token()

    # live 真源预期（默认 simForm 三参 24/0.5/0，与 UI 缺省一致；只读）
    expected = call(
        "POST",
        "/api/hippo/decay/simulate?age_hours=24&importance=0.5&access_count=0",
        token=token,
    )
    for k in ("retention", "should_archive", "should_forget", "strategy"):
        check(k in expected, f"setup: true-source key {k}", str(expected.get(k))[:40])

    user_data = tempfile.mkdtemp(prefix="cdp_cd13_")
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

        cdp.navigate(f"{BASE}/admin/hippo.html", 4.0)

        # ── P1 衰减 tab → 模拟衰减 对话框（真点击流）──
        check(bool(cdp.wait_js(CLICK_FN % "'衰减'", 15.0)), "P1 hippo: click 衰减 tab")
        check(bool(cdp.wait_js(CLICK_FN % "'模拟衰减'", 15.0)), "P1 hippo: click 模拟衰减 button")
        dlg = cdp.wait_js(
            "(() => [...document.querySelectorAll('h2')].some(h => h.textContent.trim() === '模拟衰减'))()",
            10.0,
        )
        check(bool(dlg), "P1 hippo: 模拟衰减 dialog opens")

        # ── P2/P3 点「模拟」→ 面板渲染 + 四键真值对账 ──
        check(bool(cdp.wait_js(CLICK_FN % "'模拟'", 10.0)), "P2 hippo: click 模拟 button")
        panel = cdp.wait_js(
            "(() => { const h = [...document.querySelectorAll('h3')].find(x => x.textContent.trim() === '模拟结果');"
            " return h ? h.closest('div').parentElement.innerText : null; })()",
            15.0,
        )
        check(bool(panel), "P2 hippo: 模拟结果 panel renders (pre-repair: never)")
        if panel:
            exp_ret = f"{expected['retention'] * 100:.1f}%"
            check(f"留存率: {exp_ret}" in panel, "P3 hippo: 留存率 == live true-source",
                  f"expect {exp_ret} in panel")
            exp_arch = "应归档: 是" if expected["should_archive"] else "应归档: 否"
            check(exp_arch in panel, "P3 hippo: 应归档 == live true-source", exp_arch)
            exp_forget = "应遗忘: 是" if expected["should_forget"] else "应遗忘: 否"
            check(exp_forget in panel, "P3 hippo: 应遗忘 == live true-source", exp_forget)
            check(f"策略: {expected['strategy']}" in panel, "P3 hippo: 策略 == live true-source",
                  expected["strategy"])
            check("undefined" not in panel, "P5 hippo: panel zero 'undefined' fabricated rendering")

        # ── P4 真请求方法==POST（Network 实证，修前 GET→405）──
        reqs = [
            e["params"]["request"]
            for e in cdp.events
            if e.get("method") == "Network.requestWillBeSent"
            and "/api/hippo/decay/simulate" in e.get("params", {}).get("request", {}).get("url", "")
        ]
        check(bool(reqs), "P4 hippo: simulate request observed", str(len(reqs)))
        for r in reqs:
            check(r.get("method") == "POST", "P4 hippo: request method == POST", str(r.get("method")))

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
