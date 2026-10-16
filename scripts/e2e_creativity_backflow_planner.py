"""G1-③b-1 live e2e：/api/cortex/plan 创造力回流规划面真栈取证。

真 LLM 双调用（generate_alternatives + TaskPlanner.plan）：备选方案真产出、
alternatives 加性键真回、tasks 真分解。只读探针，零生产写入。
"""

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8090"
PASS = []
FAIL = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS " if ok else "FAIL ") + name + (" | " + detail if detail else ""))


def post(path, payload, timeout=180):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def get(path, timeout=30):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8")


def main():
    status, body = get("/api/cortex/health")
    check("P0 cortex health 200", status == 200 and "ok" in body)

    goal = "为个人知识库系统设计高可用部署方案"
    status, data = post("/api/cortex/plan", {"goal": goal})
    check("P1 /plan 200", status == 200)
    check("P2 goal 逐字回显", data.get("goal") == goal)
    alts = data.get("alternatives")
    check("P3 alternatives 加性键在场", isinstance(alts, dict))
    if isinstance(alts, dict):
        items = alts.get("items") or []
        check("P4 真 LLM 备选方案 count>0", alts.get("count", 0) > 0, "count=%s" % alts.get("count"))
        approaches = [str(it.get("approach") or "") for it in items if isinstance(it, dict)]
        check(
            "P5 条目带 approach 真形",
            all(a for a in approaches) and len(approaches) == alts.get("count"),
            "approaches=%s" % approaches[:5],
        )
        check("P6 error 键缺席（真产出非哨兵）", "error" not in alts)
    tasks = data.get("tasks") or []
    check("P7 tasks 真分解 len>0", len(tasks) > 0, "len=%d" % len(tasks))
    if tasks:
        check(
            "P8 tasks 契约逐键不变",
            all(set(t.keys()) == {"index", "description", "dependencies", "priority"} for t in tasks),
        )
        check("P9 子任务描述非空", all(str(t.get("description") or "").strip() for t in tasks))

    status, body = get("/api/brain/health")
    check("P10 brain health 回归 200", status == 200)
    status, body = get("/api/cortex/stats")
    check("P11 cortex stats 回归 200", status == 200 and "plan_calls" in body)

    print("RESULT %d/%d" % (len(PASS), len(PASS) + len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
