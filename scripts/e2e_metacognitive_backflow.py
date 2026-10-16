#!/usr/bin/env python3
"""G1-④a live e2e 探针：元认知纠错回流（blind_spots → 执行 prompt 纠偏块）。

真 API 任务（echo agent 回显 prompt）→ 控制组（账本无观测=零注入）→
真 /api/brain/verify 落账 ×2 failure → 盲区真现身 → 再跑任务 →
纠偏块真进执行 prompt（echo 回显可见）+ metacognitive-context 事件留痕。
取证后 delete_group 级联 + SQL 清 metacognition_log 探针行。
"""

import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
API = BASE + "/api/ai-groups"
DB = "/home/climbing/opensoul/data/opensoul.db"


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


def run_task(gid, goal):
    st, task = req("POST", f"{API}/{gid}/tasks", {"goal": goal})
    tid = task.get("id") or task.get("task_id")
    st2, ex = req("POST", f"{API}/{gid}/tasks/{tid}/execute", {"agent_id": "echo", "goal": goal})
    row = {}
    for _ in range(40):
        st3, tasks = req("GET", f"{API}/{gid}/tasks")
        items = tasks.get("tasks") if isinstance(tasks, dict) else tasks
        items = items or []
        row = next((t for t in items if (t.get("id") or t.get("task_id")) == tid), {})
        if row.get("status") in ("awaiting_confirm", "failed", "scored"):
            break
        time.sleep(2)
    return tid, row, (st, st2)


def main():
    results = []
    mk = "G1D4A" + uuid.uuid4().hex[:8]
    area = f"e2e{mk}盲区动作"

    st, health = req("GET", BASE + "/api/health")
    results.append(check("P1 health 200", st == 200))

    # 基线：echo 命名空间无本探针盲区（既有观测如实记录）
    st, meta0 = req("GET", BASE + f"/api/brain/metacognition?agent_id=echo&tenant_id=default")
    bs0 = meta0.get("blind_spots") or []
    results.append(check("P2 基线无本探针盲区", all(area not in str(b) for b in bs0), str(bs0)[:200]))

    st, group = req("POST", API, {"name": f"g1d4a-e2e-{mk}", "description": "probe"})
    gid = group.get("id") or group.get("group_id")
    results.append(check("P3 create group", st in (200, 201) and bool(gid), f"{st} {group}"))

    goal1 = f"请把收到的内容原样输出（探针{mk}A）"
    tid1, row1, sts = run_task(gid, goal1)
    r1 = str(row1.get("result") or "")
    results.append(check("P4 控制组任务真跑", row1.get("status") == "scored" and goal1 in r1,
                         f"status={row1.get('status')} {r1[:150]}"))
    results.append(check("P5 控制组零注入（无观测=纠偏块不出）",
                         "【元认知纠偏提醒】" not in r1 and area not in r1, r1[:300]))

    # 真落账：verify ×2 failure 同 decision → 盲区（fail_count>=2）
    for i in (1, 2):
        st, vr = req("POST", BASE + "/api/brain/verify",
                     {"agent_id": "echo", "task_id": f"{mk}-t{i}", "action": area,
                      "result": {"success": False}})
        results.append(check(f"P6.{i} verify 落账 200", st == 200, f"{st} {vr}"))

    st, meta1 = req("GET", BASE + f"/api/brain/metacognition?agent_id=echo&tenant_id=default")
    bs1 = meta1.get("blind_spots") or []
    hit = [b for b in bs1 if area in str(b.get("area", ""))]
    results.append(check("P7 盲区真现身 fail_count=2", bool(hit) and int(hit[0].get("fail_count", 0)) >= 2,
                         str(bs1)[:200]))

    goal2 = f"请把收到的内容原样输出（探针{mk}B）"
    tid2, row2, sts2 = run_task(gid, goal2)
    r2 = str(row2.get("result") or "")
    results.append(check("P8 纠偏块真进执行prompt",
                         row2.get("status") == "scored"
                         and "【元认知纠偏提醒】" in r2
                         and area in r2
                         and "反复失败 2 次" in r2
                         and "以任务要求为准" in r2, f"status={row2.get('status')} {r2[:400]}"))
    if goal2 in r2 and "【元认知纠偏提醒】" in r2:
        results.append(check("P9 任务本体在前纠偏块在后", r2.index(goal2) < r2.index("【元认知纠偏提醒】")))
    else:
        results.append(check("P9 任务本体在前纠偏块在后", False, r2[:300]))

    st, ev = req("GET", f"{API}/{gid}/tasks/{tid2}/events")
    elist = ev.get("events") if isinstance(ev, dict) else ev
    elist = elist or []
    mc_ev = [str(e.get("content") or "") for e in elist if e.get("event_type") == "metacognitive-context"]
    err_ev = [str(e.get("content") or "") for e in elist if e.get("event_type") == "metacognitive-context-error"]
    results.append(check("P10 metacognitive-context 事件留痕",
                         any("元认知纠偏提醒注入执行prompt" in c and "（metacognition→执行）" in c for c in mc_ev)
                         and not err_ev, f"ev={mc_ev} err={err_ev}"))

    # 清痕：组级联 + metacognition_log 探针行
    st, dele = req("DELETE", f"{API}/{gid}")
    results.append(check("P11 delete group", st == 200, f"{st} {dele}"))
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM metacognition_log WHERE decision LIKE ? OR reasoning LIKE ? OR ref LIKE ?",
                 (f"%{mk}%", f"%{mk}%", f"{mk}%"))
    conn.commit()
    remaining = conn.execute("SELECT COUNT(*) FROM metacognition_log WHERE decision LIKE ? OR reasoning LIKE ?",
                             (f"%{mk}%", f"%{mk}%")).fetchone()[0]
    conn.close()
    results.append(check("P12 清痕 remaining=0", remaining == 0, f"remaining={remaining}"))

    ok = all(results)
    print(f"\n== G1-④a e2e {sum(results)}/{len(results)} ==")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
