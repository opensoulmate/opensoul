#!/usr/bin/env python3
"""DEC-⑲-③ live UI e2e 探针：admin-ui link 页 callback_token 轮换/撤销按钮。

真浏览器（chromium headless + CDP）复现用户点击流：
  连接器卡片 → 详情弹窗 → 「轮换」chip（confirm 对话框真接受）→ 一次性凭据弹窗
  （新证明文）→ 「撤销」chip → 「签发」chip（撤销态恢复路径）。
每步配 API 真值回读（旧证 401/新证 200、has_callback_token 真变化）。
探针自带清痕（delete connector + timeline 按 connector_id 行回读清理）。

用法：python3 scripts/e2e_link_token_ui.py
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
TL_DB = "/home/climbing/opensoul/data/timeline.db"
PORT = 9444

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


def call(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def make_jwt() -> str:
    """真 JWT = enterprise _create_token(5,'admin') 服务端同钥签名（既有 e2e 先例）。"""
    sys.path.insert(0, "/home/climbing/opensoul")
    from src.api.enterprise import _create_token  # noqa: E402

    return _create_token(5, "admin")


# ── CDP helpers ─────────────────────────────────────────────


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
                self._on_event(msg)
        return {}

    def _on_event(self, msg: dict) -> None:
        self.events.append(msg)
        # JS confirm() 对话框：真接受（复现用户点「确定」）
        if msg["method"] == "Page.javascriptDialogOpening":
            self.send(
                "Page.handleJavaScriptDialog",
                {"accept": True},
            )

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self.ws.settimeout(max(0.1, min(0.5, end - time.time())))
            try:
                msg = json.loads(self.ws.recv())
            except Exception:
                continue
            if "method" in msg:
                self._on_event(msg)

    def js(self, expr: str):
        mid = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        res = self.result(mid)
        return res.get("result", {}).get("value")

    def wait_js(self, expr: str, seconds: float = 12.0):
        end = time.time() + seconds
        while time.time() < end:
            self.pump(0.3)
            val = self.js(expr)
            if val:
                return val
        return None

    def click(self, find_js: str, seconds: float = 12.0):
        """find_js 返回真值时点击它（真 .click()=React onClick 真触发）。"""
        expr = f"(() => {{ const el = {find_js}; if (el) {{ el.click(); return true; }} return false; }})()"
        end = time.time() + seconds
        while time.time() < end:
            self.pump(0.3)
            if self.js(expr) is True:
                return True
        return False


def main() -> int:
    mark = "e2e-ui-token-" + uuid.uuid4().hex[:8]
    jwt = make_jwt()

    # 1) API 建连接器（register 签发 token0）
    st, data = call("POST", "/api/link/connectors", {
        "name": mark, "type": "webhook_in", "endpoint": "http://127.0.0.1:1/e2e-ui",
        "secret": "e2e-ui-secret", "description": "DEC-⑲-③ ui e2e",
    })
    cid = data.get("connector_id")
    token0 = data.get("callback_token")
    check(st == 200 and bool(cid) and bool(token0), "setup: connector registered + token0 issued", f"st={st} cid={cid}")

    # 2) 起真浏览器
    user_data = tempfile.mkdtemp(prefix="cdp_link_ui_")
    proc = subprocess.Popen(
        ["/usr/bin/chromium", "--headless=new", f"--remote-debugging-port={PORT}",
         "--remote-allow-origins=*", "--no-sandbox", "--disable-gpu",
         f"--user-data-dir={user_data}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    cdp = None
    try:
        import websocket  # noqa: F401
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
        # 真 JWT 预文档注入（禁 about:blank 直写 localStorage=opaque origin 坑）
        cdp.send("Page.addScriptToEvaluateOnNewDocument", {
            "source": f"try {{ localStorage.setItem('soul_token', {json.dumps(jwt)}); }} catch (e) {{}}"
        })
        cdp.send("Page.navigate", {"url": f"{BASE}/admin/link.html"})
        cdp.pump(3.0)
        ok_page = cdp.wait_js(
            f"document.body && document.body.innerText.includes({json.dumps(mark)})",
            15.0,
        )
        check(bool(ok_page), "render: link page lists test connector", mark)

        # 3) 点卡片开详情
        card_js = (
            "[...document.querySelectorAll('div.cursor-pointer')]"
            f".find(e => e.textContent.includes({json.dumps(mark)}))"
        )
        check(cdp.click(card_js), "click: connector card opens detail")
        ok_detail = cdp.wait_js("document.body.innerText.includes('连接器详情')", 8.0)
        check(bool(ok_detail), "render: detail dialog open")

        # 4) 点「轮换」（has_callback_token=true 态）→ confirm 真接受
        rot_js = (
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent.trim() === '轮换')"
        )
        check(cdp.click(rot_js), "click: 轮换 button")
        cdp.pump(2.0)
        # 一次性凭据弹窗：header=回调凭据已轮换 + 新证明文 32hex
        tok1 = cdp.wait_js(
            "[...document.querySelectorAll('pre')].map(p => p.textContent.trim())"
            ".find(t => /^[0-9a-f]{32}$/.test(t)) || ''",
            10.0,
        )
        rotated_header = cdp.js("document.body.innerText.includes('回调凭据已轮换')")
        check(bool(rotated_header), "render: rotate modal header 回调凭据已轮换")
        check(bool(tok1) and tok1 != token0, "render: new plaintext token shown once", f"tok1={tok1!r}")

        # API 真值回读：旧证即刻 401、新证 200
        st_old, _ = call("POST", f"/api/link/webhook/{cid}", {"probe": "old"})
        check(st_old == 401, f"api: old token fail-closed after rotate (webhook {st_old})")
        st_new = None
        if tok1:
            os.environ.setdefault("X", "1")
            # webhook 入口走 X-Callback-Token 头——用 urllib 自定义头
            req = urllib.request.Request(
                f"{BASE}/api/link/webhook/{cid}",
                data=json.dumps({"probe": "new"}).encode(),
                method="POST",
                headers={"Content-Type": "application/json", "X-Callback-Token": tok1},
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    st_new = r.status
            except urllib.error.HTTPError as e:
                st_new = e.code
        check(st_new == 200, f"api: new token accepted after rotate (webhook {st_new})")

        # 5) 关一次性弹窗（我已保存）→ 撤销
        check(cdp.click(
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent.trim() === '我已保存')",
            5.0,
        ) or True, "click: close one-time modal (best-effort)")
        cdp.pump(1.5)
        rev_js = (
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent.trim() === '撤销')"
        )
        check(cdp.click(rev_js), "click: 撤销 button")
        cdp.pump(2.5)
        st_line = cdp.wait_js("document.body.innerText.includes('未签发')", 8.0)
        check(bool(st_line), "render: detail shows 未签发 after revoke")
        rev_gone = cdp.js(
            "![...document.querySelectorAll('button')].some(b => b.textContent.trim() === '撤销')"
        )
        check(rev_gone is True, "render: 撤销 button hidden after revoke")
        st_rv = None
        if tok1:
            req = urllib.request.Request(
                f"{BASE}/api/link/webhook/{cid}",
                data=json.dumps({"probe": "revoked"}).encode(),
                method="POST",
                headers={"Content-Type": "application/json", "X-Callback-Token": tok1},
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    st_rv = r.status
            except urllib.error.HTTPError as e:
                st_rv = e.code
        check(st_rv == 401, f"api: revoked token fail-closed (webhook {st_rv})")

        # 6) 撤销态点「签发」= 恢复路径（同走 rotate 口）
        iss_js = (
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent.trim() === '签发')"
        )
        check(cdp.click(iss_js), "click: 签发 button (revoked-state recovery)")
        cdp.pump(2.0)
        tok2 = cdp.wait_js(
            "[...document.querySelectorAll('pre')].map(p => p.textContent.trim())"
            ".find(t => /^[0-9a-f]{32}$/.test(t)) || ''",
            10.0,
        )
        iss_header = cdp.js("document.body.innerText.includes('回调凭据已签发')")
        check(bool(iss_header), "render: re-issue modal header 回调凭据已签发")
        check(bool(tok2) and tok2 not in (token0, tok1), "render: re-issued plaintext distinct", f"tok2={tok2!r}")
        st_ok = None
        if tok2:
            req = urllib.request.Request(
                f"{BASE}/api/link/webhook/{cid}",
                data=json.dumps({"probe": "reissued"}).encode(),
                method="POST",
                headers={"Content-Type": "application/json", "X-Callback-Token": tok2},
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    st_ok = r.status
            except urllib.error.HTTPError as e:
                st_ok = e.code
        check(st_ok == 200, f"api: re-issued token accepted (webhook {st_ok})")

    finally:
        # 清痕：删 connector + timeline 按 connector_id 清探针行
        st_del, _ = call("DELETE", f"/api/link/connectors/{cid}")
        st_chk, _ = call("GET", f"/api/link/connectors/{cid}")
        check(st_del in (200, 204) and st_chk == 404, f"cleanup: connector deleted ({st_del}/{st_chk})")
        if cdp is not None:
            try:
                cdp.ws.close()
            except Exception:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        shutil.rmtree(user_data, ignore_errors=True)
        if cid:
            conn = sqlite3.connect(f"file:{TL_DB}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    "SELECT event_id FROM events WHERE summary LIKE ?",
                    (f"%{cid}%",),
                ).fetchall()
            finally:
                conn.close()
            if rows:
                conn = sqlite3.connect(TL_DB)
                conn.executemany("DELETE FROM events WHERE event_id = ?", rows)
                conn.commit()
                conn.close()
            print(f"cleanup: timeline probe rows removed={len(rows)}")

    passed = sum(1 for ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n== {passed}/{total} ==")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
