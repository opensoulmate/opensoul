#!/usr/bin/env python3
"""CD-㉖ 部署脚本：admin-ui/out → src/static/admin，dry-run 对账先行（非 _next 删除=0 才真部署）。

回读验证按 cortex 页专属产物定位（含「推理步骤」串的产物文件），残留/在场判定只认
字符串字面量与成员属性名（minify 改名不影响属性名）：
- 旧漂移残留=0：reasoning_steps（修前死键消费——真源只产 reasoning 字符串，UI 按
  reasoning_steps 数组渲染「推理步骤」块恒 falsy 永不渲染）；
- 保留面在场：推理步骤（页面锚）、whitespace-pre-wrap（reasoning 字符串逐字渲染收编）、
  confidence（真源键收编在场回归面）。
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

# 回读：cortex 页专属产物（含「推理步骤」）内旧漂移串残留=0、保留面在场
admin = pathlib.Path(ROOT) / "src" / "static" / "admin"
MARK = "推理步骤"
hits = []
for p in admin.rglob("*.js"):
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if MARK in t:
        hits.append((p, t))
print("cortex chunk files:", [str(p.relative_to(admin)) for p, _ in hits])
assert hits, "未定位到 cortex 页产物（页面锚缺失）"
fail = False
for p, t in hits:
    for k in ("reasoning_steps",):
        n = t.count(k)
        print(f"  {p.name}: old drift {k!r} count={n}")
        if n:
            fail = True
    for k in ("whitespace-pre-wrap", "confidence", "推理步骤"):
        n = t.count(k)
        print(f"  {p.name}: true-source/anchor {k!r} count={n}")
        if n < 1:
            fail = True
if fail:
    raise SystemExit("READBACK FAIL: 残留/缺席")

# 全量产物扫旧漂移串残留（reasoning_steps 任何 chunk 出现即失败）
global_hits = []
for p in admin.rglob("*.js"):
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if "reasoning_steps" in t:
        global_hits.append(str(p.relative_to(admin)))
print("global reasoning_steps residue:", global_hits)
if global_hits:
    raise SystemExit("READBACK FAIL: 全量产物仍有 reasoning_steps 残留")
print("readback OK")
