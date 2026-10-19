#!/usr/bin/env python3
"""CD-㉚ pulse 平均漂移收编真源 live 真栈探针。

真值对账四面（禁假阳性）：
1. live 真源形状：/api/pulse/stats 嵌套 precision{avg_drift_ms,...} 无平铺键；
2. 真值非遮蔽：真 tick 产出非零 precision.avg_drift_ms，UI 卡表达式（page.tsx 真源码
   逐字提取经 node 真执行）吃 live 真响应渲染值 == 真源值逐值对账（修前 '—' 遮蔽）；
3. 时间戳单位：fmtTs 真源码吃 live 真 epoch 秒渲染 2026 非 1970；
4. 部署 bundle 回读：修前漂移串 0 残留 + 嵌套键真消费 + ×1000 单一源恰一处。
取证后清痕（探针信号删除，remaining=0）。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

BASE = "http://localhost:8090"
REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "pulse" / "page.tsx"
PROBE_NAME = "e2e-cd30-probe"

results: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    results.append((bool(cond), label))
    print(("PASS" if cond else "FAIL"), label)


def api(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read().decode()
        return r.status, (json.loads(raw) if raw else None)


def node(js: str) -> str:
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"node 失败: {r.stderr}"
    return r.stdout.strip()


def main() -> int:
    # ── 1. live 真源形状 ────────────────────────────────────────
    _, stats = api("GET", "/api/pulse/stats")
    check("precision" in stats and "avg_drift_ms" in stats["precision"], "真源 precision 嵌套键在场")
    check("avg_drift_ms" not in stats, "真源无平铺 avg_drift_ms")
    check(
        set(stats["precision"]) == {"avg_drift_ms", "max_drift_ms", "sample_size"},
        "precision 嵌套键集恰 3 键",
    )

    # ── 真数据：探针信号 + 两次 tick（非零漂移真值）─────────────
    _, sig = api(
        "POST",
        "/api/pulse/signals",
        {"name": PROBE_NAME, "signal_type": "interval", "interval_ms": 60000, "max_fires": 0},
    )
    sid = sig["signal_id"]
    try:
        api("POST", f"/api/pulse/signals/{sid}/tick")
        api("POST", f"/api/pulse/signals/{sid}/tick")
        _, lst = api("GET", "/api/pulse/signals")
        _, ticks = api("GET", "/api/pulse/ticks?limit=5")
        _, stats2 = api("GET", "/api/pulse/stats")
        row = next(s for s in lst["signals"] if s["signal_id"] == sid)
        tick = next(t for t in ticks["ticks"] if t["signal_id"] == sid)
        drift = stats2["precision"]["avg_drift_ms"]
        check(isinstance(drift, (int, float)) and drift > 0, f"live 非零漂移真值产出: {drift}")
        check(
            all(isinstance(row[k], (int, float)) for k in ("created_at", "last_fired_at", "next_fire_at")),
            "信号时间戳=epoch 秒 float 真源形",
        )
        check(isinstance(tick["timestamp"], (int, float)), "tick 时间戳=epoch 秒 float 真源形")

        # ── 2. UI 卡表达式真源码经 node 吃 live 真响应逐值对账 ──
        line = next(l for l in PAGE.read_text(encoding="utf-8").splitlines() if "avg_drift_ms.toFixed" in l)
        expr = line[line.index("{") + 1 : line.rindex("}")]
        expect = f"{drift:.1f}ms"
        out = node(
            "const render=(stats)=>(" + expr + ");"
            f"console.log(JSON.stringify([render({json.dumps(stats2)}), render({{precision:{{avg_drift_ms:0}}}}), render(null)]));"
        )
        got, zero, absent = json.loads(out)
        check(got == expect, f"UI 卡渲染 == live 真源逐值对账: {got} == {expect}")
        check(zero == "0.0ms", f"真 0 显示 0.0ms 禁 '—' 遮蔽: {zero}")
        check(absent == "—", f"precision 缺席才 '—': {absent}")

        # ── 3. fmtTs 真源码吃 live 真 epoch 秒 ──────────────────
        m = re.search(r"const fmtTs = \(epochSec: number\) => (.*?);", PAGE.read_text(encoding="utf-8"))
        assert m, "fmtTs 提取失效"
        out = node(
            f"const fmtTs=(epochSec)=>{m.group(1)};"
            f"console.log(JSON.stringify([fmtTs({tick['timestamp']!r}), new Date({tick['timestamp']!r}).getFullYear()]));"
        )
        fixed, old_year = json.loads(out)
        check("2026" in fixed or "2027" in fixed, f"fmtTs 渲染真时间非 1970: {fixed}")
        check(old_year == 1970, f"修前形反证（裸 new Date=1970）: {old_year}")

        # ── 4. 部署 bundle 回读 ─────────────────────────────────
        chunks_dir = REPO / "src" / "static" / "admin" / "_next" / "static" / "chunks"
        target = next(c for c in chunks_dir.glob("*.js") if "平均漂移" in c.read_text(encoding="utf-8", errors="replace"))
        src = target.read_text(encoding="utf-8", errors="replace")
        for s in ("stats?.avg_drift_ms", "new Date(s.", "new Date(selected.", "new Date(t."):
            check(src.count(s) == 0, f"bundle 修前漂移串 0 残留: {s}")
        check(".precision?.avg_drift_ms" in src, "bundle 嵌套键真消费")
        conv = re.findall(r"new Date\([^)]*\*1e3\)|new Date\(1e3\*[^)]*\)", src)
        check(len(conv) == 1, f"bundle ×1000 单一源恰一处: {conv}")
        req = urllib.request.Request(BASE + "/admin/pulse")
        with urllib.request.urlopen(req, timeout=15) as r:
            check(r.status == 200, "GET /admin/pulse 200")
    finally:
        api("DELETE", f"/api/pulse/signals/{sid}")
        _, lst = api("GET", "/api/pulse/signals")
        remaining = sum(1 for s in lst["signals"] if s["name"] == PROBE_NAME)
        check(remaining == 0, f"清痕 remaining={remaining}")

    ok = sum(1 for c, _ in results if c)
    print(f"\n== {ok}/{len(results)} ==")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
