#!/usr/bin/env python3
"""MEM-④ brain 记忆面持久化 live e2e 探针（--pre 修前 GAP 实证 / --post 修后全绿）。

打真 :8090 栈：/remember（标量 query 参数面——MEM-② 探针坑：JSON body 被 FastAPI
静默忽略，必须 params）→ /recall 回读 → systemctl --user 重启（真·重启语义）→
/recall 复读 → sqlite3 直读 data/brain_memory.db（数据工件回读）→ DELETE 清痕
（MEM-③ 删除口）→ /api/brain/status 计数回基线 + timeline delta=0（TR-② 零事件
行面纪律）。

修前预期 GAP：重启后 /recall 回 0 条（进程内 MemoryStore 重启即清，MEM-② 边界①）
+ data/brain_memory.db 不存在（无落盘口）；修后预期全绿（重启记忆不丢+删=库行真消失）。
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
MARKER = f"mem4-e2e-{uuid.uuid4().hex[:8]}"
SESS = f"mem4-e2e-sess-{uuid.uuid4().hex[:6]}"
BRAIN_DB = Path.home() / "opensoul" / "data" / "brain_memory.db"
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


def brain_db_marker_rows() -> int:
    if not BRAIN_DB.exists():
        return -1  # 库不存在（修前形态）
    try:
        with sqlite3.connect(f"file:{BRAIN_DB}?mode=ro", uri=True) as c:
            return c.execute(
                "SELECT COUNT(*) FROM brain_memories WHERE session_id=?", (SESS,)
            ).fetchone()[0]
    except sqlite3.OperationalError:
        return -1  # 表不存在（修前形态）


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["pre", "post"], default="post")
    args = ap.parse_args()
    pre = args.mode == "pre"
    c = httpx.Client(base_url=BASE, timeout=30)

    # 0. health + 基线（status 计数 / timeline 行数）
    r = c.get("/api/brain/health")
    check(r.status_code == 200, "P0 health 200", str(r.status_code))
    base_total = c.get("/api/brain/status").json()["memory"]["total_memories"]
    tl_base = timeline_count()
    print(f"INFO baseline total_memories={base_total} timeline_rows={tl_base}")

    # 1. 写 2 条（query 参数面！）
    ids = []
    for i in (1, 2):
        r = c.post(
            "/api/brain/remember",
            params={
                "session_id": SESS,
                "content": f"{MARKER}-第{i}条持久化探针记忆",
                "user_id": "default",
            },
        )
        ok = r.status_code == 200 and r.json().get("stored") is True
        check(ok, f"P1-{i} /remember 写入", str(r.status_code))
        if ok:
            ids.append(r.json()["memory_id"])

    # 2. 重启前 /recall sanity
    r = c.get("/api/brain/recall", params={"session_id": SESS, "query": MARKER})
    n_before = len(r.json().get("memories", []))
    check(n_before == 2, "P2 重启前 /recall 见 2 条", f"got {n_before}")

    # 3. 落盘工件回读（修前：无库无表）
    rows_pre_restart = brain_db_marker_rows()
    if pre:
        check(
            rows_pre_restart in (-1, 0),
            "P3-GAP 修前无落盘口（brain_memory.db 不存在/零行）",
            f"rows={rows_pre_restart}",
        )
    else:
        check(rows_pre_restart == 2, "P3 写穿落盘 2 行（sqlite3 直读）", f"rows={rows_pre_restart}")

    # 4. 真·重启（systemctl --user）
    subprocess.run(["systemctl", "--user", "restart", "opensoul"], check=True)
    if not wait_health():
        check(False, "P4 重启后 health 恢复", "timeout")
        print("\n".join(f"{'PASS' if ok else 'FAIL'} {name}" for ok, name in results))
        return 1
    check(True, "P4 重启后 health 恢复")

    # 5. 重启后 /recall 复读（本探针核心）
    r = c.get("/api/brain/recall", params={"session_id": SESS, "query": MARKER})
    n_after = len(r.json().get("memories", []))
    after_total = c.get("/api/brain/status").json()["memory"]["total_memories"]
    rows_after_restart = brain_db_marker_rows()
    if pre:
        check(
            n_after == 0,
            "P5-GAP 修前重启即清（/recall 复读 0 条）",
            f"got {n_after} (修前预期 GAP)",
        )
        check(
            after_total == 0,
            "P6-GAP 修前 status 计数归零",
            f"got {after_total}",
        )
    else:
        check(n_after == 2, "P5 重启记忆不丢（/recall 复读 2 条）", f"got {n_after}")
        # 基线行同走写穿落盘——重启后总数=基线+探针 2 条（首轮探针作者错误断言
        # 恒 2，实测 7=基线 5+2 恰是持久化对基线行也生效的硬证，按基线相对修正）
        check(
            after_total == base_total + 2,
            "P6 重启后 status 计数=基线+2",
            f"base {base_total} -> got {after_total}",
        )
        check(rows_after_restart == 2, "P7 库行随重启存活（sqlite3 直读）", f"rows={rows_after_restart}")

        # 8. 清痕（MEM-③ 删除口与持久化同轴：删=库行真消失）
        for mid in ids:
            d = c.delete(f"/api/brain/memories/{mid}")
            check(d.status_code == 200, f"P8 删除 {mid[:8]}", str(d.status_code))
        rows_after_delete = brain_db_marker_rows()
        check(rows_after_delete == 0, "P9 删后库行归零（数据工件回读）", f"rows={rows_after_delete}")
        r = c.get("/api/brain/recall", params={"session_id": SESS, "query": MARKER})
        check(
            len(r.json().get("memories", [])) == 0,
            "P10 清痕后 /recall 归零",
        )
        final_total = c.get("/api/brain/status").json()["memory"]["total_memories"]
        check(
            final_total == base_total,
            "P11 status 计数回基线",
            f"{base_total} -> {final_total}",
        )

    # 9. timeline delta=0（TR-② 零事件行面：/remember·/recall·DELETE 不落事件）
    tl_end = timeline_count()
    check(tl_end == tl_base, "P12 timeline delta=0", f"{tl_base} -> {tl_end}")

    ok_n = sum(1 for ok, _ in results if ok)
    print(f"\n== {args.mode}: {ok_n}/{len(results)} ==")
    return 0 if ok_n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
