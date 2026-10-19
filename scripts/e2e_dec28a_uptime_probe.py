#!/usr/bin/env python3
"""DEC-㉘a-① live 探针（只读零写入，TR-② 纪律：无事件行面/资源行面副作用）。

验证（httpx/urllib 真栈打 :8090 真服务）：
- P1 /api/system/overview 真源补现：uptime_seconds 数值型、0≤值、与两次调用单调不减；
- P2 /api/health 零变化：恰 6 键、恒不产出 uptime（uptime_check_ms 禁冒充）；
- P3 /api/system/quick 零变化：恰 4 键；
- P4 /metrics 单一源同源对账：opensoul_uptime_seconds ≈ overview.uptime_seconds；
- P5 部署 chunk 回读：uptime_seconds 真消费 + 「运行时间」在场 + ㉘b 死面零残留。
"""
import json
import re
import sys
import urllib.request

BASE = "http://localhost:8090"
ok_count = 0
fail_count = 0


def check(cond, label):
    global ok_count, fail_count
    if cond:
        ok_count += 1
        print(f"  PASS {label}")
    else:
        fail_count += 1
        print(f"  FAIL {label}")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return r.status, r.read().decode("utf-8", "ignore")


print("== P1 overview 真源补现 ==")
s, body = get("/api/system/overview")
ov1 = json.loads(body)
s, body = get("/api/system/overview")
ov2 = json.loads(body)
u1, u2 = ov1.get("uptime_seconds"), ov2.get("uptime_seconds")
check(s == 200, "overview 200")
check(isinstance(u1, (int, float)), f"uptime_seconds 数值型 ({u1!r})")
check(u1 is not None and u1 >= 0, "uptime_seconds >= 0")
check(u2 >= u1, f"两次调用单调不减 ({u1} -> {u2})")
check("timestamp" in ov1 and "organs" in ov1, "既有键零回归")

print("== P2 health 零变化 ==")
s, body = get("/api/health")
h = json.loads(body)
check(set(h) == {"component", "database", "knowledge_entries", "status", "uptime_check_ms", "version"},
      f"health 恰 6 键 ({sorted(h)})")
check("uptime" not in h, "health 零 uptime（uptime_check_ms 禁冒充）")

print("== P3 quick 零变化 ==")
s, body = get("/api/system/quick")
q = json.loads(body)
check(set(q) == {"status", "db", "version", "timestamp"}, f"quick 恰 4 键 ({sorted(q)})")

print("== P4 /metrics 单一源同源对账 ==")
s, body = get("/metrics")
m = re.search(r"opensoul_uptime_seconds ([0-9.]+)", body)
check(bool(m), "opensoul_uptime_seconds 在场")
if m and isinstance(u1, (int, float)):
    mu = float(m.group(1))
    check(abs(mu - u1) < 5.0, f"同源对账 metrics={mu} vs overview={u1}")

print("== P5 部署 chunk 回读 ==")
s, body = get("/static/admin/_next/static/chunks/1t8humfeepw_g.js")
check(s == 200, "system chunk 200")
check("uptime_seconds" in body, "uptime_seconds 真消费在场")
check("运行时间" in body, "「运行时间」行在场")
check("运行时信息" in body, "「运行时信息」面板在场")
for dead in ("活跃Agent", "活跃 Agent", "近期会话"):
    check(dead not in body, f"㉘b 死面零残留: {dead}")

print(f"\nRESULT: {ok_count} pass / {fail_count} fail")
sys.exit(1 if fail_count else 0)
