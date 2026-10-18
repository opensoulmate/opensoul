#!/usr/bin/env python3
"""CD-⑳ 部署脚本：admin-ui/out → src/static/admin，dry-run 对账先行（非 _next 删除=0 才真部署）。

回读验证按 vision 页专属产物定位（含「根节点JSON」串的产物文件），残留/在场判定只认
字符串字面量与成员属性名（minify 改名不影响属性名）：
- 旧漂移残留=0：elapsed_ms||0（修前假 0 回落遮蔽形，minify 后无空格形态）；
- 保留面在场：elapsed_ms / elapsed_seconds（兄弟真源键双收编）、根节点JSON（页面锚）。
"""
import pathlib
import subprocess

ROOT = "/home/climbing/opensoul"
P2 = chr(124) * 2
OLD_FAKE_ZERO = "elapsed_ms" + P2 + "0"

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

# 回读：vision 页专属产物（含「根节点JSON」）内旧漂移串残留=0、保留面在场
admin = pathlib.Path(ROOT) / "src" / "static" / "admin"
MARK = "根节点JSON"
vis_files = []
for p in admin.rglob("*"):
    if p.is_file() and p.suffix in (".js", ".html", ".txt"):
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if MARK in t:
            vis_files.append((p, t))
print("vision artifacts:", [str(p.relative_to(admin)) for p, _ in vis_files])
if not vis_files:
    raise SystemExit("REFUSE: 未定位到 vision 页产物，回读不可信")

ok = True
for s in (OLD_FAKE_ZERO,):
    n = sum(t.count(s) for _, t in vis_files)
    print(f"old drift {s!r} residual in vision artifacts: {n}")
    if n:
        ok = False
for s in ("elapsed_ms", "elapsed_seconds", "根节点JSON"):
    n = sum(t.count(s) for _, t in vis_files)
    print(f"kept {s!r} in vision artifacts: {n}")
    if not n:
        ok = False
print("VERIFY:", "OK" if ok else "FAIL")
if not ok:
    raise SystemExit(1)
