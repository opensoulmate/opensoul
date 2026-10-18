#!/usr/bin/env python3
"""CD-⑦ 修复候选④ live UI e2e：limb 页 windows 容器形收编（真浏览器 CDP 点击流）。

诚实边界（与 CD-⑥"真数据全程"的差异如实记）：本机 live GET /api/limb/rpa/windows
=503（wayland 无 wlrctl/swaymsg/hyprctl，_tool_for_windows 真实拒绝），无法产真
窗口数据——渲染链用 Fetch 层桩响应（Page.addScriptToEvaluateOnNewDocument 预文档
注入 window.fetch 包装），桩 payload=真源容器形裸数组（元素七键 id/title/x/y/
width/height/focused——真源容器形本体由 tests/test_cd7_repair.py 行为桥真 router
真 list_windows 实跑硬证，两面合成全覆盖）。

P1 点「RPA工具」tab →「窗口列表」面板可见。
P2 点面板「刷新」→ fetch 真被调用（桩命中计数 ≥1，禁空过假阳性）。
P3/P4 窗口行真渲染桩标题 cd7-win-alpha-甲/cd7-win-beta-乙
   （修前 .windows 容器漂移恒回落 []→恒「暂无窗口」）。
P5 尺寸行真渲染 640×480/800×600（真源 width/height 键）。
P6「暂无窗口」空态零出现（修前恒空态）。
P7 全页 'undefined' 零出现（含死显示键 w.app 修前条件渲染恒假=无假渲染，接口收编）。
P8 live 真源面（无桩）：/api/limb/rpa/health 200 + /windows 本机真实态如实观测。
用法：python3 scripts/e2e_cd7_limb_windows_ui.py [--pre|--post]（--pre=换 HEAD 旧
bundle 同探针跑修前缺口硬证，与 --post 双向对照；缺省=--post）。
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
PORT = 9467
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

    def navigate(self, url: str, wait: float = 3.0) -> None:
        self.send("Page.navigate", {"url": url})
        self.pump(wait)


STUB_WINDOWS = [
    {"id": "cd7-win-1", "title": "cd7-win-alpha-甲", "x": 10, "y": 20,
     "width": 640, "height": 480, "focused": True},
    {"id": "cd7-win-2", "title": "cd7-win-beta-乙", "x": 30, "y": 40,
     "width": 800, "height": 600, "focused": False},
]


def main() -> int:
    label = "--post"
    if len(sys.argv) > 1:
        label = sys.argv[1]
    print(f"== e2e_cd7_limb_windows_ui ({label}) ==")
    token, uid = make_user_token()

    # ── P8 live 真源面（无桩）：本机真实态如实观测 ──
    try:
        health = call("GET", "/api/limb/rpa/health", token=token)
        check(health.get("component") == "Limb RPA Engine",
              "P8a: live /api/limb/rpa/health true-source", json.dumps(health)[:120])
        try:
            wins = call("GET", "/api/limb/rpa/windows", token=token)
            check(isinstance(wins, list), "P8b: live /windows bare array (host has window tool)",
                  type(wins).__name__)
        except urllib.error.HTTPError as e:
            detail = json.loads(e.read().decode() or "{}").get("detail", "")
            # 本机真实态：wayland 无窗口工具=503 真因如实（非本切片引入）
            check(e.code == 503 and "window management tool" in detail,
                  "P8b: live /windows host state honest (503 no wayland window tool)",
                  f"code={e.code} detail={detail[:80]}")
    except Exception as e:
        check(False, "P8: live true-source observation", str(e))

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
            "try { localStorage.setItem('soul_token', " + json.dumps(token) + "); } catch (e) {}\n"
            "(() => {\n"
            "  const orig = window.fetch ? window.fetch.bind(window) : null;\n"
            "  window.__cd7_stub_hits = 0;\n"
            "  window.fetch = (input, init2) => {\n"
            "    const url = (typeof input === 'string') ? input : (input && input.url) || '';\n"
            "    if (url.indexOf('/api/limb/rpa/windows') >= 0) {\n"
            "      window.__cd7_stub_hits += 1;\n"
            "      return Promise.resolve(new Response(JSON.stringify("
            + json.dumps(STUB_WINDOWS)
            + "), {status: 200, headers: {'Content-Type': 'application/json'}}));\n"
            "    }\n"
            "    return orig ? orig(input, init2) : window.fetch(input, init2);\n"
            "  };\n"
            "})();"
        )
        cdp.result(cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": init}))

        cdp.navigate(f"{BASE}/admin/limb.html", 4.0)

        def body_text() -> str:
            return cdp.js("document.body.innerText") or ""

        # 鉴权注入竞态兜底（CD-⑥ 首轮 login wall 假阴逼出）：token 缺席→重注入+reload
        if not cdp.js("localStorage.getItem('soul_token')"):
            cdp.result(cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": init}))
            cdp.js(f"localStorage.setItem('soul_token', {json.dumps(token)})")
            cdp.navigate(f"{BASE}/admin/limb.html", 4.0)

        loaded = cdp.wait_js(
            "(() => { const t = document.body.innerText;"
            " return t.includes('任务总数') && !t.includes('加载中...'); })()",
            25.0,
        )
        t = body_text()
        # P0 探针不瞎：登录墙在场=后续断言全部无效
        if "任务总数" not in t or "登录" in t[:60]:
            check(False, "P0: admin page authenticated (probe not blind on login wall)", repr(t[:120]))
            return 1
        check(True, "P0: admin page authenticated (probe not blind on login wall)")
        if not loaded:
            print("DEBUG wait_js not loaded; body len:", len(t), "snippet:", repr(t[:300]))

        # ── P1 点「RPA工具」tab → 窗口列表面板可见 ──
        clicked = cdp.click(
            "[...document.querySelectorAll('button')].find(b => b.textContent.includes('RPA工具'))"
        )
        check(clicked, "P1a: click RPA工具 tab")
        panel_visible = cdp.wait_js(
            "(() => document.body.innerText.includes('窗口列表'))()", 15.0
        )
        check(bool(panel_visible), "P1b: 窗口列表 panel visible")

        # ── P2 点面板「刷新」→ fetch 真被调用（桩命中计数）──
        clicked = cdp.click(
            "(() => { const h = [...document.querySelectorAll('h3')]"
            ".find(x => x.textContent.includes('窗口列表'));"
            " return h ? h.parentElement.querySelector('button') : null; })()"
        )
        check(clicked, "P2a: click 窗口列表 refresh button")
        hit = cdp.wait_js("(() => (window.__cd7_stub_hits || 0) >= 1)()", 15.0)
        check(bool(hit), "P2b: fetch to /api/limb/rpa/windows truly fired (stub hit >= 1)",
              f"hits={cdp.js('window.__cd7_stub_hits')}")

        # ── P3/P4 窗口行真渲染桩标题（修前恒回落 []）──
        t2 = body_text()
        check("cd7-win-alpha-甲" in t2, "P3: window row renders stub title alpha (pre-fix恒空)",
              f"alpha={'cd7-win-alpha-甲' in t2}")
        check("cd7-win-beta-乙" in t2, "P4: window row renders stub title beta (pre-fix恒空)",
              f"beta={'cd7-win-beta-乙' in t2}")

        # ── P5 尺寸行真渲染（真源 width/height 键）──
        check("640×480" in t2 and "800×600" in t2, "P5: window rows render true-source width×height",
              f"640={'640×480' in t2} 800={'800×600' in t2}")

        # ── P6 空态零出现（修前恒「暂无窗口」）──
        check("暂无窗口" not in t2, "P6: zero 暂无窗口 empty state (pre-fix always empty)",
              f"count={t2.count('暂无窗口')}")

        # ── P7 全页 'undefined' 零出现 ──
        t3 = body_text()
        check("undefined" not in t3, "P7: zero 'undefined' renders on limb page",
              f"count={t3.count('undefined')}")
    finally:
        proc.terminate()

    failed = [n for ok, n in RESULTS if not ok]
    print(f"\n== {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ({label}) ==")
    if failed:
        print("FAILED:", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
