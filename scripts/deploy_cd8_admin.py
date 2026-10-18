#!/usr/bin/env python3
"""CD-⑧ 部署脚本：admin-ui/out → src/static/admin，dry-run 对账先行（非 _next 删除=0 才真部署）。

回读验证按 dedup 页专属 chunk 定位（含「知识去重引擎」串的产物文件），避免其他
页面合法同名键干扰残留判定。产物是 minify 后的 chunk：变量名会被改名，残留/在场
判定只认属性名与字符串字面量。
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

# 回读：dedup 页专属产物（含「知识去重引擎」）内旧漂移串残留=0、新真源键真在场
admin = pathlib.Path(ROOT) / "src" / "static" / "admin"
MARK = "知识去重引擎"
dedup_files = []
for p in admin.rglob("*"):
    if p.is_file() and p.suffix in (".js", ".html", ".txt"):
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if MARK in t:
            dedup_files.append((p, t))
print("dedup artifacts:", [str(p.relative_to(admin)) for p, _ in dedup_files])
if not dedup_files:
    raise SystemExit("REFUSE: 未定位到 dedup 页产物，回读不可信")

old_strings = ["已合并"]
old_props = [".merged"]
new_strings = ["扫描条目", ".duplicate_of", "重复，相似度"]
ok = True
for s in old_strings + old_props:
    n = sum(t.count(s) for _, t in dedup_files)
    print(f"old drift '{s}' residual in dedup artifacts: {n}")
    if n:
        ok = False
for s in new_strings:
    n = sum(t.count(s) for _, t in dedup_files)
    print(f"new true key '{s}' in dedup artifacts: {n}")
    if not n:
        ok = False
print("VERIFY:", "OK" if ok else "FAIL")
if not ok:
    raise SystemExit(1)
