#!/usr/bin/env python3
"""CD-⑥ 部署脚本：admin-ui/out → src/static/admin，dry-run 对账先行（非 _next 删除=0 才真部署）。

回读验证按 traces 页专属 chunk 定位（含「暂无事件数据」串的产物文件），避免其他
页面合法同名键（created_at/agent_id 等广泛使用）干扰残留判定。产物是 minify 后
的 chunk：变量名会被改名，残留/在场判定只认属性名与字符串字面量。
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

# 回读：traces 页专属产物（含「暂无事件数据」）内旧漂移串残留=0、新真源键真在场
admin = pathlib.Path(ROOT) / "src" / "static" / "admin"
MARK = "暂无事件数据"
traces_files = []
for p in admin.rglob("*"):
    if p.is_file() and p.suffix in (".js", ".html", ".txt"):
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if MARK in t:
            traces_files.append((p, t))
print("traces artifacts:", [str(p.relative_to(admin)) for p, _ in traces_files])
if not traces_files:
    raise SystemExit("REFUSE: 未定位到 traces 页产物，回读不可信")

old_strings = ["event_count", "first_event", "last_event"]
# session_id/timestamp 属性名须按消费形判定（.session_id / .timestamp 属性访问），
# 字面串在其他页合法在场——traces 专属 chunk 内按属性访问残留判定。
old_props = [".session_id", ".timestamp"]
new_strings = ["total_events", "ended_at", "created_at", "/events"]
ok = True
for s in old_strings + old_props:
    n = sum(t.count(s) for _, t in traces_files)
    print(f"old drift '{s}' residual in traces artifacts: {n}")
    if n:
        ok = False
for s in new_strings:
    n = sum(t.count(s) for _, t in traces_files)
    print(f"new true key '{s}' in traces artifacts: {n}")
    if not n:
        ok = False
print("VERIFY:", "OK" if ok else "FAIL")
if not ok:
    raise SystemExit(1)
