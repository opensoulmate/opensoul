#!/usr/bin/env python3
"""RV-①b-⑤-3 live e2e 探针——a2a 投递留痕（push_delivery_log）重启存活双向硬证。

模式：
  --pre      修前 GAP 坐实（旧码进程：webhook 投递真发生（stub 真收 POST）但
             data/a2a_push_delivery_log.db 零落盘口=留痕只活内存，重启必丢）
  --post     修后存活（新码进程：投递尝试逐行落盘（成败两型）→restart→审计史逐行
             仍在（行数/逐键不变）→再投→增量追加不覆盖）
  --cleanup  清痕（a2a_push_delivery_log/a2a_push_configs/a2a_tasks 标记行 + timeline
             标记事件面回读 remaining=0）

标记：RV1B53-PROBE（url 路径/token/metadata.probe 多标，清痕按 payload LIKE）。
诚实边界：push_delivery_log 无 HTTP API 读面（既有读面=tests/live SQL 取证），
进程内列表的懒回载逐键 parity 由 tests/test_a2a_push_delivery_log_persist.py 双实例
实证；本探针 live 实证=库行真源（写穿/重启存活/增量追加）。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE = "http://127.0.0.1:8090"
MARKER = "RV1B53-PROBE"
LOG_DB = "/home/climbing/opensoul/data/a2a_push_delivery_log.db"
PUSH_DB = "/home/climbing/opensoul/data/a2a_push_configs.db"
A2A_DB = "/home/climbing/opensoul/data/a2a_tasks.db"
TIMELINE_DB = "/home/climbing/opensoul/data/timeline.db"
SERVICE = "opensoul.service"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}")


def rpc(method: str, params: dict | None = None, timeout: float = 60.0):
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


def task_of(resp: dict) -> dict | None:
    if "error" in resp and resp.get("error"):
        return None
    result = resp.get("result") or {}
    return result.get("task") or (result if result.get("id") else None)


def log_rows(marker: str = MARKER) -> list:
    """投递留痕库行（payload LIKE 标记）。"""
    try:
        conn = sqlite3.connect(LOG_DB)
    except sqlite3.Error:
        return []
    try:
        return conn.execute(
            "SELECT delivery_id, task_id, config_id, ok, payload FROM a2a_push_delivery_log"
            " WHERE payload LIKE ? ORDER BY rowid",
            (f"%{marker}%",),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def push_rows(marker: str = MARKER) -> list:
    try:
        conn = sqlite3.connect(PUSH_DB)
    except sqlite3.Error:
        return []
    try:
        return conn.execute(
            "SELECT task_id, config_id FROM a2a_push_configs WHERE payload LIKE ?",
            (f"%{marker}%",),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def task_rows(marker: str = MARKER) -> list:
    try:
        conn = sqlite3.connect(A2A_DB)
    except sqlite3.Error:
        return []
    try:
        return conn.execute(
            "SELECT task_id FROM a2a_tasks WHERE payload LIKE ?",
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


def start_stub():
    """真 HTTP webhook 桩（独立线程+真 socket），记录每次 POST 体。"""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            received.append({"path": self.path, "body": json.loads(body or b"{}")})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/{MARKER}", received


def closed_port_url() -> str:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}/{MARKER}"


def drive_delivery(url: str) -> tuple[str, str]:
    """建 input-required 任务→登记配置→续接触发 working+终态投递。返回 (task_id, config_id)。"""
    r = send("搜索", metadata={"probe": MARKER})
    t = task_of(r)
    if not t:
        return "", ""
    tid = t["id"]
    c = rpc("CreateTaskPushNotificationConfig", {
        "taskId": tid,
        "url": url,
        "token": MARKER,
        "authentication": {"scheme": "Bearer", "credentials": f"cred-{MARKER}"},
    })
    cfg = c.get("result") or {}
    if not cfg.get("id"):
        return tid, ""
    r2 = send(f"小猫 {MARKER}", task_id=tid, metadata={"probe": MARKER})
    t2 = task_of(r2)
    ok = t2 is not None and t2.get("status", {}).get("state") == "TASK_STATE_COMPLETED"
    check(f"drive_completed_{url[-12:]}", ok,
          f"state={t2 and t2.get('status', {}).get('state')}")
    return tid, str(cfg.get("id"))


def run_pre() -> None:
    print("== --pre：修前 GAP 坐实（旧码进程，不重启——重启会载新码）==")
    check("pre_log_db_absent_or_empty", len(log_rows()) == 0,
          f"rows={len(log_rows())}（修前零落盘口）")
    server, url, received = start_stub()
    try:
        tid, cid = drive_delivery(url)
        check("pre_task_and_config", bool(tid) and bool(cid), f"tid={tid} cid={cid}")
        time.sleep(0.5)
        check("pre_delivery_really_happened", len(received) >= 2,
              f"stub 收 {len(received)} POST（working+终态）")
        keys = [next(iter(e["body"]), None) for e in received]
        check("pre_payload_shape", "statusUpdate" in keys and "task" in keys, f"keys={keys}")
        rows = log_rows()
        check("pre_log_db_zero_rows", len(rows) == 0,
              f"rows={len(rows)}（投递真发生但零落盘=留痕只活内存，重启必丢）")
    finally:
        server.shutdown()
        server.server_close()


def run_post() -> None:
    print("== --post：修后存活（新码进程）==")
    # 先重启载新码：--pre 跑完进程仍是旧码（--pre 刻意不重启防载新码），本模式
    # 的全部断言都以新码进程为前提（探针时序纪律：修前自伤点）。
    restart()
    server, url, received = start_stub()
    try:
        # 成功投递两行（working+终态）
        tid, cid = drive_delivery(url)
        check("post_task_and_config", bool(tid) and bool(cid), f"tid={tid} cid={cid}")
        time.sleep(0.5)
        check("post_delivery_really_happened", len(received) >= 2,
              f"stub 收 {len(received)} POST")
        rows = log_rows()
        ok_rows = [r for r in rows if r[3] == 1]
        check("post_log_rows_written", len(ok_rows) >= 2,
              f"rows={[(r[0][:8], r[3]) for r in rows]}")
        sample = json.loads(rows[0][4]) if rows else {}
        check("post_row_entry_keys",
              set(sample) >= {"delivery_id", "task_id", "config_id", "url", "payload_key", "ok"},
              f"keys={sorted(sample)}")
        check("post_row_id_matches_column", sample.get("delivery_id") == rows[0][0] if rows else False)

        # 失败投递（死端口）同样留痕：ok=0+error 真因
        tid2, cid2 = drive_delivery(closed_port_url())
        check("post_fail_task_and_config", bool(tid2) and bool(cid2), f"tid={tid2}")
        time.sleep(0.5)
        rows2 = log_rows()
        bad = [r for r in rows2 if r[3] == 0]
        bad_err = all(json.loads(r[4]).get("error") for r in bad)
        check("post_failure_rows_written", len(bad) >= 2 and bad_err,
              f"bad={len(bad)} err_all={bad_err}")

        # 重启→审计史逐行仍在（行数/逐键不变）
        snap = [(r[0], r[1], r[3], r[4]) for r in log_rows()]
        restart()
        rows3 = [(r[0], r[1], r[3], r[4]) for r in log_rows()]
        check("post_log_survives_restart", rows3 == snap,
              f"before={len(snap)} after={len(rows3)}")

        # 重启后继续投递=增量追加不覆盖
        server2, url2, received2 = start_stub()
        try:
            tid3, _cid3 = drive_delivery(url2)
            time.sleep(0.5)
            rows4 = log_rows()
            check("post_increment_append_not_overwrite",
                  len(rows4) > len(rows3) and len(received2) >= 2,
                  f"rows {len(rows3)}→{len(rows4)} stub={len(received2)}")
            old_ids = {r[0] for r in rows3}
            check("post_old_rows_intact", old_ids <= {r[0] for r in rows4})
        finally:
            server2.shutdown()
            server2.server_close()
    finally:
        server.shutdown()
        server.server_close()


def run_cleanup() -> None:
    print("== --cleanup：清痕 remaining=0 ==")
    for db, table in ((LOG_DB, "a2a_push_delivery_log"), (PUSH_DB, "a2a_push_configs"),
                      (A2A_DB, "a2a_tasks")):
        conn = sqlite3.connect(db)
        try:
            cur = conn.execute(f"DELETE FROM {table} WHERE payload LIKE ?", (f"%{MARKER}%",))
            deleted = cur.rowcount
            conn.commit()
        except sqlite3.OperationalError as e:
            deleted = f"err {e}"
        finally:
            conn.close()
        print(f"deleted {table} rows: {deleted}")
    tl = timeline_marker_rows()
    check("cleanup_timeline_no_marker_rows", tl == 0, f"timeline_marker_rows={tl}")
    restart()
    check("cleanup_log_remaining_zero", len(log_rows()) == 0)
    check("cleanup_push_remaining_zero", len(push_rows()) == 0)
    check("cleanup_task_remaining_zero", len(task_rows()) == 0)
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
    for f in FAIL:
        print(f"  FAIL: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
