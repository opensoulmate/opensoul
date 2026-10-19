#!/usr/bin/env python3
"""CD-㉙ live 探针：learn 测验统计卡死显示收口（真源+部署产物双面回读）。

检查面（全部经 :8090 真 HTTP）：
1. GET /api/learn/stats 真源键集恒不产出 total_quizzes（真源零改动锁）；
2. 部署 chunk（learn 页产物，含「课程总数」）HTTP 回读：total_quizzes=0、
   「测验题」独立标签=0（只许在章节测验独立面内）、保留面在场、md:grid-cols-4=0；
3. GET /admin/learn 净 URL 200（CD-C3 尾 AdminStaticFiles 回退，页面可达）。
"""
import re
import sys
import urllib.request

BASE = "http://127.0.0.1:8090"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS" if ok else "FAIL"), name, detail)


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return r.status, r.read().decode("utf-8", "replace")


# 1. 真源键集
status, body = get("/api/learn/stats")
import json

stats = json.loads(body)
keys = set(stats.keys()) if isinstance(stats, dict) else set()
check("source-no-total-quizzes", "total_quizzes" not in keys, f"keys={sorted(keys)}")
check(
    "source-has-core-keys",
    {"total_courses", "total_chapters", "completed_chapters"} <= keys,
    f"keys={sorted(keys)}",
)

# 2. 部署产物回读
status, index_html = get("/admin/learn")
check("admin-learn-200", status == 200, f"status={status}")
chunk_paths = sorted(set(re.findall(r"/_next/static/chunks/[A-Za-z0-9_\-]+\.js", index_html)))
print("page chunks:", len(chunk_paths))
learn_chunks = []
fetched = 0
for cp in chunk_paths:
    # 部署产物挂载面=/static/admin（cd28 先例同款完整路径）；index_html 里的
    # /_next/... 是客户端路由相对形，HTTP 回读须补 /static/admin 前缀
    fetch_path = "/static/admin" + cp
    try:
        st, t = get(fetch_path)
    except Exception as e:  # 个别 preload 路径不存在容缺跳过（探针自纠非真断点）
        print("skip chunk:", fetch_path, type(e).__name__)
        continue
    fetched += 1
    if st == 200 and "课程总数" in t:
        learn_chunks.append((cp, t))
check("chunks-fetchable", fetched >= 5, f"fetched={fetched}")
check("learn-chunk-located", len(learn_chunks) >= 1, f"{[c for c, _ in learn_chunks]}")


def standalone_quiz_label(t):
    n = 0
    i = 0
    while True:
        i = t.find("测验题", i)
        if i < 0:
            return n
        if not (t[i - 5 : i] == "该章节暂无" or t[i + 3 : i + 4] == "目"):
            n += 1
        i += 3


for cp, t in learn_chunks:
    check(f"chunk-total-quizzes-0:{cp}", t.count("total_quizzes") == 0, f"count={t.count('total_quizzes')}")
    check(f"chunk-standalone-label-0:{cp}", standalone_quiz_label(t) == 0, f"count={standalone_quiz_label(t)}")
    for k in ("课程总数", "总章节", "md:grid-cols-3"):
        check(f"chunk-kept:{k}:{cp}", k in t)
    check(f"chunk-no-cols4:{cp}", "md:grid-cols-4" not in t)

n_fail = sum(1 for _, ok in results if not ok)
print(f"TOTAL {len(results) - n_fail}/{len(results)}")
sys.exit(1 if n_fail else 0)
