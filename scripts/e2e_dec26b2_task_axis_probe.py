#!/usr/bin/env python3
"""DEC-㉖B2-① live e2e 探针：gland 用量 task 轴（只读面为主 + 真 LLM chat 记账 3 发）。

验证面：
1. POST /api/gland/chat 三路由（缺省 chat / task=code / role=summarize→completion）
   真记账后 GET /api/gland/usage/recent 元素 task 逐值精确（调用点带 task 语义 live 证据）；
2. 元素键集含 task（键恒在场）+ timestamp float 等既有键零回归；
3. 部署产物 HTTP 回读：gland chunk（锚「最近调用记录」）.task 成员访问在场、
   旧漂移 .input_tokens/.output_tokens 残留=0（修前旧 chunk .task=0 双向对照在案）；
4. 真值过 UI 渲染表达式 node 渲染逐值对账；
5. timeline delta=0（TR-② 纪律零事件行面：本探针零事件写入）。
"""
import json
import subprocess
import sys
import urllib.request

BASE = "http://127.0.0.1:8090"
MARK = "最近调用记录"


def http(method, path, body=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode()
        try:
            return resp.status, json.loads(raw)
        except json.JSONDecodeError:
            return resp.status, raw


def timeline_count():
    # total_events=全局计数（stats 系统视图），比 events 分页窗稳（探针自纠：
    # limit 上限 1000 用 5000=422，且窗口法测不出窗外新增）。
    _, data = http("GET", "/api/timeline/stats")
    return int(data.get("total_events", -1))


results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}")


# 0) baseline
tl_before = timeline_count()
_, usage0 = http("GET", "/api/gland/usage")
check("baseline usage", isinstance(usage0, dict) and "call_count" in usage0,
      f"call_count={usage0.get('call_count')}")

# 1) 三路由真记账 → task 逐值精确
for name, body, expect in (
    ("chat-default", {"messages": [{"role": "user", "content": "回复一个字：好"}]}, "chat"),
    ("chat-task-code", {"messages": [{"role": "user", "content": "回复一个字：行"}], "task": "code"}, "code"),
    ("chat-role-summarize", {"messages": [{"role": "user", "content": "回复一个字：成"}], "role": "summarize"}, "completion"),
):
    try:
        st, resp = http("POST", "/api/gland/chat", body)
    except Exception as e:  # noqa: BLE001
        check(f"chat {name}", False, f"EXC {e}")
        continue
    ok_chat = st == 200 and isinstance(resp, dict)
    check(f"chat {name} 200", ok_chat, f"status={st}")
    _, recs = http("GET", "/api/gland/usage/recent?limit=1")
    elem = (recs.get("records") or [{}])[-1]
    check(f"task={expect}", elem.get("task") == expect,
          f"got={elem.get('task')!r} keys={sorted(elem)}")

# 2) 元素键集（键恒在场）+ 既有键零回归
_, recs = http("GET", "/api/gland/usage/recent?limit=5")
elements = recs.get("records") or []
if elements:
    e = elements[-1]
    want = {"provider", "model", "prompt_tokens", "completion_tokens", "total_tokens",
            "user_id", "task", "timestamp"}
    check("element keys", set(e) == want, f"got={sorted(e)}")
    check("timestamp float", isinstance(e.get("timestamp"), float), f"{type(e.get('timestamp'))}")
else:
    check("element keys", False, "no records")

# 3) 部署产物 HTTP 回读
status, html = http("GET", "/admin/gland")
check("admin gland page 200", status == 200, f"status={status}")
chunks = []
if isinstance(html, str):
    import re
    chunks = re.findall(r'/_next/static/chunks/[^"]+\.js', html)
gland_blob = ""
for c in sorted(set(chunks)):
    try:
        st2, body2 = http("GET", c if c.startswith("/admin") else "/admin" + c)
    except Exception:  # noqa: BLE001
        continue
    if isinstance(body2, str) and MARK in body2:
        gland_blob = body2
        check("gland chunk located", True, c)
        break
check("gland chunk located", bool(gland_blob), "via page html" if gland_blob else "NOT FOUND")
if gland_blob:
    check("chunk .task 在场", ".task" in gland_blob, f"count={gland_blob.count('.task')}")
    for bad in ("input_tokens", "output_tokens"):
        check(f"chunk .{bad} 残留=0", f".{bad}" not in gland_blob)

# 4) 真值过 UI 渲染表达式 node 对账（{r.task} → r.task）
if elements:
    vals = [e.get("task") for e in elements[-3:]]
    script = (
        "const recs = " + json.dumps(elements[-3:]) + ";"
        "console.log(JSON.stringify(recs.map((r) => String(r.task ?? ''))));"
    )
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    rendered = json.loads(proc.stdout.strip()) if proc.returncode == 0 else None
    expect_render = ["" if v is None else v for v in vals]
    check("node 渲染逐值对账", rendered == expect_render, f"{rendered} vs {expect_render}")

# 5) timeline delta=0（TR-② 纪律零事件行面）
tl_after = timeline_count()
check("timeline delta=0", tl_after == tl_before, f"before={tl_before} after={tl_after}")

failed = [r for r in results if not r[1]]
print(f"\n== {len(results) - len(failed)}/{len(results)} passed ==")
sys.exit(1 if failed else 0)
