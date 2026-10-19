#!/usr/bin/env python3
"""DEC-㉘b-① live e2e 探针（只读零写入，TR-② 事件行面纪律：timeline delta=0 取证）。

面：
- P1 真源：GET /api/system/overview 真产出 active_agents/recent_sessions（int ≥0），
  与独立重算逐值对账（recent_sessions=agent_sessions 24h 窗口独立 SQL、active_agents=
  协调表 5min 心跳窗独立 SQL——同语义另写 SQL 仅作对账非判法源）；quick 4 键零变化。
- P2 产物回读：system 页 chunk 内复原面在场（活跃Agent/活跃 Agent/近期会话/两键成员名/
  grid-cols-8），旧形残留=0（CD-⑱ 6 列统计条组合）。
- P3 渲染对账：真源真值过 UI value 表达式（逐字提取经 node）==展示语义（真 0='0'）。
- 取证后 timeline 事件行 delta=0（零写入证明）。
"""
import json
import pathlib
import re
import sqlite3
import subprocess
import time
import urllib.request

BASE = "http://127.0.0.1:8090"
ROOT = pathlib.Path("/home/climbing/opensoul")
DB = ROOT / "data" / "opensoul.db"
TIMELINE_DB = ROOT / "data" / "timeline.db"
PAGE = ROOT / "admin-ui" / "app" / "system" / "page.tsx"

fails = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}")
    if not ok:
        fails.append(name)


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return r.status, json.loads(r.read().decode())


def timeline_count():
    con = sqlite3.connect(str(TIMELINE_DB))
    try:
        return con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        con.close()


t0 = timeline_count()

# P1 真源
st, ov = get("/api/system/overview")
check("overview 200", st == 200)
aa, rs = ov.get("active_agents"), ov.get("recent_sessions")
check("active_agents int>=0", isinstance(aa, int) and aa >= 0, f"= {aa}")
check("recent_sessions int>=0", isinstance(rs, int) and rs >= 0, f"= {rs}")

# 独立重算（同语义对账，非判法源）
con = sqlite3.connect(str(DB))
now = time.time()
aa2 = con.execute(
    "SELECT COUNT(*) FROM agent_registry WHERE last_heartbeat > ?", (now - 300,)
).fetchone()[0]
rs2 = con.execute(
    "SELECT COUNT(*) FROM agent_sessions WHERE last_activity_at >= ?", (now - 86400,)
).fetchone()[0]
con.close()
check("active_agents==独立重算", aa == aa2, f"{aa} vs {aa2}")
check("recent_sessions==独立重算", rs == rs2, f"{rs} vs {rs2}")

st, quick = get("/api/system/quick")
check("quick 4 键零变化", st == 200 and set(quick) == {"status", "db", "version", "timestamp"}, str(sorted(quick)))

# P2 产物回读
admin = ROOT / "src" / "static" / "admin"
MARK = "执行初始化"
chunks = []
for p in admin.rglob("*"):
    if p.is_file() and p.suffix in (".js", ".html", ".txt"):
        t = p.read_text(encoding="utf-8", errors="ignore")
        if MARK in t:
            chunks.append((p, t))
check("system chunk 定位", len(chunks) >= 1, str([str(p.relative_to(admin)) for p, _ in chunks]))
if chunks:
    blob = "\n".join(t for _, t in chunks)
    for s in ("活跃Agent", "活跃 Agent", "近期会话", "active_agents", "recent_sessions",
              "grid-cols-2 sm:grid-cols-4 lg:grid-cols-8"):
        check(f"复原面在场 {s!r}", s in blob)
    check("旧形 6 列统计条残留=0", "sm:grid-cols-3 lg:grid-cols-6 gap-2" not in blob)
    check("quick 死取数残留=0", "/api/system/quick" not in blob)

# P3 渲染对账（UI value 表达式逐字提取经 node 真执行吃 live 真值）
src = re.sub(r"//.*$", "", PAGE.read_text(encoding="utf-8"), flags=re.M)
exprs = []
for key in ("active_agents", "recent_sessions"):
    m = re.search(rf"stats\.{key} != null \? stats\.{key} : '—'", src)
    check(f"表达式提取 {key}", bool(m))
    if m:
        exprs.append(m.group(0))
if len(exprs) == 2:
    probe = (
        "const run = (stats, e) => Function('stats', 'return (' + e + ')')(stats);\n"
        f"const s = {{active_agents: {aa}, recent_sessions: {rs}}};\n"
        f"console.log(JSON.stringify([String(run(s, {json.dumps(exprs[0])})), "
        f"String(run(s, {json.dumps(exprs[1])}))]));"
    )
    r = subprocess.run(["node", "-e", probe], capture_output=True, text=True, timeout=30)
    check("node 渲染执行", r.returncode == 0, r.stderr[:200])
    if r.returncode == 0:
        got = json.loads(r.stdout.strip())
        expect = [str(aa), str(rs)]
        check("渲染==真源真值", got == expect, f"{got} vs {expect}")

# 零写入证明
t1 = timeline_count()
check("timeline delta=0（零写入）", t1 == t0, f"{t0} -> {t1}")

print(f"\n{'ALL PASS' if not fails else 'FAILS: ' + str(fails)} ({len(fails)} failed)")
raise SystemExit(1 if fails else 0)
