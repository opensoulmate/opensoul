#!/usr/bin/env python3
"""RV-①b-⑪ live 重启探针——威胁史+爆破证据窗跨重启存活双向硬证。

用法：python3 scripts/e2e_immune_intrusion_persist.py --pre|--post
--pre  修前（旧进程）：威胁史/登录窗只活内存→restart→史丢+爆破计数清零（GAP 坐实）
--post 修后（新进程）：两面落盘→restart→回载可见+爆破证据窗跨重启续算（收口实证）
只碰 TEST-NET IP（192.0.2.77/.78）与自带标记 path，取证后清痕。
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
DB = "/home/climbing/opensoul/data/immune_intrusion.db"
MARK = "e2e-rv1b11"
THREAT_IP = "192.0.2.77"
LOGIN_IP = "192.0.2.78"


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def db_rows():
    if not Path(DB).exists():
        return [], []
    with sqlite3.connect(DB) as conn:
        try:
            threats = conn.execute(
                "SELECT threat_id, attack_type, source_ip, path FROM intrusion_threats "
                "WHERE (source_ip = ? AND path LIKE ?) OR source_ip = ?",
                (THREAT_IP, f"%{MARK}%", LOGIN_IP),
            ).fetchall()
        except sqlite3.OperationalError:
            threats = []
        try:
            logins = conn.execute(
                "SELECT ip, ts FROM intrusion_logins WHERE ip = ?", (LOGIN_IP,)
            ).fetchall()
        except sqlite3.OperationalError:
            logins = []
    return threats, logins


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
    if Path(DB).exists():
        with sqlite3.connect(DB) as conn:
            conn.execute(
                "DELETE FROM intrusion_threats WHERE (source_ip = ? AND path LIKE ?) OR source_ip = ?",
                (THREAT_IP, f"%{MARK}%", LOGIN_IP),
            )
            conn.execute("DELETE FROM intrusion_logins WHERE ip = ?", (LOGIN_IP,))
            conn.commit()
    # 爆破 auto-block 留痕（RV-①b-② 名单面）与运行时嫌疑面
    call("DELETE", f"/api/immune/ip/blacklist/{LOGIN_IP}")


def main():
    mode = sys.argv[1]
    results = []

    def check(name, ok, extra=""):
        results.append((name, ok, extra))

    # ── 威胁史面：inspect 检出 SQL 注入 → /intrusion/threats 可见 ──────────
    s, b = call(
        "POST",
        "/api/immune/intrusion/inspect",
        {"ip": THREAT_IP, "method": "GET", "path": f"/api/e2e/{MARK}", "body": "' OR '1'='1'"},
    )
    check("inspect_检出", s == 200 and b.get("threats_found", 0) >= 1, f"{s} {b.get('threats_found')}")
    s, b = call("GET", f"/api/immune/intrusion/threats?ip={THREAT_IP}&limit=50")
    hits = [t for t in b.get("threats", []) if MARK in t.get("path", "")]
    check("威胁史内存面可见", len(hits) >= 1, f"{s} n={len(hits)}")

    # ── 爆破证据窗：9 次失败登录（阈值 10，未触发）────────────────────────
    for i in range(9):
        s, b = call("POST", "/api/immune/intrusion/login-attempt", {"ip": LOGIN_IP, "success": False})
    check("九次失败未触发", s == 200 and b.get("blocked") is False, f"{s} {b}")

    # ── 落盘口 ─────────────────────────────────────────────────────────────
    threats, logins = db_rows()
    if mode == "--pre":
        check("零落盘口(GAP)", len(threats) == 0 and len(logins) == 0, f"threats={len(threats)} logins={len(logins)}")
    else:
        check("落盘行在场", len(threats) >= 1 and len(logins) == 9, f"threats={len(threats)} logins={len(logins)}")

    # ── restart → 回载 / 爆破证据窗续算 ───────────────────────────────────
    check("restart", restart())
    s, b = call("GET", f"/api/immune/intrusion/threats?ip={THREAT_IP}&limit=50")
    hits2 = [t for t in b.get("threats", []) if MARK in t.get("path", "")]
    s2, st = call("GET", "/api/immune/stats")
    by_type = st.get("intrusion_detection", {}).get("by_type", {})
    if mode == "--pre":
        check("修前重启史丢(GAP)", len(hits2) == 0, f"n={len(hits2)}")
    else:
        check("修后重启回载可见", len(hits2) >= 1, f"n={len(hits2)}")
        check("计数真源跨重启", by_type.get("sql_injection", 0) >= 1, f"by_type={by_type}")

    # 第 10 次失败：证据窗跨重启则触发爆破；重启清窗则不触发
    s, b = call("POST", "/api/immune/intrusion/login-attempt", {"ip": LOGIN_IP, "success": False})
    if mode == "--pre":
        check("修前爆破计数被重启清零(GAP)", b.get("blocked") is False, f"{s} {b}")
    else:
        check(
            "修后爆破证据窗跨重启续算",
            b.get("blocked") is True and "Brute force" in b.get("threat", {}).get("detail", ""),
            f"{s} {b}",
        )
        threats2, logins2 = db_rows()
        check("行真源仍在库", len(threats2) >= 1 and len(logins2) == 10, f"threats={len(threats2)} logins={len(logins2)}")

    clean()
    remaining = db_rows()
    check("清痕 remaining=0", len(remaining[0]) == 0 and len(remaining[1]) == 0, f"{len(remaining[0])}/{len(remaining[1])}")

    ok_n = sum(1 for _, ok, _ in results if ok)
    for name, ok, extra in results:
        print(f"{'PASS' if ok else 'FAIL'} | {name} | {extra}")
    print(f"== {mode} {ok_n}/{len(results)} ==")
    return 0 if ok_n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
