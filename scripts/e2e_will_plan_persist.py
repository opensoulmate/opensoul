#!/usr/bin/env python3
"""RV-①b-⑫ live e2e 探针——will/dag_planner._plans 计划持久化 修前/修后双向硬证。

用法（三阶段，各自独立跑）：
  python3 scripts/e2e_will_plan_persist.py --pre      # 修前：GAP 坐实（旧进程）
  python3 scripts/e2e_will_plan_persist.py --post     # 修后：首行 restart() 载新码再验
  python3 scripts/e2e_will_plan_persist.py --cleanup  # 清痕（SQL 删探针行+复核 remaining=0）

纪律（既有探针自伤坑记档）：--post 首行必 restart()——写入跑在载新码重启之前
=全部 FAIL 打旧进程（RV-①b-⑤-2/⑥-1 同款自伤再现防）。
"""

import json
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8090"
DB = "/home/climbing/opensoul/data/will_plans.db"
MARKER = "e2e-rv1b12-probe"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")


def api(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw) if raw else {}
        except Exception:
            return e.code, {"raw": raw}


def restart() -> None:
    subprocess.run(["systemctl", "--user", "restart", "opensoul.service"], check=True)
    for _ in range(60):
        time.sleep(1)
        try:
            status, _ = api("GET", "/api/health")
            if status == 200:
                return
        except Exception:
            continue
    raise RuntimeError("service did not come back")


def db_rows(goal_like: str = MARKER) -> list[tuple]:
    try:
        conn = sqlite3.connect(DB)
    except Exception:
        return []
    try:
        return conn.execute(
            "SELECT plan_id, goal, status, tenant_id, steps_count FROM will_plans WHERE goal LIKE ?",
            (f"%{goal_like}%",),
        ).fetchall()
    except sqlite3.OperationalError:
        return []  # 库缺失/无表=零行（修前预期态；连接自身会建空文件勿当证据）
    finally:
        conn.close()


def make_plan(goal: str) -> str:
    llm_response = json.dumps(
        {
            "steps": [
                {
                    "id": "step_0",
                    "description": "first",
                    "tool": "shell",
                    "arguments": {"cmd": "true"},
                    "depends_on": [],
                },
                {
                    "id": "step_1",
                    "description": "second",
                    "tool": "shell",
                    "arguments": {"cmd": "true"},
                    "depends_on": ["step_0"],
                },
            ]
        }
    )
    status, data = api("POST", "/api/will/dag/plan", {"goal": goal, "llm_response": llm_response})
    if status != 200:
        raise RuntimeError(f"create plan failed: {status} {data}")
    return data["plan_id"]


def run_pre() -> None:
    goal = f"{MARKER}-pre-{int(time.time())}"
    print(f"== --pre（旧进程，期望 GAP）goal={goal}")
    plan_id = make_plan(goal)
    check("pre_create_status_200", True, plan_id)
    status, data = api("GET", f"/api/will/dag/{plan_id}/status")
    check("pre_status_200_in_memory", status == 200 and data.get("goal") == goal, str(status))
    rows = db_rows(goal)
    check("pre_db_rows_zero_GAP", len(rows) == 0, f"rows={len(rows)}")
    status, _ = api("POST", f"/api/will/dag/{plan_id}/steps/step_0", {"status": "completed"})
    check("pre_step_update_200", status == 200, str(status))
    check("pre_still_zero_rows_GAP", len(db_rows(goal)) == 0, f"rows={len(db_rows(goal))}")
    restart()
    status, data = api("GET", f"/api/will/dag/{plan_id}/status")
    check("pre_restart_plan_lost_GAP", status == 404, f"status={status}")
    status, stats = api("GET", "/api/will/dag/stats")
    check(
        "pre_restart_stats_zero_GAP",
        status == 200 and all(int(stats.get(k, -1)) == 0 for k in ("total_plans", "total_steps")),
        json.dumps(stats),
    )
    _summarize()


def run_post() -> None:
    restart()  # 首行必重启载新码（⑤-2/⑥-1 探针自伤坑记档）
    goal = f"{MARKER}-post-{int(time.time())}"
    print(f"== --post（新码）goal={goal}")
    plan_id = make_plan(goal)
    check("post_create_200", True, plan_id)
    rows = db_rows(goal)
    check("post_row_written", len(rows) == 1 and rows[0][2] == "pending", str(rows))
    status, _ = api("POST", f"/api/will/dag/{plan_id}/steps/step_0", {"status": "completed"})
    check("post_step_update_200", status == 200, str(status))
    conn = sqlite3.connect(DB)
    payload_raw = conn.execute(
        "SELECT payload FROM will_plans WHERE plan_id = ?", (plan_id,)
    ).fetchone()[0]
    conn.close()
    payload = json.loads(payload_raw)
    s0 = payload["steps"][0]
    check(
        "post_step_state_in_row",
        s0["status"] == "completed" and s0["arguments"] == {"cmd": "true"},
        s0["status"],
    )
    restart()
    status, data = api("GET", f"/api/will/dag/{plan_id}/status")
    check(
        "post_restart_plan_survives",
        status == 200 and data.get("goal") == goal and data.get("progress") == 0.5,
        f"status={status} progress={data.get('progress') if status == 200 else '-'}",
    )
    status, ready = api("GET", f"/api/will/dag/{plan_id}/ready")
    check(
        "post_restart_step_state_survives",
        status == 200 and [s["step_id"] for s in ready.get("ready_steps", [])] == ["step_1"],
        json.dumps(ready)[:120],
    )
    status, stats = api("GET", "/api/will/dag/stats")
    check(
        "post_restart_stats_survive",
        status == 200 and int(stats.get("total_plans", 0)) >= 1 and int(stats.get("total_steps", 0)) >= 2,
        json.dumps(stats),
    )
    _summarize()


def run_cleanup() -> None:
    print("== --cleanup")
    rows = db_rows()
    conn = sqlite3.connect(DB)
    try:
        conn.execute("DELETE FROM will_plans WHERE goal LIKE ?", (f"%{MARKER}%",))
        conn.commit()
    except sqlite3.OperationalError:
        pass  # 无表=本来就没有探针行
    conn.close()
    check("cleanup_rows_deleted", len(rows) > 0, f"deleted={len(rows)}")
    remaining = len(db_rows())
    check("cleanup_remaining_zero", remaining == 0, f"remaining={remaining}")
    restart()
    status, data = api("GET", "/api/will/dag/stats")
    check("cleanup_restart_stats_clean", status == 200, json.dumps(data))
    _summarize()


def _summarize() -> None:
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n== {passed}/{total} passed")
    if passed != total:
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  FAIL: {name} {detail}")
        sys.exit(1)


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--pre":
        run_pre()
    elif arg == "--post":
        run_post()
    elif arg == "--cleanup":
        run_cleanup()
    else:
        print("usage: --pre | --post | --cleanup")
        sys.exit(2)
