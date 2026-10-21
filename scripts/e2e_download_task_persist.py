#!/usr/bin/env python3
"""RV-①b-⑥-2 live e2e 探针——native 下载任务行重启存活双向硬证。

模式：
  --pre      修前 GAP 坐实（旧码进程：add→/tasks 可见→data/download_tasks.db
             零落盘口→restart→/tasks 空=任务丢、半成品 dest 无人认领）
  --post     修后存活（新码进程：add→DB 行回读→restart→/tasks 存活且在途态如实
             paused→resume 真续传→done 半成品续完→DELETE 删行随 remove 回读 0）
  --cleanup  清痕（标记 dest/标记行/内存任务，remaining=0 回读）

纪律：慢源=探针自带 Range 支持 HTTP 服务（127.0.0.1 本机探针口），任务 dest 带
RV1B62-PROBE 标记目录，--cleanup 按标记删净。
"""

from __future__ import annotations

import argparse
import json
import shutil
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path

BASE = "http://127.0.0.1:8090"
MARKER = "RV1B62-PROBE"
DEST_DIR = Path("/tmp/rv1b62_probe")
DEST = DEST_DIR / f"{MARKER}.bin"
DB = Path("/home/climbing/opensoul/data/download_tasks.db")
SERVICE = "opensoul.service"
TOTAL = 1024 * 1024  # 1MB < SEGMENT_MIN_SIZE*2=2MB → 单段（半成品直写 dest 可见）

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


class _SlowHandler(BaseHTTPRequestHandler):
    """慢源：Content-Length=TOTAL，按 Range 起点吐剩余字节，每 16KB 停 0.25s
    （1MB 全程 ~16s：重启时点在途、resume 后 ~16s 内续完）。"""

    def log_message(self, *args):  # 静默
        pass

    def _start(self) -> int:
        rng = self.headers.get("Range", "")
        start = 0
        if rng.startswith("bytes="):
            try:
                start = int(rng.split("=")[1].split("-")[0])
            except ValueError:
                start = 0
        start = max(0, min(start, TOTAL))
        remain = TOTAL - start
        if start > 0:
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{TOTAL - 1}/{TOTAL}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(remain))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        return start

    def do_HEAD(self):
        self._start()

    def do_GET(self):
        start = self._start()
        pos = start
        try:
            while pos < TOTAL:
                n = min(16 * 1024, TOTAL - pos)
                self.wfile.write(b"x" * n)
                pos += n
                time.sleep(0.25)
        except (BrokenPipeError, ConnectionResetError):
            pass


def serve() -> tuple[socketserver.TCPServer, str]:
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SlowHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/{MARKER}.bin"


def health_wait(max_s: float = 60.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < max_s:
        try:
            s, r = http("GET", "/api/health", timeout=5)
            if s == 200 and r:
                return True
        except Exception:
            pass
        time.sleep(1.5)
    return False


def restart() -> None:
    subprocess.run(["systemctl", "--user", "restart", SERVICE], check=True)
    ok = health_wait()
    check("restart_health", ok)


def add_download(url: str) -> str | None:
    s, r = http("POST", "/api/download/download", {"url": url, "dest": str(DEST), "plugin_id": "native"})
    if s != 200 or not isinstance(r, dict) or "task_id" not in r:
        check("add_download", False, f"http {s} {r}")
        return None
    check("add_download", True, f"task_id={r['task_id']}")
    return r["task_id"]


def task_row(task_id: str) -> dict | None:
    s, r = http("GET", "/api/download/tasks")
    if s != 200 or not isinstance(r, dict):
        return None
    for t in r.get("tasks", []):
        if t.get("id") == task_id:
            return t
    return None


def db_rows() -> list:
    if not DB.exists():
        return []
    conn = sqlite3.connect(str(DB))
    try:
        return conn.execute(
            "SELECT task_id, status FROM download_tasks WHERE task_id IN "
            "(SELECT task_id FROM download_tasks WHERE payload LIKE ?)",
            (f"%{MARKER}%",),
        ).fetchall()
    finally:
        conn.close()


def run_pre() -> None:
    srv, url = serve()
    try:
        tid = add_download(url)
        if tid is None:
            return
        time.sleep(1.5)
        row = task_row(tid)
        check("pre_tasks_visible", row is not None, f"row={row and row.get('status')}")
        rows = db_rows()
        check("pre_db_zero_persist", rows == [], f"rows={rows} db_exists={DB.exists()}")
        restart()
        row2 = task_row(tid)
        check("pre_task_lost_after_restart", row2 is None, f"row2={row2}")
        check("pre_orphan_partial", DEST.exists(), f"dest_exists={DEST.exists()}")
    finally:
        srv.shutdown()


def run_post() -> None:
    srv, url = serve()
    try:
        tid = add_download(url)
        if tid is None:
            return
        # 等进入传输态
        t0 = time.time()
        status = None
        while time.time() - t0 < 15:
            row = task_row(tid)
            status = row and row.get("status")
            if status in ("downloading", "connecting"):
                break
            time.sleep(0.3)
        check("post_inflight_status", status in ("downloading", "connecting"), f"status={status}")
        # 等半成品真写盘（探针时序纪律：--pre 曾因 1.5s 内未写盘致 orphan 检查自伤）
        t0 = time.time()
        partial = 0
        while time.time() - t0 < 15:
            if DEST.exists():
                partial = DEST.stat().st_size
                if partial > 0:
                    break
            time.sleep(0.3)
        check("post_partial_on_disk", partial > 0, f"partial={partial}")

        rows = db_rows()
        check("post_db_row_written", len(rows) == 1, f"rows={rows}")
        if rows:
            check("post_db_row_status", rows[0][1] in ("pending", "connecting", "downloading"), f"status={rows[0][1]}")

        restart()
        row = task_row(tid)
        check("post_task_survives_restart", row is not None, f"row={row and row.get('status')}")
        if row is not None:
            check("post_reloaded_inflight_paused", row.get("status") == "paused", f"status={row.get('status')}")
        rows2 = db_rows()
        check("post_db_row_survives", len(rows2) == 1, f"rows={rows2}")

        s, r = http("POST", f"/api/download/tasks/{tid}/resume")
        check("post_resume_accepted", s == 200, f"http {s} {r}")
        t0 = time.time()
        final = None
        while time.time() - t0 < 60:
            row = task_row(tid)
            final = row and row.get("status")
            if final in ("done", "error", "cancelled"):
                break
            time.sleep(1.0)
        check("post_resume_completes", final == "done", f"final={final}")
        if DEST.exists():
            check("post_partial_resumed_complete", DEST.stat().st_size == TOTAL, f"size={DEST.stat().st_size}/{TOTAL}")
        else:
            check("post_partial_resumed_complete", False, "dest missing")

        s, r = http("DELETE", f"/api/download/tasks/{tid}")
        check("post_remove", s == 200, f"http {s} {r}")
        rows3 = db_rows()
        check("post_db_row_deleted", rows3 == [], f"rows={rows3}")
    finally:
        srv.shutdown()


def run_cleanup() -> None:
    # 内存任务清（逐个删）
    s, r = http("GET", "/api/download/tasks")
    if s == 200 and isinstance(r, dict):
        for t in r.get("tasks", []):
            if MARKER in (t.get("dest") or ""):
                http("DELETE", f"/api/download/tasks/{t['id']}")
    # 库行清
    if DB.exists():
        conn = sqlite3.connect(str(DB))
        try:
            conn.execute("DELETE FROM download_tasks WHERE payload LIKE ?", (f"%{MARKER}%",))
            conn.commit()
        finally:
            conn.close()
    shutil.rmtree(DEST_DIR, ignore_errors=True)
    rows = db_rows()
    check("cleanup_remaining_zero", rows == [] and not DEST_DIR.exists(), f"rows={rows} dir={DEST_DIR.exists()}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre", action="store_true")
    ap.add_argument("--post", action="store_true")
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    if args.pre:
        run_pre()
    elif args.post:
        run_post()
    elif args.cleanup:
        run_cleanup()
    print(f"\n=== {len(PASS)} PASS / {len(FAIL)} FAIL ===")
    for name in FAIL:
        print(f"  FAIL: {name}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
