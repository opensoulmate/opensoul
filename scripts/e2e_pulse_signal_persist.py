#!/usr/bin/env python3
"""RV-①b-⑨ live 重启探针——pulse 脉冲信号注册表跨重启存活双向硬证。

用法：python3 scripts/e2e_pulse_signal_persist.py --pre|--post|--cleanup
--pre  修前（旧进程）：信号可见但零落盘口→restart→信号丢（GAP 坐实）
--post 修后（新进程）：信号落盘→restart→回载可见+by-ID 契约（收口实证）
--cleanup 清理探针残留（含 --pre/--post 全部痕迹）
只碰自带标记 name 的信号，取证后清痕。
"""
import json
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://localhost:8090"
DB = "/home/climbing/opensoul/data/pulse_signals.db"
MARK = "e2e-rv1b9"


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def rows_with_mark():
    if not Path(DB).exists():
        return []
    with sqlite3.connect(DB) as conn:
        try:
            return conn.execute(
                "SELECT signal_id, name, status, fire_count FROM pulse_signals WHERE name LIKE ?",
                (f"%{MARK}%",),
            ).fetchall()
        except sqlite3.OperationalError:
            return []


def restart():
    subprocess.run(["systemctl", "--user", "restart", "opensoul.service"], check=True)
    for _ in range(60):
        time.sleep(1)
        try:
            s, _ = call("GET", "/api/pulse/health")
            if s == 200:
                return True
        except Exception:
            pass
    return False


def marked_signals():
    s, b = call("GET", "/api/pulse/signals")
    return [x for x in b.get("signals", []) if MARK in x.get("name", "")]


def clean():
    for sig in marked_signals():
        call("DELETE", f"/api/pulse/signals/{sig['signal_id']}")
    if Path(DB).exists():
        with sqlite3.connect(DB) as conn:
            conn.execute("DELETE FROM pulse_signals WHERE name LIKE ?", (f"%{MARK}%",))
            conn.commit()


def main():
    mode = sys.argv[1]
    results = []

    def check(name, ok, extra=""):
        results.append((name, ok, extra))

    if mode == "--cleanup":
        clean()
        rows_left = rows_with_mark()
        sigs_left = marked_signals()
        check("清痕", len(rows_left) == 0 and len(sigs_left) == 0,
              f"remaining_rows={len(rows_left)} remaining_sigs={len(sigs_left)}")
    else:
        # 1) 创建信号（用户真实路径 POST /api/pulse/signals）
        s, b = call(
            "POST", "/api/pulse/signals",
            {"name": f"{MARK}-信号", "signal_type": "interval", "interval_ms": 1000.0,
             "payload": {"type": "probe"}},
        )
        check("创建信号", s == 200, f"{s} {b}")
        sid = b.get("signal_id", "")
        # 2) by-ID 可见（内存面）
        s2, b2 = call("GET", f"/api/pulse/signals/{sid}")
        check("by_ID可见", s2 == 200 and b2.get("name", "").startswith(MARK), f"{s2}")
        # 3) tick 一次（fire_count 迁移=状态快照落行面）
        s3, _ = call("POST", f"/api/pulse/signals/{sid}/tick")
        check("tick", s3 == 200, f"{s3}")
        # 4) 落盘口
        rows = rows_with_mark()
        if mode == "--pre":
            check("零落盘口(GAP)", len(rows) == 0, f"rows={rows}")
        else:
            check("落盘行在场", len(rows) >= 1, f"rows={rows}")
            check("行含fire_count", any(r[3] == 1 for r in rows), f"rows={rows}")
        # 5) restart→回载
        check("restart", restart())
        sigs2 = marked_signals()
        s4, b4 = call("GET", f"/api/pulse/signals/{sid}") if sid else (404, {})
        if mode == "--pre":
            check("修前重启信号丢(GAP)", len(sigs2) == 0 and s4 == 404,
                  f"sigs={len(sigs2)} by_id={s4}")
        else:
            check("修后重启回载可见", len(sigs2) >= 1 and s4 == 200,
                  f"sigs={[(x['name'], x['status'], x['fire_count']) for x in sigs2]} by_id={s4}")
            check("fire_count跨重启", b4.get("fire_count") == 1, f"fire_count={b4.get('fire_count')}")
            check("payload跨重启", b4.get("payload") == {"type": "probe"}, f"payload={b4.get('payload')}")
            rows2 = rows_with_mark()
            check("行真源仍在库", len(rows2) >= 1, f"rows={rows2}")
            # stats total_ticks 对账下限（在册 fire_count 合计）
            s5, st = call("GET", "/api/pulse/stats")
            total_ticks = st.get("total_ticks", -1)
            check("total_ticks对账下限", s5 == 200 and total_ticks >= 1, f"total_ticks={total_ticks}")
        clean()
        rows_left = rows_with_mark()
        sigs_left = marked_signals()
        check("清痕", len(rows_left) == 0 and len(sigs_left) == 0,
              f"remaining_rows={len(rows_left)} remaining_sigs={len(sigs_left)}")

    ok_n = sum(1 for _, ok, _ in results if ok)
    for name, ok, extra in results:
        print(f"{'PASS' if ok else 'FAIL'} | {name} | {extra}")
    print(f"== {mode} {ok_n}/{len(results)} ==")
    sys.exit(0 if ok_n == len(results) else 1)


if __name__ == "__main__":
    main()
