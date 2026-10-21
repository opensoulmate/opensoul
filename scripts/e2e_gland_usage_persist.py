#!/usr/bin/env python3
"""RV-①b-① live e2e 探针：gland 用量 UI 真源重启不丢（修前/修后同探针双向硬证）。

步骤：①GET /api/gland/usage 基线 ②POST /api/gland/chat 真 LLM 小调用（真走
ModelRouter.chat→token_meter.record 写穿）③GET /api/gland/usage/recent 差分找出
探针行 ④systemctl --user restart opensoul ⑤重启后 /usage/recent 回读——探针行在
场=重启不丢（修前旧代码此处 GAP：行随内存清零消失）⑥按探针行精确四元组
(timestamp,model,prompt_tokens,completion_tokens) 清痕，报告 remaining。

用法：python3 scripts/e2e_gland_usage_persist.py [--no-restart] [--keep]
urllib 真栈零第三方依赖。输出 JSON 结果（逐步 ok + summary）。
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8090"
DB = "/home/climbing/opensoul/data/gland_usage.db"
RESULTS: list[dict] = []


def step(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"step": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'} | {name} | {detail}")
    return ok


def http_json(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(BASE + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data, timeout=120) as resp:
        return json.loads(resp.read().decode())


def wait_health(timeout_s: float = 90.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            http_json("GET", "/api/gland/health")
            return True
        except Exception:
            time.sleep(1.5)
    return False


def usage_rows(limit: int = 200) -> list[dict]:
    return http_json("GET", f"/api/gland/usage/recent?limit={limit}")["records"]


def main() -> int:
    no_restart = "--no-restart" in sys.argv
    keep = "--keep" in sys.argv

    wait_health() or step("health", False, "service not reachable") or sys.exit(1)
    step("health", True, "gland health 200")

    before = http_json("GET", "/api/gland/usage")
    rows_before = usage_rows()
    keys_before = {
        (r["timestamp"], r["model"], r["prompt_tokens"], r["completion_tokens"])
        for r in rows_before
    }

    # ② 真 LLM 小调用（真 record() 写穿面）
    chat_ok = True
    try:
        resp = http_json(
            "POST",
            "/api/gland/chat",
            {
                "messages": [{"role": "user", "content": "Reply with the single word: pong"}],
                "task": "chat",
                "max_tokens": 8,
            },
        )
    except urllib.error.HTTPError as exc:
        chat_ok = False
        resp = {"error": f"HTTP {exc.code}: {exc.read().decode()[:200]}"}
    step("chat real llm call", chat_ok, json.dumps(resp, ensure_ascii=False)[:200])

    after = http_json("GET", "/api/gland/usage")
    rows_after = usage_rows()
    new_rows = [
        r
        for r in rows_after
        if (r["timestamp"], r["model"], r["prompt_tokens"], r["completion_tokens"])
        not in keys_before
    ]
    probe_row = None
    for r in reversed(new_rows):  # 最新优先
        if r.get("task") == "chat":
            probe_row = r
            break
    step(
        "usage incremented + probe row recorded",
        after["call_count"] > before["call_count"] and probe_row is not None,
        f"call_count {before['call_count']}->{after['call_count']}, new_rows={len(new_rows)}, probe={json.dumps(probe_row, ensure_ascii=False)[:160] if probe_row else None}",
    )

    if no_restart:
        print(json.dumps({"results": RESULTS, "mode": "no-restart"}, ensure_ascii=False))
        return 0

    # ④ 重启
    subprocess.run(["systemctl", "--user", "restart", "opensoul"], check=False)
    up = wait_health()
    step("service back after restart", up, "")

    # ⑤ 重启回读
    rows_post = usage_rows()
    survived = probe_row is not None and any(
        r["timestamp"] == probe_row["timestamp"]
        and r["model"] == probe_row["model"]
        and r["prompt_tokens"] == probe_row["prompt_tokens"]
        and r["completion_tokens"] == probe_row["completion_tokens"]
        for r in rows_post
    )
    post = http_json("GET", "/api/gland/usage")
    step(
        "probe row survives restart",
        survived,
        f"post call_count={post['call_count']} rows={len(rows_post)}",
    )

    # ⑥ 清痕（探针行精确四元组）
    remaining = 0
    if probe_row is not None and not keep:
        conn = sqlite3.connect(DB)
        try:
            cur = conn.execute(
                "DELETE FROM gland_usage WHERE timestamp=? AND model=? AND prompt_tokens=? AND completion_tokens=?",
                (
                    probe_row["timestamp"],
                    probe_row["model"],
                    probe_row["prompt_tokens"],
                    probe_row["completion_tokens"],
                ),
            )
            conn.commit()
            remaining = conn.execute("SELECT COUNT(*) FROM gland_usage").fetchone()[0]
            step("cleanup probe row", True, f"deleted={cur.rowcount}, table remaining={remaining}")
        finally:
            conn.close()

    ok_all = all(r["ok"] for r in RESULTS)
    print(json.dumps({"results": RESULTS, "all_ok": ok_all}, ensure_ascii=False))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
