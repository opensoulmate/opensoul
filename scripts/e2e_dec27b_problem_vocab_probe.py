#!/usr/bin/env python3
"""DEC-㉗b-① live e2e 探针（只读零写入=TR-② 纪律零事件行面）：logs 页问题类事件词表计数。

四面：
P1 真源：GET /api/events/summary 形状恰 6 键 + by_type 真 dict；独立重算问题类计数
   （闭集∪预留∪*_rejected）与修前公式（by_type.error+by_type.alert）同数据对照——
   修前卡值 vs 修后卡值同探针内双向硬证（health_alert 等异名词表在场时修后必更大）。
P2 部署产物回读（HTTP :8090 static chunk）：旧标签「错误/告警」0 残留、
   「问题类事件」+问题类闭集字面量真进产物。
P3 行为桥：admin-ui/app/logs/page.tsx 源码逐字提取 problemEventCount+闭集常量
   →node 真执行吃 live by_type →==独立重算（禁口径漂移）。
P4 清痕对账：timeline events 行数前后 delta=0（探针零写入自证）。
"""
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

BASE = "http://localhost:8090"
ROOT = Path("/home/climbing/opensoul")
PAGE = ROOT / "admin-ui" / "app" / "logs" / "page.tsx"

APPROVED = {
    "error", "alert", "security", "intrusion_detected", "brute_force",
    "ip_blocked", "ip_blacklisted", "permission_denied", "content_blocked",
    "request_blocked", "health_alert", "component_error", "pipeline_blocked",
    "webhook_failed",
}
SUFFIX = "_rejected"

FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}")
    if not ok:
        FAILS.append(name)


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return r.status, r.read()


def timeline_count():
    import sqlite3

    db = ROOT / "data" / "timeline.db"
    if not db.exists():
        return -1
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()


def main():
    t0 = timeline_count()

    # P1 真源
    st, body = get("/api/events/summary")
    check("P1 summary http 200", st == 200, f"st={st}")
    resp = json.loads(body)
    check(
        "P1 summary 形状恰 6 键",
        set(resp)
        == {"total_events", "by_organ", "by_type", "most_active_organ", "recent_count", "collected_at"},
        f"keys={sorted(resp)}",
    )
    by_type = resp["by_type"]
    assert isinstance(by_type, dict)
    expect = sum(
        v for k, v in by_type.items()
        if (k in APPROVED or k.endswith(SUFFIX)) and isinstance(v, (int, float))
    )
    legacy = (by_type.get("error") or 0) + (by_type.get("alert") or 0)
    check("P1 修后计数>=修前公式（预留语义类不丢）", expect >= legacy, f"new={expect} legacy={legacy}")
    aliases = {k: v for k, v in by_type.items() if k in APPROVED and k not in ("error", "alert")}
    if aliases:
        check(
            "P1 异名词表真计数（修前恒 0 读者语义漏计已修）",
            expect > legacy,
            f"aliases={aliases} new={expect} legacy={legacy}",
        )
    else:
        print("INFO P1 本次 by_type 无异名词表键（修前/修后同值如实记）", by_type)

    # P2 部署产物回读
    st, idx = get("/admin/")
    check("P2 /admin/ 200", st == 200)
    chunks = set()
    for page_path in ("/admin/", "/admin/logs"):
        try:
            st_p, idx_p = get(page_path)
        except Exception:
            continue
        if st_p == 200:
            chunks |= set(
                re.findall(r"/_next/static/chunks/[A-Za-z0-9_\-]+\.js", idx_p.decode("utf-8", "ignore"))
            )
    log_js = []
    for c in sorted(chunks):
        # 家族坑（cd29 先例）：chunk 相对路径须带 /admin 挂载前缀
        try:
            st2, b = get("/admin" + c)
        except Exception:
            continue
        if st2 == 200 and "SSE客户端" in b.decode("utf-8", "ignore"):
            log_js.append(b.decode("utf-8", "ignore"))
    check("P2 logs 页产物定位", bool(log_js), f"chunks={len(chunks)}")
    if log_js:
        joined = "\n".join(log_js)
        check("P2 旧标签「错误/告警」0 残留", joined.count("错误/告警") == 0, f"n={joined.count('错误/告警')}")
        for s in ("问题类事件", "health_alert", "webhook_failed", "_rejected", "最近事件"):
            check(f"P2 词表/标签在场 {s}", s in joined)

    # P3 行为桥
    src = PAGE.read_text(encoding="utf-8")
    m = re.search(r"(const PROBLEM_TYPE_KEYS[\s\S]*?function problemEventCount\([\s\S]*?\n\})", src)
    check("P3 helper 提取", bool(m))
    if m:
        probe = m.group(1) + "\nconsole.log(problemEventCount(" + json.dumps(by_type) + "))"
        r = subprocess.run(["node", "-e", probe], capture_output=True, text=True, timeout=30)
        check("P3 node 执行", r.returncode == 0, r.stderr[:200])
        if r.returncode == 0:
            node_count = int(r.stdout.strip())
            check("P3 node 计数==独立重算", node_count == expect, f"node={node_count} expect={expect}")

    # P4 清痕对账
    t1 = timeline_count()
    check("P4 timeline events delta=0（零写入）", t0 == t1, f"before={t0} after={t1}")

    print(f"\nRESULT {len(FAILS)} FAIL")
    if FAILS:
        print("FAILED CHECKS:", FAILS)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
