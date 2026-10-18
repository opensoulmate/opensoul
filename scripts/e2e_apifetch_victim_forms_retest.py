#!/usr/bin/env python3
"""GLAND-⑤ 受害面消费方逐页复测：apiFetch Content-Type 根因修的 8 处带体调用点真点击流复测。

GLAND-④ 修了 lib/api.ts 根因（带 JSON 体必声明 application/json），census 12 处带体
无头断点同修：gland 3（GLAND-④ 已真点击流实证）+ 剩余 8 处「随修复活，未逐页点击流
复测」——本探针逐点复现用户真实操作路径（按钮/点击流），证明「接线了≠跑通了」缺口闭合：

  1. vision 生成图表（POST /api/vision/chart/bar/json）+ 产物回读 + 清痕
  2. reflex 添加缓存（POST /api/reflex/cache）+ 回读
  3. reflex 模糊查询（POST /api/reflex/lookup）命中真源
  4. reflex 编辑条目（PATCH /api/reflex/cache/{id}）+ 回读 + 清痕
  5. trajectory 新建会话（POST /api/trajectory/sessions）+ 回读
  6. trajectory 回放→从此处分支（POST /api/trajectory/sessions/{id}/fork）+ 回读 + 清痕
  7. heredity 新建迁移（POST /api/heredity/migrations）+ 回读（内存注册表如实记）
  8. notifications 转发到 Echo（POST /api/notifications/{id}/forward）+ echo history 回读 + 清痕

每点断言三层：UI 真渲染结果（修前 422 静默断点=表单必炸）+ API 真值回读 +
Network 域 content-type 观测（application/json 真发出，可观测时硬断言）。
真浏览器（chromium headless + CDP）复现点击流，探针自带清痕。

用法：python3 scripts/e2e_apifetch_victim_forms_retest.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
PORT = 9446
MARK = "e2e-victim-" + uuid.uuid4().hex[:8]

RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    print(("PASS" if ok else "FAIL"), name, detail)


def call(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, raw
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


# ── CDP helpers（e2e_gland_provider_edit_ui.py 同款家族） ─────────────────


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

    def click(self, find_js: str, seconds: float = 12.0) -> bool:
        """find_js 返回元素时真 .click()（React onClick 真触发）。"""
        expr = f"(() => {{ const el = {find_js}; if (el) {{ el.click(); return true; }} return false; }})()"
        end = time.time() + seconds
        while time.time() < end:
            self.pump(0.3)
            if self.js(expr) is True:
                return True
        return False

    def set_field(self, find_js: str, value: str) -> bool:
        """React 受控 input/textarea 赋值（native setter + input 事件）。"""
        expr = (
            "(() => { const el = "
            + find_js
            + "; if (!el) return false; "
            "const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype; "
            "const setter = Object.getOwnPropertyDescriptor(proto, 'value').set; "
            f"setter.call(el, {json.dumps(value)}); "
            "el.dispatchEvent(new Event('input', { bubbles: true })); return true; })()"
        )
        return self.js(expr) is True

    def set_select(self, find_js: str, value: str) -> bool:
        """React 受控 select 赋值（native setter + change 事件）。"""
        expr = (
            "(() => { const el = "
            + find_js
            + "; if (!el) return false; "
            "const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set; "
            f"setter.call(el, {json.dumps(value)}); "
            "el.dispatchEvent(new Event('change', { bubbles: true })); return true; })()"
        )
        return self.js(expr) is True

    def content_type_for(self, url_substr: str, methods: tuple = ("POST", "PUT", "PATCH")):
        """Network 域观测该 URL 真请求 content-type（GET 不计防同 URL 读请求污染）。"""
        observed = False
        ct = None
        for ev in self.events:
            if ev.get("method") != "Network.requestWillBeSent":
                continue
            req = ev.get("params", {}).get("request", {})
            if url_substr not in req.get("url", ""):
                continue
            if req.get("method") not in methods:
                continue
            observed = True
            rid = ev.get("params", {}).get("requestId")
            for ev2 in self.events:
                if (
                    ev2.get("method") == "Network.requestWillBeSentExtraInfo"
                    and ev2.get("params", {}).get("requestId") == rid
                ):
                    for k, v in (ev2.get("params", {}).get("headers") or {}).items():
                        if k.lower() == "content-type":
                            ct = v
            if ct is None:
                for k, v in (req.get("headers") or {}).items():
                    if k.lower() == "content-type":
                        ct = v
        return observed, ct

    def check_ct(self, url_substr: str, label: str) -> None:
        """带体请求必真发 application/json：请求未观测=FAIL（禁 None 空过假阳性）。"""
        observed, ct = self.content_type_for(url_substr)
        ok = observed and ct is not None and "application/json" in ct
        check(ok, label, f"observed={observed} content-type={ct!r}")


def ph(value: str) -> str:
    """按 placeholder 定位（input/textarea 通吃）。"""
    return (
        "[...document.querySelectorAll('input,textarea')]"
        ".find(el => el.placeholder === " + json.dumps(value) + ")"
    )


def btn_exact(label: str) -> str:
    return (
        "[...document.querySelectorAll('button')]"
        ".find(b => b.textContent.trim() === " + json.dumps(label) + ")"
    )


def dlg_btn(anchor: str, label: str) -> str:
    return (
        "(() => { const d = [...document.querySelectorAll('div.fixed')]"
        ".find(x => x.textContent.includes(" + json.dumps(anchor) + ")); "
        "return d ? [...d.querySelectorAll('button')].find(b => b.textContent.trim().endsWith("
        + json.dumps(label) + ")) : null; })()"
    )


# ── 各受害面复测 ─────────────────────────────────────────────────────────


def sec_vision(cdp: CDP) -> None:
    st, data = call("GET", "/api/vision/outputs")
    before = set()
    for item in (data.get("outputs") or data.get("files") or []):
        name = item.get("filename") if isinstance(item, dict) else item
        if name:
            before.add(name)
    cdp.send("Page.navigate", {"url": f"{BASE}/admin/vision.html"})
    cdp.pump(2.5)
    ok_page = cdp.wait_js("document.body && document.body.innerText.includes('生成图表')", 25.0)
    check(bool(ok_page), "vision: page renders 生成图表",
          repr({
              "body": (cdp.js("document.body ? document.body.innerText.slice(0, 120).replace(/\\n/g, ' | ') : ''") or ""),
              "href": cdp.js("location.href"),
              "ls": cdp.js("localStorage.getItem('soul_token') ? 'yes' : 'no'"),
          }))
    title_mark = MARK + "-chart"
    check(cdp.set_field(ph("图表标题"), title_mark), "vision: set 图表标题 input")
    check(cdp.click(btn_exact("生成图表")), "vision: click 生成图表 (victim #1 POST body)")
    ok_preview = cdp.wait_js(
        "!!document.querySelector('img[src^=\"data:image\"]') || "
        "(document.body.innerText && document.body.innerText.includes('已生成图表') && "
        "!document.body.innerText.includes('API error'))",
        15.0,
    )
    check(bool(ok_preview), "vision: chart rendered (apiFetch JSON face live)")
    st, data = call("GET", "/api/vision/outputs")
    after = set()
    new_files = []
    for item in (data.get("outputs") or data.get("files") or []):
        name = item.get("filename") if isinstance(item, dict) else item
        if name:
            after.add(name)
    new_files = sorted(after - before)
    check(len(new_files) >= 1, "vision: output file created (API readback)", repr(new_files))
    cdp.check_ct("/api/vision/chart/bar/json", "vision: victim #1 request content-type")
    for f in new_files:
        st, _ = call("DELETE", f"/api/vision/outputs/{f}")
        check(st == 200, f"vision cleanup: output removed {f} ({st})")
    st, data = call("GET", "/api/vision/outputs")
    now = set()
    for item in (data.get("outputs") or data.get("files") or []):
        name = item.get("filename") if isinstance(item, dict) else item
        if name:
            now.add(name)
    check(not (now & set(new_files)), "vision cleanup: readback zero residue")


def sec_reflex(cdp: CDP) -> None:
    q_mark = MARK + "-q"
    a_mark = MARK + "-a1"
    a2_mark = MARK + "-a2"
    cdp.send("Page.navigate", {"url": f"{BASE}/admin/reflex.html"})
    cdp.pump(2.5)
    ok_page = cdp.wait_js("document.body && document.body.innerText.includes('添加缓存')", 12.0)
    check(bool(ok_page), "reflex: page renders 添加缓存")

    # 2) 添加缓存（victim #2 POST body）
    check(cdp.click(btn_exact("添加缓存")), "reflex: click 添加缓存 opens dialog")
    ok_dlg = cdp.wait_js(
        "!![...document.querySelectorAll('div.fixed')]"
        ".find(x => x.textContent.includes('留空=永久'))", 8.0
    )
    check(bool(ok_dlg), "reflex: create dialog open")
    check(cdp.set_field(ph("高频问题..."), q_mark), "reflex: set query")
    check(cdp.set_field(ph("标准回答..."), a_mark), "reflex: set response")
    check(cdp.set_field(ph("分类"), "e2e"), "reflex: set category")
    check(cdp.click(dlg_btn("留空=永久", "添加")), "reflex: click 添加 (submit)")
    ok_added = cdp.wait_js(f"document.body.innerText.includes({json.dumps(q_mark)})", 10.0)
    check(bool(ok_added), "reflex: entry row visible (apiFetch JSON face live)")
    st, data = call("GET", "/api/reflex/cache")
    entries = data.get("entries") or []
    row = next((e for e in entries if e.get("query") == q_mark), None)
    check(
        bool(row) and row.get("response") == a_mark,
        "reflex: UI create真落库 (API readback)",
        repr({k: row.get(k) for k in ("query", "response")} if row else None),
    )
    entry_id = (row or {}).get("entry_id")
    cdp.check_ct("/api/reflex/cache", "reflex: victim #2 create request content-type")

    # 3) 模糊查询（victim #3 POST body）——命中真源条目
    check(cdp.click(btn_exact("模糊查询")), "reflex: tab 模糊查询")
    check(cdp.set_field(ph("输入问题，系统将使用模糊匹配查找缓存..."), q_mark), "reflex: set lookup query")
    check(cdp.click(btn_exact("查询")), "reflex: click 查询 (victim #3)")
    ok_hit = cdp.wait_js("document.body.innerText.includes('缓存命中')", 10.0)
    check(bool(ok_hit), "reflex: lookup hit on真源 entry (POST body真修)")
    cdp.check_ct("/api/reflex/lookup", "reflex: victim #3 lookup request content-type")

    # 4) 编辑条目（victim #4 PATCH body）
    check(cdp.click(btn_exact("缓存列表")), "reflex: tab 缓存列表")
    row_edit_js = (
        "(() => { const mark = " + json.dumps(q_mark) + "; "
        "const btns = [...document.querySelectorAll('button[title=\"编辑\"]')]; "
        "for (const b of btns) { let p = b.parentElement; "
        "for (let i = 0; i < 8 && p; i++) { "
        "if (p.textContent.includes(mark) && p.querySelector('button[title=\"删除\"]')) return b; "
        "p = p.parentElement; } } return null; })()"
    )
    check(cdp.click(row_edit_js), "reflex: click row 编辑")
    edit_ta_js = (
        "(() => { const mark = " + json.dumps(q_mark) + "; "
        "const tas = [...document.querySelectorAll('textarea')]; "
        "for (const t of tas) { let p = t.parentElement; "
        "for (let i = 0; i < 12 && p; i++) { "
        "if (p.textContent.includes(mark)) return t; "
        "p = p.parentElement; } } return null; })()"
    )
    ok_edit = cdp.wait_js(edit_ta_js + " !== null", 8.0)
    check(bool(ok_edit), "reflex: inline edit form open")
    check(cdp.set_field(edit_ta_js, a2_mark), "reflex: edit response text")
    check(cdp.click(btn_exact("保存")), "reflex: click 保存 (victim #4 PATCH)")
    ok_saved = cdp.wait_js(f"document.body.innerText.includes({json.dumps(a2_mark)})", 10.0)
    check(bool(ok_saved), "reflex: edited text visible (apiFetch JSON face live)")
    st, data = call("GET", f"/api/reflex/cache/{entry_id}" if entry_id else "/api/reflex/cache")
    got = data.get("response") if isinstance(data, dict) else None
    if got is None and entry_id:
        st2, data2 = call("GET", "/api/reflex/cache")
        for e in data2.get("entries") or []:
            if e.get("entry_id") == entry_id:
                got = e.get("response")
    check(got == a2_mark, "reflex: PATCH真落库 (API readback)", repr(got))
    cdp.check_ct(f"/api/reflex/cache/{entry_id}", "reflex: victim #4 PATCH request content-type")

    # 清痕
    st, _ = call("DELETE", f"/api/reflex/cache/{entry_id}")
    check(st == 200, f"reflex cleanup: entry removed ({st})")
    st, data = call("GET", "/api/reflex/cache")
    left = [e for e in (data.get("entries") or []) if e.get("entry_id") == entry_id]
    check(not left, "reflex cleanup: readback zero residue")


def sec_trajectory(cdp: CDP) -> None:
    task_mark = MARK + "-task"
    cdp.send("Page.navigate", {"url": f"{BASE}/admin/trajectory.html"})
    cdp.pump(2.5)
    ok_page = cdp.wait_js("document.body && document.body.innerText.includes('新建会话')", 12.0)
    check(bool(ok_page), "trajectory: page renders 新建会话")

    # 5) 新建会话（victim #5 POST body）
    check(cdp.click(btn_exact("新建会话")), "trajectory: click 新建会话 opens dialog")
    ok_dlg = cdp.wait_js("document.body.innerText.includes('新建轨迹会话')", 8.0)
    check(bool(ok_dlg), "trajectory: create dialog open")
    check(cdp.set_field(ph("hermes-agent"), "e2e-agent"), "trajectory: set agent id")
    check(cdp.set_field(ph("描述这个轨迹会话的任务..."), task_mark), "trajectory: set task description")
    check(cdp.click(dlg_btn("新建轨迹会话", "创建")), "trajectory: click 创建 (submit victim #5)")
    ok_created = cdp.wait_js(f"document.body.innerText.includes({json.dumps(task_mark)})", 10.0)
    check(bool(ok_created), "trajectory: session row visible (apiFetch JSON face live)")
    st, data = call("GET", "/api/trajectory/sessions?limit=100")
    sessions = data.get("sessions") or []
    src = next((s for s in sessions if s.get("task_description") == task_mark), None)
    check(bool(src), "trajectory: UI create真落库 (API readback)", repr((src or {}).get("id") or (src or {}).get("session_id")))
    src_id = (src or {}).get("id") or (src or {}).get("session_id") or ""
    cdp.check_ct("/api/trajectory/sessions", "trajectory: victim #5 create request content-type")

    # fork 前置：给会话 1 条事件（setup 非受害面，API 直调）
    st, ev = call("POST", f"/api/trajectory/sessions/{src_id}/events", {
        "event_type": "custom", "agent_id": "e2e-agent", "content": MARK + "-evt", "status": "ok",
    })
    check(st in (200, 201), f"trajectory setup: event recorded for fork point ({st})")

    # 6) 回放→从此处分支（victim #6 POST body）
    check(cdp.set_field(ph("搜索会话ID、Agent、任务描述..."), task_mark), "trajectory: search filter")
    row_btn_js = (
        "[...document.querySelectorAll('button')]"
        ".find(b => b.textContent.includes(" + json.dumps(task_mark) + "))"
    )
    check(cdp.click(row_btn_js), "trajectory: open session detail")
    ok_modal = cdp.wait_js("document.body.innerText.includes('回放')", 8.0)
    check(bool(ok_modal), "trajectory: SessionEvents modal open")
    check(cdp.click(btn_exact("回放")), "trajectory: click 回放 (step 0)")
    ok_fork_btn = cdp.wait_js("document.body.innerText.includes('从此处分支')", 8.0)
    check(bool(ok_fork_btn), "trajectory: 从此处分支 button appears")
    cdp.js("window.__lastAlert = null")
    check(cdp.click("[...document.querySelectorAll('button')].find(b => b.textContent.includes('从此处分支'))"),
          "trajectory: click 从此处分支 (victim #6)")
    ok_alert = cdp.wait_js("window.__lastAlert", 8.0)
    check(bool(ok_alert) and "分支创建成功" in str(ok_alert), "trajectory: fork success alert", repr(ok_alert))
    st, data = call("GET", "/api/trajectory/sessions?limit=100")
    sessions = data.get("sessions") or []
    forked = next(
        (s for s in sessions
         if str(s.get("task_description", "")).startswith("[Fork] ") and task_mark in s.get("task_description", "")),
        None,
    )
    check(bool(forked), "trajectory: forked session真落库 (API readback)",
          repr((forked or {}).get("id") or (forked or {}).get("session_id")))
    cdp.check_ct("/fork", "trajectory: victim #6 fork request content-type")

    # 清痕：源会话+fork 会话
    for sid in [src_id, (forked or {}).get("id") or (forked or {}).get("session_id")]:
        if sid:
            st, _ = call("DELETE", f"/api/trajectory/sessions/{sid}")
            check(st == 200, f"trajectory cleanup: session removed {sid[:12]}… ({st})")
    st, data = call("GET", "/api/trajectory/sessions?limit=100")
    left = [s for s in (data.get("sessions") or []) if task_mark in s.get("task_description", "")]
    check(not left, "trajectory cleanup: readback zero residue")


def sec_heredity(cdp: CDP) -> None:
    comp_id = MARK + "-comp"
    st, data = call("POST", "/api/heredity/components", {
        "component_id": comp_id, "component_name": "e2e victim component", "version": "1.0.0",
    })
    check(st in (200, 201), f"heredity setup: component registered ({st})", repr(data)[:120])
    cdp.send("Page.navigate", {"url": f"{BASE}/admin/heredity.html"})
    cdp.pump(2.5)
    ok_page = cdp.wait_js("document.body && document.body.innerText.includes('新建迁移')", 12.0)
    check(bool(ok_page), "heredity: page renders 新建迁移")
    check(cdp.click(btn_exact("新建迁移")), "heredity: click 新建迁移 opens dialog")
    ok_dlg = cdp.wait_js("document.body.innerText.includes('试运行')", 8.0)
    check(bool(ok_dlg), "heredity: create dialog open")
    sel_js = (
        "(() => { const d = [...document.querySelectorAll('div.fixed')]"
        ".find(x => x.textContent.includes('试运行')); "
        "return d ? d.querySelector('select') : null; })()"
    )
    check(cdp.set_select(sel_js, comp_id), "heredity: select component")
    check(cdp.set_field(ph("0.1.0"), "0.1.0"), "heredity: set from_version")
    check(cdp.set_field(ph("0.2.0"), "0.2.0"), "heredity: set to_version")
    # 真点击流点「试运行」复选框（dry_run=true 计划零副作用）
    check(cdp.click(
        "(() => { const d = [...document.querySelectorAll('div.fixed')]"
        ".find(x => x.textContent.includes('试运行')); "
        "return d ? d.querySelector('input[type=\"checkbox\"]') : null; })()"
    ), "heredity: check 试运行 (dry_run)")
    check(cdp.click(dlg_btn("试运行", "创建")), "heredity: click 创建 (submit victim #7)")
    # 迁移列表在「迁移」tab 下（默认 tab=组件）——真点击流切 tab 再断言
    check(cdp.click(btn_exact("迁移")), "heredity: click 迁移 tab")
    ok_created = cdp.wait_js(
        "document.body.innerText.includes('0.2.0') && document.body.innerText.includes('试运行')", 10.0
    )
    check(bool(ok_created), "heredity: migration listed (apiFetch JSON face live)",
          repr((cdp.js("document.body.innerText.slice(0, 200).replace(/\\n/g, ' | ')") or "")))
    st, data = call("GET", "/api/heredity/migrations")
    migs = data.get("migrations") or []
    row = next(
        (m for m in migs if m.get("component_id") == comp_id
         and m.get("from_version") == "0.1.0" and m.get("to_version") == "0.2.0"),
        None,
    )
    check(bool(row) and row.get("dry_run") is True, "heredity: UI create真落注册表 (API readback)",
          repr({k: (row or {}).get(k) for k in ("migration_id", "status", "dry_run")}))
    cdp.check_ct("/api/heredity/migrations", "heredity: victim #7 create request content-type")
    # 清痕边界如实记：VersionRegistry._migrations 纯内存注册表、无删除端点——
    # dry_run 计划行零副作用，重启即清（报告如实记，不虚报已清）。


def sec_notifications(cdp: CDP) -> None:
    # setup：清既有测试残留（title 硬标识 '🧪 Test Notification'=POST /test 唯一产生面）
    # 使目标行文本唯一；内存行计数如实记。
    st, data = call("GET", "/api/notifications/recent?limit=200")
    stale = [n for n in (data.get("notifications") or []) if n.get("title") == "🧪 Test Notification"]
    for n in stale:
        call("DELETE", f"/api/notifications/{n['id']}")
    print(f"INFO notifications: pre-cleaned {len(stale)} stale test rows")
    st, data = call("POST", "/api/notifications/test")
    # POST /test 响应=通知本体（含 id），非包裹形（禁想当然）
    notif = data.get("notification") if isinstance(data.get("notification"), dict) else data
    notif_id = (notif or {}).get("id", "")
    check(st == 200 and bool(notif_id), "notifications setup: test notification pushed", repr(notif_id))

    cdp.send("Page.navigate", {"url": f"{BASE}/admin/notifications.html"})
    cdp.pump(2.5)
    ok_page = cdp.wait_js("document.body && document.body.innerText.includes('Test Notification')", 15.0)
    check(bool(ok_page), "notifications: test notification row rendered")
    fwd_btn_js = (
        "(() => { const mark = 'Test Notification'; "
        "const btns = [...document.querySelectorAll('button[title=\"转发到Echo\"]')]; "
        "for (const b of btns) { let p = b.parentElement; "
        "for (let i = 0; i < 8 && p; i++) { "
        "if (p.textContent.includes(mark) && p.querySelector('button[title=\"关闭\"]')) return b; "
        "p = p.parentElement; } } return null; })()"
    )
    check(cdp.click(fwd_btn_js), "notifications: click 转发到Echo opens dialog")
    ok_dlg = cdp.wait_js("document.body.innerText.includes('转发到 Echo 通道')", 8.0)
    check(bool(ok_dlg), "notifications: forward dialog open")
    sel_js = (
        "(() => { const d = [...document.querySelectorAll('div.fixed')]"
        ".find(x => x.textContent.includes('转发到 Echo 通道')); "
        "return d ? d.querySelector('select') : null; })()"
    )
    check(cdp.set_select(sel_js, "console"), "notifications: select console channel")
    # 转发前 echo history 快照（msg_id 差集回读基线）
    st, before_h = call("GET", "/api/echo/history?limit=100")
    before_ids = {m.get("msg_id") for m in (before_h.get("messages") or [])}
    check(cdp.click(dlg_btn("转发到 Echo 通道", "转发")), "notifications: click 转发 (victim #8 POST body)")
    ok_closed = cdp.wait_js(
        "!document.body.innerText.includes('转发到 Echo 通道') && !document.body.innerText.includes('API error')",
        10.0,
    )
    check(bool(ok_closed), "notifications: forward success dialog closed (apiFetch JSON face live)")
    cdp.check_ct("/forward", "notifications: victim #8 forward request content-type")
    # 回读：echo dispatcher history 真收到新消息（console 通道；msg_id 差集防旧文本假阳性）
    st, after_h = call("GET", "/api/echo/history?limit=100")
    new_msgs = [
        m for m in (after_h.get("messages") or [])
        if m.get("msg_id") not in before_ids and "Test Notification" in json.dumps(m, ensure_ascii=False)
    ]
    check(bool(new_msgs), "notifications: echo history真收到转发 (msg_id diff readback)",
          repr([m.get("msg_id") for m in new_msgs][:3]))
    # 清痕：dismiss 测试通知
    st, _ = call("DELETE", f"/api/notifications/{notif_id}")
    check(st == 200, f"notifications cleanup: test notification dismissed ({st})")
    st, data = call("GET", "/api/notifications/recent?limit=200")
    left = [n for n in (data.get("notifications") or []) if n.get("id") == notif_id]
    check(not left, "notifications cleanup: readback zero residue")


def main() -> int:
    jwt = make_jwt()
    user_data = tempfile.mkdtemp(prefix="cdp_victim_ui_")
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
        cdp.send("Page.addScriptToEvaluateOnNewDocument", {
            "source": (
                f"try {{ localStorage.setItem('soul_token', {json.dumps(jwt)}); }} catch (e) {{}}"
                "try { window.__lastAlert = null; window.alert = (m) => { window.__lastAlert = String(m); }; } catch (e) {}"
                "try { window.confirm = () => true; } catch (e) {}"
            )
        })

        sec_vision(cdp)
        sec_reflex(cdp)
        sec_trajectory(cdp)
        sec_heredity(cdp)
        sec_notifications(cdp)
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

    n_pass = sum(1 for ok, _, _ in RESULTS if ok)
    n_fail = len(RESULTS) - n_pass
    print(f"\n== SUMMARY == PASS {n_pass} / {len(RESULTS)}  FAIL {n_fail}")
    for ok, name, detail in RESULTS:
        if not ok:
            print("  FAIL:", name, detail)
    report = {
        "mark": MARK,
        "total": len(RESULTS), "pass": n_pass, "fail": n_fail,
        "results": [{"ok": ok, "name": name, "detail": detail} for ok, name, detail in RESULTS],
    }
    out = f"/home/climbing/opensoul/data/apifetch_victim_retest_{time.strftime('%Y%m%d_%H%M%S')}.json"
    with open(out, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("report:", out)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
