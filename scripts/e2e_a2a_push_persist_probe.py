#!/usr/bin/env python3
"""RV-①b-⑤-2 live e2e 探针——a2a 推送配置重启存活双向硬证。

模式：
  --pre      修前 GAP 坐实（旧码进程：登记推送配置→restart→GetTaskPushNotificationConfig
             -32001 配置丢 + data/a2a_push_configs.db 零落盘口）
  --post     修后存活（新码进程：配置落盘行→restart→逐键取回+重启后 webhook 投递真
             收 POST（回载配置仍是有效投递依据=不只可读）+删除写穿重启后仍无）
  --cleanup  清痕（a2a_push_configs/a2a_tasks 标记行 + timeline 标记事件面回读
             remaining=0）

标记：RV1B52-PROBE（url 路径/token/metadata.probe 多标，清痕按 payload LIKE）。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE = "http://127.0.0.1:8090"
MARKER = "RV1B52-PROBE"
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


def push_rows(marker: str = MARKER) -> list:
    try:
        conn = sqlite3.connect(PUSH_DB)
    except sqlite3.Error:
        return []
    try:
        return conn.execute(
            "SELECT task_id, config_id, payload FROM a2a_push_configs"
            " WHERE payload LIKE ? ORDER BY rowid",
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
    """真 HTTP webhook 桩（独立线程+真 socket），记录每次 POST 头与体。"""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            received.append(
                {"path": self.path, "headers": dict(self.headers),
                 "body": json.loads(body or b"{}")}
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/{MARKER}", received


def run_pre() -> None:
    print("== --pre：修前 GAP 坐实（旧码进程）==")
    check("pre_push_db_absent", not __import__("pathlib").Path(PUSH_DB).exists(),
          f"push_db={PUSH_DB}")
    r = send(f"{MARKER} 你好", metadata={"probe": MARKER})
    t = task_of(r)
    check("pre_task_created", t is not None)
    if not t:
        return
    tid = t["id"]
    c = rpc("CreateTaskPushNotificationConfig", {
        "taskId": tid,
        "url": f"http://127.0.0.1:1/{MARKER}",
        "token": MARKER,
    })
    cfg = (c.get("result") or {})
    check("pre_config_created_in_memory", bool(cfg.get("id")), f"id={cfg.get('id')}")
    rows = push_rows()
    check("pre_push_db_zero_rows", len(rows) == 0, f"rows={len(rows)}（修前零落盘口）")
    restart()
    g = rpc("GetTaskPushNotificationConfig", {"taskId": tid, "id": cfg.get("id", "")})
    lost = g.get("error") is not None
    check("pre_config_lost_after_restart", lost,
          f"resp={json.dumps(g.get('error') or g.get('result'))[:120]}")


def run_post() -> None:
    print("== --post：修后存活（新码进程）==")
    server, url, received = start_stub()
    try:
        # 裸「搜索」= input-required（不打 LLM，快）——留非终态，重启后续接触发投递
        r = send("搜索", metadata={"probe": MARKER})
        t = task_of(r)
        check("post_task_input_required",
              t is not None and t.get("status", {}).get("state") == "TASK_STATE_INPUT_REQUIRED",
              f"state={t and t.get('status', {}).get('state')}")
        if not t:
            return
        tid = t["id"]
        c = rpc("CreateTaskPushNotificationConfig", {
            "taskId": tid,
            "url": url,
            "token": MARKER,
            "authentication": {"scheme": "Bearer", "credentials": f"cred-{MARKER}"},
        })
        cfg = (c.get("result") or {})
        cid = cfg.get("id")
        check("post_config_created", bool(cid), f"id={cid}")
        rows = push_rows()
        ok_row = len(rows) == 1 and rows[0][1] == cid
        check("post_push_db_row_written", ok_row, f"rows={[(r[0], r[1]) for r in rows]}")

        restart()

        g = rpc("GetTaskPushNotificationConfig", {"taskId": tid, "id": cid})
        got = g.get("result") or {}
        ok_keys = (
            got.get("id") == cid
            and got.get("taskId") == tid
            and got.get("url") == url
            and got.get("token") == MARKER
            and (got.get("authentication") or {}).get("scheme") == "Bearer"
            and (got.get("authentication") or {}).get("credentials") == f"cred-{MARKER}"
        )
        check("post_config_survives_restart_verbatim", ok_keys,
              f"got={json.dumps(got, ensure_ascii=False)[:200]}")

        ls = rpc("ListTaskPushNotificationConfigs", {"taskId": tid})
        ids = [c.get("id") for c in (ls.get("result") or {}).get("configs", [])]
        check("post_list_sees_config", cid in ids, f"ids={ids}")

        # 重启后 webhook 投递真跑：续接 → working + terminal 各投一次（回载配置=投递依据）
        received.clear()
        r2 = send("小猫", task_id=tid, metadata={"probe": MARKER})
        t2 = task_of(r2)
        check("post_continuation_completed",
              t2 is not None and t2.get("status", {}).get("state") == "TASK_STATE_COMPLETED",
              f"state={t2 and t2.get('status', {}).get('state')}")
        time.sleep(0.5)
        keys = [next(iter(e["body"]), None) for e in received]
        auths = [e["headers"].get("Authorization") for e in received]
        check("post_restart_delivery_works",
              len(received) == 2 and "statusUpdate" in keys and "task" in keys,
              f"keys={keys}")
        check("post_delivery_auth_header_roundtrip",
              all(a == f"Bearer cred-{MARKER}" for a in auths), f"auths={auths}")

        # 删除写穿：删 → 库行消 → 重启 → 仍无（-32001）
        d = rpc("DeleteTaskPushNotificationConfig", {"taskId": tid, "id": cid})
        check("post_delete_ok", "error" not in (d or {}) or not d.get("error"),
              f"resp={json.dumps(d)[:80]}")
        check("post_push_db_row_gone", len(push_rows()) == 0)
        restart()
        g2 = rpc("GetTaskPushNotificationConfig", {"taskId": tid, "id": cid})
        check("post_delete_survives_restart", g2.get("error") is not None,
              f"resp={json.dumps(g2.get('error') or g2.get('result'))[:100]}")
    finally:
        server.shutdown()
        server.server_close()


def run_cleanup() -> None:
    print("== --cleanup：清痕 remaining=0 ==")
    conn = sqlite3.connect(PUSH_DB)
    try:
        cur = conn.execute("DELETE FROM a2a_push_configs WHERE payload LIKE ?",
                           (f"%{MARKER}%",))
        deleted_push = cur.rowcount
        conn.commit()
    except sqlite3.OperationalError as e:
        deleted_push = f"err {e}"
    finally:
        conn.close()
    conn = sqlite3.connect(A2A_DB)
    try:
        cur = conn.execute("DELETE FROM a2a_tasks WHERE payload LIKE ?",
                           (f"%{MARKER}%",))
        deleted_tasks = cur.rowcount
        conn.commit()
    except sqlite3.OperationalError as e:
        deleted_tasks = f"err {e}"
    finally:
        conn.close()
    print(f"deleted push rows: {deleted_push}, task rows: {deleted_tasks}")
    tl = timeline_marker_rows()
    check("cleanup_timeline_no_marker_rows", tl == 0, f"timeline_marker_rows={tl}")
    restart()
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
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
