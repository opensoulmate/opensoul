#!/usr/bin/env python3
"""CD-⑧ live UI e2e：DedupResult 面收编（真浏览器 CDP 点击流）。

真数据全程（不打形状桩）：真 knowledge 入库两条同文条目（1 个 pair）→ 真
/api/dedup/duplicates 报告 → /admin/dedup.html 真渲染真点击流：
S0 安全前置：全库 pre-existing 重复对必须为 0（「自动去重」不带 user_id=全库去重，
   若有他用户重复对在场点击即删他人数据 → REFUSE 中止禁碰生产数据）。
P1 重复对卡片真标题渲染（CD-④ 面零回归）。
P2 点「自动去重」→确认对话框→「确认去重」→ 结果面板真渲染（修前 details 元素
   dict 渲染 {d} 必炸 React child=出结果面板即整页崩）。
P3 结果面板真值：发现重复/已删除/扫描条目 三卡真值 1/1/2（修前第三卡是死键
   merged「已合并」恒空白）。
P4 details 元素 dict 真源键渲染：明细行含真 title+「重复，相似度」+100%。
P5 无 crash 无假渲染：window error 监听零「Objects are not valid as a React
   child」、结果面板下方「知识去重引擎」banner 仍在（页未崩）、'undefined' 零出现、
   「已合并」零出现。
清痕：真主删除面 DELETE 剩余条目 → knowledge 行 COUNT 回 seed 前 → remaining=0。

用法：python3 scripts/e2e_cd8_dedup_result_ui.py
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
PORT = 9467
MARK = "CD8E2E结果面板标题"
BODY = "CD8E2E结果面板内容" * 20
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
    with urllib.request.urlopen(req, timeout=20) as r:
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


def knowledge_count() -> int:
    db = sqlite3.connect(DB)
    n = db.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
    db.close()
    return n


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

    def add_init_script(self, source: str) -> None:
        self.send("Page.addScriptToEvaluateOnNewDocument", {"source": source})

    def navigate(self, url: str, wait: float = 3.0) -> None:
        self.send("Page.navigate", {"url": url})
        self.pump(wait)


def main() -> int:
    token, uid = make_user_token()
    pre_count = knowledge_count()

    # ── S0 安全前置：全库去重禁碰他人数据（auto-dedup 不带 user_id）──
    pre_report = call("GET", "/api/dedup/duplicates", token=token)
    check(
        pre_report.get("total_pairs", -1) == 0,
        "S0: zero pre-existing duplicate pairs (auto-dedup is whole-db, refuse if foreign pairs exist)",
        f"total_pairs={pre_report.get('total_pairs')}",
    )
    if pre_report.get("total_pairs"):
        return 1

    # ── 真数据 seed：两条同文条目=1 个 pair ──
    ids: list[str] = []
    for _ in range(2):
        row = call(
            "POST",
            f"/api/knowledge/?user_id={uid}",
            token=token,
            body={"title": MARK, "content": BODY, "content_type": "text"},
        )
        ids.append(str(row["id"]))
    check(len(ids) == 2 and all(ids), "setup: 2 knowledge rows created (1 pair)", ",".join(ids))

    report = call("GET", "/api/dedup/duplicates", token=token)
    pairs = [
        p
        for p in report.get("duplicates", [])
        if p["item_a"]["id"] in ids or p["item_b"]["id"] in ids
    ]
    check(len(pairs) == 1, "setup: exactly 1 duplicate pair from true source", f"{len(pairs)} pairs")

    user_data = tempfile.mkdtemp(prefix="cdp_cd8_")
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
            "try { localStorage.setItem('soul_token', " + json.dumps(token) + "); } catch (e) {}"
        )
        # crash-class 监听：React dict child 渲染错误必为 uncaught error
        cdp.add_init_script(
            "window.__errs = [];"
            "window.addEventListener('error', function(e){ window.__errs.push(String(e && e.message || e)); });"
            "window.addEventListener('unhandledrejection', function(e){ window.__errs.push(String(e && e.reason)); });"
        )

        cdp.navigate(f"{BASE}/admin/dedup.html", 4.0)

        def body_text() -> str:
            return cdp.js("document.body.innerText") or ""

        # ── P1 重复对卡片真标题渲染（CD-④ 面零回归）──
        ok_titles = cdp.wait_js(
            f"(() => document.body.innerText.includes({json.dumps(MARK)}))()", 20.0
        )
        check(bool(ok_titles), "P1: pair card renders real title (CD-④ face zero-regression)")
        if not ok_titles:
            # 失败自诊断：dump 真实 body 状态（加载中/错误条/其他）+ Network 真实响应
            print("DIAG body:", repr(body_text()[:400]))
            print("DIAG errs:", cdp.js("window.__errs"))
            for e in cdp.events:
                if e.get("method") == "Network.responseReceived":
                    url = e.get("params", {}).get("response", {}).get("url", "")
                    if "dedup" in url:
                        print("DIAG net:", url, e["params"]["response"].get("status"))

        # ── P2 自动去重→确认→结果面板真渲染（修前出面板即页崩）──
        ok_btn = cdp.click(
            "[...document.querySelectorAll('button')].find(b => b.textContent.includes('自动去重') && b.offsetParent !== null)"
        )
        check(ok_btn, "P2a: click 自动去重")
        ok_dialog = cdp.wait_js(
            "(() => document.body.innerText.includes('确认自动去重'))()", 8.0
        )
        check(bool(ok_dialog), "P2b: confirm dialog appears")
        ok_confirm = cdp.click(
            "[...document.querySelectorAll('button')].find(b => b.textContent.includes('确认去重') && b.offsetParent !== null)"
        )
        check(ok_confirm, "P2c: click 确认去重")
        ok_panel = cdp.wait_js("(() => document.body.innerText.includes('去重完成'))()", 15.0)
        check(bool(ok_panel), "P2: dedup result panel renders (pre-fix dict child = whole-page crash)")

        # ── P3 结果面板真值（修前第三卡死键 merged 恒空白）──
        def stat_card(label: str):
            return cdp.js(
                "(() => { const lbl = [...document.querySelectorAll('div')]"
                f".find(d => d.children.length === 0 && d.textContent === {json.dumps(label)});"
                " if (!lbl || !lbl.parentElement) return null;"
                " return lbl.parentElement.textContent; })()"
            )

        cdp.pump(1.0)
        c_found = stat_card("发现重复")
        c_removed = stat_card("已删除")
        c_total = stat_card("扫描条目")
        check(c_found == "1发现重复", "P3a: 发现重复 card real value 1", repr(c_found))
        check(c_removed == "1已删除", "P3b: 已删除 card real value 1", repr(c_removed))
        check(c_total == "2扫描条目", "P3c: 扫描条目 card real value 2 (pre-fix merged dead key blank)", repr(c_total))

        # ── P4 details 元素 dict 真源键渲染 ──
        detail_text = cdp.js(
            "(() => { const ps = [...document.querySelectorAll('p')];"
            " const p = ps.find(x => x.textContent.includes('重复，相似度')); return p ? p.textContent : null; })()"
        )
        ok_detail = bool(detail_text) and MARK in detail_text and "100%" in detail_text
        check(ok_detail, "P4: details element renders dict keys (title/duplicate_of/similarity)", repr(detail_text))

        # ── P5 无 crash 无假渲染 ──
        errs = cdp.js("window.__errs") or []
        react_child_errs = [e for e in errs if "valid as a React child" in str(e)]
        check(not react_child_errs, "P5a: zero 'Objects are not valid as a React child' errors", repr(errs[:3]))
        t = body_text()
        check("知识去重引擎" in t, "P5b: page alive — banner below result panel still rendered")
        check("undefined" not in t, "P5c: zero 'undefined' fabrication in rendered page")
        check("已合并" not in t, "P5d: merged dead display face zero (pre-fix blank 已合并 stat)")

        # ── API 真值回读：去重真删 1 条、我方 pair 消失 ──
        report2 = call("GET", "/api/dedup/duplicates", token=token)
        left = [
            p
            for p in report2.get("duplicates", [])
            if p["item_a"]["id"] in ids or p["item_b"]["id"] in ids
        ]
        check(not left, "API: seeded pair removed by real dedup", f"{len(left)} left")
    finally:
        proc.terminate()

    # ── 清痕：真主删除面 DELETE /api/knowledge/{id} ──
    for kid in ids:
        try:
            call("DELETE", f"/api/knowledge/{kid}?user_id={uid}", token=token)
        except Exception as e:
            print("WARN cleanup", kid, e)
    post_count = knowledge_count()
    check(post_count <= pre_count, f"cleanup: knowledge rows back to pre-seed (pre={pre_count} post={post_count})")

    failed = [n for ok, n in RESULTS if not ok]
    print(f"\n== {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ==")
    if failed:
        print("FAILED:", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
