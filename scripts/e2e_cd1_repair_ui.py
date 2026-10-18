#!/usr/bin/env python3
"""CD-① 修复件 live UI e2e：admin-ui 四处消费面收编真源键/端点（真浏览器 CDP 点击流）。

P1 workspace 页（真数据）：/api/dir 真源 → 目录列表真渲染（修前 /api/workspace/dir 404
   整页错误条）。
P2 permissions 页（真数据，admin 身份）：/api/permission/policies 真源行数组收编 →
   角色表真渲染（修前 /api/permission/roles/all 恒空）。
P3 limb 页（真源形状桩——本机缺 grim 截图工具，端点 503 环境边界）：ScreenshotResponse
   真键 base64 → 截图真显示（修前 data.image 分支恒假永不显示）。
P4 hermes 页（真源形状桩——真 send 会真发消息，禁副作用探针）：{ok,content,source}
   → 发送成功提示真可达（修前 data.status 无键恒显「状态: undefined」）。

桩只替换目标调用的响应载荷形状（形状=live openapi/源码真源逐键），其余请求全真。
零服务端写入（全程只读+桩），无清痕面。
用法：python3 scripts/e2e_cd1_repair_ui.py
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
PORT = 9461

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
    """真 JWT = src.services.auth.create_access_token 服务端同钥签名（既有 e2e 先例）。
    身份=admin 用户（casbin g,admin,admin → /api/permission/policies admin-only 真过）。"""
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

    def click(self, find_js: str, seconds: float = 12.0) -> bool:
        expr = f"(() => {{ const el = {find_js}; if (el) {{ el.click(); return true; }} return false; }})()"
        end = time.time() + seconds
        while time.time() < end:
            self.pump(0.3)
            if self.js(expr) is True:
                return True
        return False

    def set_input(self, find_js: str, value: str) -> bool:
        expr = (
            "(() => { const el = "
            + find_js
            + "; if (!el) return false; "
            "const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set "
            "? Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set "
            ": Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set; "
            f"setter.call(el, {json.dumps(value)}); "
            "el.dispatchEvent(new Event('input', { bubbles: true })); return true; })()"
        )
        return self.js(expr) is True

    def add_init_script(self, source: str) -> None:
        self.send("Page.addScriptToEvaluateOnNewDocument", {"source": source})

    def navigate(self, url: str, wait: float = 3.0) -> None:
        self.send("Page.navigate", {"url": url})
        self.pump(wait)


PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

STUB_JS = (
    "(() => { const orig = window.fetch.bind(window); "
    "const mk = (obj) => new Response(JSON.stringify(obj), { status: 200, "
    "headers: { 'Content-Type': 'application/json' } }); "
    "window.fetch = (input, init) => { "
    "const url = (typeof input === 'string') ? input : ((input && input.url) || ''); "
    f"if (url.includes('/api/limb/rpa/screenshot')) return Promise.resolve(mk({{ base64: {json.dumps(PNG_B64)}, "
    "format: 'png', width: 1, height: 1, display_server: 'stub' })); "
    "if (url.includes('/api/hermes/sessions/send')) return Promise.resolve(mk({ ok: true, "
    "content: 'stub-out', source: 'hermes-cli' })); "
    "if (url.includes('/api/hermes/sessions?')) return Promise.resolve(mk({ sessions: "
    "[{ id: 'stub-sess-1', name: 'stubSessOne', platform: 'cli', last_message: 'hi' }], total: 1 })); "
    "return orig(input, init); }; })()"
)


def main() -> int:
    token = make_user_token()

    # 真源预期（P1）：可见（非隐藏）目录项名
    listing = call("GET", "/api/dir?path=~", token=token)
    expect_names = [e["name"] for e in listing.get("entries", []) if not e.get("hidden")]
    check(bool(expect_names), "setup: /api/dir true-source listing non-empty",
          f"{len(expect_names)} visible entries")

    user_data = tempfile.mkdtemp(prefix="cdp_cd1_repair_")
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
        cdp.add_init_script(STUB_JS)

        # ── P1 workspace 页：真数据目录列表（修前 404 整页错误条）──
        cdp.navigate(f"{BASE}/admin/workspace.html", 3.5)
        ok_entries = cdp.wait_js(
            f"(() => {{ const t = document.body.innerText; return {json.dumps(expect_names[:3])}"
            ".some(n => t.includes(n)); })()", 30.0
        )
        check(bool(ok_entries), "P1 workspace: dir entries render from /api/dir true source",
              f"expect any of {expect_names[:3]}")
        err_bar = cdp.js("document.body.innerText.includes('API error 404')")
        check(err_bar is not True, "P1 workspace: zero 404 error bar (ghost endpoint gone)")
        # 真点击流：点进第一个目录行（行=Button variant=row，名后缀 '/'）→ 子列表真刷新
        first_dir = expect_names[0]
        ok_click = cdp.click(
            "[...document.querySelectorAll('button')].find(b => b.textContent.trim().startsWith("
            + json.dumps(first_dir + "/") + "))"
        )
        check(ok_click, f"P1 workspace: click entry row ({first_dir})")
        cdp.pump(2.0)

        # ── P2 permissions 页：角色表真渲染（修前 /roles/all 恒空）──
        cdp.navigate(f"{BASE}/admin/permissions.html", 3.5)
        ok_roles = cdp.wait_js(
            "(() => { const rows = [...document.querySelectorAll('tbody tr')].map(r => r.textContent); "
            "return rows.some(t => t.includes('admin')) && rows.some(t => t.includes('user')); })()",
            15.0,
        )
        check(bool(ok_roles), "P2 permissions: roles table renders derived role names (admin+user)")
        ok_count = cdp.js(
            "(() => { const rows = [...document.querySelectorAll('tbody tr')].map(r => r.textContent); "
            "return rows.some(t => t.includes('admin') && t.includes('条')); })()"
        )
        check(ok_count is True, "P2 permissions: 关联策略数 column populated from policies rows")
        ok_tab = cdp.click(
            "[...document.querySelectorAll('button')].find(b => b.textContent.includes('权限策略'))"
        )
        check(ok_tab, "P2 permissions: click 权限策略 tab")
        ok_pol = cdp.wait_js(
            "(() => { const rows = [...document.querySelectorAll('tbody tr')].map(r => r.textContent); "
            "return rows.some(t => t.includes('knowledge') && t.includes('read')); })()", 12.0
        )
        check(bool(ok_pol), "P2 permissions: policies rows render [role,resource,action] mapping")

        # ── P3 limb 页：截图真显示（真源形状桩，修前 data.image 恒假）──
        cdp.navigate(f"{BASE}/admin/limb.html", 3.5)
        ok_tools_tab = cdp.click(
            "[...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'RPA工具')"
        )
        check(ok_tools_tab, "P3 limb: click RPA工具 tab")
        ok_shot_btn = cdp.click(
            "[...document.querySelectorAll('button')].find(b => b.textContent.trim().includes('截取屏幕'))"
        )
        check(ok_shot_btn, "P3 limb: click 截图 button")
        ok_img = cdp.wait_js(
            "(() => { const img = document.querySelector('img[alt=\"Screenshot\"]'); "
            "return img && img.src && img.src.startsWith('data:image/png;base64,') ? img.src.length : 0; })()",
            12.0,
        )
        check(bool(ok_img), "P3 limb: screenshot img rendered from base64 true-source key",
              f"src_len={ok_img}")

        # ── P4 hermes 页：发送成功提示真可达（真源形状桩，修前 状态: undefined）──
        cdp.navigate(f"{BASE}/admin/hermes.html", 3.5)
        ok_row = cdp.wait_js("document.body.innerText.includes('stubSessOne')", 15.0)
        check(bool(ok_row), "P4 hermes: session row renders (stub list)")
        ok_send_btn = cdp.click("document.querySelector('button[title=\"发送消息\"]')")
        check(ok_send_btn, "P4 hermes: click 发送消息 opens dialog")
        ok_dlg = cdp.wait_js(
            "!![...document.querySelectorAll('div.fixed')].find(x => x.textContent.includes('发送消息到'))",
            10.0,
        )
        check(bool(ok_dlg), "P4 hermes: send dialog open")
        ok_ta = cdp.set_input(
            "[...document.querySelectorAll('div.fixed textarea')].find(x => true)", "e2e probe text"
        )
        check(ok_ta, "P4 hermes: type message")
        ok_send = cdp.click(
            "(() => { const d = [...document.querySelectorAll('div.fixed')]"
            ".find(x => x.textContent.includes('发送消息到')); "
            "return d ? [...d.querySelectorAll('button')].find(b => b.textContent.trim() === '发送') : null; })()"
        )
        check(ok_send, "P4 hermes: click 发送")
        ok_result = cdp.wait_js(
            "(() => { const d = [...document.querySelectorAll('div.fixed')]"
            ".find(x => x.textContent.includes('发送消息到')); "
            "return d && d.textContent.includes('消息已发送') ? d.textContent.includes('消息已发送') : 0; })()",
            12.0,
        )
        check(bool(ok_result), "P4 hermes: 消息已发送 reachable (data.ok true-source key)")
        bad = cdp.js("document.body.innerText.includes('状态: undefined')")
        check(bad is not True, "P4 hermes: zero '状态: undefined' (dead key gone)")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    passed = sum(1 for ok, _ in RESULTS if ok)
    print(f"\n== {passed}/{len(RESULTS)} ==")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
