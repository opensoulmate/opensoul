#!/usr/bin/env python3
"""CD-32 部署脚本：admin-ui/out → src/static/admin，dry-run 对账先行（非 _next 删除=0 才真部署）。

回读验证按 connectors 页专属产物定位（含「运行进程」串的产物文件），残留/在场判定
只认字符串字面量与成员属性名（minify 改局部变量名不影响属性名）：
- 旧漂移残留=0（只认漂移专属形）：p.name/p.memory 成员访问（connectors 页进程面唯一
  消费点，修前 p.name 恒空 + p.memory.toFixed TypeError 崩坏）；
- 保留面在场：.command/.executable/.mem（真源键收编消费）；services 面 CD-31 收口键
  在场（.process_name/.local_address/.local_port，禁误扩回归）。
"""
import pathlib
import re
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

# 回读：connectors 页专属产物（含「运行进程」）内旧漂移串残留=0、真源消费在场
admin = pathlib.Path(ROOT) / "src" / "static" / "admin"
MARK = "运行进程"
hits = []
for p in admin.rglob("*.js"):
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if MARK in t:
        hits.append((p, t))
print("connectors chunk files:", [str(p.relative_to(admin)) for p, _ in hits])
assert hits, "未定位到 connectors 页产物（页面锚缺失）"
fail = False
for p, t in hits:
    # 旧漂移残留=0：.memory（进程面唯一 .memory 消费，修前 p.memory.toFixed 崩坏面）。
    # 工具边界如实记：修前进程名面 .name 与本页 tool.name/adapter.name 消费在 minify
    # 后接收者名归一不可区分，bundle 层不判 .name 残留——该项零残留由源码消费面
    # （tests/test_cd32_repair.py 4 例）+ census findings 归零（同文件 2 例）锁死。
    n = len(re.findall(r"\.memory\b", t))
    print(f"  {p.name}: old drift member '.memory' count={n}")
    if n:
        fail = True
    for k in (".command", ".executable", ".mem", ".process_name", ".local_address", ".local_port"):
        n = t.count(k)
        print(f"  {p.name}: true-source {k!r} count={n}")
        if n < 1:
            fail = True
    n = len(re.findall(r"\.command\|\|\w*\.executable", t))
    print(f"  {p.name}: process-name fallback-form count={n}")
    if n < 1:
        fail = True
if fail:
    raise SystemExit("READBACK FAIL: 残留/缺席")
print("readback OK")
