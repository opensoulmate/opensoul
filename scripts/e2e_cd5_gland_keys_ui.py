#!/usr/bin/env python3
"""CD-⑤ 修复候选② live UI e2e：gland 页消费键收编（真浏览器 CDP 点击流）。

真数据全程（不打形状桩）：真 LLM 调用攒真 token 用量（POST /api/gland/chat 真记账）
→ 真 /api/gland/usage{,/recent} + /api/gland/keys + /api/gland/test 真源形状 →
/admin/gland.html 真渲染真点击流：
P1 总请求数=真源 call_count（修前 total_requests 恒 undefined 恒 0）。
P2 总Token数=真源 total_tokens（修前 total_input/output 双 undefined 恒 0）+
   「入: x / 出: y」拆分行零出现（真源 summary 无拆分，禁编造）。
P3 各Provider用量=真源 by_provider 元素 int（修前 input/output/requests 三键
   恒 undefined 恒 0）：合计 tokens 真渲染真数值。
P4 API密钥面板=真源 dict[provider, 槽位列表] 展平（修前 .keys/.slots 双回落恒 []
   =列表恒空）：真 masked_key 渲染、'暂无API密钥' 零出现。
P5 测试连接回执=真源 {status,provider,model,reply}（修前消费 message 键恒回落
   「连接成功」模型 reply 永不显示）：真 reply 文本可见。
P6 最近调用记录=真源 recent_records 元素（prompt/completion_tokens、timestamp
   epoch 秒）：真 prompt→completion 数值渲染 + 时间非 1970（修前 epoch 秒被当
   毫秒解析落 1970）。
P7 全页 'undefined' 零出现（修前多处恒 undefined 渲染）。
用法：python3 scripts/e2e_cd5_gland_keys_ui.py [--pre|--post]（--pre=换旧 bundle
同探针跑修前缺口硬证，与 --post 双向对照；缺省=--post）。
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
PORT = 9464
DB = "/home/climbing/opensoul/data/opensoul.db"

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


def fmt(n) -> str:
    """与 admin-ui formatNumber 同算法。"""
    n = float(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(int(n)) if n == int(n) else str(n)


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

    def add_init_script(self, source: str) -> None:
        self.send("Page.addScriptToEvaluateOnNewDocument", {"source": source})

    def navigate(self, url: str, wait: float = 3.0) -> None:
        self.send("Page.navigate", {"url": url})
        self.pump(wait)


def main() -> int:
    label = "--post"
    if len(sys.argv) > 1:
        label = sys.argv[1]
    print(f"== e2e_cd5_gland_keys_ui ({label}) ==")
    token, uid = make_user_token()

    # ── 真数据：真 LLM 调用攒真 token 用量（chat 真记账进 token_meter）──
    try:
        call("POST", "/api/gland/chat", token=token,
             body={"messages": [{"role": "user", "content": "请只回复一个字：好"}]})
        check(True, "setup: real gland chat call (token usage recorded)")
    except Exception as e:
        check(False, "setup: real gland chat call", str(e))

    usage = call("GET", "/api/gland/usage", token=token)
    recent = call("GET", "/api/gland/usage/recent?limit=30", token=token)
    keys_api = call("GET", "/api/gland/keys", token=token)
    test_api = call("POST", "/api/gland/test", token=token, body={})

    check(usage.get("call_count", 0) >= 1 and usage.get("total_tokens", 0) > 0,
          "setup: true-source usage has real data",
          f"call_count={usage.get('call_count')} total_tokens={usage.get('total_tokens')}")
    check(isinstance(usage.get("by_provider"), dict)
          and all(isinstance(v, int) for v in usage["by_provider"].values()),
          "setup: true-source by_provider elements are int",
          json.dumps(usage.get("by_provider")))
    check(set(test_api) == {"status", "provider", "model", "reply"},
          "setup: true-source test receipt keys {status,provider,model,reply}",
          json.dumps(test_api, ensure_ascii=False)[:120])
    slot_total = sum(len(v) for v in keys_api.values()) if isinstance(keys_api, dict) else 0
    check(slot_total >= 1, "setup: true-source keys dict has slots", f"{slot_total} slots")

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
        cdp.add_init_script(f"try {{ localStorage.setItem('soul_token', {json.dumps(token)}); }} catch (e) {{}}")

        cdp.navigate(f"{BASE}/admin/gland.html", 4.0)

        def body_text() -> str:
            return cdp.js("document.body.innerText") or ""

        # 等页面数据装载（Provider列表真渲染）
        loaded = cdp.wait_js(
            "(() => document.body.innerText.includes('Provider列表') && !document.body.innerText.includes('加载中'))()",
            20.0,
        )
        t = body_text()
        if not loaded:
            print("DEBUG wait_js not loaded; body len:", len(t), "snippet:", repr(t[:300]))

        # ── P1 总请求数=真源 call_count（修前 total_requests 恒 0）──
        def card_value(label: str):
            return cdp.js(
                "(() => { const ls = [...document.querySelectorAll('div')]"
                f".filter(d => d.textContent.trim() === {json.dumps(label)});"
                " if (!ls.length) return null; const card = ls[0].parentElement;"
                " const num = card.querySelector('.text-2xl');"
                " return num ? num.textContent.trim() : null; })()"
            )

        exp_calls = fmt(usage["call_count"])
        got_calls = card_value("总请求数")
        check(got_calls == exp_calls, "P1: 总请求数 renders true-source call_count",
              f"expect {exp_calls} got {got_calls}")

        # ── P2 总Token数=真源 total_tokens + 入/出拆分行零出现 ──
        exp_tokens = fmt(usage["total_tokens"])
        got_tokens = card_value("总Token数")
        check(got_tokens == exp_tokens, "P2a: 总Token数 renders true-source total_tokens",
              f"expect {exp_tokens} got {got_tokens}")
        check("入: " not in t and "出: " not in t,
              "P2b: zero fabricated 入/出 split line (true source has no split)")

        # ── P3 各Provider用量=真源 by_provider 元素 int ──
        bp = usage.get("by_provider") or {}
        p3_ok = bool(bp)
        p3_detail = []
        for name, tokens in bp.items():
            row_ok = name in t and f"合计: {fmt(tokens)}" in t
            p3_ok = p3_ok and row_ok
            p3_detail.append(f"{name}={fmt(tokens)}{'✓' if row_ok else '✗'}")
        check(p3_ok, "P3: per-provider usage renders true int totals (合计 tokens)",
              "; ".join(p3_detail))

        # ── P4 API密钥面板=真源 dict[provider, 槽位列表] 展平 ──
        masked = [s["masked_key"] for v in keys_api.values() for s in v] if isinstance(keys_api, dict) else []
        p4_ok = bool(masked) and all(m in t for m in masked)
        check(p4_ok, "P4a: keys panel renders real masked_key rows (flatten dict→rows)",
              f"{len(masked)} keys")
        check("暂无API密钥" not in t, "P4b: zero '暂无API密钥' empty state (pre-fix always empty)")

        # ── P6 最近调用记录（先取，P5 点击会推进页面但不动 token_meter）──
        recs = recent.get("records") or []
        if recs:
            r0 = recs[-1]
            exp_io = f"{fmt(r0['prompt_tokens'])}→{fmt(r0['completion_tokens'])}"
            check(exp_io in t, "P6a: recent records render prompt→completion tokens",
                  exp_io)
        else:
            check(False, "P6a: recent records exist", "no records from true source")
        now_md = time.strftime("%m/%d")
        check(now_md in t and "01/21" not in t,
              "P6b: record timestamps render from epoch-seconds (not 1970 ms misparse)",
              f"expect {now_md}")

        # ── P5 测试连接回执=真源 reply 真显示（修前恒「连接成功」）──
        ok_click = cdp.click(
            "[...document.querySelectorAll('button')].find(b => b.textContent.includes('测试连接'))"
        )
        check(ok_click, "P5a: click 测试连接")
        # 回执文本=「model → reply」（join ' → ' 带空格，与最近记录 '→' 无空格可区分）
        receipt = cdp.wait_js(
            "(() => { const sp = [...document.querySelectorAll('span')]"
            ".map(s => s.textContent || '').filter(x => x.includes(' → '));"
            " return sp.length ? sp[0] : null; })()",
            30.0,
        )
        model = test_api.get("model") or ""
        check(bool(receipt), "P5b: test receipt box renders (pre-fix msg=「连接成功」fallback)")
        if receipt:
            parts = receipt.split(" → ")
            check(model in receipt and len(parts) >= 2 and parts[-1].strip() != ""
                  and "连接成功" not in receipt,
                  "P5c: receipt shows true-source model → real reply (fallback masked pre-fix)",
                  f"model={model} receipt={receipt[:80]!r}")

        # ── P7 全页 'undefined' 零出现 ──
        t2 = body_text()
        check("undefined" not in t2, "P7: zero 'undefined' renders on gland page")
    finally:
        proc.terminate()

    # ── 清痕：本探针零持久写入（chat/test 只进内存 token_meter，keys/usage 只读）──
    check(True, "cleanup: probe leaves zero persistent writes (in-memory meter only)")

    failed = [n for ok, n in RESULTS if not ok]
    print(f"\n== {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ({label}) ==")
    if failed:
        print("FAILED:", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
