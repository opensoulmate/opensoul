#!/usr/bin/env python3
"""H1-②b-⑭ 异步面 fallback 链接线 live e2e 探针（--pre 修前 / --post 修后）。

验证：POST /api/download/download（异步）auto 路径 ack 契约——
修前 plugin=择优单引擎 id、无 fallback_chain 键（链不进异步）；
修后 plugin 如实 "auto"（真赢家未定禁自报）+ fallback_chain 链序可观测，
显式 plugin_id/"native" 5 键契约不变。真实 HTTP 打 live :8090。
"""

import argparse
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8090"


def _req(method: str, path: str, payload: dict | None = None, timeout: float = 20.0):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body) if body else {}
        except json.JSONDecodeError:
            return e.code, {"raw": body}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre", action="store_true")
    ap.add_argument("--post", action="store_true")
    args = ap.parse_args()
    if not (args.pre or args.post):
        print("need --pre or --post")
        return 2

    results: list[tuple[bool, str]] = []

    def check(ok: bool, label: str):
        results.append((ok, label))
        print(f"{'PASS' if ok else 'FAIL'}  {label}")

    tmp = Path(tempfile.mkdtemp(prefix="e2e_async_chain_"))

    st, health = _req("GET", "/api/download/health")
    check(st == 200 and health.get("status") == "ok", f"health 200 ok ({st})")

    st, rows = _req("GET", "/api/download/plugins")
    plugin_rows = rows.get("plugins", []) if isinstance(rows, dict) else []
    known = {"native"} | {
        r["id"] for r in plugin_rows if isinstance(r, dict) and "id" in r
    }
    check(st == 200 and isinstance(plugin_rows, list), f"plugins listing 200 ({len(known) - 1} plugins)")

    # ── 异步 auto ack 契约（本拍对象） ──────────────────────────────
    dest = str(tmp / "dead.bin")
    st, data = _req(
        "POST",
        "/api/download/download",
        {"url": "http://127.0.0.1:1/x", "dest": dest, "resume": True},
    )
    check(st == 200, f"async auto HTTP 200 ({st})")
    if st == 200:
        plugin_key = data.get("plugin")
        chain = data.get("fallback_chain")
        if args.pre:
            check(
                plugin_key in known and plugin_key != "auto",
                f"--pre 修前契约：plugin=引擎 id 非 'auto'（{plugin_key}）",
            )
            check(chain is None, f"--pre 修前无 fallback_chain 键（{chain}）")
        else:
            check(plugin_key == "auto", f"--post plugin 如实 'auto'（{plugin_key}）")
            check(
                isinstance(chain, list) and all(isinstance(x, str) for x in chain),
                f"--post fallback_chain 链序可观测（{chain}）",
            )
            check(
                set(data) == {"task_id", "dest", "url", "status", "plugin", "fallback_chain"},
                f"--post ack 恰 6 键（{sorted(data)}）",
            )
        check(data.get("status") == "downloading", f"ack status=downloading（{data.get('status')}）")
        tid = data.get("task_id")
        removed = False
        for _ in range(20):
            st3, _ = _req("DELETE", f"/api/download/tasks/{tid}")
            if st3 in (200, 404):
                removed = True
                break
            time.sleep(0.25)
        check(removed, f"async 任务清理（DELETE /tasks/{tid}）")

    # ── 显式 native 5 键契约不变面 ─────────────────────────────────
    dest2 = str(tmp / "dead2.bin")
    st, data2 = _req(
        "POST",
        "/api/download/download",
        {"url": "http://127.0.0.1:1/x", "dest": dest2, "plugin_id": "native"},
    )
    if st == 200:
        check(data2.get("plugin") == "native", f"显式 native 契约不变（{data2.get('plugin')}）")
        check(
            set(data2) == {"task_id", "dest", "url", "status", "plugin"},
            f"显式面恰 5 键零变化（{sorted(data2)}）",
        )
        tid2 = data2.get("task_id")
        for _ in range(20):
            st4, _ = _req("DELETE", f"/api/download/tasks/{tid2}")
            if st4 in (200, 404):
                break
            time.sleep(0.25)
    else:
        check(False, f"显式 native 200（got {st}）")

    # ── 清痕 ──────────────────────────────────────────────────────
    for f in tmp.glob("*"):
        try:
            f.unlink()
        except OSError:
            pass
    try:
        tmp.rmdir()
    except OSError:
        pass

    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    print(f"\n== {passed}/{total} passed ==")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
