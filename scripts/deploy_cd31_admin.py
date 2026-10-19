#!/usr/bin/env python3
"""CD-31 部署脚本：admin-ui/out → src/static/admin，dry-run 对账先行（非 _next 删除=0 才真部署）。

回读验证按 connectors 页专属产物定位（含「监听服务」串的产物文件），残留/在场判定
只认字符串字面量与成员属性名（minify 改局部变量名不影响属性名）：
- 旧漂移残留=0（只认漂移专属形）：.host/.port 成员访问（connectors 页唯一消费点）、
  .name||.host 回落形（修前服务名双回落全 undefined）；
- 保留面在场：.process_name/.local_address/.local_port（真源键收编消费）。
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

# 回读：connectors 页专属产物（含「监听服务」）内旧漂移串残留=0、真源消费在场
admin = pathlib.Path(ROOT) / "src" / "static" / "admin"
MARK = "监听服务"
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
    for k in (".host", ".port"):
        n = t.count(k)
        print(f"  {p.name}: old drift member {k!r} count={n}")
        if n:
            fail = True
    n = len(re.findall(r"\.name\|\|\w*\.host", t))
    print(f"  {p.name}: old drift fallback-form count={n}")
    if n:
        fail = True
    for k in (".process_name", ".local_address", ".local_port"):
        n = t.count(k)
        print(f"  {p.name}: true-source {k!r} count={n}")
        if n < 1:
            fail = True
if fail:
    raise SystemExit("READBACK FAIL: 残留/缺席")

# 全量产物扫旧漂移专属形残留（任何 chunk 出现即失败）。
# 工具边界如实记：.host/.port 成员访问在 Next 运行时/URL 解析器内部（hostname/port、
# URL 类 getHost）天然存在，全局面只认漂移专属回落形 .name||.host（修前服务名双回落，
# minify 后属性名存活）；.host/.port 零残留判定收在 connectors 页专属 chunk（上方，
# 页面源码 .host/.port 唯一消费点证据），修前修后同边界。
DRIFT_PATTERNS = (r"\.name\|\|\w*\.host",)
global_hits = []
for p in admin.rglob("*.js"):
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    for pat in DRIFT_PATTERNS:
        if re.search(pat, t):
            global_hits.append(f"{p.relative_to(admin)}:{pat}")
print("global drift residue:", global_hits)
if global_hits:
    raise SystemExit("READBACK FAIL: 全量产物仍有旧漂移残留")
print("readback OK")
