#!/usr/bin/env python3
"""GLAND-④ live UI e2e 探针：admin-ui gland 页 provider 编辑流（PATCH 就地改，不触钥）。

真浏览器（chromium headless + CDP）复现用户点击流：
  provider 行 → 「编辑配置」按钮 → 编辑弹窗（预填）→ 改优先级+模型 → 保存
  → 行回读 + API 真值回读（配置真改、钥一字不动）→ 摘牌（enabled=false）
  → 持钥改 base_url = 409 真因可见 + 原子拒零写入 → 无变更保存钮禁用。
--pre ：对部署前构建断言编辑流不存在（缺口硬证）。
--post：对部署后构建跑完整点击流（收口硬证）。
探针自带清痕（DELETE provider 级联清钥 + 回读归零）。

用法：python3 scripts/e2e_gland_provider_edit_ui.py --pre|--post
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
PORT = 9445

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


def provider_row(mark: str) -> dict | None:
    _, data = call("GET", "/api/gland/providers")
    for p in data.get("providers", []):
        if p.get("name") == mark:
            return p
    return None


# ── CDP helpers（e2e_link_token_ui.py 同款） ─────────────────


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

    def wait_js(self, expr: str, seconds: float = 12.0):
        end = time.time() + seconds
        while time.time() < end:
            self.pump(0.3)
            val = self.js(expr)
            if val:
                return val
        return None

    def click(self, find_js: str, seconds: float = 12.0):
        """find_js 返回元素时真 .click()（React onClick 真触发）。"""
        expr = f"(() => {{ const el = {find_js}; if (el) {{ el.click(); return true; }} return false; }})()"
        end = time.time() + seconds
        while time.time() < end:
            self.pump(0.3)
            if self.js(expr) is True:
                return True
        return False

    def set_input(self, find_js: str, value: str) -> bool:
        """React 受控输入赋值（native setter + input 事件）。"""
        expr = (
            "(() => { const el = "
            + find_js
            + "; if (!el) return false; "
            "const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set; "
            f"setter.call(el, {json.dumps(value)}); "
            "el.dispatchEvent(new Event('input', { bubbles: true })); return true; })()"
        )
        return self.js(expr) is True


def edit_btn_js(mark: str) -> str:
    """行内定位（探针自纠：首版 6 级父级上溯越过行界命中列表容器=误点别行）。"""
    return (
        "(() => { const mark = "
        + json.dumps(mark)
        + "; const rows = [...document.querySelectorAll('div')]"
        ".filter(d => (d.className || '').includes('hover:bg-muted/30')); "
        "const row = rows.find(r => r.textContent.includes(mark) "
        "&& r.querySelector('button[title=\"编辑配置\"]')); "
        "return row ? row.querySelector('button[title=\"编辑配置\"]') : null; })()"
    )


def dlg_input_js(idx: int, anchor: str = '编辑Provider') -> str:
    """弹窗第 idx 个 input（编辑弹窗：0=base_url 1=models 2=priority 3=enabled；添加弹窗：0=name 1=base_url 2=models 3=api_key 4=priority）。"""
    return (
        "(() => { const d = [...document.querySelectorAll('div.fixed')]"
        ".find(x => x.textContent.includes(" + json.dumps(anchor) + ")); "
        "return d ? d.querySelectorAll('input')[" + str(idx) + "] : null; })()"
    )


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "--post"
    mark = "e2e-edit-" + uuid.uuid4().hex[:8]
    # 非重叠命名（探针自纠②：mark2 用 mark+"-add" 前缀超串，行匹配 includes(mark)
    # 误命中 mark2 行=又误点别行；两名字互不为子串才可 includes 定位）
    mark2 = "e2e-add-" + uuid.uuid4().hex[:8]
    jwt = make_jwt()

    # 1) API 建测试 provider + 1 把钥（持钥面=409 改道拒的真实前提）
    st, data = call("POST", "/api/gland/providers", {
        "name": mark,
        "base_url": "http://127.0.0.1:1/e2e-edit",
        "models": {"chat": "model-a"},
        "priority": 5,
    })
    check(st == 200 and data.get("provider") == mark, "setup: provider registered", f"st={st} {data}")
    st, data = call("POST", "/api/gland/keys", {"provider": mark, "api_key": "sk-e2e-edit-KEY1"})
    check(st == 200, "setup: key added (held-key face)", f"st={st}")

    user_data = tempfile.mkdtemp(prefix="cdp_gland_ui_")
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
        cdp.send("Page.navigate", {"url": f"{BASE}/admin/gland.html"})
        cdp.pump(3.0)
        ok_page = cdp.wait_js(
            f"document.body && document.body.innerText.includes({json.dumps(mark)})", 15.0
        )
        check(bool(ok_page), "render: gland page lists test provider", mark)

        if mode == "--pre":
            # 缺口硬证：编辑流整体不存在（按钮/弹窗/保存链全无）
            has_btn = cdp.js(
                f"!!document.querySelector('button[title=\"编辑配置\"]')"
            )
            check(has_btn is False, "pre: no 编辑配置 button (edit flow absent)")
            has_dlg = cdp.js("document.body.innerText.includes('编辑Provider')")
            check(has_dlg is False, "pre: no edit dialog string (edit flow absent)")
            # 部署产物同证
            import subprocess as sp
            out = sp.run(
                ["grep", "-rl", "编辑配置", "/home/climbing/opensoul/src/static/admin/_next"],
                capture_output=True, text=True,
            )
            check(out.returncode != 0, "pre: deployed chunks contain zero edit-flow strings")
        else:
            # 2a) 添加Provider 表单真用户路径（apiFetch Content-Type 根因修的消费面：
            # 修前 fetch 默认 text/plain → FastAPI 422 静默断点，表单必炸）
            ok_add_btn = cdp.click(
                "[...document.querySelectorAll('button')].find(b => b.textContent.trim() === '添加Provider')"
            )
            check(ok_add_btn, "click: 添加Provider opens add dialog")
            ok_add_dlg = cdp.wait_js(
                "!![...document.querySelectorAll('div.fixed')]"
                ".find(x => x.textContent.includes('添加Provider'))", 8.0
            )
            check(bool(ok_add_dlg), "render: add dialog open")
            check(cdp.set_input(dlg_input_js(0, '添加Provider'), mark2), "input: add name")
            check(cdp.set_input(dlg_input_js(1, '添加Provider'), "http://127.0.0.1:1/e2e-add"), "input: add base_url")
            check(cdp.set_input(dlg_input_js(2, '添加Provider'), "chat:add-model"), "input: add models")
            check(cdp.set_input(dlg_input_js(4, '添加Provider'), "2"), "input: add priority")
            ok_add_submit = cdp.click(
                "(() => { const d = [...document.querySelectorAll('div.fixed')]"
                ".find(x => x.textContent.includes('添加Provider')); "
                "return d ? [...d.querySelectorAll('button')].find(b => b.textContent.trim() === '添加') : null; })()"
            )
            check(ok_add_submit, "click: 添加 (submit)")
            ok_added = cdp.wait_js(
                f"document.body.innerText.includes({json.dumps(mark2)})", 10.0
            )
            check(bool(ok_added), "render: added provider row visible (apiFetch JSON face真修)")
            row2 = provider_row(mark2)
            check(
                bool(row2) and row2.get("priority") == 2 and row2.get("models") == {"chat": "add-model"},
                "api: UI add created provider via apiFetch (Content-Type fix live)",
                repr(row2),
            )
            st_rm2, _ = call("DELETE", f"/api/gland/providers/{mark2}")
            check(st_rm2 == 200, f"cleanup: UI-added provider removed ({st_rm2})")

            # 2) 点「编辑配置」开弹窗（预填真值）
            check(cdp.click(edit_btn_js(mark)), "click: 编辑配置 opens dialog")
            ok_dlg = cdp.wait_js("document.body.innerText.includes('编辑Provider')", 8.0)
            check(bool(ok_dlg), "render: edit dialog open")
            prefill = cdp.js(
                "(() => { const d = [...document.querySelectorAll('div.fixed')]"
                ".find(x => x.textContent.includes('编辑Provider')); if (!d) return null; "
                "const ins = d.querySelectorAll('input'); "
                "return [ins[0].value, ins[1].value, ins[2].value, ins[3].checked]; })()"
            )
            check(
                prefill == ["http://127.0.0.1:1/e2e-edit", "chat:model-a", "5", True],
                "render: dialog prefilled from row truth", repr(prefill),
            )

            # 3) 改优先级 5→7 + 模型 chat:model-b → 保存
            check(cdp.set_input(dlg_input_js(2), "7"), "input: priority -> 7")
            check(cdp.set_input(dlg_input_js(1), "chat:model-b"), "input: models -> chat:model-b")
            ok_save = cdp.click(
                "(() => { const d = [...document.querySelectorAll('div.fixed')]"
                ".find(x => x.textContent.includes('编辑Provider')); "
                "return d ? [...d.querySelectorAll('button')].find(b => b.textContent.trim() === '保存') : null; })()"
            )
            check(ok_save, "click: 保存 (priority+models)")
            ok_closed = cdp.wait_js(
                "![...document.querySelectorAll('div.fixed')]"
                ".some(x => x.textContent.includes('编辑Provider'))", 10.0
            )
            check(bool(ok_closed), "render: dialog closed after save")
            row = provider_row(mark)
            check(
                bool(row) and row.get("priority") == 7 and row.get("models") == {"chat": "model-b"}
                and row.get("base_url") == "http://127.0.0.1:1/e2e-edit",
                "api: PATCH applied (priority 7, models model-b, base_url untouched)",
                repr(row),
            )
            ui_row = cdp.js(
                f"document.body.innerText.includes('优先级: 7') && document.body.innerText.includes('chat: model-b')"
            )
            check(ui_row is True, "render: row shows updated priority+models")

            # 4) 钥一字不动（脱敏展示：完整钥文绝不出现在读面）
            _, kdata = call("GET", "/api/gland/keys")
            slots = kdata.get(mark) or []
            check(
                len(slots) == 1 and "sk-e2e-edit-KEY1" not in json.dumps(slots),
                "api: keys untouched by config edit (1 slot, plaintext never returned)",
                repr(slots),
            )

            # 5) 摘牌（enabled=false 不丢钥）
            check(cdp.click(edit_btn_js(mark)), "click: 编辑配置 (re-open for disable)")
            cdp.pump(1.0)
            ok_chk = cdp.click(dlg_input_js(3))
            check(ok_chk, "click: 启用 checkbox -> off")
            ok_save2 = cdp.click(
                "(() => { const d = [...document.querySelectorAll('div.fixed')]"
                ".find(x => x.textContent.includes('编辑Provider')); "
                "return d ? [...d.querySelectorAll('button')].find(b => b.textContent.trim() === '保存') : null; })()"
            )
            check(ok_save2, "click: 保存 (enabled off)")
            cdp.pump(2.0)
            row = provider_row(mark)
            check(bool(row) and row.get("enabled") is False, "api: enabled=false applied", repr(row))
            _, kdata = call("GET", "/api/gland/keys")
            check(len(kdata.get(mark) or []) == 1, "api: disable kept keys (摘牌不丢钥)")
            ui_off = cdp.wait_js("document.body.innerText.includes('不可用')", 8.0)
            check(bool(ui_off), "render: row shows 不可用 after disable")

            # 6) 持钥改 base_url = 409 真因可见 + 原子拒零写入
            check(cdp.click(edit_btn_js(mark)), "click: 编辑配置 (re-open for retarget)")
            cdp.pump(1.0)
            check(cdp.set_input(dlg_input_js(0), "http://127.0.0.1:2/retarget"), "input: base_url -> new endpoint")
            ok_save3 = cdp.click(
                "(() => { const d = [...document.querySelectorAll('div.fixed')]"
                ".find(x => x.textContent.includes('编辑Provider')); "
                "return d ? [...d.querySelectorAll('button')].find(b => b.textContent.trim() === '保存') : null; })()"
            )
            check(ok_save3, "click: 保存 (retarget attempt)")
            err_txt = cdp.wait_js(
                "(() => { const d = [...document.querySelectorAll('div.fixed')]"
                ".find(x => x.textContent.includes('编辑Provider')); if (!d) return ''; "
                "const t = d.innerText; return t.includes('API error 409') ? t : ''; })()",
                10.0,
            )
            check(bool(err_txt), "render: 409真因 visible in dialog", (err_txt or "")[:120])
            still_open = cdp.js(
                "!![...document.querySelectorAll('div.fixed')]"
                ".some(x => x.textContent.includes('编辑Provider'))"
            )
            check(still_open is True, "render: dialog stays open on 409")
            row = provider_row(mark)
            check(
                bool(row) and row.get("base_url") == "http://127.0.0.1:1/e2e-edit",
                "api: retarget atomically rejected (base_url unchanged)", repr(row),
            )
            # 弹窗里真因文案带「remove the keys」指路（ServerProvider update_provider 文案）
            has_hint = cdp.js(
                "(() => { const d = [...document.querySelectorAll('div.fixed')]"
                ".find(x => x.textContent.includes('编辑Provider')); "
                "return d ? d.innerText.includes('remove the keys') : false; })()"
            )
            check(has_hint is True, "render: 409 detail verbatim (remove the keys ...)")

            # 7) 无变更 = 保存钮禁用（禁空转假成功）
            check(cdp.set_input(dlg_input_js(0), "http://127.0.0.1:1/e2e-edit"), "input: base_url reverted (no change)")
            dis = cdp.js(
                "(() => { const d = [...document.querySelectorAll('div.fixed')]"
                ".find(x => x.textContent.includes('编辑Provider')); if (!d) return null; "
                "const b = [...d.querySelectorAll('button')].find(b => b.textContent.trim() === '保存'); "
                "return b ? b.disabled : null; })()"
            )
            check(dis is True, "render: 保存 disabled with zero changes")

    finally:
        # 清痕：DELETE provider 级联清钥 + 回读归零（含 UI 添加面 mark2 残余）
        st_del, data_del = call("DELETE", f"/api/gland/providers/{mark}")
        call("DELETE", f"/api/gland/providers/{mark2}")
        _, pdata = call("GET", "/api/gland/providers")
        _, kdata = call("GET", "/api/gland/keys")
        remaining = (
            sum(1 for p in pdata.get("providers", []) if p.get("name") in (mark, mark2))
            + len(kdata.get(mark) or []) + len(kdata.get(mark2) or [])
        )
        check(
            st_del == 200 and data_del.get("keys_removed") == 1 and remaining == 0,
            f"cleanup: provider+keys removed ({st_del} keys_removed={data_del.get('keys_removed')}) remaining={remaining}",
        )
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
    total = len(RESULTS)
    print(f"\n== {passed}/{total} ==")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
