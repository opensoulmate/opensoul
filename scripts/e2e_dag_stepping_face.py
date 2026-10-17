#!/usr/bin/env python3
"""H1-②b-⑧ dag 步进轴 live e2e 探针。

用法：
  python3 scripts/e2e_dag_stepping_face.py --pre   # 修前（旧服务）：步进两口 404 缺口硬证
  python3 scripts/e2e_dag_stepping_face.py         # 修后：就绪查询/状态推进全链

安全纪律：只操作探针自建计划（内存面，重启即清）；租户/用户收尾清痕 remaining=0。
注意：探针建的计划会留在服务内存（无删除口，F3b-⑧b 边界），/dag/stats 计数随探针
+N 如实（探针内用前后差分断言不虚增）。
"""

import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8090"
REPO = Path(__file__).resolve().parent.parent
MARK = "e2e-dag-" + str(int(time.time()))
PASS = 0
FAIL = 0

LLM_JSON_DEP = (
    '{"steps": [{"id": "s1", "description": "first"}, '
    '{"id": "s2", "description": "second", "depends_on": ["s1"]}]}'
)
LLM_JSON_ONE = '{"steps": [{"id": "s1", "description": "only"}]}'


def req(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read().decode() or "{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"detail": raw}


def check(name, cond, detail: object = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def main():
    pre = "--pre" in sys.argv
    mode = "pre" if pre else "post"
    print(f"=== e2e dag stepping face [{mode}] ===")

    st0, stats0 = req("GET", "/api/will/dag/stats")
    check("stats 基线 200", st0 == 200, stats0)

    # ── 计划建立（两代服务共有面） ──────────────────────────────────
    st, plan_a = req("POST", "/api/will/dag/plan", {"goal": "g-dep", "llm_response": LLM_JSON_DEP})
    check("create plan A 200", st == 200, plan_a)
    pid_a = plan_a.get("plan_id", "")
    st, plan_b = req("POST", "/api/will/dag/plan", {"goal": "g-one", "llm_response": LLM_JSON_ONE})
    check("create plan B 200", st == 200, plan_b)
    pid_b = plan_b.get("plan_id", "")

    if pre:
        # ── 修前缺口硬证：步进轴两口全缺席，计划建完永不推进 ────────
        st, d = req("GET", f"/api/will/dag/{pid_a}/ready")
        check("ready 查询面缺席 404", st == 404, (st, d))
        st, d = req("POST", f"/api/will/dag/{pid_a}/steps/s1", {"status": "completed"})
        check("步状态推进面缺席 404", st == 404, (st, d))
        st, d = req("POST", f"/api/will/dag/{pid_b}/steps/s1", {"status": "completed"})
        check("步状态推进面缺席 404 (B)", st == 404, (st, d))
        st, d = req("GET", f"/api/will/dag/{pid_a}/status")
        check("计划建完永不推进（步骤恒 pending）", st == 200 and d["steps"][0]["status"] == "pending", d)
        print(f"PASS={PASS} FAIL={FAIL}")
        return

    # ── 就绪查询面 ─────────────────────────────────────────────────
    st, d = req("GET", f"/api/will/dag/{pid_a}/ready")
    check("ready 200", st == 200, d)
    check("ready 顶层键集恰 4", set(d) == {"plan_id", "count", "ready_steps", "tenant_id"}, set(d))
    check("就绪判法：仅 s1（s2 依赖未满足）", d.get("count") == 1 and d["ready_steps"][0]["step_id"] == "s1", d)
    check(
        "就绪步键集恰 7",
        set(d["ready_steps"][0])
        == {"step_id", "description", "tool_name", "arguments", "status", "depends_on", "estimated_duration"},
        set(d["ready_steps"][0]),
    )
    check("行归属回显 default", d.get("tenant_id") == "default", d)

    # ── 步状态推进面：全周期 ───────────────────────────────────────
    st, d = req(
        "POST",
        f"/api/will/dag/{pid_a}/steps/s1",
        {"status": "completed", "result": {"out": 1}},
    )
    check("推进 s1 completed 200", st == 200, d)
    check(
        "推进响应键集恰 9",
        set(d)
        == {"plan_id", "step_id", "status", "result", "error", "progress", "is_complete", "plan_status", "tenant_id"},
        set(d),
    )
    check("progress 0.5 + 未完成", d.get("progress") == 0.5 and d.get("is_complete") is False, d)
    check("result 往返", d.get("result") == {"out": 1}, d)

    st, d = req("GET", f"/api/will/dag/{pid_a}/ready")
    check("推进后 s2 进就绪集", d.get("count") == 1 and d["ready_steps"][0]["step_id"] == "s2", d)

    st, d = req("POST", f"/api/will/dag/{pid_a}/steps/s2", {"status": "failed", "error": "boom"})
    check("推进 s2 failed→计划终态 failed", st == 200 and d.get("plan_status") == "failed" and d.get("is_complete") is True, d)
    check("error 往返", d.get("error") == "boom", d)

    # 幂等重放：终态只定一次，计数不虚增
    for _ in range(2):
        st, d = req("POST", f"/api/will/dag/{pid_a}/steps/s2", {"status": "failed", "error": "boom"})
        check("终态步幂等重放 200", st == 200, d)
    st, d = req("POST", f"/api/will/dag/{pid_b}/steps/s1", {"status": "completed"})
    check("推进 B s1→completed", st == 200 and d.get("plan_status") == "completed", d)
    for _ in range(2):
        req("POST", f"/api/will/dag/{pid_b}/steps/s1", {"status": "completed"})

    st, stats1 = req("GET", "/api/will/dag/stats")
    d_total = stats1.get("total_plans", 0) - stats0.get("total_plans", 0)
    d_failed = stats1.get("failed_plans", 0) - stats0.get("failed_plans", 0)
    d_completed = stats1.get("completed_plans", 0) - stats0.get("completed_plans", 0)
    check("stats total 差分恰 +2", d_total == 2, (stats0, stats1))
    check("stats failed 差分恰 +1（重放不虚增）", d_failed == 1, (stats0, stats1))
    check("stats completed 差分恰 +1（重放不虚增）", d_completed == 1, (stats0, stats1))

    # ── 404/422 如实 ───────────────────────────────────────────────
    st, d = req("GET", "/api/will/dag/plan_missing/ready")
    check("缺计划 ready 404", st == 404, (st, d))
    st, d = req("POST", "/api/will/dag/plan_missing/steps/s1", {"status": "completed"})
    check("缺计划推进 404", st == 404, (st, d))
    st, d = req("POST", f"/api/will/dag/{pid_b}/steps/nope", {"status": "completed"})
    check("缺步 404 带真因", st == 404 and "nope" in str(d.get("detail", "")), (st, d))
    st, d = req("GET", f"/api/will/dag/{pid_b}/status")
    check("缺步 404 零副作用（s1 仍 completed 不被洗）", st == 200 and d["steps"][0]["status"] == "completed", d)
    st, d = req("POST", f"/api/will/dag/{pid_b}/steps/s1", {"status": "bogus"})
    check("非法 status 422", st == 422, (st, d))

    # ── 租户闸门全链 ───────────────────────────────────────────────
    created_tenants, created_users, tokens = [], [], {}
    for side, uname in (("a", MARK + "-a"), ("b", MARK + "-b")):
        st, t = req("POST", "/api/nest/tenants", {"name": uname, "tier": "free"})
        tid = t.get("tenant_id") or t.get("id")
        created_tenants.append(tid)
        st, _ = req(
            "POST",
            "/api/enterprise/auth/register",
            {"username": uname, "password": "e2e-pass-1", "tenant_id": tid},
        )
        created_users.append(uname)
        st, login = req(
            "POST", "/api/enterprise/auth/login", {"username": uname, "password": "e2e-pass-1"}
        )
        tokens[side] = login.get("access_token")
        check(f"tenant {side} token", st == 200 and bool(tokens[side]), login)

    st, plan_t = req(
        "POST",
        "/api/will/dag/plan",
        {"goal": "g-tenant", "llm_response": LLM_JSON_DEP},
        token=tokens["a"],
    )
    check("A 建计划行归属派生", st == 200 and plan_t.get("tenant_id") == created_tenants[0], plan_t)
    pid_t = plan_t.get("plan_id", "")

    st, d = req("GET", f"/api/will/dag/{pid_t}/ready", token=tokens["b"])
    check("B 冒读就绪面 403 带真因", st == 403 and "dag plan access denied" in str(d.get("detail", "")), (st, d))
    st, d = req("POST", f"/api/will/dag/{pid_t}/steps/s1", {"status": "completed"}, token=tokens["b"])
    check("B 冒写推进 403", st == 403, (st, d))
    st, d = req("GET", f"/api/will/dag/{pid_t}/ready")
    check("匿名冒读 403", st == 403, (st, d))
    st, d = req("GET", f"/api/will/dag/{pid_t}/status", token=tokens["a"])
    check("行零改写（s1 仍 pending）", st == 200 and d["steps"][0]["status"] == "pending", d)
    st, d = req("POST", f"/api/will/dag/{pid_t}/steps/s1", {"status": "completed"}, token=tokens["a"])
    check("A 正对照推进 200", st == 200 and d.get("status") == "completed", d)

    # ── 清痕 ───────────────────────────────────────────────────────
    for tid in created_tenants:
        req("DELETE", f"/api/nest/tenants/{tid}")
    ent = REPO / "data" / "enterprise.db"
    con = sqlite3.connect(ent)
    users_deleted = 0
    for name in created_users:
        cur = con.execute("DELETE FROM users WHERE username = ?", (name,))
        users_deleted += cur.rowcount
    con.commit()
    remaining_users = con.execute(
        "SELECT COUNT(*) FROM users WHERE username LIKE ?", (MARK + "%",)
    ).fetchone()[0]
    con.close()
    nest = REPO / "data" / "nest.db"
    con = sqlite3.connect(nest)
    remaining_tenants = con.execute(
        "SELECT COUNT(*) FROM tenants WHERE name LIKE ?", (MARK + "%",)
    ).fetchone()[0]
    con.close()
    check(
        f"清痕 remaining=0 (users_deleted={users_deleted})",
        remaining_users == 0 and remaining_tenants == 0,
        (remaining_users, remaining_tenants),
    )

    print(f"PASS={PASS} FAIL={FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
