#!/usr/bin/env python3
"""CD-㉕ 部署脚本：admin-ui/out → src/static/admin，dry-run 对账先行（非 _next 删除=0 才真部署）。

回读验证按 benchmark 页专属产物定位（含「最佳平均延迟」串的产物文件），
残留/在场判定只认字符串字面量与成员属性名（minify 改名不影响属性名）：
- 旧漂移残留=0：organs?.join（修前 lastResult.organs?.join / run.organs?.join 两处
  漂移读——前者死键恒 undefined、后者对 JSON 串调 .join 必炸）+ "全部"（修前
  真源不产出的 fabricate）；
- 保留面在场：JSON.parse（runOrgansLabel 收编真源 organs JSON 串）、.organ
  （真源 results[].organ 目标列表派生）、最佳平均延迟（页面锚）。
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

# 回读：benchmark 页专属产物（含「最佳平均延迟」）内旧漂移串残留=0、保留面在场
admin = pathlib.Path(ROOT) / "src" / "static" / "admin"
MARK = "最佳平均延迟"
hits = []
for p in admin.rglob("*.js"):
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if MARK in t:
        hits.append((p, t))
print("benchmark chunk files:", [str(p.relative_to(admin)) for p, _ in hits])
assert hits, "未定位到 benchmark 页产物（页面锚缺失）"
fail = False
for p, t in hits:
    for k in ("organs?.join", "全部"):
        n = t.count(k)
        print(f"  {p.name}: old drift {k!r} count={n}")
        if n:
            fail = True
    for k in ("JSON.parse", ".organ", "最佳平均延迟"):
        n = t.count(k)
        print(f"  {p.name}: true-source/anchor {k!r} count={n}")
        if n < 1:
            fail = True
if fail:
    raise SystemExit("READBACK FAIL: 残留/缺席")
print("readback OK")
