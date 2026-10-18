#!/usr/bin/env python3
"""CD-⑳ live UI e2e：vision 预览 elapsed 收编（真源零改动）。

--post（修后新 bundle，真 chromium+CDP+setCacheDisabled）：
P1 vision 页渲染（生成图表按钮在场）；
P2 bar tab 点生成→页面自身 /api/vision/chart/bar/json 真响应回读（CDP
   getResponseBody）→预览头 ms 段==resp.elapsed_ms 逐值对账；
P3 mindmap tab 点生成→页面自身 /api/vision/mindmap/json 真响应回读→预览头 ms 段
   ==round(resp.elapsed_seconds*1000) 逐值对账（修前恒假 0ms 遮蔽真值的核心断言）；
P4 真值非 0 时假 0 零出现（' · 0ms' 禁出现）；
P5 'undefined' 正文零出现 + 预览头形态 engine · format · Xms · YKB。

对账真值=页面自身真响应回读（非探针自取快照），CD-⑪/CD-⑱ 反自伤纪律。
用法：python3 scripts/e2e_cd20_vision_preview_elapsed_ui.py --post
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8090"
PORT = 9489

MODE = sys.argv[1] if len(sys.argv) > 1 else "--post"

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


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

    def raw(self, method: str, params: dict | None = None):
        mid = self.send(method, params)
        return self.result(mid)

    def navigate(self, url: str, wait: float = 3.0) -> None:
        self.send("Page.navigate", {"url": url})
        self.pump(wait)


def click_button_by_text(cdp: CDP, text: str) -> bool:
    return bool(
        cdp.js(
            "(() => { const bs = [...document.querySelectorAll('button')];"
            f" const b = bs.find(x => x.innerText.trim() === {json.dumps(text)});"
            " if (b) { b.click(); return true; } return false; })()"
        )
    )


def last_response_body(cdp: CDP, url_part: str, after: int) -> dict | None:
    evs = [
        e
        for e in cdp.events[after:]
        if e.get("method") == "Network.responseReceived"
        and url_part in e.get("params", {}).get("response", {}).get("url", "")
    ]
    if not evs:
        return None
    rid = evs[-1]["params"]["requestId"]
    try:
        b = cdp.raw("Network.getResponseBody", {"requestId": rid})
        return json.loads(b.get("body", ""))
    except Exception:
        return None


def preview_header(body: str) -> str | None:
    """预览头行形态：engine · format · Xms[ · YKB]。"""
    for line in body.splitlines():
        if re.search(r"\d+ms", line) and " · " in line:
            return line.strip()
    return None


def main() -> int:
    token = make_user_token()

    user_data = tempfile.mkdtemp(prefix="p_cd20_")
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

        cdp.navigate(f"{BASE}/admin/vision.html", 4.0)
        tok_ok = cdp.js("(() => !!localStorage.getItem('soul_token'))()")
        check(bool(tok_ok), "setup: soul_token 注入在场")
        ready = cdp.wait_js(
            "(() => [...document.querySelectorAll('button')].some(b => b.innerText.trim() === '生成图表'))()",
            20.0,
        )
        check(bool(ready), "P1: vision 页渲染（生成图表按钮在场）")
        if not ready:
            print("DIAG body:", repr((cdp.js("document.body.innerText") or "")[:300]))
            fails = [n for ok, n in RESULTS if not ok]
            print(f"\n== RESULT: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed (aborted) ==")
            return 1

        def list_outputs() -> list[str]:
            try:
                with urllib.request.urlopen(BASE + "/api/vision/outputs", timeout=10) as r:
                    data = json.loads(r.read().decode())
                return sorted(o["filename"] for o in data.get("outputs", []))
            except Exception:
                return []

        outs_before = list_outputs()

        # ── P2 bar tab（默认）生成 → 预览头 ms==页面真响应 elapsed_ms ──
        mark = len(cdp.events)
        check(click_button_by_text(cdp, "生成图表"), "P2: bar 生成按钮真点击")
        bar_resp = None
        end = time.time() + 30
        while time.time() < end and bar_resp is None:
            cdp.pump(0.5)
            bar_resp = last_response_body(cdp, "/api/vision/chart/bar/json", mark)
        check(bar_resp is not None, "P2 前置: 页面自身 bar/json 响应可回读")
        cdp.pump(1.0)
        body = cdp.js("document.body.innerText") or ""
        if bar_resp is not None:
            header = preview_header(body)
            check(header is not None, "P2: 预览头渲染", repr(header))
            if header is not None:
                expect_ms = str(bar_resp["elapsed_ms"])
                got = re.search(r"(\d+)ms", header)
                check(
                    got is not None and got.group(1) == expect_ms,
                    "P2: bar ms 段==页面真响应 elapsed_ms 逐值对账",
                    f"got={got.group(1) if got else None!r} expect={expect_ms!r} header={header!r}",
                )

        # ── P3 mindmap tab 生成 → 预览头 ms==round(elapsed_seconds*1000)（核心修复）──
        check(click_button_by_text(cdp, "思维导图"), "P3: 思维导图 tab 真点击")
        cdp.pump(0.5)
        mark = len(cdp.events)
        check(click_button_by_text(cdp, "生成图表"), "P3: mindmap 生成按钮真点击")
        mm_resp = None
        end = time.time() + 30
        while time.time() < end and mm_resp is None:
            cdp.pump(0.5)
            mm_resp = last_response_body(cdp, "/api/vision/mindmap/json", mark)
        check(mm_resp is not None, "P3 前置: 页面自身 mindmap/json 响应可回读")
        cdp.pump(1.0)
        body = cdp.js("document.body.innerText") or ""
        if mm_resp is not None:
            header = preview_header(body)
            check(header is not None, "P3: 预览头渲染", repr(header))
            if header is not None:
                expect_ms = str(round(mm_resp["elapsed_seconds"] * 1000))
                got = re.search(r"(\d+)ms", header)
                check(
                    got is not None and got.group(1) == expect_ms,
                    "P3: mindmap ms 段==round(真响应 elapsed_seconds*1000) 逐值对账（修前恒 0）",
                    f"got={got.group(1) if got else None!r} expect={expect_ms!r} header={header!r}",
                )
                if int(expect_ms) > 0:
                    check(
                        " · 0ms" not in (header or ""),
                        "P4: 真值非 0 时假 0 零出现",
                        repr(header),
                    )
                check(
                    re.match(r"^\S+ · \S+ · \d+ms( · [\d.]+KB)?$", header or "") is not None,
                    "P5: 预览头形态 engine · format · Xms · YKB",
                    repr(header),
                )

        check("undefined" not in body, "P5: 'undefined' 正文零出现")

        # 取证清痕：本拍生成的输出文件（save_output:true 真落盘）按新增清单精确删
        outs_after = list_outputs()
        new_outs = [n for n in outs_after if n not in outs_before]
        print("new outputs from this run:", new_outs)
        remaining = []
        for name in new_outs:
            req = urllib.request.Request(
                BASE + "/api/vision/outputs/" + urllib.parse.quote(name), method="DELETE"
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    r.read()
            except Exception as e:  # noqa: BLE001
                remaining.append((name, str(e)))
        left = [n for n in list_outputs() if n not in outs_before]
        check(not left, "P6: 取证清痕 remaining=0（本拍新增输出全清）", f"left={left} errs={remaining}")

    finally:
        proc.terminate()
        proc.wait(timeout=10)

    fails = [n for ok, n in RESULTS if not ok]
    print(f"\n== RESULT ({MODE}): {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ==")
    if fails:
        print("FAILS:", fails)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
