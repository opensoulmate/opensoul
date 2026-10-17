#!/usr/bin/env python3
"""H1-②b-⑬ manager.download fallback 链 live e2e 探针。

--pre  跑在旧码上（/download/sync auto=单引擎直跑）：全败 error=单插件错文案，
       无 "All plugins failed" 链耗尽面。
--post 跑在新码上（auto=跨插件 fallback 链）：全败 error=链耗尽文案带试过清单。

数据驱动：真机可用插件面决定期望（有可续传插件→断言链耗尽；没有→回落 native
契约，断言不硬造链文案）。取证后清痕。
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


def _req(method: str, path: str, payload: dict | None = None, timeout: float = 120.0):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
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

    tmp = Path(tempfile.mkdtemp(prefix="e2e_fbchain_"))

    st, health = _req("GET", "/api/download/health")
    check(st == 200 and health.get("status") == "ok", f"health 200 ok ({st})")

    st, plugins = _req("GET", "/api/download/plugins")
    raw_rows = plugins.get("plugins", []) if st == 200 else []
    rows: list[dict] = [r for r in raw_rows if isinstance(r, dict)]
    known = {p["id"] for p in rows if p.get("status") == "available"} | {"native"}
    resume_capable = [
        p["id"] for p in rows if p.get("status") == "available" and p.get("supports_resume")
    ]
    check(st == 200 and isinstance(rows, list), f"plugins listing 200 ({len(rows)} rows)")
    print(f"      available={sorted(known - {'native'})} resume_capable={resume_capable}")

    # ── auto 同步全败面（链语义区分面） ────────────────────────────
    dest = str(tmp / "dead.bin")
    st, data = _req(
        "POST",
        "/api/download/download/sync",
        {"url": "http://127.0.0.1:1/x", "dest": dest, "resume": True},
    )
    keys = {
        "status", "dest", "url", "total_bytes", "downloaded_bytes", "speed",
        "eta", "progress_pct", "plugin", "supports_resume", "error", "task_id",
    }
    check(st == 200, f"sync auto all-fail HTTP 200 ({st})")
    check(set(data.keys()) == keys, f"sync response key set 恰 12 键 ({sorted(data.keys())})")
    err = str(data.get("error") or "")
    check(data.get("status") == "error", f"all-fail status=error 禁谎报 ok ({data.get('status')})")

    if resume_capable:
        tried_visible = any(pid in err for pid in resume_capable)
        if args.pre:
            check(
                "All plugins failed" not in err,
                f"--pre 旧码单引擎文案无链耗尽面（error={err[:90]!r}）",
            )
        else:
            check("All plugins failed" in err, f"--post 链耗尽文案在场（error={err[:90]!r}）")
            check(
                tried_visible,
                f"--post 试过清单可观测（error 命中 {resume_capable} 之一）",
            )
        check(data.get("plugin") in known, f"plugin 键不 fabricate（{data.get('plugin')}）")
    else:
        check(
            data.get("plugin") == "native",
            f"无可续传插件→回落 native 契约（plugin={data.get('plugin')}）",
        )

    # ── 异步 auto 契约不变面（链不进异步=留档边界） ────────────────
    dest2 = str(tmp / "dead2.bin")
    st, data2 = _req(
        "POST",
        "/api/download/download",
        {"url": "http://127.0.0.1:1/x", "dest": dest2, "resume": True},
    )
    check(st == 200, f"async auto HTTP 200 ({st})")
    if st == 200:
        check(
            data2.get("plugin") in known,
            f"async plugin 键=真实引擎非 'auto'（{data2.get('plugin')}）",
        )
        tid = data2.get("task_id")
        removed = False
        for _ in range(20):
            st3, _ = _req("DELETE", f"/api/download/tasks/{tid}")
            if st3 in (200, 404):
                removed = True
                break
            time.sleep(0.25)
        check(removed, f"async 任务清理（DELETE /tasks/{tid}）")

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
