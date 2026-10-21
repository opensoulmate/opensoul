#!/usr/bin/env python3
"""MEM-⑤ hippo 面持久化 live e2e 探针（--pre 修前 GAP 实证 / --post 修后全绿）。

打真 :8090 栈：POST /api/hippo/memories（session_id=探针桶，自动建父会话行）
→ GET 回读 → systemctl --user 重启（真·重启语义）→ GET 复读 → sqlite3 直读
data/hippo_memory.db（数据工件回读）→ DELETE 清痕（删=库行真消失）→ 再重启
复核 404 → timeline delta=0（TR-② 零事件行面纪律）。

修前预期 GAP：重启后探针行全清（api/hippo.py 双单例纯内存，MEM-④ 诚实边界③）
+ data/hippo_memory.db 不存在（无落盘口）；修后预期全绿（重启记忆/会话不丢）。
"""

import argparse
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8090"
MARKER = f"mem5-e2e-{uuid.uuid4().hex[:8]}"
SESS = f"mem5-e2e-sess-{uuid.uuid4().hex[:6]}"
HIPPO_DB = Path.home() / "opensoul" / "data" / "hippo_memory.db"
TIMELINE_DB = Path.home() / "opensoul" / "data" / "timeline.db"

results: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = ""):
    results.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


def timeline_count() -> int:
    if not TIMELINE_DB.exists():
        return 0
    with sqlite3.connect(f"file:{TIMELINE_DB}?mode=ro", uri=True) as c:
        return c.execute("SELECT COUNT(*) FROM events").fetchone()[0]


def hippo_marker_rows() -> int:
    if not HIPPO_DB.exists():
        return -1  # 库不存在（修前形态）
    try:
        with sqlite3.connect(f"file:{HIPPO_DB}?mode=ro", uri=True) as c:
            return c.execute(
                "SELECT COUNT(*) FROM hippo_memories WHERE session_id=?", (SESS,)
            ).fetchone()[0]
    except sqlite3.OperationalError:
        return -1  # 表不存在（修前形态）


def hippo_session_rows() -> int:
    if not HIPPO_DB.exists():
        return -1
    try:
        with sqlite3.connect(f"file:{HIPPO_DB}?mode=ro", uri=True) as c:
            return c.execute(
                "SELECT COUNT(*) FROM hippo_sessions WHERE session_id=?", (SESS,)
            ).fetchone()[0]
    except sqlite3.OperationalError:
        return -1


def wait_health(timeout_s: float = 90.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if httpx.get(f"{BASE}/api/health", timeout=5).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def restart() -> bool:
    subprocess.run(["systemctl", "--user", "restart", "opensoul"], check=True)
    return wait_health()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["pre", "post"], default="post")
    args = ap.parse_args()
    pre = args.mode == "pre"
    c = httpx.Client(base_url=BASE, timeout=30)

    # 0. health + 基线（timeline 行数）
    r = c.get("/api/health")
    check(r.status_code == 200, "P0 health 200", str(r.status_code))
    tl_base = timeline_count()
    base_list = c.get("/api/hippo/memories", params={"user_id": "default"}).json()["total"]
    print(f"INFO baseline default-memories={base_list} timeline_rows={tl_base}")

    # 1. 写 2 条（session_id=探针桶→自动建父会话行归属 'default'）
    ids = []
    for i in (1, 2):
        r = c.post(
            "/api/hippo/memories",
            json={
                "session_id": SESS,
                "content": f"{MARKER}-第{i}条持久化探针记忆",
                "tags": ["mem5-e2e"],
            },
            params={"user_id": "default"},
        )
        ok = r.status_code == 200 and "memory_id" in r.json()
        check(ok, f"P1-{i} /api/hippo/memories 写入", str(r.status_code))
        if ok:
            ids.append(r.json()["memory_id"])

    # 2. 重启前回读 sanity
    r = c.get("/api/hippo/memories", params={"session_id": SESS, "user_id": "default"})
    n_before = r.json()["total"] if r.status_code == 200 else -1
    check(n_before == 2, "P2 重启前回读 2 条", f"got {n_before}")

    # 3. 落盘工件回读（修前：无库无表）
    rows_pre = hippo_marker_rows()
    sess_pre = hippo_session_rows()
    if pre:
        check(rows_pre in (-1, 0), "P3-GAP 修前无落盘口（hippo_memory.db 无表/零行）", f"rows={rows_pre}")
        check(sess_pre in (-1, 0), "P3b-GAP 修前父会话行无落盘", f"rows={sess_pre}")
    else:
        check(rows_pre == 2, "P3 写穿落盘 2 行（sqlite3 直读）", f"rows={rows_pre}")
        check(sess_pre == 1, "P3b 父会话行写穿落盘 1 行", f"rows={sess_pre}")

    # 4. 真·重启
    if not restart():
        check(False, "P4 重启后 health 恢复", "timeout")
        return 1
    check(True, "P4 重启后 health 恢复")

    # 5. 重启后复读（本探针核心）
    rows_after = hippo_marker_rows()
    r = c.get("/api/hippo/memories", params={"session_id": SESS, "user_id": "default"})
    n_after = r.json()["total"] if r.status_code == 200 else -1
    if pre:
        check(n_after in (0, -1), "P5-GAP 修前重启即清（复读 0/404）", f"got {n_after}")
        for i, mid in enumerate(ids):
            rr = c.get(f"/api/hippo/memories/{mid}", params={"user_id": "default"})
            check(rr.status_code == 404, f"P6-GAP-{i} 修前 by-ID 404（行已消失）", str(rr.status_code))
    else:
        check(n_after == 2, "P5 重启记忆不丢（复读 2 条）", f"got {n_after}")
        check(rows_after == 2, "P6 库行随重启存活（sqlite3 直读）", f"rows={rows_after}")
        for i, mid in enumerate(ids):
            rr = c.get(f"/api/hippo/memories/{mid}", params={"user_id": "default"})
            ok = rr.status_code == 200 and f"{MARKER}-第{i + 1}条" in rr.json().get("content", "")
            check(ok, f"P7-{i} by-ID 取回真内容", str(rr.status_code))
        # 父会话行同活（归属闸真命中）
        rr = c.get("/api/hippo/sessions", params={"user_id": "default"})
        sess_seen = any(s["session_id"] == SESS for s in rr.json()["sessions"]) if rr.status_code == 200 else False
        check(sess_seen, "P8 父会话行随重启存活（列表真见）", str(rr.status_code))

        # 6. 清痕（删=库行真消失）
        for mid in ids:
            d = c.delete(f"/api/hippo/memories/{mid}", params={"user_id": "default"})
            check(d.status_code == 200, f"P9 删除 {mid[:8]}", str(d.status_code))
        rows_del = hippo_marker_rows()
        check(rows_del == 0, "P10 删后库行归零（数据工件回读）", f"rows={rows_del}")

        # 父会话行清痕（探针桶）
        d = c.delete(f"/api/hippo/sessions/{SESS}", params={"user_id": "default"})
        check(d.status_code == 200, "P11 删除父会话行", str(d.status_code))

        # 7. 再重启复核 404（真消失非仅内存删）
        if not restart():
            check(False, "P12 二次重启 health 恢复", "timeout")
            return 1
        for i, mid in enumerate(ids):
            rr = c.get(f"/api/hippo/memories/{mid}", params={"user_id": "default"})
            check(rr.status_code == 404, f"P13-{i} 二次重启后 404（真消失）", str(rr.status_code))
        check(hippo_marker_rows() == 0, "P14 二次重启后库行恒 0", f"rows={hippo_marker_rows()}")

    # 8. timeline delta=0
    tl_end = timeline_count()
    check(tl_end == tl_base, "P15 timeline delta=0", f"{tl_base} -> {tl_end}")

    ok_n = sum(1 for ok, _ in results if ok)
    print(f"\n== {args.mode}: {ok_n}/{len(results)} ==")
    return 0 if ok_n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
