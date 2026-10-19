#!/usr/bin/env python3
"""DEC-㉖B2-① 部署脚本：admin-ui/out → src/static/admin，dry-run 对账先行（非 _next
删除=0 才真部署）。

回读验证按 gland 页专属产物定位（含「最近调用记录」串的产物文件），残留/在场判定
只认字符串字面量与成员属性名（minify 改局部变量名不影响成员属性名）：
- task 列恢复面在场：`.task` 成员访问（{r.task} minify 后成员属性名存活）；
- 旧漂移残留=0：`.input_tokens`/`.output_tokens` 成员访问（CD-⑤ 收编面，禁回潮）；
- 保留面在场：`prompt_tokens`/`completion_tokens` 成员访问 + 「最近调用记录」页面锚。
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

# 回读：gland 页专属产物（含「最近调用记录」）内旧漂移串残留=0、恢复面在场
admin = pathlib.Path(ROOT) / "src" / "static" / "admin"
MARK = "最近调用记录"
gland_files = []
for p in admin.rglob("*.js"):
    try:
        if MARK in p.read_text(encoding="utf-8"):
            gland_files.append(p)
    except UnicodeDecodeError:
        pass
print("gland chunk files:", [str(p.relative_to(admin)) for p in gland_files])
assert gland_files, "gland 页产物未定位（锚点「最近调用记录」缺失）"

blob = "\n".join(p.read_text(encoding="utf-8") for p in gland_files)

# 恢复面在场
assert ".task" in blob, "task 列恢复面（.task 成员访问）未进产物"
for keep in ("prompt_tokens", "completion_tokens"):
    assert keep in blob, f"保留面缺失: {keep}"

# 旧漂移残留=0
for bad in ("input_tokens", "output_tokens"):
    assert f".{bad}" not in blob, f"旧漂移键残留回潮: .{bad}"

print("readback ok: .task 在场、input/output_tokens 残留=0、保留面在场")
