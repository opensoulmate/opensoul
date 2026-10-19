#!/usr/bin/env python3
"""DEC-㉕-①（DEDUP-①）live e2e：dedup 报告元素时间/类型显示恢复（真源补现）。

真数据全程：真 knowledge 入库两条同文条目（content_type 各异 text/markdown、
created_at 相隔 ≥1s 保证可区分）→ 真 /api/dedup/duplicates 报告 → 部署 chunk
HTTP 回读 → /admin/dedup.html 真渲染真点击流：
P1 真源形状：元素 item 恰 4 键 {id,title,created_at,content_type}、逐值真落
   （created_at_a≠created_at_b、content_type 逐 item 精确匹配 text/markdown、
   item_a=较早行随 ORDER BY created_at ASC）、平铺形零产出。
P2 部署 chunk：嵌套键真消费（.item_a.created_at/.item_b.content_type 等）、
   平铺形（created_at_a/created_at_b）与 'unknown' fabrication 零出现。
P3 浏览器展开卡片：两条时间行真渲染且==页面自身真值（API created_at 过同一
   formatTime 算法在页内重算逐字对账，CD-⑪ 反自伤纪律）、类型徽章真渲染
   'text'/'markdown' 逐 item、'unknown' 零出现。
清痕：DELETE /api/knowledge/{id} ×2（真主删除面）→ 报告回读 + knowledge 行
COUNT 回基线 + 事件行面清痕（TR-② 纪律：探针清痕必须含事件行面）。

用法：python3 scripts/e2e_dedup_time_type_probe.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
PORT = 9464
MARK = "DEDUP1E2E"
TITLE = f"{MARK}同文标题"
BODY = f"{MARK}同文内容" * 20
DB = "/home/climbing/opensoul/data/opensoul.db"
TIMELINE_DB = "/home/climbing/opensoul/data/timeline.db"
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


def timeline_marker_rows() -> int:
    try:
        db = sqlite3.connect(TIMELINE_DB)
        n = db.execute(
            "SELECT COUNT(*) FROM events WHERE summary LIKE ? OR detail LIKE ?",
            (f"%{MARK}%", f"%{MARK}%"),
        ).fetchone()[0]
        db.close()
        return n
    except Exception as e:  # 如实报
        print("WARN timeline count", e)
        return -1


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
    pre_timeline = timeline_marker_rows()

    # ── seed：两条同文条目（content_type 各异、created_at 相隔 ≥1s 可区分）──
    ids: list[str] = []
    for ctype in ("text", "markdown"):
        row = call(
            "POST",
            f"/api/knowledge/?user_id={uid}",
            token=token,
            body={"title": TITLE, "content": BODY, "content_type": ctype},
        )
        ids.append(str(row["id"]))
        if ctype == "text":
            # created_at=formatTime 分辨率到分钟——保证两行显示标签可区分：跨分钟再入库
            now = time.time()
            time.sleep(60 - (now % 60) + 2)
    check(len(ids) == 2 and all(ids), "setup: 2 knowledge rows (text then markdown)", ",".join(ids))

    # ── P1 真源形状 + 逐值对账 ──
    report = call("GET", "/api/dedup/duplicates", token=token)
    pairs = [p for p in report.get("duplicates", [])
             if p["item_a"]["id"] in ids and p["item_b"]["id"] in ids]
    check(len(pairs) == 1, "P1a: exactly 1 pair among seeded rows", f"{len(pairs)} pairs")
    pair = pairs[0] if pairs else {}
    a, b = pair.get("item_a", {}), pair.get("item_b", {})
    check(set(a.keys()) == {"id", "title", "created_at", "content_type"}
          and set(b.keys()) == {"id", "title", "created_at", "content_type"},
          "P1b: item keys exactly {id,title,created_at,content_type}",
          json.dumps(pair, ensure_ascii=False)[:160])
    check(a.get("content_type") == "text" and b.get("content_type") == "markdown",
          "P1c: content_type verbatim per item (item_a=text earlier, item_b=markdown)",
          f"a={a.get('content_type')} b={b.get('content_type')}")
    check(bool(a.get("created_at")) and bool(b.get("created_at"))
          and a.get("created_at") != b.get("created_at"),
          "P1d: created_at real and distinct (no fabrication/mixup)",
          f"a={a.get('created_at')} b={b.get('created_at')}")
    check(a.get("id") == ids[0] and b.get("id") == ids[1],
          "P1e: item_a=earlier row per ORDER BY created_at ASC", f"{a.get('id')} {b.get('id')}")
    flat_absent = all(k not in pair for k in ("created_at_a", "created_at_b", "content_type"))
    check(flat_absent, "P1f: flat shape stays absent (CD-④ adjudication axis unchanged)")

    # ── P2 部署 chunk HTTP 回读 ──
    try:
        with urllib.request.urlopen(f"{BASE}/admin/dedup.html", timeout=10) as r:
            html = r.read().decode()
        srcs = re.findall(r'src="(/admin/_next/static/chunks/[^"]+)"', html)
        chunk = ""
        for s in srcs:
            with urllib.request.urlopen(BASE + s, timeout=10) as r:
                body_txt = r.read().decode()
            if "条目 A" in body_txt:
                chunk = body_txt
                break
        check(bool(chunk), "P2a: deployed dedup chunk located via live HTTP", f"{len(srcs)} scripts")
        check(".item_a.created_at" in chunk and ".item_b.created_at" in chunk
              and ".item_a.content_type" in chunk and ".item_b.content_type" in chunk,
              "P2b: nested restored keys consumed in deployed chunk")
        check("created_at_a" not in chunk and "created_at_b" not in chunk
              and "'unknown'" not in chunk,
              "P2c: flat keys + 'unknown' fabrication absent in deployed chunk")
    except Exception as e:
        check(False, "P2: chunk readback", str(e))
        chunk = ""

    # ── P3 真浏览器展开卡片 ──
    user_data = tempfile.mkdtemp(prefix="cdp_dedup1_")
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
        cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
        cdp.add_init_script(f"try {{ localStorage.setItem('soul_token', {json.dumps(token)}); }} catch (e) {{}}")
        cdp.navigate(f"{BASE}/admin/dedup.html", 4.0)

        # 等卡片真渲染再点（防加载竞态假阴——CD-④ 探针同款 wait 前置）
        ok_loaded = cdp.wait_js(
            f"(() => document.body.innerText.includes({json.dumps(TITLE)}))()",
            20.0,
        )
        check(bool(ok_loaded), "P3-pre: seeded pair card rendered")

        ok_open = cdp.click(
            "[...document.querySelectorAll('button')].find(b => b.textContent.includes("
            + json.dumps(TITLE) + "))"
        )
        check(ok_open, "P3a: click seeded pair card to expand")

        # 页面自身真值：API created_at 过页面同款 formatTime 算法在页内重算（反自伤纪律）
        fmt_expr = (
            "(() => { const fmt = (ts) => { if (!ts) return '-'; try { return new Date(ts).toLocaleString('zh-CN', "
            "{ month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }); } catch (e) { return ts; } }; "
            f"return [fmt({json.dumps(a.get('created_at'))}), fmt({json.dumps(b.get('created_at'))})]; }})()"
        )
        expected_times = cdp.js(fmt_expr) or []
        check(len(expected_times) == 2 and expected_times[0] != expected_times[1],
              "P3b: in-page recompute of expected time labels (distinct)", str(expected_times))

        def expanded_text() -> str:
            return cdp.js(
                "(() => { const btns=[...document.querySelectorAll('button')].filter(b => b.textContent.includes("
                + json.dumps(TITLE) + ")); const root = btns[0] && btns[0].parentElement; "
                "return root ? root.innerText : ''; })()"
            ) or ""

        end = time.time() + 12
        ok_times = None
        while time.time() < end:
            cdp.pump(0.3)
            t = expanded_text()
            if expected_times and all(x in t for x in expected_times):
                ok_times = True
                break
        check(ok_times is True,
              "P3: both time rows render == page-own true values (verbatim)",
              f"expected={expected_times}")

        t_exp = expanded_text()
        check("text" in t_exp and "markdown" in t_exp,
              "P3c: type badges render real content_type per item", t_exp.replace("\n", "|")[:120])
        check("unknown" not in (cdp.js("document.body.innerText") or ""),
              "P3d: zero fabricated 'unknown' badge in full page")
    finally:
        proc.terminate()

    # ── 清痕：真主删除面 + 事件行面（TR-② 纪律）──
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
    # 事件行面清痕（若有标记行）
    try:
        db = sqlite3.connect(TIMELINE_DB)
        cur = db.execute(
            "DELETE FROM events WHERE summary LIKE ? OR detail LIKE ?",
            (f"%{MARK}%", f"%{MARK}%"),
        )
        db.commit()
        deleted = cur.rowcount
        db.close()
        post_timeline = timeline_marker_rows()
        check(post_timeline == 0,
              f"cleanup: timeline event rows with marker cleared (deleted={deleted}, pre={pre_timeline})")
    except Exception as e:
        check(False, "cleanup: timeline event rows", str(e))

    failed = [n for ok, n in RESULTS if not ok]
    print(f"\n== {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ==")
    if failed:
        print("FAILED:", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
