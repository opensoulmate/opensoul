#!/usr/bin/env python3
"""CD-⑱ live UI e2e：system 页常驻横幅/统计面消费键收口（真源零改动）。

--pre（修前旧 bundle）：修前症状硬证——「器官 undefined/undefined」、活跃Agent/
近期会话/运行时间死显示在场、/api/system/quick 请求真发（死消费面在场）、
'undefined' 在正文出现。
--post（修后新 bundle）：P1 页面渲染（统计条 6 卡）；P2 器官卡值==live
/api/system/overview organs.healthy_count/total_count 逐值对账（'undefined/undefined'
零出现）；P3 知识条目卡==knowledge.total_entries；P4 CPU/内存卡==metrics 真值；
P5 死显示标签零出现（活跃Agent/近期会话/运行时间/banner 运行）；P6 网络零请求
/api/system/quick（死消费面清零）；P7 banner 数据库==live health.database.type
（fabrication 收口）；P8 三真源形状（消费键收编依据）。

全真数据零桩（只读面），无清痕面。
用法：python3 scripts/e2e_cd18_system_stats_ui.py --pre|--post
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

BASE = "http://127.0.0.1:8090"
PORT = 9488

MODE = sys.argv[1] if len(sys.argv) > 1 else "--post"
PRE = MODE == "--pre"

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


def call(path: str):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
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

    def raw(self, method: str, params: dict | None = None):
        mid = self.send(method, params)
        return self.result(mid)

    def navigate(self, url: str, wait: float = 3.0) -> None:
        self.send("Page.navigate", {"url": url})
        self.pump(wait)


def line_value(body: str, label: str) -> str | None:
    """统计卡 innerText 形态：标签行的下一行=值行。"""
    lines = [l.strip() for l in body.splitlines()]
    for i, l in enumerate(lines):
        if l == label and i + 1 < len(lines):
            return lines[i + 1]
    return None


def main() -> int:
    token = make_user_token()

    # live 真源（只读）——消费键收编依据
    health = call("/api/health")
    quick = call("/api/system/quick")

    if PRE:
        check(set(quick) == {"status", "db", "version", "timestamp"}, "P8: live /api/system/quick 恰 4 键", str(sorted(quick)))
    else:
        check(set(quick) == {"status", "db", "version", "timestamp"}, "P8: live /api/system/quick 恰 4 键", str(sorted(quick)))
        check(set(health) == {"component", "database", "knowledge_entries", "status", "uptime_check_ms", "version"},
              "P8: live /api/health 恰 6 键", str(sorted(health)))
        check("uptime" not in health, "P8: live health 零 uptime（uptime_check_ms 禁冒充）")

    user_data = tempfile.mkdtemp(prefix="p_cd18_")
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
        check(bool(ready), "setup: system page rendered")
        if not ready:
            print("DIAG body:", repr((cdp.js("document.body.innerText") or "")[:300]))
            fails = [n for ok, n in RESULTS if not ok]
            print(f"\n== RESULT: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed (aborted) ==")
            return 1

        # 统计条就绪（器官卡出现）
        bar_ready = cdp.wait_js(
            "(() => /器官\\n/.test(document.body.innerText))()", 15.0
        )
        check(bool(bar_ready), "P1: 统计条渲染（器官卡在场）")
        cdp.pump(1.0)
        body = cdp.js("document.body.innerText") or ""
        flat = re.sub(r"\s+", " ", body)

        # 对账真值=页面自己消费的 /api/system/overview 响应（CDP getResponseBody 真响应）——
        # CPU/内存是实时变动值，探针自取快照≠页面渲染时点值（CD-⑪ 同型探针自伤，禁重犯）
        ov_events = [
            e for e in cdp.events
            if e.get("method") == "Network.responseReceived"
            and "/api/system/overview" in e.get("params", {}).get("response", {}).get("url", "")
        ]
        page_ov = None
        if ov_events:
            rid = ov_events[-1]["params"]["requestId"]
            try:
                b = cdp.raw("Network.getResponseBody", {"requestId": rid})
                page_ov = json.loads(b.get("body", ""))
            except Exception:
                page_ov = None
        check(page_ov is not None, "P4 前置: 页面自身 /api/system/overview 响应可回读（对账真值来源）")
        if not PRE and page_ov is None:
            fails = [n for ok, n in RESULTS if not ok]
            print(f"\n== RESULT: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed (aborted: no page ov) ==")
            return 1

        organ_v = line_value(body, "器官")
        kb_v = line_value(body, "知识条目")
        cpu_v = line_value(body, "CPU")
        mem_v = line_value(body, "内存")

        if PRE:
            check("undefined/undefined" in body, "PRE 硬证: 「器官 undefined/undefined」在场", repr(organ_v))
            check(any("活跃Agent" in l or "活跃 Agent" in l for l in body.splitlines()), "PRE 硬证: 活跃Agent 死显示在场")
            check(any("近期会话" in l for l in body.splitlines()), "PRE 硬证: 近期会话 死显示在场")
            check("运行时间" in body, "PRE 硬证: 运行时间 死显示在场")
            check("undefined" in body, "PRE 硬证: 'undefined' 假渲染在场")
        else:
            organs = page_ov["organs"]
            metrics = page_ov["metrics"]
            knowledge = page_ov["knowledge"]
            expect_organ = f"{organs['healthy_count']}/{organs['total_count']}"
            expect_cpu = f"{metrics['cpu_percent']:.1f}%"
            expect_mem = f"{metrics['memory']['used_mb']} MB"
            check(organ_v == expect_organ, "P2: 器官卡值==页面真值", f"got={organ_v!r} expect={expect_organ!r}")
            check(organ_v is not None and "undefined" not in organ_v, "P2: 器官卡零 undefined")
            check(kb_v == str(knowledge["total_entries"]), "P3: 知识条目卡==页面真值", f"got={kb_v!r}")
            check(cpu_v == expect_cpu, "P4: CPU 卡==页面真值", f"got={cpu_v!r} expect={expect_cpu!r}")
            check(mem_v == expect_mem, "P4: 内存卡==页面真值", f"got={mem_v!r} expect={expect_mem!r}")
            check("undefined" not in body, "P5: 'undefined' 正文零出现")
            for label in ("活跃Agent", "活跃 Agent", "近期会话", "运行时间"):
                check(not any(label in l for l in body.splitlines()), f"P5: 死显示「{label}」零出现")
            check("运行 —" not in body, "P5: banner 运行 span 零出现")
            db_type = health["database"]["type"]
            check(f"数据库 {db_type}" in flat, "P7: banner 数据库==live health.database.type", f"expect 数据库 {db_type}")
            for label in ("版本", "CPU 使用率", "内存使用", "已启用器官"):
                check(any(l == label for l in (x.strip() for x in body.splitlines())), f"P8: 面板行「{label}」在场")

        cdp.pump(1.0)
        quick_reqs = [
            e for e in cdp.events
            if e.get("method") == "Network.requestWillBeSent"
            and "/api/system/quick" in e.get("params", {}).get("request", {}).get("url", "")
        ]
        if PRE:
            check(len(quick_reqs) >= 1, "PRE 硬证: /api/system/quick 请求真发（死消费面在场）", f"n={len(quick_reqs)}")
        else:
            check(len(quick_reqs) == 0, "P6: /api/system/quick 网络零请求（死消费面清零）", f"n={len(quick_reqs)}")

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
