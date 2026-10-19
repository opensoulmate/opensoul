#!/usr/bin/env python3
"""CD-㉙ 部署脚本：admin-ui/out → src/static/admin，dry-run 对账先行（非 _next 删除=0 才真部署）。

回读验证按 learn 页专属产物定位（含「课程总数」串的产物文件），残留/在场判定只认
字符串字面量与成员属性名（minify 改局部变量名不影响属性名）：
- 旧死显示残留=0：total_quizzes（死声明/消费键，属性名 survive minify）、
  「测验题」统计卡标签独立串（只许出现在「该章节暂无测验题」/「测验题目」两个
  章节测验独立面里，禁独立标签=死卡复发）；
- 保留面在场：课程总数/总章节/已完成 三卡（真源键消费）、md:grid-cols-3（grid 收列）。
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

# 回读：learn 页专属产物（含「课程总数」）内死显示残留=0、保留面在场
admin = pathlib.Path(ROOT) / "src" / "static" / "admin"
MARK = "课程总数"
hits = []
for p in admin.rglob("*.js"):
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if MARK in t:
        hits.append((p, t))
print("learn chunk files:", [str(p.relative_to(admin)) for p, _ in hits])
assert hits, "未定位到 learn 页产物（页面锚缺失）"
fail = False


def standalone_quiz_label(t: str) -> int:
    """「测验题」独立标签出现数（只许在「该章节暂无测验题」/「测验题目」内）。"""
    n = 0
    i = 0
    while True:
        i = t.find("测验题", i)
        if i < 0:
            return n
        ctx_prev, ctx_next = t[i - 5 : i], t[i + 3 : i + 4]
        in_empty = ctx_prev == "该章节暂无"
        in_hint = ctx_next == "目"
        if not (in_empty or in_hint):
            n += 1
        i += 3


for p, t in hits:
    n = t.count("total_quizzes")
    print(f"  {p.name}: dead key 'total_quizzes' count={n}")
    if n:
        fail = True
    n = standalone_quiz_label(t)
    print(f"  {p.name}: standalone '测验题' label count={n}")
    if n:
        fail = True
    for k in ("课程总数", "总章节", "已完成", "md:grid-cols-3"):
        c = t.count(k)
        print(f"  {p.name}: kept {k!r} count={c}")
        if not c:
            fail = True
    n = t.count("md:grid-cols-4")
    print(f"  {p.name}: stale 'md:grid-cols-4' count={n}")
    if n:
        fail = True

if fail:
    raise SystemExit("REFUSE: 回读验证不过（残留/保留面缺失）")
print("CD-29 deploy + readback OK")
