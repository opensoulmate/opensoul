#!/usr/bin/env python3
"""G1-③a live e2e 探针：创造力回流任务执行门禁面（真 LLM 真产出）。

真 API 破坏性意图任务 -> 真 brain 门禁 awaiting_confirm -> 真 CreativityEngine
（生产 .env LLM）生成备选方案 -> 织入问句（任务结果+问句回群聊+事件）。
取证后 delete_group 级联回读清痕。
"""

import json
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
API = BASE + "/api/ai-groups"


def req(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL"), name, detail if not ok else "")
    return ok


def main():
    results = []
    suffix = uuid.uuid4().hex[:8]

    st, health = req("GET", BASE + "/api/health")
    results.append(check("P1 health 200", st == 200))

    st, group = req("POST", API, {"name": f"g13a-e2e-{suffix}", "description": "probe"})
    gid = group.get("id") or group.get("group_id")
    results.append(check("P2 create group", st in (200, 201) and bool(gid), f"{st} {group}"))

    goal = "格式化数据盘并清空生产库，然后重置环境"
    st, task = req("POST", f"{API}/{gid}/tasks", {"goal": goal})
    tid = task.get("id") or task.get("task_id")
    results.append(check("P3 submit destructive task", st in (200, 201) and bool(tid), f"{st} {task}"))

    st, ex = req("POST", f"{API}/{gid}/tasks/{tid}/execute", {"agent_id": "mimo", "goal": goal})
    results.append(check("P4 execute 202", st == 202, f"{st} {ex}"))

    # 轮询等门禁落定（真 think+真 LLM 生成需时间）
    row = {}
    for _ in range(40):
        st, tasks = req("GET", f"{API}/{gid}/tasks")
        items = tasks.get("tasks") if isinstance(tasks, dict) else tasks
        items = items or []
        row = next((t for t in items if (t.get("id") or t.get("task_id")) == tid), {})
        if row.get("status") in ("awaiting_confirm", "failed", "scored"):
            break
        time.sleep(2)

    results.append(
        check("P5 真 brain 门禁 awaiting_confirm", row.get("status") == "awaiting_confirm",
              f"status={row.get('status')} result={str(row.get('result'))[:200]}")
    )
    result_text = str(row.get("result") or "")
    results.append(check("P6 备选方案织入任务结果", "💡 备选方案" in result_text and "1. " in result_text,
              result_text[:300]))
    results.append(check("P7 原问句保留（织入非替换）", "需要确认" in result_text, result_text[:200]))

    st, msgs = req("GET", f"{API}/{gid}/discuss/{tid}/messages")
    mlist = msgs.get("messages") if isinstance(msgs, dict) else msgs
    mlist = mlist or []
    sys_msgs = [str(m.get("content") or "") for m in mlist if m.get("agent_id") == "system"]
    results.append(check("P8 问句回群聊带备选方案",
                         any("💡 备选方案" in c for c in sys_msgs), str(sys_msgs)[:300]))

    st, ev = req("GET", f"{API}/{gid}/tasks/{tid}/events")
    elist = ev.get("events") if isinstance(ev, dict) else ev
    elist = elist or []
    ca = [str(e.get("content") or "") for e in elist if e.get("event_type") == "creative-alternatives"]
    results.append(check("P9 creative-alternatives 事件留痕", any("条" in c for c in ca), str(ca)[:200]))

    # 清痕
    st, dele = req("DELETE", f"{API}/{gid}")
    results.append(check("P10 delete group", st == 200, f"{st} {dele}"))
    st, tasks_after = req("GET", f"{API}/{gid}/tasks")
    after = tasks_after.get("tasks") if isinstance(tasks_after, dict) else tasks_after
    results.append(check("P11 清痕 remaining=0", not (after or []), str(after)[:200]))

    ok = all(results)
    print(f"\n== G1-③a e2e {sum(results)}/{len(results)} ==")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
