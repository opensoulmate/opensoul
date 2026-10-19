#!/usr/bin/env python3
"""CD-33 live e2e 探针：bar_chart_json 请求守卫与流式兄弟 /chart/bar 同型同码同文案。

修前（2026-10-19 curl 实录，同探针逻辑）：
- bar/json 空输入 → 200 空图表 fabricated（15KB PNG）
- bar/json 长度不齐 → 500 Internal Server Error 裸错
- 流式兄弟两种形 → 400 如实拒
= 同一 BarChartRequest 校验面分叉。

用法：python3 e2e_cd33_bar_guard_probe.py [--base http://localhost:8090]
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request

FAILS: list[str] = []
COUNTS = {"pass": 0, "fail": 0}


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        COUNTS["pass"] += 1
        print(f"  PASS {name}")
    else:
        COUNTS["fail"] += 1
        FAILS.append(f"{name}: {detail}")
        print(f"  FAIL {name} — {detail}")


def post(base: str, path: str, payload: dict):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = None
            return resp.status, parsed, body
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = None
        return e.code, parsed, body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8090")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    with urllib.request.urlopen(base + "/api/health", timeout=10) as r:
        check("P0 health 200", r.status == 200)

    fam = {"format", "engine", "chart_type", "size_bytes", "elapsed_ms", "output_file", "image_base64"}

    # P1-P3 空输入守卫平价（三形态）
    for tag, payload in [
        ("P1 双空", {"labels": [], "values": []}),
        ("P2 labels 空", {"labels": [], "values": [1.0]}),
        ("P3 values 空", {"labels": ["a"], "values": []}),
    ]:
        s_new, d_new, _ = post(base, "/api/vision/chart/bar/json", payload)
        s_old, d_old, _ = post(base, "/api/vision/chart/bar", payload)
        check(f"{tag} bar/json 400", s_new == 400, f"got {s_new}")
        check(f"{tag} 兄弟 400", s_old == 400, f"got {s_old}")
        check(
            f"{tag} 同码同文案",
            s_new == s_old == 400
            and isinstance(d_new, dict)
            and isinstance(d_old, dict)
            and d_new.get("detail") == d_old.get("detail"),
            f"new={d_new} old={d_old}",
        )

    # P4 长度不齐（修前 500 裸错）
    payload = {"labels": ["a", "b", "c"], "values": [1.0, 2.0]}
    s_new, d_new, _ = post(base, "/api/vision/chart/bar/json", payload)
    s_old, d_old, _ = post(base, "/api/vision/chart/bar", payload)
    check("P4 不齐 bar/json 400（修前 500）", s_new == 400, f"got {s_new}")
    check(
        "P4 不齐同码同文案",
        s_new == s_old == 400
        and isinstance(d_new, dict)
        and isinstance(d_old, dict)
        and d_new.get("detail") == d_old.get("detail"),
        f"new={d_new} old={d_old}",
    )

    # P5 合法输入零变化（7 键 + 真 PNG base64）
    s, d, _ = post(base, "/api/vision/chart/bar/json", {"labels": ["a", "b"], "values": [1.0, 2.0]})
    ok_shape = s == 200 and isinstance(d, dict) and set(d.keys()) == fam
    check("P5 bar/json 合法 200 恰 7 键", ok_shape, f"got {s} keys={set(d.keys()) if isinstance(d, dict) else d}")
    if isinstance(d, dict):
        raw = base64.b64decode(d.get("image_base64", "")) if d.get("image_base64") else b""
        check("P5 真 PNG base64", raw.startswith(b"\x89PNG"), f"head={raw[:8]!r}")
        check("P5 chart_type=bar", d.get("chart_type") == "bar", str(d.get("chart_type")))

    # P6 流式兄弟合法零变化
    s, _, body = post(base, "/api/vision/chart/bar", {"labels": ["a", "b"], "values": [1.0, 2.0]})
    check("P6 兄弟合法 200 真图像", s == 200 and len(body) > 100, f"got {s} len={len(body)}")

    # P7/P8 line/pie/scatter 三 /json 变体零回归
    for tag, path, good, bad in [
        ("P7a line", "/api/vision/chart/line/json", {"x": [1.0, 2.0], "series": {"s": [1.0, 2.0]}}, {"x": [], "series": {}}),
        ("P7b pie", "/api/vision/chart/pie/json", {"labels": ["a"], "values": [1.0]}, {"labels": [], "values": []}),
        ("P7c scatter", "/api/vision/chart/scatter/json", {"x": [1.0], "y": [1.0]}, {"x": [], "y": []}),
    ]:
        s, d, _ = post(base, path, good)
        check(f"{tag} 合法 200 恰 7 键", s == 200 and isinstance(d, dict) and set(d.keys()) == fam, f"got {s}")
        s2, _, _ = post(base, path, bad)
        check(f"{tag} 空输入 400 零回归", s2 == 400, f"got {s2}")

    print(f"\n== CD-33 probe: {COUNTS['pass']}/{COUNTS['pass'] + COUNTS['fail']} ==")
    if FAILS:
        for f in FAILS:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
