#!/usr/bin/env python3
"""DEC-㉘b-① 部署脚本：admin-ui/out → src/static/admin，dry-run 对账先行（非 _next
删除=0 才真部署）。CD-⑱/㉘a 部署脚本同款纪律。

回读验证按 system 页专属产物定位（含「执行初始化」串的产物文件），残留/在场判定只认
字符串字面量与属性名（minify 改名不影响成员属性名）：
- 复原面在场：活跃Agent/近期会话/活跃 Agent（卡+行标签字面量）、active_agents/
  recent_sessions（成员属性名，stats 消费形 minify 存活）、grid-cols-2 sm:grid-cols-4
  lg:grid-cols-8（统计条 grid 字面量）；
- 旧形残留=0：sm:grid-cols-3 lg:grid-cols-6 gap-2（CD-⑱ 6 列统计条组合）、
  /api/system/quick（死取数）、SQLite（硬编码 fabrication）；
- 保留面在场：healthy_count/total_entries/uptime_seconds（既有真源键）、执行初始化/
  健康检查（页面锚）。
"""
import pathlib
import subprocess

ROOT = "/home/climbing/opensoul"
r = subprocess.run(
    ["rsync", "-a", "--delete", "--dry-run", "--itemize-changes",
     "admin-ui/out/", "src/static/admin/"],
    capture_output=True, text=True, cwd=ROOT,
)
deletions = [l for l in r.stdout.splitlines() if l.startswith("*deleting")]
non_next = [l for l in deletions if "_next/" not in l]
print(f"dry-run deletions total={len(deletions)} non-_next={len(non_next)}")
for l in non_next[:10]:
    print("  NON-NEXT DEL:", l)
if non_next:
    raise SystemExit("REFUSE: 非 _next 删除不为 0，部署中止")
r2 = subprocess.run(
    ["rsync", "-a", "--delete", "admin-ui/out/", "src/static/admin/"],
    capture_output=True, text=True, cwd=ROOT,
)
print("deploy rc:", r2.returncode)
if r2.returncode != 0:
    print(r2.stderr)
    raise SystemExit(1)

# 回读：system 页专属产物（含「执行初始化」）内旧形残留=0、复原面/保留面在场
admin = pathlib.Path(ROOT) / "src" / "static" / "admin"
MARK = "执行初始化"
sys_files = []
for p in admin.rglob("*"):
    if p.is_file() and p.suffix in (".js", ".html", ".txt"):
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if MARK in t:
            sys_files.append((p, t))
print("system artifacts:", [str(p.relative_to(admin)) for p, _ in sys_files])
if not sys_files:
    raise SystemExit("REFUSE: 未定位到 system 页产物，回读不可信")

old_strings = [
    "sm:grid-cols-3 lg:grid-cols-6 gap-2",
    "/api/system/quick",
    "SQLite",
]
restored_strings = [
    "活跃Agent",
    "活跃 Agent",
    "近期会话",
    "active_agents",
    "recent_sessions",
    "grid-cols-2 sm:grid-cols-4 lg:grid-cols-8",
]
kept_strings = [
    "healthy_count",
    "total_entries",
    "uptime_seconds",
    "执行初始化",
    "健康检查",
]

fail = False
for p, t in sys_files:
    for s in old_strings:
        if s in t:
            print(f"  RESIDUE {s!r} in {p.relative_to(admin)}")
            fail = True
    for s in restored_strings:
        if s not in t:
            print(f"  MISSING restored {s!r} in {p.relative_to(admin)}")
            fail = True
    for s in kept_strings:
        if s not in t:
            print(f"  MISSING kept {s!r} in {p.relative_to(admin)}")
            fail = True
if fail:
    raise SystemExit("REFUSE: 回读对账不过")
print("readback OK: restored in place, residues=0")
