#!/usr/bin/env python3
"""CD-㉘ 部署脚本：admin-ui/out → src/static/admin，dry-run 对账先行（非 _next 删除=0 才真部署）。

回读验证按 knowledge-graph 页专属产物定位（含「添加关系」串的产物文件），残留/在场判定
只认字符串字面量与成员属性名（minify 改局部变量名不影响属性名）：
- 旧漂移残留=0（只认漂移专属形，rel.type/relation_type/node_type 真键不在残留面）：
  .type.toLowerCase（修前 e.type.toLowerCase() 对 undefined 调 toLowerCase=搜索即页崩
  TypeError 崩坏面）、.type!==（修前列表过滤 e.type!==typeFilter 恒 undefined）；
- 保留面在场：entity_type（真源键收编消费 ×12）、source_entity_id（relationForm
  请求契约 RelationCreate 真键，禁误伤）；
- 全 chunk `.type` 成员访问恰 1 处=relations 元素 {rel.type} 徽章（CD-㉗ 真源键，
  禁误伤）——多一处即漂移复发、少一处即真源键被误删。
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
chunks = [
    p for p in (admin / "_next" / "static" / "chunks").glob("*.js")
    if MARK in p.read_text(encoding="utf-8", errors="replace")
]
if len(chunks) != 1:
    raise SystemExit(f"knowledge-graph 产物定位失败：命中 {len(chunks)} 个 chunk")
src = chunks[0].read_text(encoding="utf-8", errors="replace")
print("chunk:", chunks[0].name)

fails = []
for dead in (".type.toLowerCase", ".type!=="):
    n = src.count(dead)
    print(f"drift residue {dead!r} = {n}")
    if n:
        fails.append(dead)
n_et = src.count("entity_type")
print(f"kept entity_type = {n_et}")
if n_et < 10:
    fails.append("entity_type<10")
n_req = src.count("source_entity_id")
print(f"kept source_entity_id (request contract) = {n_req}")
if n_req < 1:
    fails.append("source_entity_id=0")
import re
n_type = len(re.findall(r"\.type\b", src))
print(f".type member accesses = {n_type} (expect 1 = rel.type badge)")
if n_type != 1:
    fails.append(f".type count={n_type}")
if "children:e.type}" not in src and "children:e.type}," not in src:
    # rel.type 徽章渲染形态（minify 变量名可能变，退化为 .type 恰 1 已锁）
    print("note: children:e.type 形态未见（.type 恰 1 已锁）")

if fails:
    raise SystemExit(f"REFUSE: 回读验证失败 {fails}")
print("deploy+回读验证 OK")
