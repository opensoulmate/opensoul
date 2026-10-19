#!/usr/bin/env python3
"""CD-24 live 真栈探针：两真源形真响应键形对账 + 部署 chunk 回读 + 取证清痕。

零假设：不写死键值，逐键与真源产出键集对账（结果元素=latency 嵌套形、
latest 元素=benchmarks 行平铺形）；探针自建运行按 run_id 精确清痕（绝不全表删）。
"""
import json
import sqlite3
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8090"
DB = Path.home() / ".opensoul" / "benchmark.db"
CHUNK = "/admin/_next/static/chunks/3hh371a48sej4.js"

RESULT_ELEMENT_KEYS = {
    "organ", "label", "iterations", "success", "errors", "latency",
    "throughput_rps", "total_duration_ms", "timestamp",
}
LATENCY_KEYS = {"min_ms", "max_ms", "avg_ms", "p50_ms", "p95_ms", "p99_ms"}
LATEST_ROW_KEYS = {
    "id", "run_id", "organ", "label", "iterations", "success", "errors",
    "min_ms", "max_ms", "avg_ms", "p50_ms", "p95_ms", "p99_ms", "rps",
    "total_ms", "timestamp",
}

checks = []


def ck(name, ok, detail=""):
    checks.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (" | " + detail if detail else ""))


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode()), r.status


def post(path):
    req = urllib.request.Request(BASE + path, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode()), r.status


# 0) 服务在场
_, st = get("/api/health")
ck("health 200", st == 200)

# 1) 运行真基准（1 次迭代轻负载）→ 结果元素 latency 嵌套形
run, st = post("/api/benchmark/quick/soul?iterations=1")
ck("quick 200", st == 200)
run_id = run.get("run_id", "")
ck("run_id 在场", bool(run_id), run_id)
elems = run.get("results", [])
ck("results 非空", len(elems) == 1)
e = elems[0]
ck("元素键集恰真源 9 键", set(e.keys()) == RESULT_ELEMENT_KEYS, str(sorted(e.keys())))
ck("latency 嵌套恰 6 键", set(e.get("latency", {}).keys()) == LATENCY_KEYS)
ck("元素无平铺 avg_ms/success_rate", "avg_ms" not in e and "success_rate" not in e)
ck("success 计数在场", isinstance(e.get("success"), int))

# 2) latest 元素=benchmarks 行平铺形
latest, st = get("/api/benchmark/latest")
ck("latest 200", st == 200)
rows = latest.get("latest", {})
ck("latest 含 soul 行", "soul" in rows)
row = rows.get("soul", {})
ck("latest 行键集恰真源 15 键", set(row.keys()) == LATEST_ROW_KEYS, str(sorted(row.keys())))
ck("latest 平铺 avg_ms 在场无 success_rate", "avg_ms" in row and "success_rate" not in row)

# 3) 部署 chunk 回读（live 真服务静态面）
with urllib.request.urlopen(BASE + CHUNK, timeout=30) as r:
    body = r.read().decode("utf-8", "ignore")
    ck("chunk 200", r.status == 200)
ck("chunk 旧漂移串 success_rate 残留=0", body.count("success_rate") == 0)
ck("chunk 旧漂移串 total_time_ms 残留=0", body.count("total_time_ms") == 0)
ck("chunk 真源 .latency 消费在场", body.count(".latency") >= 1)
ck("chunk 成功率锚在场", body.count("成功率") >= 1)

# 4) 取证清痕：只删探针 run_id 行（绝不全表删，防误删真实历史）
conn = sqlite3.connect(str(DB))
cur = conn.cursor()
cur.execute("DELETE FROM benchmarks WHERE run_id=?", (run_id,))
n_b = cur.rowcount
cur.execute("DELETE FROM benchmark_runs WHERE run_id=?", (run_id,))
n_r = cur.rowcount
conn.commit()
left = conn.execute("SELECT COUNT(*) FROM benchmarks WHERE run_id=?", (run_id,)).fetchone()[0]
conn.close()
ck("探针行精确清痕", left == 0 and n_b == 1 and n_r == 1, f"benchmarks del={n_b} runs del={n_r}")

failed = [c for c in checks if not c[1]]
print(f"\n== {len(checks) - len(failed)}/{len(checks)} PASS ==")
if failed:
    raise SystemExit("PROBE FAIL: " + ", ".join(c[0] for c in failed))
