#!/usr/bin/env python3
"""RV-①b-⑤-1 live e2e 探针——a2a 任务行重启存活双向硬证。

模式：
  --pre      修前 GAP 坐实（旧码进程：SendMessage→restart→GetTask 404=重启即丢
             + data/a2a_tasks.db 零落盘口）
  --post     修后存活（新码进程：SendMessage→restart→GetTask 历史/状态/metadata
             逐键存活 + DB 行回读 + 重启后任务仍可续接真跑=不只可读）
  --cleanup  清痕（a2a_tasks.db 标记行 + timeline 标记事件面回读 remaining=0）

标记：RV1B5-PROBE（消息文本 + metadata.probe 双标，清痕按 payload LIKE）。
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

BASE = "http://127.0.0.1:8090"
MARKER = "RV1B5-PROBE"
A2A_DB = "/home/climbing/opensoul/data/a2a_tasks.db"
TIMELINE_DB = "/home/climbing/opensoul/data/timeline.db"
SERVICE = "opensoul.service"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}")


def rpc(method: str, params: dict | None = None, timeout: float = 40.0):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + "/a2a", data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def http_get(path: str, timeout: float = 10.0):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def health_wait(max_s: float = 60.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < max_s:
        try:
            r = http_get("/api/health", timeout=5)
            if r:
                return True
        except Exception:
            time.sleep(1.5)
    return False


def restart() -> None:
    subprocess.run(["systemctl", "--user", "restart", SERVICE], check=True)
    ok = health_wait()
    check("restart_health", ok)


def send(text: str, metadata: dict | None = None, task_id: str | None = None) -> dict:
    message: dict = {"role": "user", "parts": [{"text": text}]}
    if task_id:
        message["taskId"] = task_id
    params: dict = {"message": message}
    if metadata:
        params["metadata"] = metadata
    return rpc("SendMessage", params)


def get_task(task_id: str) -> dict:
    return rpc("GetTask", {"id": task_id})


def task_of(resp: dict) -> dict | None:
    """SendMessage → {"task":...}（v1.0 包裹）；GetTask → 裸 Task；error → None。"""
    if "error" in resp and resp.get("error"):
        return None
    result = resp.get("result") or {}
    return result.get("task") or (result if result.get("id") else None)


def db_rows(marker: str = MARKER) -> list:
    try:
        conn = sqlite3.connect(A2A_DB)
    except sqlite3.Error:
        return []
    try:
        return conn.execute(
            "SELECT task_id, context_id, state, status_timestamp, payload"
            " FROM a2a_tasks WHERE payload LIKE ? ORDER BY task_id",
            (f"%{marker}%",),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def timeline_marker_rows() -> int:
    try:
        conn = sqlite3.connect(TIMELINE_DB)
    except sqlite3.Error:
        return 0
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM events WHERE summary LIKE ? OR details LIKE ?",
            (f"%{MARKER}%", f"%{MARKER}%"),
        ).fetchone()
        return int(row[0])
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


def run_pre() -> None:
    print("== --pre：修前 GAP 坐实（旧码进程）==")
    r = send(f"{MARKER} 你好，回复一个字即可", metadata={"probe": MARKER})
    t = task_of(r)
    check("pre_send_created", t is not None, f"task={t and t.get('id')}")
    if not t:
        return
    tid = t["id"]
    g = task_of(get_task(tid))
    check("pre_get_task_in_memory", g is not None and g.get("id") == tid)
    rows = db_rows()
    check("pre_db_zero_rows", len(rows) == 0, f"rows={len(rows)}（修前零落盘口）")
    restart()
    g2 = get_task(tid)
    gone = g2.get("error") is not None
    check("pre_restart_task_lost", gone, f"resp={json.dumps(g2.get('error') or g2.get('result'))[:120]}")
    lt = rpc("ListTasks")
    total = (lt.get("result") or {}).get("totalSize")
    check("pre_list_empty_after_restart", total == 0, f"totalSize={total}")


def run_post() -> None:
    print("== --post：修后存活（新码进程）==")
    r1 = send(f"{MARKER} 你好，回复一个字即可", metadata={"probe": MARKER})
    t1 = task_of(r1)
    check("post_send1_created", t1 is not None, f"task={t1 and t1.get('id')}")
    if not t1:
        return
    tid1 = t1["id"]
    check("post_send1_completed", t1.get("status", {}).get("state") == "TASK_STATE_COMPLETED",
          f"state={t1.get('status', {}).get('state')}")
    # 裸「搜索」=技能参数缺失 → input-required + pending 路由记忆（不打 LLM，快）
    # metadata 带同一标记：清痕/回读按 payload LIKE 覆盖全部探针行（作者错误教训：
    # 首版 task2 无标记→db_rows 过滤漏行误报 FAIL，行其实在）。
    r2 = send("搜索", metadata={"probe": MARKER})
    t2 = task_of(r2)
    check("post_send2_input_required",
          t2 is not None and t2.get("status", {}).get("state") == "TASK_STATE_INPUT_REQUIRED",
          f"state={t2 and t2.get('status', {}).get('state')}")
    if not t2:
        return
    tid2 = t2["id"]

    rows = db_rows()
    ids = {r[0] for r in rows}
    check("post_db_rows_written", {tid1, tid2} <= ids, f"rows={len(rows)} ids={sorted(ids)}")

    restart()

    g1 = task_of(get_task(tid1))
    ok1 = (
        g1 is not None
        and g1.get("id") == tid1
        and g1.get("status", {}).get("state") == "TASK_STATE_COMPLETED"
    )
    check("post_restart_task1_survives", ok1, f"state={g1 and g1.get('status', {}).get('state')}")
    if g1:
        roles = [m.get("role") for m in g1.get("history", [])]
        utext = json.dumps(g1.get("history", []), ensure_ascii=False)
        check("post_restart_history_intact",
              roles == ["ROLE_USER", "ROLE_AGENT"] and MARKER in utext,
              f"roles={roles}")
        check("post_restart_metadata_intact",
              (g1.get("metadata") or {}).get("probe") == MARKER,
              f"metadata={g1.get('metadata')}")
        ctx_ok = bool(g1.get("contextId")) and all(
            m.get("contextId") == g1.get("contextId") for m in g1.get("history", [])
        )
        check("post_restart_context_roundtrip", ctx_ok)

    g2 = task_of(get_task(tid2))
    ok2 = (
        g2 is not None
        and g2.get("status", {}).get("state") == "TASK_STATE_INPUT_REQUIRED"
        and (g2.get("metadata") or {}).get("_pending_skill_id") == "search"
    )
    check("post_restart_task2_pending_survives", ok2,
          f"state={g2 and g2.get('status', {}).get('state')} meta={g2 and g2.get('metadata')}")

    lt = rpc("ListTasks")
    total = (lt.get("result") or {}).get("totalSize") or 0
    listed = {t.get("id") for t in (lt.get("result") or {}).get("tasks", [])}
    check("post_restart_list_sees_both", {tid1, tid2} <= listed, f"totalSize={total}")

    rows2 = db_rows()
    st1 = {r[0]: r[2] for r in rows2}
    check("post_db_state_column_matches",
          st1.get(tid1) == "completed" and st1.get(tid2) == "input-required",
          f"states={st1}")

    # 重启后任务仍可续接真跑=不只可读（pending 回填→search 真执行→completed）
    r3 = send("小猫", task_id=tid2)
    t3 = task_of(r3)
    check("post_restart_continuation_runs",
          t3 is not None and t3.get("status", {}).get("state") == "TASK_STATE_COMPLETED",
          f"state={t3 and t3.get('status', {}).get('state')}")
    if t3:
        check("post_continuation_history_4", len(t3.get("history", [])) == 4,
              f"len={len(t3.get('history', []))}")


def run_cleanup() -> None:
    print("== --cleanup：清痕 remaining=0 ==")
    conn = sqlite3.connect(A2A_DB)
    try:
        cur = conn.execute("DELETE FROM a2a_tasks WHERE payload LIKE ?", (f"%{MARKER}%",))
        conn.commit()
        deleted = cur.rowcount
    except sqlite3.OperationalError as e:
        deleted = f"err {e}"
    finally:
        conn.close()
    print(f"deleted a2a_tasks rows: {deleted}")
    tl = timeline_marker_rows()
    check("cleanup_timeline_no_marker_rows", tl == 0, f"timeline_marker_rows={tl}")
    restart()
    remaining = len(db_rows())
    check("cleanup_remaining_zero", remaining == 0, f"remaining={remaining}")
    lt = rpc("ListTasks")
    listed = [
        t.get("id")
        for t in (lt.get("result") or {}).get("tasks", [])
        if MARKER in json.dumps(t, ensure_ascii=False)
    ]
    check("cleanup_memory_no_marker_tasks", not listed, f"listed={listed}")


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
        run_cleanup()
    print(f"\n== RESULT: {len(PASS)} passed / {len(FAIL)} failed ==")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
