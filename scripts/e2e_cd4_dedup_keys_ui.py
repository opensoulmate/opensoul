#!/usr/bin/env python3
"""CD-④ 修复候选① live UI e2e：dedup 页元素键收编（真浏览器 CDP 点击流）。

真数据全程（不打形状桩）：真 knowledge 入库两组同文条目（甲/乙两组=两个独立
pair）→ 真 /api/dedup/duplicates 报告 → /admin/dedup.html 真渲染真点击流：
P1 重复对卡片真标题渲染（修前 title_a/id_a 双 undefined=标题/ID 全空渲染）。
P2 无假渲染：'unknown' content_type 徽章（修前恒 'unknown'）零出现。
P3 展开卡片真 ID 渲染（修前 id 空白；条目 A/B 面板真源 item id）。
P4 搜索过滤真可用（修前平铺 id 裸 toLowerCase 对 undefined 必炸=输入即页崩）：
   无匹配词→"无匹配的重复项"；甲组词→甲组卡片回来且乙组卡片消失（真过滤）。
P5 删除按钮真生效（修前 onRemove(undefined) 本地过滤 p.id_a!==undefined 恒假=
   整列表全清）：点「删除条目B（保留A）」→ 甲组对消失、乙组对仍在（两 pair 判别）。
清痕：DELETE /api/knowledge/{id} ×4（真主删除面）→ /api/dedup/duplicates 我方
pair 消失 + knowledge 行 COUNT 回到 seed 前 → remaining=0。

用法：python3 scripts/e2e_cd4_dedup_keys_ui.py
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
PORT = 9463
MARK_A = "CD4E2E甲组标题"
MARK_B = "CD4E2E乙组标题"
BODY_A = "CD4E2E甲组内容" * 20
BODY_B = "CD4E2E乙组内容" * 20
DB = "/home/climbing/opensoul/data/opensoul.db"
SEARCH_SEL = "document.querySelector('input[placeholder^=\"搜索标题\"]')"

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

    def set_input(self, find_js: str, value: str) -> bool:
        expr = (
            "(() => { const el = "
            + find_js
            + "; if (!el) return false; "
            "const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set; "
            f"setter.call(el, {json.dumps(value)}); "
            "el.dispatchEvent(new Event('input', { bubbles: true })); return true; })()"
        )
        return self.js(expr) is True

    def add_init_script(self, source: str) -> None:
        self.send("Page.addScriptToEvaluateOnNewDocument", {"source": source})

    def navigate(self, url: str, wait: float = 3.0) -> None:
        self.send("Page.navigate", {"url": url})
        self.pump(wait)


def main() -> int:
    token, uid = make_user_token()
    pre_count = knowledge_count()

    # ── 真数据 seed：甲/乙两组同文条目=两个独立 pair（组内 similarity=1.0）──
    ids: list[str] = []
    for title, body in ((MARK_A, BODY_A), (MARK_B, BODY_B)):
        for _ in range(2):
            row = call(
                "POST",
                f"/api/knowledge/?user_id={uid}",
                token=token,
                body={"title": title, "content": body, "content_type": "text"},
            )
            ids.append(str(row["id"]))
    check(len(ids) == 4 and all(ids), "setup: 4 knowledge rows created (2 pairs)", ",".join(ids))

    # 真源形状取证（修后元素=嵌套形，UI 收编同形）
    report = call("GET", "/api/dedup/duplicates", token=token)
    pairs = [p for p in report.get("duplicates", [])
             if p["item_a"]["id"] in ids or p["item_b"]["id"] in ids]
    check(len(pairs) == 2, "setup: 2 independent duplicate pairs from true source", f"{len(pairs)} pairs")
    shape_ok = all(
        set(p.keys()) == {"item_a", "item_b", "similarity"}
        and set(p["item_a"].keys()) == {"id", "title"}
        and set(p["item_b"].keys()) == {"id", "title"}
        for p in pairs
    )
    check(shape_ok, "setup: true-source elements nested {item_a:{id,title},item_b:{id,title},similarity}",
          json.dumps(pairs[0], ensure_ascii=False)[:120] if pairs else "no pair")

    user_data = tempfile.mkdtemp(prefix="cdp_cd4_")
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
        cdp.add_init_script(f"try {{ localStorage.setItem('soul_token', {json.dumps(token)}); }} catch (e) {{}}")

        cdp.navigate(f"{BASE}/admin/dedup.html", 4.0)

        def body_text() -> str:
            return cdp.js("document.body.innerText") or ""

        # ── P1 真标题渲染（修前 title_a/id_a 双 undefined=全空）──
        ok_titles = cdp.wait_js(
            f"(() => {{ const t = document.body.innerText; return t.includes({json.dumps(MARK_A)}) && t.includes({json.dumps(MARK_B)}); }})()",
            20.0,
        )
        check(bool(ok_titles), "P1: both pair cards render real titles from item_a/item_b.title")

        # ── P2 无假渲染（修前恒 'unknown' content_type 徽章）──
        check("unknown" not in body_text(), "P2: zero fabricated 'unknown' content_type badge")

        # ── P3 展开卡片真 ID 渲染（修前 id 空白）──
        ok_open = cdp.click(
            "[...document.querySelectorAll('button')].find(b => b.textContent.includes("
            + json.dumps(MARK_A) + "))"
        )
        check(ok_open, "P3a: click pair card (甲组) to expand")
        a_ids = [i for i in ids[:2]]
        ok_ids = cdp.wait_js(
            f"(() => {{ const t = document.body.innerText; return t.includes({json.dumps(a_ids[0])}) && t.includes({json.dumps(a_ids[1])}); }})()",
            12.0,
        )
        check(bool(ok_ids), "P3: expanded card renders real ids from item_a/item_b.id", ",".join(a_ids))

        # ── P4 搜索过滤真可用（修前输入即 TypeError 页崩）──
        ok_set = cdp.set_input(SEARCH_SEL, "zzz-cd4-nomatch")
        check(ok_set, "P4a: type into dedup search input")
        ok_empty = cdp.wait_js(
            "(() => document.body.innerText.includes('无匹配的重复项') || "
            "document.body.innerText.includes('尝试调整筛选条件'))()",
            10.0,
        )
        check(bool(ok_empty), "P4: filter runs without crash (no-match state reachable)")
        cdp.set_input(SEARCH_SEL, "甲组")
        ok_filter = cdp.wait_js(
            f"(() => {{ const t = document.body.innerText; return t.includes({json.dumps(MARK_A)}) && !t.includes({json.dumps(MARK_B)}); }})()",
            10.0,
        )
        check(bool(ok_filter), "P4: real-key filter matches 甲组 only (乙组 filtered out)")

        # ── P5 删除按钮真生效（修前 onRemove(undefined) 整列表全清）──
        cdp.set_input(SEARCH_SEL, "")
        cdp.pump(1.0)
        # 甲组卡片 P3 已展开（expandedId 持续）——仅当未展开才补点，防收起后按钮不可见
        if cdp.js("([...document.querySelectorAll('button')].some(b => b.textContent.includes('删除条目B') && b.offsetParent !== null))") is not True:
            cdp.click(
                "[...document.querySelectorAll('button')].find(b => b.textContent.includes("
                + json.dumps(MARK_A) + "))"
            )
            cdp.pump(0.5)
        ok_del = cdp.click(
            "[...document.querySelectorAll('button')].find(b => b.textContent.includes('删除条目B') && b.offsetParent !== null)"
        )
        check(ok_del, "P5a: click 删除条目B（保留A）on 甲组 card")
        ok_judged = None
        end = time.time() + 10
        while time.time() < end:
            cdp.pump(0.3)
            t = body_text()
            if (not t or (json.dumps(MARK_A)[1:-1] not in t and MARK_A not in t)) and MARK_B in t:
                ok_judged = True
                break
        check(ok_judged is True,
              "P5: delete removes 甲组 pair only, 乙组 pair survives (pre-fix undefined id wipes ALL pairs)")
    finally:
        proc.terminate()

    # ── 清痕：真主删除面 DELETE /api/knowledge/{id} ──
    for kid in ids:
        try:
            call("DELETE", f"/api/knowledge/{kid}?user_id={uid}", token=token)
        except Exception as e:
            print("WARN cleanup", kid, e)
    report2 = call("GET", "/api/dedup/duplicates", token=token)
    left = [p for p in report2.get("duplicates", [])
            if p["item_a"]["id"] in ids or p["item_b"]["id"] in ids]
    post_count = knowledge_count()
    check(not left, "cleanup: seeded pairs gone from report", f"{len(left)} left")
    check(post_count <= pre_count, f"cleanup: knowledge rows back to pre-seed (pre={pre_count} post={post_count})")

    failed = [n for ok, n in RESULTS if not ok]
    print(f"\n== {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ==")
    if failed:
        print("FAILED:", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
