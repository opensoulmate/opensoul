#!/usr/bin/env python3
"""DEC-㉗a-② 部署脚本：admin-ui/out → src/static/admin，dry-run 对账先行（非 _next 删除=0 才真部署）。

回读验证按 logs 页专属产物定位（含「SSE客户端」串的产物文件），判定只认属性名与
字符串字面量（minify 改名不影响）：
- 修前回落形残留=0：recent_count 后紧跟 ||0 / )||0（fabricate 0 形）；
- 新形在场：recent_count 消费 + typeof number 判法 + '—' 占位字面量 + 「最近事件」卡文本；
- 保留面在场：总事件卡回落形（本地真计数）/错误·告警卡/活跃器官/总事件。
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

# 回读：logs 页专属产物（含「SSE客户端」）内新形在场、修前形残留=0
admin = pathlib.Path(ROOT) / "src" / "static" / "admin"
MARK = "SSE\u5ba2\u6237\u7aef"
logs_files = []
for p in admin.rglob("*"):
    if p.is_file() and p.suffix in (".js", ".html", ".txt"):
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if MARK in t:
            logs_files.append((p, t))
print("logs artifacts:", [str(p.relative_to(admin)) for p, _ in logs_files])
if not logs_files:
    raise SystemExit("REFUSE: 未定位到 logs 页产物，回读不可信")

ok = True
# 修前回落形残留=0（minify 后 recent_count 访问紧跟 ||0）
old_pat = re.compile(r"recent_count\s*\)?\s*\|\|\s*0")
for p, t in logs_files:
    hits = old_pat.findall(t)
    print(f"old fallback residue in {p.name}: {len(hits)}")
    if hits:
        ok = False
# 新形在场：typeof number 判法 + '—' 占位 + recent_count 消费 + 卡文本
new_checks = [
    ("recent_count", "recent_count"),
    ('"number"', "typeof number 判法(minify 形)"),
    ("\u2014", "'—' 占位"),
    ("\u6700\u8fd1\u4e8b\u4ef6", "\u6700\u8fd1\u4e8b\u4ef6 \u5361\u6587\u672c"),
]
for s, label in new_checks:
    n = sum(t.count(s) for _, t in logs_files)
    print(f"new-form '{label}': {n}")
    if not n:
        ok = False
# 保留面在场：问题类卡/活跃器官/总事件（勘误如实记：总事件卡回落形
# summary?.total_events || displayLogs.length 的接收者是局部标识符，minify 改名后
# bundle 层不可区分（CD-32「minify 接收者归一不可区分」同族工具边界）——
# 该项范围纪律由 tests/test_dec27a_fallback_form.py 源码面锁死，不在 bundle 层判）
kept = ["\u95ee\u9898\u7c7b\u4e8b\u4ef6", "\u6d3b\u8dc3\u5668\u5b98", "\u603b\u4e8b\u4ef6"]
for s in kept:
    n = sum(t.count(s) for _, t in logs_files)
    print(f"kept '{s}': {n}")
    if not n:
        ok = False

# 逐 recent_count 出现点上下文回读（人工可核）
for p, t in logs_files:
    for m in re.finditer(r"recent_count", t):
        ctx = t[max(0, m.start() - 60):m.end() + 80].replace("\n", " ")
        print(f"CTX {p.name}: ...{ctx}...")

print("RESULT:", "PASS" if ok else "FAIL")
raise SystemExit(0 if ok else 1)
