#!/usr/bin/env python3
"""RV-①b-④ live e2e 探针——echo 消息历史重启存活双向硬证。

模式：
  --pre      修前 GAP 坐实（旧码进程：send→restart→历史归零=重启即丢）
  --post     修后存活（新码进程：send→restart→历史仍在+DB 行回读+msg_id 不撞）
  --cleanup  清痕（echo_history 标记行 + timeline message_sent 标记事件 + 复重启
             清内存面，remaining=0 回读）

纪律：TR-② 事件行面——echo send 会写 timeline message_sent 事件，探针标记
RV1B4-PROBE 进 summary，--cleanup 按标记删净并回读 remaining=0。
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
MARKER = "RV1B4-PROBE"
ECHO_DB = "/home/climbing/opensoul/data/echo_history.db"
TIMELINE_DB = "/home/climbing/opensoul/data/timeline.db"
SERVICE = "opensoul.service"

PASS = []
FAIL = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}")


def http(method: str, path: str, payload: dict | None = None, timeout: float = 20.0):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def health_wait(max_s: float = 60.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < max_s:
        try:
            r = http("GET", "/api/health", timeout=5)
            if r.get("status") in ("ok", "healthy", True) or r:
                return True
        except Exception:
            time.sleep(1.5)
    return False


def restart() -> None:
    subprocess.run(["systemctl", "--user", "restart", SERVICE], check=True)
    ok = health_wait()
    check("restart_health", ok)


def send(title: str, tenant: str | None = None) -> dict:
    # 匿名（tenant 'default'）或带 token 由调用方决定——探针走匿名面即可
    return http("POST", "/api/echo/send",
                {"channel": "console", "title": title, "content": f"{MARKER} content"})


def history(limit: int = 200) -> list:
    return http("GET", f"/api/echo/history?limit={limit}")["messages"]


def stats() -> dict:
    return http("GET", "/api/echo/stats")


def echo_db_rows(like: str = f"%{MARKER}%") -> list:
    if not __import__("pathlib").Path(ECHO_DB).exists():
        return []
    conn = sqlite3.connect(ECHO_DB)
    try:
        return conn.execute(
            "SELECT msg_id, title, status, tenant_id, content FROM echo_history"
            " WHERE title LIKE ? ORDER BY timestamp", (like,)
        ).fetchall()
    finally:
        conn.close()


def timeline_marker_count() -> int:
    p = __import__("pathlib").Path(TIMELINE_DB)
    if not p.exists():
        return 0
    conn = sqlite3.connect(TIMELINE_DB)
    try:
        n = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'")
        if not n.fetchone():
            return 0
        return conn.execute(
            "SELECT COUNT(*) FROM events WHERE summary LIKE ?", (f"%{MARKER}%",)
        ).fetchone()[0]
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre", action="store_true")
    ap.add_argument("--post", action="store_true")
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()

    if args.pre:
        # 修前：send→restart→历史归零（GAP 坐实）
        r = send(f"{MARKER}-pre-1")
        check("pre_send", r.get("success") is True, r.get("msg_id", ""))
        pre_ids = {m["msg_id"] for m in history()}
        check("pre_in_memory", r["msg_id"] in pre_ids)
        restart()
        post_ids = {m["msg_id"] for m in history()}
        check("pre_GAP_restart_dropped", r["msg_id"] not in post_ids,
              f"after_restart={len(post_ids)}")

    if args.post:
        st0 = stats()["total_messages"]
        r1 = send(f"{MARKER}-post-1")
        r2 = send(f"{MARKER}-post-2")
        check("post_send_x2", bool(r1.get("success")) and bool(r2.get("success")),
              f"{r1.get('msg_id')} {r2.get('msg_id')}")
        check("post_ids_distinct", r1["msg_id"] != r2["msg_id"])
        restart()
        ids = {m["msg_id"] for m in history()}
        check("post_survives_1", r1["msg_id"] in ids)
        check("post_survives_2", r2["msg_id"] in ids)
        st1 = stats()["total_messages"]
        check("post_stats_grew", st1 == st0 + 2, f"{st0}→{st1}")
        rows = echo_db_rows()
        db_ids = {row[0] for row in rows}
        check("post_db_rows", r1["msg_id"] in db_ids and r2["msg_id"] in db_ids,
              f"db_rows={len(rows)}")
        row1 = [row for row in rows if row[0] == r1["msg_id"]][0]
        # 行形 (msg_id, title, status, tenant_id, content)——列序探针自纠：title=idx1
        check("post_row_fields", row1[1].startswith(MARKER) and row1[2] == "sent"
              and row1[3] == "default" and row1[4] == f"{MARKER} content", repr(row1))
        # 重启后 counter 回填：再发一条不撞旧行
        r3 = send(f"{MARKER}-post-3")
        check("post_no_collision", r3["msg_id"] not in {r1["msg_id"], r2["msg_id"]},
              r3.get("msg_id", ""))
        rows3 = echo_db_rows()
        check("post_three_rows", len(rows3) == 3, f"rows={len(rows3)}")

    if args.cleanup:
        n0 = len(echo_db_rows())
        t0 = timeline_marker_count()
        if __import__("pathlib").Path(ECHO_DB).exists():
            conn = sqlite3.connect(ECHO_DB)
            conn.execute("DELETE FROM echo_history WHERE title LIKE ?", (f"%{MARKER}%",))
            conn.commit()
            conn.close()
        if t0 and __import__("pathlib").Path(TIMELINE_DB).exists():
            conn = sqlite3.connect(TIMELINE_DB)
            conn.execute("DELETE FROM events WHERE summary LIKE ?", (f"%{MARKER}%",))
            conn.commit()
            conn.close()
        restart()  # 清内存面（懒回载后无标记行）
        remaining = len(echo_db_rows())
        t_rem = timeline_marker_count()
        hist_markers = [m for m in history() if MARKER in m["title"]]
        check("cleanup_remaining_zero", remaining == 0 and t_rem == 0 and not hist_markers,
              f"echo_db={remaining} timeline={t_rem} memory={len(hist_markers)} (was {n0}/{t0})")

    print(f"\n== RESULT {len(PASS)} PASS / {len(FAIL)} FAIL ==")
    if FAIL:
        print("FAILED:", FAIL)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
