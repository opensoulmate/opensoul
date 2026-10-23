#!/usr/bin/env python3
"""GR-①b-② live e2e 探针——push_delivery_log 内存窗收口用户真实路径零回归 + 零可观测变化双向硬证。

模式：
  --pre      修前基线（当前运行进程=GR-①b-② 之前代码）：真 webhook 投递（stub 真收
             POST）→留痕落库（成败两型）→快照（行数/逐键/count 对账）落 JSON。
  --post     修后对照（restart 载新码）：同探针同断言重跑→输出与 --pre 快照逐键一致
             （零可观测变化硬证）+再投递增量追加不覆盖+count()==行数==全表（窗裁剪
             不丢审计史）。
  --cleanup  清痕（a2a_push_delivery_log/a2a_push_configs/a2a_tasks 标记行回读
             remaining=0）。

标记：GR1B2-PROBE（url 路径/token/metadata.probe 多标，清痕按 payload LIKE）。
诚实边界：push_delivery_log 无 HTTP API 读面（⑤-3 既有边界），内存窗/回载有界/
读面回落的进程内行为主证=pytest tests/test_a2a_delivery_log_window.py 真实路径
（真 socket+真 SQLite）+控制组（worktree 旧码 10F/3P）；本探针 live 面=用户真实
路径（a2a API→webhook 投递）零回归+落库/计数对账+修前/修后同探针输出逐键一致。
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
from pathlib import Path

BASE = "http://127.0.0.1:8090"
MARKER = "GR1B2-PROBE"
LOG_DB = "/home/climbing/opensoul/data/a2a_push_delivery_log.db"
PUSH_DB = "/home/climbing/opensoul/data/a2a_push_configs.db"
A2A_DB = "/home/climbing/opensoul/data/a2a_tasks.db"
SERVICE = "opensoul.service"
SNAPSHOT = Path("/tmp/gr1b2_probe_pre_snapshot.json")

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
    if resp.get("error"):
        return None
    result = resp.get("result") or {}
    return result.get("task") or (result if result.get("id") else None)


def log_rows(marker: str = MARKER) -> list:
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


def log_count_all() -> int:
    """count 真源（全表，含历史非标记行）——窗裁剪不丢审计史对账面。"""
    try:
        conn = sqlite3.connect(LOG_DB)
    except sqlite3.Error:
        return -1
    try:
        row = conn.execute("SELECT COUNT(*) FROM a2a_push_delivery_log").fetchone()
        return int(row[0])
    except sqlite3.OperationalError:
        return -1
    finally:
        conn.close()


def start_stub():
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
    """建 input-required 任务→登记配置→续接触发 working+终态投递（用户真实路径）。"""
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
    check(f"drive_completed_{url[-10:]}", ok,
          f"state={t2 and t2.get('status', {}).get('state')}")
    return tid, str(cfg.get("id"))


def run_round(tag: str) -> dict:
    """一轮完整探针：成功+失败投递→行/计数对账。返回快照 dict（修前/修后对照面）。"""
    server, url, received = start_stub()
    try:
        tid, cid = drive_delivery(url)
        check(f"{tag}_task_and_config", bool(tid) and bool(cid), f"tid={tid} cid={cid}")
        time.sleep(0.5)
        check(f"{tag}_delivery_really_happened", len(received) >= 2,
              f"stub 收 {len(received)} POST（working+终态）")
        keys = [next(iter(e["body"]), None) for e in received]
        check(f"{tag}_payload_shape", "statusUpdate" in keys and "task" in keys, f"keys={keys}")
    finally:
        server.shutdown()
        server.server_close()
    # 失败投递同样留痕（失败留痕禁静默）
    tid2, cid2 = drive_delivery(closed_port_url())
    check(f"{tag}_fail_task_and_config", bool(tid2) and bool(cid2), f"tid={tid2}")
    time.sleep(0.5)
    rows = log_rows()
    ok_rows = [r for r in rows if r[3] == 1]
    bad_rows = [r for r in rows if r[3] == 0]
    check(f"{tag}_rows_ok_fail_both", len(ok_rows) >= 2 and len(bad_rows) >= 2,
          f"ok={len(ok_rows)} fail={len(bad_rows)}")
    bad_err = all(json.loads(r[4]).get("error") for r in bad_rows)
    check(f"{tag}_fail_rows_have_cause", bad_err)
    sample = json.loads(rows[0][4]) if rows else {}
    check(f"{tag}_row_entry_keys",
          set(sample) >= {"delivery_id", "task_id", "config_id", "url", "payload_key", "ok"},
          f"keys={sorted(sample)}")
    # 落库/计数对账：count 真源==全表行数（窗裁剪不丢审计史）
    total = log_count_all()
    conn = sqlite3.connect(LOG_DB)
    try:
        real = conn.execute("SELECT COUNT(*) FROM a2a_push_delivery_log").fetchone()[0]
    finally:
        conn.close()
    check(f"{tag}_count_truth_matches_rows", total == real, f"count={total} rows={real}")
    return {
        "rows": [(r[0], r[1], r[2], r[3], r[4]) for r in rows],
        "ok": len(ok_rows),
        "fail": len(bad_rows),
        "count_all": total,
    }


def run_pre() -> None:
    print("== --pre：修前基线（当前进程=GR-①b-② 之前代码）==")
    snap = run_round("pre")
    SNAPSHOT.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    print(f"快照落盘 {SNAPSHOT} rows={len(snap['rows'])}")


def run_post() -> None:
    print("== --post：修后对照（restart 载新码）==")
    if not SNAPSHOT.exists():
        check("pre_snapshot_present", False, "缺 --pre 快照（先跑 --pre）")
        return
    pre = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    restart()  # 载新码（懒回载=有界 load_recent 路径由此真实走到）
    snap = run_round("post")
    # 零可观测变化硬证：修前/修后同探针输出逐键一致（行内容/成败型/计数口径）
    pre_shape = [(r[0], r[1], r[2], r[3]) for r in pre["rows"]]
    post_shape = [(r[0], r[1], r[2], r[3]) for r in snap["rows"][: len(pre_shape)]]
    check("zero_observable_change_rows", post_shape == pre_shape,
          f"pre={len(pre_shape)} post={len(snap['rows'])}")
    check("zero_observable_change_counts",
          (snap["ok"] >= pre["ok"] and snap["fail"] >= pre["fail"]
           and snap["count_all"] >= pre["count_all"]),
          f"pre(ok={pre['ok']},fail={pre['fail']},count={pre['count_all']}) "
          f"post(ok={snap['ok']},fail={snap['fail']},count={snap['count_all']})")
    # 重启后增量追加不覆盖（旧 id 全保留）
    old_ids = {r[0] for r in pre["rows"]}
    new_ids = {r[0] for r in snap["rows"]}
    check("increment_append_not_overwrite", old_ids <= new_ids and len(new_ids) >= len(old_ids),
          f"{len(old_ids)}→{len(new_ids)}")


def run_cleanup() -> None:
    print("== --cleanup：清痕 remaining=0 ==")
    remaining = 0
    for db, table in ((LOG_DB, "a2a_push_delivery_log"), (PUSH_DB, "a2a_push_configs"),
                      (A2A_DB, "a2a_tasks")):
        conn = sqlite3.connect(db)
        try:
            cur = conn.execute(f"DELETE FROM {table} WHERE payload LIKE ?", (f"%{MARKER}%",))
            deleted = cur.rowcount
            conn.commit()
        except sqlite3.OperationalError as e:
            print(f"  (skip {table}: {e})")
            deleted = 0
        finally:
            conn.close()
        row_n = log_count_all() if table == "a2a_push_delivery_log" else 0
        print(f"  {table}: deleted={deleted}")
        if table == "a2a_push_delivery_log":
            remaining += len(log_rows())
    check("cleanup_remaining_zero", remaining == 0, f"remaining={remaining}")
    if SNAPSHOT.exists():
        SNAPSHOT.unlink()


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
    print(f"\n== RESULT: {len(PASS)} PASS / {len(FAIL)} FAIL ==")
    for name in FAIL:
        print(f"  FAIL {name}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
