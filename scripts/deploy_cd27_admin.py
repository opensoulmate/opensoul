#!/usr/bin/env python3
"""CD-㉗ 部署脚本：admin-ui/out → src/static/admin，dry-run 对账先行（非 _next 删除=0 才真部署）。

回读验证按 knowledge-graph 页专属产物定位（含「添加关系」串的产物文件），残留/在场判定
只认字符串字面量与成员属性名（minify 改局部变量名不影响属性名）：
- 旧漂移残留=0（只认漂移专属形，relationForm 请求契约/GraphEdge 真键不在残留面）：
  source_entity_id===（修前 isSource 判定对 undefined 恒 false）、
  .target_entity_id.slice/.source_entity_id.slice（修前 undefined.slice TypeError 崩坏面）；
- 保留面在场：target_id.slice+source_id===（真源行键收编消费）、
  source_entity_id:（relationForm 请求契约 RelationCreate 真键，禁误伤）。
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

# 回读：knowledge-graph 页专属产物（含「添加关系」）内旧漂移串残留=0、保留面在场
admin = pathlib.Path(ROOT) / "src" / "static" / "admin"
MARK = "添加关系"
hits = []
for p in admin.rglob("*.js"):
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if MARK in t:
        hits.append((p, t))
print("knowledge-graph chunk files:", [str(p.relative_to(admin)) for p, _ in hits])
assert hits, "未定位到 knowledge-graph 页产物（页面锚缺失）"
fail = False
for p, t in hits:
    for k in ("source_entity_id===", ".target_entity_id.slice", ".source_entity_id.slice"):
        n = t.count(k)
        print(f"  {p.name}: old drift {k!r} count={n}")
        if n:
            fail = True
    for k in ("target_id.slice", "source_id===", "source_entity_id:"):
        n = t.count(k)
        print(f"  {p.name}: true-source/contract {k!r} count={n}")
        if n < 1:
            fail = True
if fail:
    raise SystemExit("READBACK FAIL: 残留/缺席")

# 全量产物扫旧漂移串残留（漂移专属形任何 chunk 出现即失败）
global_hits = []
for p in admin.rglob("*.js"):
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    for k in ("source_entity_id===", ".target_entity_id.slice", ".source_entity_id.slice"):
        if k in t:
            global_hits.append(f"{p.relative_to(admin)}:{k}")
print("global drift residue:", global_hits)
if global_hits:
    raise SystemExit("READBACK FAIL: 全量产物仍有旧漂移残留")
print("readback OK")
