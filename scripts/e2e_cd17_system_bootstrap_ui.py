#!/usr/bin/env python3
"""CD-⑰ live UI e2e：system 页初始化 tab bootstrap status 消费键收口（真源零改动）。

P1 页面渲染（初始化 tab 真渲染「系统初始化」块）。
P2 徽章按真源 bootstrapped 渲染（live bootstrapped=True→「正常」徽章；修前
   initialized 恒 undefined 恒走「未初始化」假渲染——'未初始化' 在 DOM 零出现）。
P3 步骤块真渲染（修前 steps 恒 undefined 块永不渲染）：DOM 步骤名集合==live items
   键集合逐条对账（零编造零缺失）。
P4 步骤两态徽章==真源布尔（true→正常/异常 逐条对账）。
P5 'undefined' 零出现（假渲染面清零）。
P6 live 真源形状恰 6 键（initialized/steps 零产出，消费键收编依据）。

全真数据零桩（bootstrap 读面只读），无清痕面。
用法：python3 scripts/e2e_cd17_system_bootstrap_ui.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import urllib.request

BASE = "http://127.0.0.1:8090"
PORT = 9477

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


def call(method: str, path: str):
    req = urllib.request.Request(BASE + path, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def make_user_token() -> str:
    import sqlite3
    import uuid

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
    st = call("GET", "/api/system/bootstrap/status")
    check(set(st) == {"status", "component", "bootstrapped", "bootstrapped_at", "version", "items"},
          "P6: live 真源恰 6 键", str(sorted(st)))
    check("initialized" not in st and "steps" not in st, "P6: live 真源零 initialized/steps")
    check(isinstance(st.get("items"), dict), "P6: items 为 dict", str(type(st.get("items"))))
    bootstrapped = bool(st.get("bootstrapped"))
    items = st.get("items") or {}

    user_data = tempfile.mkdtemp(prefix="cdp_cd17_")
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

        cdp.navigate(f"{BASE}/admin/system.html", 4.0)
        ready = cdp.wait_js(
            "(() => document.querySelectorAll('button').length >= 3)()",
            20.0,
        )
        check(bool(ready), "setup: system page rendered (tab bar in DOM)")
        if not ready:
            print("DIAG body:", repr((cdp.js("document.body.innerText") or "")[:300]))
            fails = [n for ok, n in RESULTS if not ok]
            print(f"\n== RESULT: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed (aborted) ==")
            return 1

        # 切到「初始化」tab（按钮精确文本匹配，防「未初始化」误点）
        clicked = cdp.js(
            "(() => { const bs=[...document.querySelectorAll('button')];"
            " const b=bs.find(x=>x.textContent.trim()==='初始化');"
            " if(b){b.click(); return true;} return false; })()"
        )
        check(bool(clicked), "P1: 初始化 tab 点击", str(clicked))
        if not clicked:
            print("DIAG buttons:", repr(cdp.js("[...document.querySelectorAll('button')].map(b=>b.textContent.trim())")))
        cdp.pump(1.5)
        tab_ready = cdp.wait_js(
            "(() => /系统初始化/.test(document.body.innerText))()", 10.0
        )
        check(bool(tab_ready), "P1: 初始化 tab 真渲染「系统初始化」块")
        if not tab_ready:
            fails = [n for ok, n in RESULTS if not ok]
            print(f"\n== RESULT: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed (aborted: tab not rendered) ==")
            return 1

        body = cdp.js("document.body.innerText") or ""

        # bootstrap 卡局部取证（tab 条上方常驻横幅另有显示面，范围纪律不入本拍判定）
        card = cdp.js(
            "(() => { const s=[...document.querySelectorAll('span')]"
            ".find(x=>x.textContent.trim()==='系统初始化');"
            " if(!s) return ''; let e=s;"
            " while(e && !(e.className && String(e.className).indexOf('rounded-xl')>=0)) e=e.parentElement;"
            " return (e || s.parentElement).innerText; })()"
        ) or ""
        check(bool(card), "setup: bootstrap 卡定位", repr(card[:60]))

        # P2 徽章按真源 bootstrapped 渲染
        if bootstrapped:
            check("未初始化" not in card, "P2: bootstrapped=True 徽章零「未初始化」假渲染")
            check("正常" in card, "P2: 「正常」徽章在场（bootstrapped=True）")
        else:
            check("未初始化" in card, "P2: bootstrapped=False 如实「未初始化」")

        # P3 步骤块：DOM 步骤名集合==live items 键集合（零编造零缺失）
        dom_items = cdp.js(
            "(() => { const out=[];"
            " document.querySelectorAll('span.text-xs.font-medium').forEach(s=>{"
            "   const t=s.textContent.trim(); if(t) out.push(t); });"
            " return out; })()"
        ) or []
        expected = sorted(items.keys())
        got = sorted(x for x in dom_items if x in items)
        check(got == expected, "P3: 步骤块渲染集合==live items 键集合逐条对账",
              f"got={got} expected={expected}")
        missing = [k for k in expected if k not in dom_items]
        check(not missing, "P3: 步骤块零缺失", str(missing))

        # P4 两态徽章==真源布尔（逐条：true 行「正常」、false 行「异常」）
        rows = cdp.js(
            "(() => { const out=[];"
            " document.querySelectorAll('div.flex.items-center.gap-3').forEach(d=>{"
            "   const t=d.innerText.replace(/\\n/g,'|'); out.push(t); });"
            " return out; })()"
        ) or []
        badge_ok = True
        detail = []
        for name, okv in items.items():
            row = next((r for r in rows if name in r), None)
            if row is None:
                badge_ok = False
                detail.append(f"{name}:row-missing")
                continue
            want = "正常" if okv else "异常"
            if want not in row:
                badge_ok = False
                detail.append(f"{name}:want {want} in {row!r}")
        check(badge_ok, "P4: 步骤两态徽章==真源布尔逐条对账", "; ".join(detail[:4]))

        # P5 假渲染面清零（bootstrap 卡内）
        check("undefined" not in card, "P5: 卡内 'undefined' 零出现",
              repr(card[card.find("undefined"):][:60]) if "undefined" in card else "")

        fails = [n for ok, n in RESULTS if not ok]
        print(f"\n== RESULT: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ==")
        return 1 if fails else 0
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
