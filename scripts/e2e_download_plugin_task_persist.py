#!/usr/bin/env python3
"""RV-①b-⑥-3 live e2e 探针——download_plugins 插件任务行重启存活双向硬证。

模式：
  --pre      修前 GAP 坐实（旧码进程：wget 插件任务→pause→/tasks plugin_tasks
             可见 paused→data/download_plugin_tasks.db 零落盘口→restart→
             plugin_tasks 空=任务与续传上下文全丢）
  --post     修后存活（新码进程：pause→DB 行回读→restart→plugin_tasks 存活且
             status=paused（进程手柄如实不谎报 downloading）→resume 真续传→
             done 后注册表/DB 行双双归零）
  --cleanup  清痕（标记 dest/标记行/在途任务，remaining=0 回读）

纪律：慢源=探针自带 Range 支持 HTTP 服务（127.0.0.1 本机探针口），任务 dest 带
RV1B63-PROBE 标记目录，--cleanup 按标记删净。引擎=显式 plugin_id=wget
（真二进制真 resume：wget -c -O 续传本机已实证）。
"""

from __future__ import annotations

import argparse
import base64
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
MARKER = "RV1B63-PROBE"
DEST_DIR = Path("/tmp/rv1b63_probe")
DEST = DEST_DIR / f"{MARKER}.bin"
DB = Path("/home/climbing/opensoul/data/download_plugin_tasks.db")
SERVICE = "opensoul.service"
TOTAL = 1024 * 1024  # 1MB 慢源 ~16s：pause 时点在途、resume 后续完

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
    """慢源：Content-Length=TOTAL，按 Range 起点吐剩余字节，每 16KB 停 0.25s。"""

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


def start_plugin_download(url: str) -> str | None:
    s, r = http("POST", "/api/download/download",
                {"url": url, "dest": str(DEST), "plugin_id": "wget", "resume": True})
    if s != 200 or not isinstance(r, dict) or "task_id" not in r:
        check("start_plugin_download", False, f"http {s} {r}")
        return None
    check("start_plugin_download", True, f"task_id={r['task_id']} plugin={r.get('plugin')}")
    return r["task_id"]


def plugin_row(token: str) -> dict | None:
    s, r = http("GET", "/api/download/tasks")
    if s != 200 or not isinstance(r, dict):
        return None
    for t in r.get("plugin_tasks", []):
        if t.get("task_id") == token or t.get("registry_key") == str(DEST):
            return t
    return None


def db_rows() -> list:
    if not DB.exists():
        return []
    conn = sqlite3.connect(str(DB))
    try:
        return conn.execute(
            "SELECT plugin, task_id, state FROM download_plugin_tasks WHERE payload LIKE ?",
            (f"%{MARKER}%",),
        ).fetchall()
    finally:
        conn.close()


def _wait_partial(min_bytes: int = 1, budget: float = 15.0) -> int:
    t0 = time.time()
    partial = 0
    while time.time() - t0 < budget:
        if DEST.exists():
            partial = DEST.stat().st_size
            if partial >= min_bytes:
                return partial
        time.sleep(0.3)
    return partial


def run_pre() -> None:
    srv, url = serve()
    try:
        token = start_plugin_download(url)
        if token is None:
            return
        partial = _wait_partial()
        check("pre_partial_on_disk", partial > 0, f"partial={partial}")
        s, r = http("POST", f"/api/download/tasks/{token}/pause")
        check("pre_pause_accepted", s == 200 and isinstance(r, dict) and r.get("status") == "paused",
              f"http {s} {r}")
        row = plugin_row(token)
        check("pre_plugin_task_visible_paused", row is not None and row.get("status") == "paused",
              f"row={row}")
        rows = db_rows()
        check("pre_db_zero_persist", rows == [], f"rows={rows} db_exists={DB.exists()}")
        restart()
        row2 = plugin_row(token)
        check("pre_task_lost_after_restart", row2 is None, f"row2={row2}")
        rows2 = db_rows()
        check("pre_db_still_zero", rows2 == [], f"rows={rows2}")
        check("pre_orphan_partial", DEST.exists() and DEST.stat().st_size == partial,
              f"dest={DEST.exists()} size={DEST.stat().st_size if DEST.exists() else 0}/{partial}")
    finally:
        srv.shutdown()


def run_post() -> None:
    srv, url = serve()
    try:
        token = start_plugin_download(url)
        if token is None:
            return
        partial = _wait_partial()
        check("post_partial_on_disk", partial > 0, f"partial={partial}")
        s, r = http("POST", f"/api/download/tasks/{token}/pause")
        check("post_pause_accepted", s == 200 and isinstance(r, dict) and r.get("status") == "paused",
              f"http {s} {r}")
        rows = db_rows()
        check("post_db_row_written", len(rows) == 1, f"rows={rows}")
        if rows:
            check("post_db_row_state_paused", rows[0][2] == "paused", f"state={rows[0][2]}")
            check("post_db_row_plugin_key", rows[0][0] == "WgetPlugin", f"plugin={rows[0][0]}")

        restart()
        row = plugin_row(token)
        check("post_task_survives_restart", row is not None, f"row={row}")
        if row is not None:
            check("post_reloaded_state_paused", row.get("status") == "paused",
                  f"status={row.get('status')}")
            check("post_reloaded_url_dest", row.get("url") == url and row.get("dest") == str(DEST),
                  f"url={row.get('url')} dest={row.get('dest')}")
        rows2 = db_rows()
        check("post_db_row_survives", len(rows2) == 1 and rows2[0][2] == "paused", f"rows={rows2}")

        s, r = http("POST", f"/api/download/tasks/{token}/resume")
        check("post_resume_accepted", s == 200, f"http {s} {r}")
        # resume 真续传：下载重跑（wget -c）→ 终态后注册表行与 DB 行双双归零
        t0 = time.time()
        gone = False
        while time.time() - t0 < 60:
            row = plugin_row(token)
            if row is None:
                gone = True
                break
            time.sleep(1.0)
        check("post_resume_completes_task_clears", gone, f"row={plugin_row(token)}")
        size = DEST.stat().st_size if DEST.exists() else 0
        check("post_resumed_file_complete", size == TOTAL, f"size={size}/{TOTAL}")
        rows3 = db_rows()
        check("post_db_row_cleared_after_done", rows3 == [], f"rows={rows3}")
    finally:
        srv.shutdown()


def run_cleanup() -> None:
    s, r = http("GET", "/api/download/tasks")
    if s == 200 and isinstance(r, dict):
        for t in r.get("plugin_tasks", []):
            if MARKER in (t.get("registry_key") or ""):
                http("DELETE", f"/api/download/tasks/{t['task_id']}")
    if DB.exists():
        conn = sqlite3.connect(str(DB))
        try:
            conn.execute("DELETE FROM download_plugin_tasks WHERE payload LIKE ?", (f"%{MARKER}%",))
            conn.commit()
        finally:
            conn.close()
    shutil.rmtree(DEST_DIR, ignore_errors=True)
    rows = db_rows()
    check("cleanup_remaining_zero", rows == [] and not DEST_DIR.exists(),
          f"rows={rows} dir={DEST_DIR.exists()}")


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
