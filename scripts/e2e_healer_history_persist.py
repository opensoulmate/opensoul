#!/usr/bin/env python3
"""RV-①b-⑬ live e2e 探针——healer 修复史持久化 修前/修后双向硬证。

用法（三阶段，各自独立跑）：
  python3 scripts/e2e_healer_history_persist.py --pre      # 修前：GAP 坐实（旧进程）
  python3 scripts/e2e_healer_history_persist.py --post     # 修后：首行 restart() 载新码再验
  python3 scripts/e2e_healer_history_persist.py --cleanup  # 清痕（删探针行+复核 remaining=0）

纪律（既有探针自伤坑记档）：--post 首行必 restart()——写入跑在载新码重启之前
=全部 FAIL 打旧进程（RV-①b-⑤-2/⑥-1 同款自伤再现防）。
探针行识别=rowid 增量 + organ + 时间窗三重过滤（防误伤生产真行），行 id 落
/tmp/e2e_healer_history_rows.json 供 --cleanup 精确删。
"""

import json
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8090"
DB = "/home/climbing/opensoul/data/healer_history.db"
STATE = "/tmp/e2e_healer_history_rows.json"
ORGAN = "capture"  # 探针器官（_ORGANS 真器官，health 口轻）
TOL = 2.0  # 时间窗容差（秒）

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")


def api(method: str, path: str) -> tuple[int, dict]:
    req = urllib.request.Request(BASE + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw) if raw else {}
        except Exception:
            return e.code, {"raw": raw}


def restart() -> None:
    subprocess.run(["systemctl", "--user", "restart", "opensoul.service"], check=True)
    for _ in range(60):
        time.sleep(1)
        try:
            status, _ = api("GET", "/api/health")
            if status == 200:
                return
        except Exception:
            continue
    raise RuntimeError("service did not come back")


def max_rowid() -> int:
    try:
        conn = sqlite3.connect(DB)
    except Exception:
        return 0
    try:
        row = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM healer_history").fetchone()
        return int(row[0])
    except sqlite3.OperationalError:
        return 0  # 无表=修前预期态
    finally:
        conn.close()


def probe_rows(after_rowid: int, t0: float) -> list[dict]:
    try:
        conn = sqlite3.connect(DB)
    except Exception:
        return []
    try:
        conn.row_factory = sqlite3.Row
        return [
            dict(r)
            for r in conn.execute(
                "SELECT rowid AS rid, entry_id, organ, healthy, severity, symptoms,"
                " root_cause, recommended_action, action_taken, action_success,"
                " response_time_ms, timestamp FROM healer_history"
                " WHERE rowid > ? AND organ = ? AND timestamp >= ?",
                (int(after_rowid), ORGAN, float(t0 - TOL)),
            )
        ]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def history_with_marker(t0: float) -> list[dict]:
    status, data = api("GET", "/api/healer/history?limit=200")
    if status != 200:
        return []
    return [
        e
        for e in data.get("history", [])
        if e.get("organ") == ORGAN and float(e.get("timestamp", 0)) >= t0 - TOL
    ]


UI_KEYS = {
    "organ",
    "healthy",
    "severity",
    "symptoms",
    "root_cause",
    "action_taken",
    "action_success",
    "response_time_ms",
    "timestamp",
}


def run_pre() -> None:
    base_rid = max_rowid()
    t0 = time.time()
    print(f"== --pre（旧进程，期望 GAP）t0={t0:.0f} base_rid={base_rid}")
    status, data = api("POST", f"/api/healer/diagnose/{ORGAN}")
    check("pre_diagnose_200", status == 200 and data.get("organ") == ORGAN, str(status))
    entries = history_with_marker(t0)
    check("pre_history_shows_entry", len(entries) == 1, f"n={len(entries)}")
    rows = probe_rows(base_rid, t0)
    check("pre_db_rows_zero_GAP", len(rows) == 0, f"rows={len(rows)}")
    restart()
    entries = history_with_marker(t0)
    check("pre_restart_history_lost_GAP", len(entries) == 0, f"n={len(entries)}")
    status, stats = api("GET", "/api/healer/stats")
    check(
        "pre_restart_stats_zero_GAP",
        status == 200 and int(stats.get("total_diagnoses", -1)) == 0,
        json.dumps({k: stats.get(k) for k in ("total_diagnoses", "healed", "failed")}),
    )
    _summarize()


def run_post() -> None:
    restart()  # 首行必重启载新码（⑤-2/⑥-1 探针自伤坑记档）
    base_rid = max_rowid()
    t0 = time.time()
    print(f"== --post（新码）t0={t0:.0f} base_rid={base_rid}")
    status, data = api("POST", f"/api/healer/diagnose/{ORGAN}")
    check("post_diagnose_200", status == 200 and data.get("organ") == ORGAN, str(status))
    rows = probe_rows(base_rid, t0)
    check("post_row_written", len(rows) == 1, f"rows={len(rows)}")
    if len(rows) == 1:
        row = rows[0]
        check(
            "post_row_parity_with_response",
            row["severity"] == data.get("severity")
            and bool(row["healthy"]) == data.get("healthy")
            and json.loads(row["symptoms"]) == data.get("symptoms")
            and row["root_cause"] == data.get("root_cause"),
            row["severity"],
        )
    entries = history_with_marker(t0)
    check(
        "post_history_reads_row_ui_keys",
        len(entries) == 1 and set(entries[0]) == UI_KEYS,
        f"n={len(entries)} keys={sorted(entries[0]) if entries else '-'}",
    )
    restart()
    entries = history_with_marker(t0)
    check("post_restart_history_survives", len(entries) == 1, f"n={len(entries)}")
    status, stats = api("GET", "/api/healer/stats")
    check(
        "post_restart_stats_survive",
        status == 200 and int(stats.get("total_diagnoses", 0)) >= 1,
        json.dumps({k: stats.get(k) for k in ("total_diagnoses", "healed", "failed")}),
    )
    # 探针行 id + 时间窗落盘供 --cleanup 精确删与页面读面复核
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"rids": [r["rid"] for r in rows], "t0": t0}, f)
    check("post_state_saved", len(rows) >= 1, STATE)
    _summarize()


def run_cleanup() -> None:
    print("== --cleanup")
    try:
        with open(STATE, encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        state = {}
    rids = state.get("rids", [])
    t0 = float(state.get("t0", 0.0))
    conn = sqlite3.connect(DB)
    try:
        deleted = 0
        for rid in rids:
            cur = conn.execute("DELETE FROM healer_history WHERE rowid = ?", (int(rid),))
            deleted += cur.rowcount
        conn.commit()
        remaining = sum(
            conn.execute("SELECT COUNT(*) FROM healer_history WHERE rowid = ?", (int(rid),)).fetchone()[0]
            for rid in rids
        )
    except sqlite3.OperationalError:
        deleted, remaining = 0, 0
    finally:
        conn.close()
    check("cleanup_rows_deleted", deleted == len(rids) and len(rids) > 0, f"deleted={deleted}/{len(rids)}")
    check("cleanup_remaining_zero", remaining == 0, f"remaining={remaining}")
    restart()  # 重启清内存缓存，页面读面复核才有意义
    entries = history_with_marker(t0) if t0 else []
    check("cleanup_history_read_face_clean", len(entries) == 0, f"n={len(entries)}")
    _summarize()


def _summarize() -> None:
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n== {passed}/{total} passed")
    if passed != total:
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  FAIL: {name} {detail}")
        sys.exit(1)


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--pre":
        run_pre()
    elif arg == "--post":
        run_post()
    elif arg == "--cleanup":
        run_cleanup()
    else:
        print("usage: --pre | --post | --cleanup")
        sys.exit(2)
