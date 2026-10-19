#!/usr/bin/env python3
"""DEC-㉗a-① 部署脚本：admin-ui/out → src/static/admin，dry-run 对账先行（非 _next 删除=0 才真部署）。

回读验证按 logs 页专属产物定位（含「SSE客户端」串的产物文件），判定只认属性名与
字符串字面量（minify 改名不影响）：
- 恢复面在场：recent_count（真源键消费）、最近事件（卡文本）、grid-cols-5（5 卡）；
- 保留面在场：错误/告警卡查询键（㉗b 拍板前维持现状）、CD-⑪ 真源键。
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

# 回读：logs 页专属产物（含「SSE客户端」）内恢复面在场、保留面在场
admin = pathlib.Path(ROOT) / "src" / "static" / "admin"
MARK = "SSE客户端"
logs_files = []
for p in admin.rglob("*"):
    if p.is_file() and p.suffix in (".js", ".html", ".txt"):
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if MARK in t:
            logs_files.append((p, t))
print("logs artifacts:", [str(p.relative_to(admin)) for p, _ in logs_files])
if not logs_files:
    raise SystemExit("REFUSE: 未定位到 logs 页产物，回读不可信")

restored_strings = ["recent_count", "最近事件"]
kept_strings = ["错误/告警", "活跃器官", "总事件", "probes_configured", "sse_subscribers"]
ok = True
for s in restored_strings:
    n = sum(t.count(s) for _, t in logs_files)
    print(f"restored '{s}' in logs artifacts: {n}")
    if not n:
        ok = False
for s in kept_strings:
    n = sum(t.count(s) for _, t in logs_files)
    print(f"kept '{s}' in logs artifacts: {n}")
    if not n:
        ok = False
grid5 = sum(1 for _, t in logs_files if "grid-cols-2 md:grid-cols-5" in t)
print(f"grid-cols-5 artifacts: {grid5}")
if not grid5:
    ok = False
grid4 = sum(1 for _, t in logs_files if "grid-cols-2 md:grid-cols-4" in t)
print(f"grid-cols-4 residual artifacts: {grid4}")
print("VERIFY:", "OK" if ok else "FAIL")
if not ok:
    raise SystemExit(1)
