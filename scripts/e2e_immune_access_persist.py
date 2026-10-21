#!/usr/bin/env python3
"""RV-①b-② immune 黑白名单持久化 live e2e 探针（--pre 修前 GAP 实证 / --post 修后全绿）。

打真 :8090 栈：POST /api/immune/ip/blacklist（marker）→ /ip/check 黑名单门命中计数
→ POST /ip/whitelist（marker）→ /ip/lists 回读 → systemctl --user 重启（真·重启语义
=本切片核心证据）→ /lists /ip/check 复读（修前=重启即解封 GAP）→ 撤白名单后再拦
（hit_count 写穿直证）→ sqlite3 直读 data/immune_access.db（数据工件回读）→
DELETE 清痕（写删写穿）→ timeline 事件行清痕（TR-② 零事件行面纪律）。

修前预期 GAP：重启后 marker 黑名单/白名单条目消失（进程内内存重启即清）、种子零
落盘（无落盘口）；修后预期全绿（重启封禁不丢+库行回读+hit_count 随行）。

注：白名单非空=「名单外全拦」（白名单门先于黑名单门），被白名单门拦下不计黑名
单 hit_count（既有语义）——hit 写穿直证故排在撤白名单之后。
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
MARKER = f"rv1b2-e2e-{uuid.uuid4().hex[:8]}"
BAN_IP = "203.0.113." + str(100 + uuid.uuid4().int % 100)  # TEST-NET-3 探针专用
ALLOW_IP = "203.0.113." + str(10 + uuid.uuid4().int % 80)
ACCESS_DB = Path.home() / "opensoul" / "data" / "immune_access.db"
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


def timeline_marker_rows() -> int:
    if not TIMELINE_DB.exists():
        return 0
    with sqlite3.connect(f"file:{TIMELINE_DB}?mode=ro", uri=True) as c:
        return c.execute(
            "SELECT COUNT(*) FROM events WHERE summary LIKE ? OR detail LIKE ?",
            (f"%{MARKER}%", f"%{MARKER}%"),
        ).fetchone()[0]


def access_db_rows() -> list[tuple]:
    if not ACCESS_DB.exists():
        return []  # 库不存在（修前形态）
    try:
        with sqlite3.connect(f"file:{ACCESS_DB}?mode=ro", uri=True) as c:
            return c.execute(
                "SELECT list_kind, entry_key, ip, reason, hit_count FROM immune_access"
                " WHERE reason LIKE ? ORDER BY list_kind, entry_key",
                (f"%{MARKER}%",),
            ).fetchall()
    except sqlite3.OperationalError:
        return []  # 表不存在（修前形态）


def wait_health(timeout_s: float = 90.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE}/api/immune/health", timeout=3)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1.5)
    return False


def restart_service() -> None:
    subprocess.run(["systemctl", "--user", "restart", "opensoul.service"], check=True)
    time.sleep(3)
    if not wait_health():
        print("FATAL: service not healthy after restart")
        sys.exit(2)


def lists() -> dict:
    r = httpx.get(f"{BASE}/api/immune/ip/lists", timeout=5)
    r.raise_for_status()
    return r.json()


def check_ip(ip: str) -> dict:
    r = httpx.get(f"{BASE}/api/immune/ip/check/{ip}", timeout=5)
    r.raise_for_status()
    return r.json()


def marker_entries(data: dict) -> tuple[list, list]:
    bl = [e for e in data.get("blacklist", []) if MARKER in (e.get("reason") or "")]
    wl = [e for e in data.get("whitelist", []) if MARKER in (e.get("reason") or "")]
    return bl, wl


def row_hit(rows: list[tuple], ip: str) -> int | None:
    for r in rows:
        if r[2] == ip:
            return r[4]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["pre", "post"], required=True)
    args = ap.parse_args()
    print(f"=== e2e_immune_access_persist mode={args.mode} marker={MARKER} "
          f"ban={BAN_IP} allow={ALLOW_IP} ===")

    if not wait_health():
        print("FATAL: service not healthy")
        return 2
    tl0 = timeline_count()
    print(f"timeline baseline={tl0}")

    # ── 播种黑名单 + 黑名单门命中（hit 计数基线）───────────────
    r = httpx.post(f"{BASE}/api/immune/ip/blacklist",
                   json={"ip": BAN_IP, "reason": MARKER}, timeout=5)
    check(r.status_code == 200, "seed_blacklist", f"status={r.status_code}")
    res = check_ip(BAN_IP)
    check(res.get("allowed") is False and res.get("reason") == MARKER,
          "blacklist_gate_block_before_restart", str(res))

    # ── 播种白名单（白名单-only 门自此生效）────────────────────
    r = httpx.post(f"{BASE}/api/immune/ip/whitelist",
                   json={"ip": ALLOW_IP, "reason": MARKER}, timeout=5)
    check(r.status_code == 200, "seed_whitelist", f"status={r.status_code}")

    data = lists()
    bl, wl = marker_entries(data)
    check(len(bl) == 1 and bl[0]["ip"] == BAN_IP, "lists_blacklist_entry", str(bl))
    check(len(wl) == 1 and wl[0]["ip"] == ALLOW_IP, "lists_whitelist_entry", str(wl))
    res = check_ip(BAN_IP)
    check(res.get("allowed") is False, "check_blocked_before_restart", str(res))

    rows = access_db_rows()
    # 行级判定（同断言双模：--pre FAIL=修前种子零落盘 GAP / --post PASS=写穿真落盘）
    check(len(rows) == 2 and row_hit(rows, BAN_IP) == 1,
          "db_rows_before_restart", str(rows))

    # ── 真重启（本切片核心证据）────────────────────────────────
    restart_service()

    data = lists()
    bl, wl = marker_entries(data)
    check(len(bl) == 1 and bl[0]["ip"] == BAN_IP, "blacklist_survives_restart", str(bl))
    check(len(wl) == 1 and wl[0]["ip"] == ALLOW_IP, "whitelist_survives_restart", str(wl))
    res = check_ip(BAN_IP)
    check(res.get("allowed") is False, "check_blocked_after_restart", str(res))
    res2 = check_ip(ALLOW_IP)
    check(res2.get("allowed") is True, "whitelisted_allowed_after_restart", str(res2))

    rows = access_db_rows()
    check(len(rows) == 2 and row_hit(rows, BAN_IP) == 1,
          "db_rows_after_restart_hit_restored", str(rows))

    # ── hit_count 写穿直证：撤白名单（白名单门短路不计黑命中）后再拦 ──
    r = httpx.delete(f"{BASE}/api/immune/ip/whitelist/{ALLOW_IP}", timeout=5)
    check(r.status_code == 200, "drop_whitelist_for_hit_probe", f"status={r.status_code}")
    res = check_ip(BAN_IP)
    check(res.get("allowed") is False and res.get("reason") == MARKER,
          "blacklist_gate_block_after_restart", str(res))
    rows = access_db_rows()
    check(row_hit(rows, BAN_IP) == 2, "hit_count_write_through", str(rows))

    # ── 清痕（写删写穿+事件行面 TR-② 纪律）─────────────────────
    r = httpx.delete(f"{BASE}/api/immune/ip/blacklist/{BAN_IP}", timeout=5)
    check(r.status_code == 200, "cleanup_delete_blacklist", f"status={r.status_code}")
    r = httpx.delete(f"{BASE}/api/immune/ip/whitelist/{ALLOW_IP}", timeout=5)
    check(r.status_code == 200, "cleanup_delete_whitelist_idempotent", f"status={r.status_code}")
    bl, wl = marker_entries(lists())
    check(bl == [] and wl == [], "cleanup_lists_empty", f"bl={bl} wl={wl}")
    rows = access_db_rows()
    check(rows == [], "cleanup_db_rows_remaining=0", str(rows))

    # timeline 事件行清痕（ip_blacklisted 事件 summary 含 marker）
    if timeline_marker_rows() > 0 and TIMELINE_DB.exists():
        with sqlite3.connect(str(TIMELINE_DB)) as c:
            c.execute(
                "DELETE FROM events WHERE summary LIKE ? OR detail LIKE ?",
                (f"%{MARKER}%", f"%{MARKER}%"),
            )
            c.commit()
    check(timeline_marker_rows() == 0, "cleanup_timeline_events_remaining=0")
    print(f"timeline delta={timeline_count() - tl0}（背景事件如实报，marker 行已清）")

    ok = sum(1 for o, _ in results if o)
    print(f"=== {ok}/{len(results)} ===")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
