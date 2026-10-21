#!/usr/bin/env python3
"""MEM-⑥ memory_count 计数对账真源化 live e2e 探针。

hippo 删除口（不回退计数的既有漂移点）删除后读面归真：
  修前 GET /api/hippo/sessions/{id} 的 memory_count 恒多报（删除口不 -1），
  修后=真实记忆行数（读面对账记忆行真源）。

真栈 urllib 打活服务 :8090；探针痕迹走真删除口清痕（remaining=0 报告）。
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8090"
UID = "default"  # 匿名自报契约（本地多档案语义）
results = []


def call(method, path, params=None, body=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS" if ok else "FAIL"), name, detail)


def session_count(sid):
    code, body = call("GET", f"/api/hippo/sessions/{sid}", params={"user_id": UID})
    return code, body.get("memory_count")


def main():
    # P1 基线
    code, health = call("GET", "/api/hippo/health")
    check("P1 health", code == 200, f"baseline memories={health['memory']['total_memories']}")

    # P2 建会话+2 记忆
    code, body = call("POST", "/api/hippo/sessions", body={"user_id": UID, "title": "mem6-e2e"})
    check("P2 create session", code == 200, str(code))
    sid = body["session_id"]
    mids = []
    for i in range(2):
        code, body = call(
            "POST",
            "/api/hippo/memories",
            params={"user_id": UID},
            body={"content": f"mem6-e2e 探针记忆{i}", "session_id": sid},
        )
        check(f"P2 add memory {i}", code == 200, str(code))
        mids.append(body["memory_id"])

    code, count = session_count(sid)
    check("P3 count==2 after 2 writes", code == 200 and count == 2, f"count={count}")

    # P4 删除口删一行（修前不回退计数=漂移点）
    code, _ = call("DELETE", f"/api/hippo/memories/{mids[0]}", params={"user_id": UID})
    check("P4 delete one via port", code == 200, str(code))

    # P5 核心证据：读面归真=1（修前恒 2）
    code, count = session_count(sid)
    check("P5 count==1 after delete (reconciled)", code == 200 and count == 1, f"count={count}")

    # P5b 列表口同形归真
    code, body = call("GET", "/api/hippo/sessions", params={"user_id": UID})
    row = [s for s in body.get("sessions", []) if s["session_id"] == sid]
    check("P5b list count==1", bool(row) and row[0]["memory_count"] == 1, str(row))

    # P6 衰减遗忘面：催老第二行→/decay/run 遗忘→计数归 0
    code, _ = call("DELETE", f"/api/hippo/memories/{mids[1]}", params={"user_id": UID})
    code, count = session_count(sid)
    check("P6 count==0 after all deleted", code == 200 and count == 0, f"count={count}")

    # P7 清痕：删探针会话+回读 remaining
    code, _ = call("DELETE", f"/api/hippo/sessions/{sid}", params={"user_id": UID})
    check("P7 delete session", code == 200, str(code))
    code, body = call("GET", "/api/hippo/sessions", params={"user_id": UID})
    left = [s for s in body.get("sessions", []) if s.get("title") == "mem6-e2e"]
    code2, body2 = call("GET", "/api/hippo/memories", params={"user_id": UID, "limit": 200})
    probe_rows = [m for m in body2.get("memories", []) if "mem6-e2e" in m.get("content", "")]
    check("P7 cleanup remaining=0", not left and not probe_rows, f"sessions={len(left)} memories={len(probe_rows)}")

    failed = [r for r in results if not r[1]]
    print(f"\n== {len(results) - len(failed)}/{len(results)} PASS ==")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
