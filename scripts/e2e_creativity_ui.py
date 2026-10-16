#!/usr/bin/env python3
"""G1-③b-2 live UI e2e 探针：admin-ui 备选方案 UI（on-demand 生成/重生成）。

真浏览器（chromium headless + CDP）复现用户点击流：
  cortex 页 → 备选方案 tab → 输入问题 → 生成 → 结构化备选方案块渲染（UI==API 真值
  回读：fetch 拦截捕获 /api/brain/creativity 响应载荷，逐条对照 DOM）→ 重新生成
  （on-demand 重生成真再次调用）→ 哨兵错误分支（桩响应渲染诚实 error 行）→
  任务规划 tab（真 LLM 规划+备选方案块，G1-③b-1 加性键 UI 真消费）。

截图留 /tmp/cdp_shots/ 供视检。零数据落库（纯计算面）无需清痕。

用法：python3 scripts/e2e_creativity_ui.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
PORT = 9447
MARK = "e2e-creative-ui-" + uuid.uuid4().hex[:6]

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
        with urllib.request.urlopen(req, timeout=60) as r:
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


class CDP:
    def __init__(self, ws_url: str):
        import websocket

        self.ws = websocket.create_connection(ws_url, timeout=10)
        self.mid = 0

    def send(self, method: str, params: dict | None = None):
        self.mid += 1
        self.ws.send(json.dumps({"id": self.mid, "method": method, "params": params or {}}))
        return self.mid

    def result(self, mid: int, deadline: float = 15.0):
        end = time.time() + deadline
        while time.time() < end:
            self.ws.settimeout(max(0.1, end - time.time()))
            try:
                msg = json.loads(self.ws.recv())
            except Exception:
                continue
            if msg.get("id") == mid:
                return msg.get("result", {})
        return {}

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self.ws.settimeout(max(0.1, min(0.5, end - time.time())))
            try:
                json.loads(self.ws.recv())
            except Exception:
                continue

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

    def set_controlled(self, find_js: str, value: str) -> bool:
        """React 受控输入赋值（native setter + input 事件，React 真感知）。"""
        proto = "HTMLTextAreaElement" if "textarea" in find_js else "HTMLInputElement"
        expr = (
            "(() => { const el = " + find_js + "; if (!el) return false; "
            "const s = Object.getOwnPropertyDescriptor(window." + proto + ".prototype, 'value').set; "
            "s.call(el, " + json.dumps(value) + "); "
            "el.dispatchEvent(new Event('input', { bubbles: true })); return true; })()"
        )
        return self.js(expr) is True

    def screenshot(self, path: str) -> None:
        mid = self.send("Page.captureScreenshot", {"format": "png"})
        res = self.result(mid, 20.0)
        import base64

        data = res.get("data")
        if data:
            with open(path, "wb") as f:
                f.write(base64.b64decode(data))


def main() -> int:
    jwt = make_jwt()
    shot_dir = "/tmp/cdp_shots"
    os.makedirs(shot_dir, exist_ok=True)

    st, hb = call("GET", "/api/brain/health")
    check(st == 200, "P0: brain health", f"st={st}")

    user_data = tempfile.mkdtemp(prefix="cdp_creative_ui_")
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
        # 真 JWT 预文档注入 + fetch 拦截（捕获真响应载荷供 UI==API 对照；
        # e2e-force-sentinel 标记=哨兵分支渲染验证用桩，后端哨兵归一由 pytest 锁）
        cdp.send("Page.addScriptToEvaluateOnNewDocument", {
            "source": f"""
(() => {{
  try {{ localStorage.setItem('soul_token', {json.dumps(jwt)}); }} catch (e) {{}}
  const origFetch = window.fetch.bind(window);
  window.__captured = {{}};
  window.__capturedCount = 0;
  window.fetch = async (...args) => {{
    const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || '';
    if (url.includes('/api/brain/creativity') && url.includes('e2e-force-sentinel')) {{
      const body = {{ alternatives: [{{ approach: '错误', description: 'e2e强制哨兵' }}],
                    payload: {{ count: 0, items: [], error: 'e2e强制哨兵' }} }};
      return new Response(JSON.stringify(body), {{ status: 200, headers: {{ 'Content-Type': 'application/json' }} }});
    }}
    const res = await origFetch(...args);
    try {{
      if (url.includes('/api/brain/creativity') || url.includes('/api/cortex/plan')) {{
        window.__capturedCount += 1;
        const key = url.split('?')[0];
        const clone = res.clone();
        clone.json().then(j => {{ window.__captured[key] = j; }}).catch(() => {{}});
      }}
    }} catch (e) {{}}
    return res;
  }};
}})();
"""
        })
        cdp.send("Page.navigate", {"url": f"{BASE}/admin/cortex.html"})
        cdp.pump(3.0)
        ok_page = cdp.wait_js("document.body && document.body.innerText.includes('任务规划')", 15.0)
        check(bool(ok_page), "P1a: cortex page renders", MARK)

        # P1b: 切到 备选方案 tab
        tab_js = (
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent.trim() === '备选方案')"
        )
        check(cdp.click(tab_js), "P1b: click 备选方案 tab")
        ok_tab = cdp.wait_js("document.body.innerText.includes('创造力引擎')", 8.0)
        check(bool(ok_tab), "P1c: creativity panel visible (on-demand 面)")

        # P2: 输入问题 → 生成 → 结构化备选方案块
        problem = f"设计一个高可用的文件同步方案 {MARK}"
        ta_js = "[...document.querySelectorAll('textarea')].find(t => (t.placeholder||'').includes('描述要解决的问题'))"
        check(cdp.set_controlled(ta_js, problem), "P2a: fill problem textarea")
        gen_js = "[...document.querySelectorAll('button')].find(b => b.textContent.trim() === '生成')"
        check(cdp.click(gen_js), "P2b: click 生成")
        ok_block = cdp.wait_js("document.body.innerText.includes('备选方案（创造力回流）')", 60.0)
        check(bool(ok_block), "P2c: 备选方案块渲染（on-demand 真生成）")

        # P3: UI == API 真值回读（fetch 拦截捕获载荷 vs DOM）
        payload = cdp.wait_js(
            "(window.__captured['/api/brain/creativity'] || {}).payload || null", 10.0
        )
        check(isinstance(payload, dict), "P3a: captured API payload", json.dumps(payload, ensure_ascii=False)[:120] if payload else "none")
        if isinstance(payload, dict):
            items = payload.get("items") or []
            text = cdp.js("document.body.innerText") or ""
            if items:
                missing = [it.get("approach") for it in items if str(it.get("approach") or "") not in text]
                check(not missing, "P3b: UI 逐条==API 真值（approach 全见）", f"missing={missing}")
                badge = f"{len(items)} 条"
                check(badge in text, "P3c: 条数徽章==载荷 count", badge)
            else:
                err = str(payload.get("error") or "")
                check(bool(err) and err in text, "P3b': 零产出=诚实 error 行真渲染", err)
            check("仅供参考，以目标要求为准，可全不采纳" in text, "P3d: 软建议免责文案在场")

        # P4: 重新生成（on-demand 重生成=真再次调用）
        n_before = cdp.js("window.__capturedCount") or 0
        regen_js = "[...document.querySelectorAll('button')].find(b => b.textContent.trim() === '重新生成')"
        check(cdp.click(regen_js), "P4a: click 重新生成")
        cdp.wait_js(f"(window.__capturedCount > {int(n_before)})", 60.0)
        n_after = cdp.js("window.__capturedCount") or 0
        check(n_after > n_before, "P4b: 重生成=真再次调用（无缓存）", f"{n_before}->{n_after}")
        check(bool(cdp.js("document.body.innerText.includes('备选方案（创造力回流）')")), "P4c: 重生成后块仍在")

        cdp.screenshot(f"{shot_dir}/g1_3b_2_creativity.png")

        # P5: 哨兵错误分支（桩响应——UI 分支渲染验证，后端哨兵归一由 pytest 锁）
        check(cdp.set_controlled(ta_js, f"e2e-force-sentinel {MARK}"), "P5a: fill forced-sentinel problem")
        gen_js2 = "[...document.querySelectorAll('button')].find(b => ['生成','重新生成'].includes(b.textContent.trim()))"
        check(cdp.click(gen_js2), "P5b: click 生成（哨兵桩）")
        ok_err = cdp.wait_js("document.body.innerText.includes('创造力回流无产出：e2e强制哨兵')", 20.0)
        check(bool(ok_err), "P5c: 哨兵→诚实 error 行（禁当真方案）")
        err_text = cdp.js("document.body.innerText") or ""
        check("0 条" in err_text, "P5d: 哨兵 0 条徽章（不 fabricate 方案卡）")
        cdp.screenshot(f"{shot_dir}/g1_3b_2_sentinel.png")

        # P6: 任务规划 face（G1-③b-1 加性键 UI 真消费，真 LLM）
        check(cdp.click(tab_js.replace("'备选方案'", "'任务规划'")), "P6a: click 任务规划 tab")
        in_js = "[...document.querySelectorAll('input')].find(t => (t.placeholder||'').includes('输入目标'))"
        check(cdp.set_controlled(in_js, f"搭建一个家庭实验室服务器集群 {MARK}"), "P6b: fill plan goal")
        plan_js = "[...document.querySelectorAll('button')].find(b => b.textContent.trim() === '分解')"
        check(cdp.click(plan_js), "P6c: click 分解")
        ok_plan = cdp.wait_js("document.body.innerText.includes('目标:')", 90.0)
        check(bool(ok_plan), "P6d: 规划结果渲染（真 LLM 分解）")
        plan_payload = cdp.wait_js(
            "(window.__captured['/api/cortex/plan'] || {}).alternatives || null", 10.0
        )
        check(isinstance(plan_payload, dict), "P6e: captured plan.alternatives 载荷")
        if isinstance(plan_payload, dict):
            ptext = cdp.js("document.body.innerText") or ""
            pitems = plan_payload.get("items") or []
            if pitems:
                pmiss = [it.get("approach") for it in pitems if str(it.get("approach") or "") not in ptext]
                check(not pmiss, "P6f: 规划面备选方案逐条==API 真值", f"missing={pmiss}")
                check(f"{len(pitems)} 条" in ptext, "P6g: 规划面条数徽章==载荷")
            else:
                perr = str(plan_payload.get("error") or "")
                check(bool(perr) and ("创造力回流无产出" in ptext), "P6f': 规划面零产出=诚实 error")
        cdp.screenshot(f"{shot_dir}/g1_3b_2_plan.png")

    finally:
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

    passed = sum(1 for ok, _ in RESULTS if ok)
    print(f"\n== {passed}/{len(RESULTS)} ==")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
