#!/usr/bin/env python3
"""CD-⑱ 部署脚本：admin-ui/out → src/static/admin，dry-run 对账先行（非 _next 删除=0 才真部署）。

回读验证按 system 页专属产物定位（含「执行初始化」串的产物文件），残留/在场判定只认
字符串字面量与属性名（minify 改名不影响成员属性名）：
- 旧漂移残留=0：organs_healthy/organs_total/active_agents/recent_sessions/memory_mb/
  disk_usage（死统计键）、/api/system/quick（死取数）、活跃Agent/近期会话/运行时间
  （死显示标签）、SQLite（硬编码 fabrication）；
- 保留面在场：healthy_count/total_count/total_entries/cpu_percent/used_mb（真源键
  消费）、执行初始化/健康检查（页面锚）。
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

# 回读：system 页专属产物（含「执行初始化」）内旧漂移串残留=0、保留面在场
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
    "organs_healthy", "organs_total", "active_agents", "recent_sessions",
    "memory_mb", "disk_usage", "/api/system/quick",
    "活跃Agent", "近期会话", "运行时间", "SQLite",
]
kept_strings = [
    "healthy_count", "total_count", "total_entries", "cpu_percent", "used_mb",
    "执行初始化", "健康检查",
]
ok = True
for s in old_strings:
    n = sum(t.count(s) for _, t in sys_files)
    print(f"old drift '{s}' residual in system artifacts: {n}")
    if n:
        ok = False
for s in kept_strings:
    n = sum(t.count(s) for _, t in sys_files)
    print(f"kept '{s}' in system artifacts: {n}")
    if not n:
        ok = False
print("VERIFY:", "OK" if ok else "FAIL")
if not ok:
    raise SystemExit(1)
