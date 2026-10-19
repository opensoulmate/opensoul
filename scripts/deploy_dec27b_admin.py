#!/usr/bin/env python3
"""DEC-㉗b-① 部署脚本：admin-ui/out → src/static/admin，dry-run 对账先行（非 _next 删除=0 才真部署）。

回读验证按 logs 页专属产物定位（含「SSE客户端」串的产物文件），残留/在场判定只认
字符串字面量（minify 改名不影响字符串字面量；成员访问形 minify 接收者归一不可区分，
不作 bundle 判定——由源码级测试锁死）：
- 旧残留=0：「错误/告警」（修前卡标签字面量，随词表收编退场）；
- 保留面在场：「问题类事件」（新卡标签）、「最近事件」（㉗a 卡）、问题类闭集字面量
  （health_alert/webhook_failed/intrusion_detected/brute_force/ip_blacklisted/
  _rejected 后缀串）真进产物=词表闭集真落 bundle。
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

# 回读：logs 页专属产物（含「SSE客户端」）内旧标签残留=0、词表闭集真进产物
admin = pathlib.Path(ROOT) / "src" / "static" / "admin"
MARK = "SSE客户端"
log_files = []
for p in admin.rglob("*"):
    if p.is_file() and p.suffix in (".js", ".html", ".txt"):
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if MARK in t:
            log_files.append((p, t))
print("logs artifacts:", [str(p.relative_to(admin)) for p, _ in log_files])
if not log_files:
    raise SystemExit("REFUSE: 未定位到 logs 页产物，回读不可信")

old_strings = ["错误/告警"]
kept_strings = [
    "问题类事件", "最近事件", "总事件",
    "health_alert", "webhook_failed", "intrusion_detected",
    "brute_force", "ip_blacklisted", "_rejected",
]
ok = True
for s in old_strings:
    n = sum(t.count(s) for _, t in log_files)
    print(f"old label '{s}' residual in logs artifacts: {n}")
    if n:
        ok = False
for s in kept_strings:
    n = sum(t.count(s) for _, t in log_files)
    print(f"kept '{s}' in logs artifacts: {n}")
    if not n:
        ok = False
print("VERIFY:", "OK" if ok else "FAIL")
if not ok:
    raise SystemExit(1)
