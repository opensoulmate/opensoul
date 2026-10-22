#!/usr/bin/env python3
"""RV-①b-⑩ gland provider 注册配置持久化 — live e2e 探针（真 :8090 + 真重启）。

三阶段（自检式：前置条件不满足即如实报 PRE-CONDITION VIOLATED，禁假证据）：
  --pre-register   服务仍跑旧码时：POST 注册 → 可见 → data/gland_providers.db
                   零落盘口（GAP 半证）。不做重启（避免撞并发测试/车道）。
  --pre-verify     任意时点重启后：--pre-register 注册的名字必须已丢（GAP 另半证
                   ——旧码零落盘口，重启后无论跑什么码行都不存在）。
  --post           新码循环：POST（带钥）→ 行回读（无钥材料）→ PATCH → 重启 →
                   配置在/钥不随行 → 同名 409 → DELETE → 删行 → 重启 → 不复活 +
                   settings 补位照常（bootstrap 旗标）→ 清痕 remaining=0。

用法：.venv/bin/python scripts/e2e_gland_provider_persist.py --pre-register
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8090"
DB = os.path.expanduser("~/opensoul/data/gland_providers.db")
PRE_NAME = "rv1b10-pre"
POST_NAME = "rv1b10-post"
API_KEY = "sk-rv1b10-secret-e2e"

ok_count = 0
fail_count = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok_count, fail_count
    if cond:
        ok_count += 1
        print(f"  ok  {label}")
    else:
        fail_count += 1
        print(f"  FAIL {label} {detail}")


def req(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def provider_names() -> list[str]:
    _, data = req("GET", "/api/gland/providers")
    return [p["name"] for p in data.get("providers", [])]


def provider_row(name: str) -> dict | None:
    if not os.path.exists(DB):
        return None
    conn = sqlite3.connect(DB)
    try:
        cur = conn.execute(
            "SELECT name, base_url, models, enabled, priority FROM gland_providers WHERE name=?",
            (name,),
        )
        r = cur.fetchone()
    finally:
        conn.close()
    if r is None:
        return None
    return {
        "name": r[0],
        "base_url": r[1],
        "models": json.loads(r[2]) if r[2] else {},
        "enabled": bool(r[3]),
        "priority": int(r[4]),
    }


def rows_count() -> int:
    if not os.path.exists(DB):
        return 0
    conn = sqlite3.connect(DB)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM gland_providers").fetchone()[0])
    finally:
        conn.close()


def restart_service() -> None:
    print("  ..  systemctl --user restart opensoul")
    subprocess.run(["systemctl", "--user", "restart", "opensoul"], check=False)
    for _ in range(60):
        time.sleep(1)
        try:
            status, _ = req("GET", "/api/gland/health")
            if status == 200:
                return
        except Exception:
            pass
    raise SystemExit("service did not come back after restart")


def wait_health() -> None:
    for _ in range(30):
        try:
            status, _ = req("GET", "/api/gland/health")
            if status == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    raise SystemExit("service not healthy")


def phase_pre_register() -> None:
    print("[pre-register] 旧码 GAP 半证：注册→可见→零落盘口")
    wait_health()
    names = provider_names()
    if PRE_NAME in names:
        print("PRE-CONDITION VIOLATED: pre 名已存在（上一轮残留或服务已跑新码）")
        sys.exit(2)
    status, _ = req(
        "POST",
        "/api/gland/providers",
        {"name": PRE_NAME, "base_url": "http://rv1b10-pre.example/v1", "models": {"chat": "m"}, "priority": 99},
    )
    check("POST 注册 200", status == 200, f"got {status}")
    check("GET 可见", PRE_NAME in provider_names())
    check(
        "data/gland_providers.db 零落盘口（旧码不写盘）",
        not os.path.exists(DB),
        f"db exists={os.path.exists(DB)} rows={rows_count()}",
    )
    print(f"pre-register done: ok={ok_count} fail={fail_count}")


def phase_pre_verify() -> None:
    print("[pre-verify] 重启后 pre 名已丢（GAP 另半证）")
    wait_health()
    check("GET 列表无 pre 名（重启即丢=修前 GAP）", PRE_NAME not in provider_names(), f"names={provider_names()}")
    print(f"pre-verify done: ok={ok_count} fail={fail_count}")


def phase_post() -> None:
    print("[post] 新码循环：写穿→回读→重启不丢→删行不复活→清痕")
    wait_health()
    # 0) 补位三件套先行（重启后 bootstrap 旗标语义）：openai 必在
    check("settings 补位 openai 在（bootstrap 旗标）", "openai" in provider_names(), f"names={provider_names()}")
    # 1) POST 带钥注册 → 行回读（钥材料绝不落盘）
    status, _ = req(
        "POST",
        "/api/gland/providers",
        {
            "name": POST_NAME,
            "base_url": "http://rv1b10-post.example/v1",
            "models": {"chat": "m1", "embedding": "e1"},
            "priority": 2,
            "api_key": API_KEY,
        },
    )
    check("POST 注册 200", status == 200, f"got {status}")
    row = provider_row(POST_NAME)
    check("行回读逐键", row == {"name": POST_NAME, "base_url": "http://rv1b10-post.example/v1", "models": {"chat": "m1", "embedding": "e1"}, "enabled": True, "priority": 2}, f"row={row}")
    raw = open(DB, "rb").read() if os.path.exists(DB) else b""
    check("钥材料不落盘", API_KEY.encode() not in raw)
    # 2) PATCH → 行随更
    status, _ = req("PATCH", f"/api/gland/providers/{POST_NAME}", {"priority": 7, "enabled": False})
    check("PATCH 200", status == 200, f"got {status}")
    row = provider_row(POST_NAME)
    check("PATCH 行随更（priority=7 enabled=False）", row is not None and row["priority"] == 7 and row["enabled"] is False, f"row={row}")
    # 3) 重启 → 配置在、钥不随行
    restart_service()
    check("重启后注册配置不丢", POST_NAME in provider_names(), f"names={provider_names()}")
    row = provider_row(POST_NAME)
    check("重启后行=PATCH 后当前态", row is not None and row["priority"] == 7 and row["enabled"] is False, f"row={row}")
    _, data = req("GET", "/api/gland/keys")
    keys = data.get("keys", {}).get(POST_NAME, [])
    check("钥不随行（诚实边界：钥面另件 RV-①b-③）", keys == [], f"keys={keys}")
    check("补位 openai 重启后仍在（外部行不吞补位）", "openai" in provider_names(), f"names={provider_names()}")
    # 4) 同名再注册 = 409（回载行进冲突判定）
    status, _ = req("POST", "/api/gland/providers", {"name": POST_NAME, "base_url": "http://y", "models": {}})
    check("同名再注册 409", status == 409, f"got {status}")
    # 5) DELETE → 删行 → 重启 → 不复活
    status, _ = req("DELETE", f"/api/gland/providers/{POST_NAME}")
    check("DELETE 200", status == 200, f"got {status}")
    check("删行后 rows 归零（该名行删）", provider_row(POST_NAME) is None, f"count={rows_count()}")
    restart_service()
    check("删行重启不复活", POST_NAME not in provider_names(), f"names={provider_names()}")
    # 6) 清痕：pre 名（若仍残留）+ rows 对账
    if PRE_NAME in provider_names():
        req("DELETE", f"/api/gland/providers/{PRE_NAME}")
    check("清痕 remaining=0", provider_row(POST_NAME) is None and provider_row(PRE_NAME) is None, f"count={rows_count()}")
    print(f"post done: ok={ok_count} fail={fail_count}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--pre-register":
        phase_pre_register()
    elif mode == "--pre-verify":
        phase_pre_verify()
    elif mode == "--post":
        phase_post()
    else:
        print(__doc__)
        sys.exit(1)
    sys.exit(0 if fail_count == 0 else 1)
