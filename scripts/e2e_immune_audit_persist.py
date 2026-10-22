#!/usr/bin/env python3
"""RV-①b-⑧ live 重启探针——安全审计留痕跨重启存活双向硬证。

用法：python3 scripts/e2e_immune_audit_persist.py --pre|--post
--pre  修前（旧进程）：留痕可见但零落盘口→restart→史丢（GAP 坐实）
--post 修后（新进程）：留痕落盘→restart→回载可见（收口实证）
只碰 TEST-NET IP 与自带标记 detail，取证后清痕。
"""
import json
import sqlite3
import subprocess
import sys
import time
import urllib.request

BASE = "http://localhost:8090"
DB = "/home/climbing/opensoul/data/immune_audit.db"
MARK = "e2e-rv1b8"


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
    if not __import__("pathlib").Path(DB).exists():
        return []
    with sqlite3.connect(DB) as conn:
        try:
            return conn.execute(
                "SELECT detail, action, risk_level FROM immune_audit_log WHERE detail LIKE ?",
                (f"%{MARK}%",),
            ).fetchall()
        except sqlite3.OperationalError:
            return []


def restart():
    subprocess.run(["systemctl", "--user", "restart", "opensoul.service"], check=True)
    for _ in range(60):
        time.sleep(1)
        try:
            s, _ = call("GET", "/api/immune/health")
            if s == 200:
                return True
        except Exception:
            pass
    return False


def clean():
    if __import__("pathlib").Path(DB).exists():
        with sqlite3.connect(DB) as conn:
            conn.execute("DELETE FROM immune_audit_log WHERE detail LIKE ?", (f"%{MARK}%",))
            conn.commit()
    # 清运行时名单痕迹（--pre/--post 都会拉黑一次 TEST-NET IP）
    call("DELETE", "/api/immune/ip/blacklist/192.0.2.77")


def main():
    mode = sys.argv[1]
    results = []

    def check(name, ok, extra=""):
        results.append((name, ok, extra))

    # 1) 制造审计留痕（CONFIG_CHANGE：blacklist 写点真消费 audit.log）
    s, b = call("POST", "/api/immune/ip/blacklist", {"ip": "192.0.2.77", "reason": MARK})
    check("blacklist_写点", s == 200, f"{s} {b}")
    # 2) 内存面可见
    s, b = call("GET", "/api/immune/audit/log?limit=50")
    details = [e.get("detail", "") for e in b.get("entries", [])]
    check("内存面可见", any(MARK in d for d in details), f"{s} n={len(details)}")
    # 3) 落盘口
    rows = rows_with_mark()
    if mode == "--pre":
        check("零落盘口(GAP)", len(rows) == 0, f"rows={rows}")
    else:
        check("落盘行在场", len(rows) >= 1, f"rows={rows}")
    # 4) restart→回载
    check("restart", restart())
    s, b = call("GET", "/api/immune/audit/log?limit=50")
    details2 = [e.get("detail", "") for e in b.get("entries", [])]
    s2, st = call("GET", "/api/immune/audit/stats")
    counters = st.get("counters", {})
    if mode == "--pre":
        check("修前重启史丢(GAP)", not any(MARK in d for d in details2), f"n={len(details2)}")
    else:
        check("修后重启回载可见", any(MARK in d for d in details2), f"n={len(details2)} details={[d for d in details2 if MARK in d]}")
        check("计数真源跨重启", any(k.startswith("config_change") for k in counters), f"counters={counters}")
        rows2 = rows_with_mark()
        check("行真源仍在库", len(rows2) >= 1, f"rows={rows2}")
        # cleanup 裁内存不回退计数（契约保真）
        s3, cb = call("POST", "/api/immune/audit/cleanup", {"keep_last": 1})
        s4, st4 = call("GET", "/api/immune/audit/stats")
        total_after = sum(st4.get("counters", {}).values())
        rows3 = rows_with_mark()
        check("cleanup不删行不回退计数", s3 == 200 and len(rows3) >= 1 and total_after >= 1, f"cleanup={cb} counters={st4.get('counters')} rows={len(rows3)}")

    clean()
    rows_left = rows_with_mark()
    check("清痕", len(rows_left) == 0, f"remaining={len(rows_left)}")

    ok_n = sum(1 for _, ok, _ in results if ok)
    for name, ok, extra in results:
        print(f"{'PASS' if ok else 'FAIL'} | {name} | {extra}")
    print(f"== {mode} {ok_n}/{len(results)} ==")
    sys.exit(0 if ok_n == len(results) else 1)


if __name__ == "__main__":
    main()
