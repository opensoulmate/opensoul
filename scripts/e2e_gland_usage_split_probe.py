#!/usr/bin/env python3
"""DEC-㉖-① live e2e：gland 用量显示拆分恢复（真源补现 入/出 token 拆分 B1）。

真数据全程：真 LLM 调用（POST /api/gland/chat，token_meter.record 真记账）→
GET /api/gland/usage 真源形状 → 部署 chunk HTTP 回读 → /admin/gland.html 真渲染：
P1 真源形状：summary 恰新增两轴 total_prompt_tokens/total_completion_tokens（入+出
   ==total_tokens 自洽）、by_provider 元素恰 {prompt,completion,total,requests}、
   requests==真调用次数、逐 provider 两轴求和==顶层两轴；
P2 部署 chunk：total_prompt_tokens/data.prompt 等真消费在场、旧漂移键
   （total_input_tokens/data.input 等）与 {r.task} 零出现；
P3 浏览器渲染：总Token卡「入: x / 出: y」行==API 真值过 formatNumber 同算法逐字
   对账（CD-⑪ 反自伤纪律）、by_provider 行「请求/入/出/合计」真渲染逐值对账。
清痕：token_meter 为进程内存（无资源行可清）；timeline 事件行面查 MARK 零残留
（TR-② 纪律：探针清痕必须含事件行面）。

用法：python3 scripts/e2e_gland_usage_split_probe.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
PORT = 9467
MARK = "GLAND26E2E"
TIMELINE_DB = "/home/climbing/opensoul/data/timeline.db"

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


def call(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else {}


def make_user_token() -> str:
    """真 JWT（soul_token 登录态，dedup 探针同款；不触凭据）。"""
    sys.path.insert(0, "/home/climbing/opensoul")
    from src.services.auth import create_access_token  # noqa: E402

    db = sqlite3.connect("/home/climbing/opensoul/data/opensoul.db")
    row = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()
    db.close()
    if not row:
        raise RuntimeError("admin user row missing")
    return create_access_token(uuid.UUID(str(row[0])))


def format_number(n: float) -> str:
    """页面 formatNumber 算法逐字复制（反自伤：同一算法算期望值）。"""
    if n >= 1000000:
        return f"{n / 1000000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(int(n))


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

    def add_init_script(self, source: str) -> None:
        self.send("Page.addScriptToEvaluateOnNewDocument", {"source": source})

    def navigate(self, url: str, wait: float = 3.0) -> None:
        self.send("Page.navigate", {"url": url})
        self.pump(wait)


def main() -> int:
    pre_timeline = timeline_marker_rows()

    # ── seed：真 LLM 调用（token_meter.record 真记账）──
    before = call("GET", "/api/gland/usage")
    before_calls = before.get("call_count", 0)
    reply = call(
        "POST",
        "/api/gland/chat",
        body={"messages": [{"role": "user", "content": f"请原样回复：{MARK}"}], "task": "chat"},
    )
    check(bool(reply), "setup: real chat call recorded", str(reply)[:120])

    usage = call("GET", "/api/gland/usage")

    # ── P1 真源形状 ──
    check(
        usage.get("call_count", 0) == before_calls + 1,
        "P1a: call_count incremented by exactly 1 real call",
        f"{before_calls} -> {usage.get('call_count')}",
    )
    check(
        isinstance(usage.get("total_prompt_tokens"), int)
        and isinstance(usage.get("total_completion_tokens"), int),
        "P1b: summary split axes produced (total_prompt_tokens/total_completion_tokens)",
        f"p={usage.get('total_prompt_tokens')} c={usage.get('total_completion_tokens')}",
    )
    check(
        usage.get("total_prompt_tokens", -1) + usage.get("total_completion_tokens", -1)
        == usage.get("total_tokens", -2),
        "P1c: prompt + completion == total (axes self-consistent)",
        f"{usage.get('total_prompt_tokens')}+{usage.get('total_completion_tokens')}=={usage.get('total_tokens')}",
    )
    by = usage.get("by_provider", {})
    check(bool(by), "P1d: by_provider non-empty after real call", json.dumps(by)[:160])
    shape_ok = all(
        set(v.keys()) == {"prompt", "completion", "total", "requests"} for v in by.values()
    )
    check(shape_ok, "P1e: by_provider element exactly {prompt,completion,total,requests}")
    check(
        sum(v["requests"] for v in by.values()) == usage.get("call_count", -1),
        "P1f: element requests sum == call_count (no phantom calls)",
        f"{sum(v['requests'] for v in by.values())} == {usage.get('call_count')}",
    )
    check(
        sum(v["prompt"] for v in by.values()) == usage.get("total_prompt_tokens", -1)
        and sum(v["completion"] for v in by.values()) == usage.get("total_completion_tokens", -1),
        "P1g: per-provider axis sums == top-level axes (single source, no drift)",
    )

    # ── P2 部署 chunk 回读（构建产物文件直读）──
    import pathlib

    chunk_dir = pathlib.Path("/home/climbing/opensoul/src/static/admin/_next/static/chunks")
    joined = ""
    for p in chunk_dir.glob("*.js"):
        joined += p.read_text(encoding="utf-8", errors="ignore")
    check("total_prompt_tokens" in joined, "P2a: deployed chunk consumes total_prompt_tokens")
    check("total_completion_tokens" in joined, "P2b: deployed chunk consumes total_completion_tokens")
    check(".prompt" in joined and ".completion" in joined, "P2c: deployed chunk consumes element split keys")
    residue = {
        k: joined.count(k)
        for k in ("total_input_tokens", "total_output_tokens", "budget_used_pct")
    }
    check(
        all(v == 0 for v in residue.values()),
        "P2d: old drift keys absent in deployed chunks",
        json.dumps(residue),
    )
    check(
        not re.search(r"\{r\.task\}", joined), "P2e: {r.task} absent in deployed chunks"
    )

    # ── P3 浏览器真渲染 ──
    import subprocess
    import tempfile

    prof = tempfile.mkdtemp(prefix="cdp_gland26_")
    proc = subprocess.Popen(
        [
            "chromium", "--headless=new", f"--remote-debugging-port={PORT}",
            "--remote-allow-origins=*",
            "--no-first-run", "--no-default-browser-check", "--window-size=1440,2200",
            f"--user-data-dir={prof}", "about:blank",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        import urllib.request as ur

        ws_url = None
        for _ in range(40):
            time.sleep(0.5)
            try:
                tabs = json.loads(ur.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=3).read())
                for t in tabs:
                    if t.get("type") == "page":
                        ws_url = t["webSocketDebuggerUrl"]
                        break
            except Exception:
                continue
            if ws_url:
                break
        check(bool(ws_url), "P3a: CDP headless attached", str(ws_url))
        cdp = CDP(ws_url)
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        cdp.add_init_script(
            "try { localStorage.setItem('soul_token', "
            + json.dumps(make_user_token())
            + "); } catch (e) {}"
        )
        cdp.navigate(f"http://127.0.0.1:8090/admin/gland.html", wait=6)
        cdp.pump(3)

        # 总Token卡拆分行==API 真值过同一 formatNumber 算法逐字对账
        body_text = cdp.js("document.body.innerText") or ""
        exp_in = format_number(usage.get("total_prompt_tokens", 0))
        exp_out = format_number(usage.get("total_completion_tokens", 0))
        expect_line = f"入: {exp_in} / 出: {exp_out}"
        check(expect_line in body_text, "P3b: top split line renders == page-derived truth",
              f"expect={expect_line!r}")
        check("入: " in body_text and "出: " in body_text, "P3c: split labels rendered")

        # by_provider 行：请求/入/出/合计 逐值对账（取第一个 provider）
        prov = next(iter(by.items()), None)
        if prov:
            name, v = prov
            exp_req = f"{format_number(v['requests'])} 请求"
            exp_pin = f"入: {format_number(v['prompt'])}"
            exp_pout = f"出: {format_number(v['completion'])}"
            exp_sum = f"合计: {format_number(v['total'])}"
            for label, exp in (("请求行", exp_req), ("入", exp_pin), ("出", exp_pout), ("合计", exp_sum)):
                check(exp in body_text, f"P3d[{label}]: by_provider row renders {exp!r}", name)
        check("unknown" not in body_text, "P3e: no 'unknown' fabrication on page")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    # ── 清痕（TR-② 纪律：含事件行面）──
    post_timeline = timeline_marker_rows()
    if post_timeline and post_timeline > 0:
        db = sqlite3.connect(TIMELINE_DB)
        db.execute(
            "DELETE FROM events WHERE summary LIKE ? OR detail LIKE ?", (f"%{MARK}%", f"%{MARK}%")
        )
        db.commit()
        db.close()
    check(
        timeline_marker_rows() == 0,
        "cleanup: timeline event rows with MARK == 0 (TR-② discipline)",
        f"pre={pre_timeline} post={timeline_marker_rows()}",
    )

    ok = sum(1 for o, _ in RESULTS if o)
    print(f"\n== {ok}/{len(RESULTS)} ==")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
