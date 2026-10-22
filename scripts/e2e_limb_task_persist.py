#!/usr/bin/env python3
"""RV-①b-⑦-1 live e2e 探针——limb RPA 任务行重启存活双向硬证。

模式：
  --pre      修前 GAP 坐实（旧码进程：create→/tasks 可见→data/limb_tasks.db
             零落盘口→restart→GET 404+list 空=任务丢）
  --post     修后存活（新码进程：先 restart 载新码→create/execute→DB 行回读
             逐键→restart→任务存活（含 results/variables）→cancel 行随落→
             DELETE 删行→restart 仍 404）
  --cleanup  清痕（按 RV1B71-PROBE 标记删 API 任务+DB 行，remaining=0 回读）

纪律：任务 name 恒带 RV1B71-PROBE 标记，--cleanup 按标记删净；只动探针自己
建的任务，不碰既有生产任务。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8090"
MARKER = "RV1B71-PROBE"
DB = Path("/home/climbing/opensoul/data/limb_tasks.db")
SERVICE = "opensoul.service"

PASS = []
FAIL = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}")


def http(method: str, path: str, payload: dict | None = None, timeout: float = 20.0):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            txt = resp.read().decode()
            return resp.status, (json.loads(txt) if txt else None)
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt) if txt else None
        except Exception:
            return e.code, {"raw": txt[:200]}


def health_wait(timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, _ = http("GET", "/api/limb/health", timeout=5)
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def restart() -> None:
    subprocess.run(["systemctl", "--user", "restart", SERVICE], check=True)
    ok = health_wait()
    check("restart_health", ok)


def db_rows() -> list[tuple]:
    if not DB.exists():
        return []
    conn = sqlite3.connect(str(DB))
    try:
        return conn.execute(
            "SELECT task_id, status, payload FROM limb_tasks"
        ).fetchall()
    finally:
        conn.close()


def probe_rows() -> list[tuple]:
    out = []
    for r in db_rows():
        try:
            name = json.loads(r[2]).get("name") or ""
        except Exception:
            continue
        if MARKER in name:
            out.append(r)
    return out


def create_task(tag: str) -> dict | None:
    status, data = http(
        "POST",
        "/api/limb/tasks",
        {
            "name": f"{MARKER}-{tag}",
            "actions": [
                {"action_type": "navigate", "target": "https://example.com"},
                {"action_type": "click", "target": "#btn"},
            ],
            "tags": ["probe"],
            "variables": {"url": "https://x", "中文": "值"},
        },
    )
    if status == 200 and isinstance(data, dict) and data.get("task_id"):
        return data
    check(f"create_{tag}", False, f"status={status} data={str(data)[:120]}")
    return None


def run_pre() -> None:
    print("== --pre 修前 GAP 坐实 ==")
    task = create_task("pre")
    if task is None:
        return
    tid = task["task_id"]
    check("pre_create_visible", task.get("name") == f"{MARKER}-pre", tid)

    status, data = http("GET", f"/api/limb/tasks/{tid}")
    check("pre_get_visible", status == 200 and data.get("task_id") == tid)

    rows = probe_rows()
    check("pre_zero_persist_row", len(rows) == 0, f"rows={len(rows)} db_exists={DB.exists()}")

    restart()

    status, _ = http("GET", f"/api/limb/tasks/{tid}")
    check("pre_restart_get_gone", status == 404, f"status={status}")
    status, data = http("GET", "/api/limb/tasks")
    names = [t.get("name") for t in (data or {}).get("tasks", [])]
    check("pre_restart_list_gone", not any(MARKER in (n or "") for n in names), f"names={names}")

    print(f"RESULT --pre PASS={len(PASS)} FAIL={len(FAIL)}")


def run_post() -> None:
    print("== --post 修后存活 ==")
    restart()  # 先重启载新码（⑥-2/⑥-1 探针时序纪律）

    # 1) create→行写穿
    task = create_task("post")
    if task is None:
        return
    tid = task["task_id"]
    rows = {r[0]: r for r in probe_rows()}
    check("post_row_written", tid in rows, f"rows={sorted(rows)}")
    if tid in rows:
        payload = json.loads(rows[tid][2])
        check(
            "post_row_fields",
            payload.get("name") == f"{MARKER}-post"
            and payload.get("tenant_id") == "default"
            and len(payload.get("actions", [])) == 2
            and payload.get("variables", {}).get("中文") == "值",
            f"payload_keys={sorted(payload)[:6]}",
        )

    # 2) execute→终态行随落（results 落盘）
    status, data = http("POST", f"/api/limb/tasks/{tid}/execute")
    check("post_execute", status == 200 and data.get("status") == "completed")
    rows = {r[0]: r for r in probe_rows()}
    payload = json.loads(rows[tid][2])
    check(
        "post_terminal_row",
        payload.get("status") == "completed" and len(payload.get("results", [])) == 2,
        f"status={payload.get('status')} results={len(payload.get('results', []))}",
    )

    # 3) restart→任务存活逐键
    restart()
    status, data = http("GET", f"/api/limb/tasks/{tid}")
    check("post_restart_get_alive", status == 200 and data.get("task_id") == tid, f"status={status}")
    if status == 200:
        check(
            "post_restart_fields",
            data.get("name") == f"{MARKER}-post"
            and data.get("status") == "completed"
            and len(data.get("results", [])) == 2
            and data.get("tenant_id") == "default",
            f"status={data.get('status')} results={len(data.get('results', []))}",
        )
    status, data2 = http("GET", "/api/limb/tasks")
    names = [t.get("name") for t in (data2 or {}).get("tasks", [])]
    check("post_restart_list_alive", f"{MARKER}-post" in names, f"names={names}")

    # 4) in-flight running 行降 failed（直写真实在途行=列+payload 同步 running
    #    ——_sync_task 单点写入两处恒同步，探针复现真实行形禁只改列造漂移）
    t2 = create_task("inflight")
    if t2 is not None:
        tid2 = t2["task_id"]
        conn = sqlite3.connect(str(DB))
        try:
            row = conn.execute(
                "SELECT payload FROM limb_tasks WHERE task_id=?", (tid2,)
            ).fetchone()
            payload = json.loads(row[0])
            payload["status"] = "running"
            conn.execute(
                "UPDATE limb_tasks SET status='running', payload=? WHERE task_id=?",
                (json.dumps(payload, ensure_ascii=False), tid2),
            )
            conn.commit()
        finally:
            conn.close()
        restart()
        status, data = http("GET", f"/api/limb/tasks/{tid2}")
        check(
            "post_inflight_downshift",
            status == 200
            and data.get("status") == "failed"
            and "interrupted by service restart" in (data.get("error") or ""),
            f"status={data.get('status') if data else None} error={str((data or {}).get('error'))[:60]}",
        )

    # 5) cancel 行随落→restart 仍 cancelled
    t3 = create_task("cancel")
    if t3 is not None:
        tid3 = t3["task_id"]
        status, _ = http("POST", f"/api/limb/tasks/{tid3}/cancel")
        check("post_cancel", status == 200)
        restart()
        status, data = http("GET", f"/api/limb/tasks/{tid3}")
        check(
            "post_cancel_survives",
            status == 200 and data.get("status") == "cancelled",
            f"status={data.get('status') if data else None}",
        )

    # 6) DELETE 删行→restart 仍 404
    status, _ = http("DELETE", f"/api/limb/tasks/{tid}")
    check("post_delete", status == 200)
    rows = {r[0]: r for r in probe_rows()}
    check("post_delete_row_gone", tid not in rows, f"rows={sorted(rows)}")
    restart()
    status, _ = http("GET", f"/api/limb/tasks/{tid}")
    check("post_delete_stays_gone", status == 404, f"status={status}")

    # 7) stats 回载行计入（total_tasks 含回载行）
    status, data = http("GET", "/api/limb/stats")
    check(
        "post_stats_counting",
        status == 200 and (data or {}).get("total_tasks", 0) >= 1,
        f"stats={str(data)[:120]}",
    )

    print(f"RESULT --post PASS={len(PASS)} FAIL={len(FAIL)}")


def cleanup() -> None:
    print("== --cleanup 清痕 ==")
    # API 删探针任务（非 running 都可删）
    status, data = http("GET", "/api/limb/tasks?limit=500")
    for t in (data or {}).get("tasks", []):
        if MARKER in (t.get("name") or ""):
            http("DELETE", f"/api/limb/tasks/{t['task_id']}")
    # DB 残行（探针标记）直清
    if DB.exists():
        conn = sqlite3.connect(str(DB))
        try:
            conn.execute(
                "DELETE FROM limb_tasks WHERE payload LIKE ?", (f"%{MARKER}%",)
            )
            conn.commit()
        finally:
            conn.close()
    remaining = len(probe_rows())
    check("cleanup_remaining_zero", remaining == 0, f"remaining={remaining}")
    status, data = http("GET", "/api/limb/tasks?limit=500")
    names = [t.get("name") for t in (data or {}).get("tasks", [])]
    check("cleanup_api_zero", not any(MARKER in (n or "") for n in names), f"names={names}")
    print(f"RESULT --cleanup PASS={len(PASS)} FAIL={len(FAIL)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre", action="store_true")
    ap.add_argument("--post", action="store_true")
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()
    if args.pre:
        run_pre()
    if args.post:
        run_post()
    if args.cleanup:
        cleanup()
    print(f"TOTAL PASS={len(PASS)} FAIL={len(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
