#!/usr/bin/env python3
"""CD-㉓ 部署脚本：admin-ui/out → src/static/admin，dry-run 对账先行（非 _next 删除=0 才真部署）。

回读验证按 agent-nodes 页专属产物定位（含「未找到相关记忆」串的产物文件），
残留/在场判定只认字符串字面量与成员属性名（minify 改名不影响属性名）：
- 旧漂移残留=0：.results / .query（修前 RecallResult 死声明键消费形）；
- 保留面在场：.sources / .answer（真源键双收编）、未找到相关记忆（页面锚）。
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

# 回读：agent-nodes 页专属产物（含「未找到相关记忆」）内旧漂移串残留=0、保留面在场
admin = pathlib.Path(ROOT) / "src" / "static" / "admin"
MARK = "未找到相关记忆"
hits = []
for p in admin.rglob("*.js"):
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if MARK in t:
        hits.append((p, t))
print("agent-nodes chunk files:", [str(p.relative_to(admin)) for p, _ in hits])
assert hits, "未定位到 agent-nodes 页产物（页面锚缺失）"
fail = False
for p, t in hits:
    for k in (".results", ".query"):
        n = t.count(k)
        print(f"  {p.name}: old drift {k!r} count={n}")
        if n:
            fail = True
    for k in (".sources", ".answer"):
        n = t.count(k)
        print(f"  {p.name}: true-source {k!r} count={n}")
        if n < 1:
            fail = True
if fail:
    raise SystemExit("READBACK FAIL: 残留/缺席")
print("readback OK")
