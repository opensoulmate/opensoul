#!/usr/bin/env python3
"""DEC-㉘a-① 部署脚本：admin-ui/out → src/static/admin，dry-run 对账先行（非 _next 删除=0 才真部署）。

回读验证按 system 页专属产物定位（含「OpenSoul 系统」串的产物文件），判定只认属性名与
字符串字面量（minify 改名不影响）：
- 恢复面在场：uptime_seconds（真源键消费）、运行时间（运行时信息行）、运行（banner span）；
- 保留面在场：运行时信息、数据库、CD-⑱ 真源键（database?.type 派生显示）；
- ㉘b 待拍死面零残留：活跃Agent、近期会话。
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

# 回读：system 页专属产物（含「OpenSoul 系统」）内恢复面在场、死面零残留
admin = pathlib.Path(ROOT) / "src" / "static" / "admin"
MARK = "OpenSoul 系统"
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

restored_strings = ["uptime_seconds", "运行时间", "运行时信息"]
kept_strings = ["数据库", "版本"]
dead_strings = ["活跃Agent", "活跃 Agent", "近期会话"]
ok = True
for s in restored_strings:
    n = sum(t.count(s) for _, t in sys_files)
    print(f"restored '{s}' in system artifacts: {n}")
    if not n:
        ok = False
for s in kept_strings:
    n = sum(t.count(s) for _, t in sys_files)
    print(f"kept '{s}' in system artifacts: {n}")
    if not n:
        ok = False
for s in dead_strings:
    n = sum(t.count(s) for _, t in sys_files)
    print(f"dead '{s}' residual in system artifacts: {n}")
    if n:
        ok = False
print("VERIFY:", "OK" if ok else "FAIL")
if not ok:
    raise SystemExit(1)
