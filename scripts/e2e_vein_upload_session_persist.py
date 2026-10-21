#!/usr/bin/env python3
"""RV-①b-⑥-1 live e2e 探针——vein 上传会话行重启存活双向硬证。

模式：
  --pre      修前 GAP 坐实（旧码进程：init+chunk0→restart→status 404=会话丢、
             data/vein_upload_sessions.db 零落盘口、孤儿分片目录残留）
  --post     修后存活（新码进程：init+chunk0→DB 行回读→restart→status 200 逐键
             存活→续传 chunk1→complete 真组装 content_hash→删行随 complete）
  --cleanup  清痕（标记会话/标记文件/标记分片目录/库行，remaining=0 回读）

纪律：TR-② 事件行面——complete 会写 timeline file_uploaded 事件，探针标记
RV1B6-PROBE 进文件名，--cleanup 按标记删净并回读 remaining=0。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8090"
MARKER = "RV1B6-PROBE"
UPLOADS_DIR = Path("/home/climbing/opensoul/data/vein/uploads")
UPLOAD_DB = Path("/home/climbing/opensoul/data/vein_upload_sessions.db")
FILES_DIR = Path("/home/climbing/opensoul/data/vein/files")
SERVICE = "opensoul.service"
STATE = Path("/tmp/rv1b6_probe_state.json")

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


def multipart(path: str, filename: str, payload: bytes, timeout: float = 20.0):
    boundary = "----e2e" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + path, data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
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


def db_rows(upload_id: str | None = None) -> list:
    if not UPLOAD_DB.exists():
        return []
    conn = sqlite3.connect(str(UPLOAD_DB))
    try:
        if upload_id:
            return conn.execute(
                "SELECT upload_id, filename, received_chunks, completed_at, tenant_id"
                " FROM vein_upload_sessions WHERE upload_id = ?", (upload_id,)
            ).fetchall()
        return conn.execute(
            "SELECT upload_id, filename FROM vein_upload_sessions WHERE filename LIKE ?",
            (f"%{MARKER}%",),
        ).fetchall()
    finally:
        conn.close()


def marker_files() -> list:
    s, r = http("GET", "/api/vein/files")
    if s != 200 or not isinstance(r, dict):
        return []
    rows = r.get("files", r if isinstance(r, list) else [])
    return [f for f in rows if str(f.get("name", "")).startswith(MARKER)]


def marker_dirs() -> list:
    if not UPLOADS_DIR.exists():
        return []
    return [p for p in UPLOADS_DIR.iterdir() if p.is_dir()]


def save_state(uid: str) -> None:
    ids = load_states()
    if uid and uid not in ids:
        ids.append(uid)
    STATE.write_text(json.dumps({"upload_ids": ids}))


def load_states() -> list:
    if STATE.exists():
        data = json.loads(STATE.read_text())
        if "upload_ids" in data:
            return data.get("upload_ids", [])
        uid = data.get("upload_id", "")
        return [uid] if uid else []
    return []


def flow(pre: bool) -> None:
    fname = f"{MARKER}-{'pre' if pre else 'post'}-{uuid.uuid4().hex[:6]}.bin"
    s, r = http("POST", "/api/vein/upload/chunked/init",
                {"filename": fname, "total_size": 6, "chunk_size": 3})
    check("init_200", s == 200 and isinstance(r, dict) and r.get("upload_id"), f"{s} {r}")
    uid = (r or {}).get("upload_id", "")
    save_state(uid)

    s, r = multipart(f"/api/vein/upload/chunked/{uid}/chunk/0", "chunk", b"abc")
    check("chunk0_200", s == 200 and (r or {}).get("received_chunks") == [0], f"{s}")

    rows = db_rows(uid)
    if pre:
        check("db_no_row_GAP", rows == [], f"rows={rows} db_exists={UPLOAD_DB.exists()}")
    else:
        check("db_row_written", len(rows) == 1 and json.loads(rows[0][2]) == [0], f"{rows}")

    restart()

    s, r = http("GET", f"/api/vein/upload/chunked/{uid}")
    if pre:
        check("GAP_status_404_after_restart", s == 404, f"status={s} body={r}")
        s2, r2 = http("GET", "/api/vein/upload/chunked")
        gone = all(d.get("upload_id") != uid for d in (r2 or {}).get("sessions", []))
        check("GAP_list_gone", gone, f"list={r2}")
        d = UPLOADS_DIR / uid
        check("GAP_orphan_chunk_dir", d.exists(), f"dir={d}")
    else:
        check("status_survives_restart", s == 200 and (r or {}).get("received_chunks") == [0]
              and (r or {}).get("filename") == fname, f"{s} {r}")
        # 续传：补第二片→complete 真组装
        s, r = multipart(f"/api/vein/upload/chunked/{uid}/chunk/1", "chunk", b"def")
        check("chunk1_resume_200", s == 200 and (r or {}).get("is_complete") is True, f"{s} {r}")
        s, r = http("POST", f"/api/vein/upload/chunked/{uid}/complete")
        want_hash = hashlib.sha256(b"abcdef").hexdigest()
        check("complete_200_hash", s == 200 and (r or {}).get("content_hash") == want_hash,
              f"{s} {r}")
        check("row_dropped_after_complete", db_rows(uid) == [], f"{db_rows(uid)}")
        check("session_gone_after_complete",
              http("GET", f"/api/vein/upload/chunked/{uid}")[0] == 404)
        files = marker_files()
        check("file_stored", any(f.get("name") == fname for f in files), f"{files}")


def cleanup() -> None:
    uids = load_states()
    # 标记文件全清（API 删）
    for f in marker_files():
        s, _ = http("DELETE", f"/api/vein/files/{f.get('file_id')}")
        check(f"del_file_{f.get('file_id')}", s in (200, 404), f"status={s}")
    # 标记会话（API 删，旧码缺行=404 也如实记）
    for uid in uids:
        s, _ = http("DELETE", f"/api/vein/upload/chunked/{uid}")
        print(f"info delete_session {uid} status={s}")
    # 标记分片目录（旧码孤儿面：目录名=upload_id，本探针自建）
    for uid in uids:
        if uid and (UPLOADS_DIR / uid).exists():
            shutil.rmtree(UPLOADS_DIR / uid)
            print(f"info removed chunk dir {uid}")
    # 库行残留：探针自有行直接删净（崩溃残留兜底）
    if uids and UPLOAD_DB.exists():
        conn = sqlite3.connect(str(UPLOAD_DB))
        try:
            for uid in uids:
                conn.execute("DELETE FROM vein_upload_sessions WHERE upload_id = ?", (uid,))
            conn.commit()
        finally:
            conn.close()
    remain_rows = db_rows()
    for uid in uids:
        remain_rows = [r for r in remain_rows if r[0] == uid] + [r for r in db_rows(uid)]
        remain_rows = list({r[0] for r in remain_rows})
    remain_files = marker_files()
    remain_dirs = [d for d in marker_dirs() if d.name in set(uids)]
    check("remaining_rows_0", remain_rows == [], f"{remain_rows}")
    check("remaining_files_0", remain_files == [], f"{remain_files}")
    check("remaining_dirs_0", remain_dirs == [], f"{remain_dirs}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre", action="store_true")
    ap.add_argument("--post", action="store_true")
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()

    if args.pre:
        flow(pre=True)
    elif args.post:
        # 首行 restart 载新码（RV-①b-⑤-2 探针时序自纠同款：写入必须跑在载新码
        # 之后，否则首轮写穿断言打在旧进程上=探针自伤）
        restart()
        flow(pre=False)
    elif args.cleanup:
        cleanup()
    else:
        ap.error("need --pre/--post/--cleanup")

    print(f"\n== RESULT: {len(PASS)} PASS / {len(FAIL)} FAIL ==")
    for n in FAIL:
        print("FAIL", n)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
