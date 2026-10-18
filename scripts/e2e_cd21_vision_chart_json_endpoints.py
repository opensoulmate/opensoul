#!/usr/bin/env python3
"""CD-㉑ live e2e：line/pie/scatter /json 端点面补齐——UI 真实请求体真打真源。

三层断言：HTTP 200（修前 404 死路径）+ 响应恰 chart/json 家族 7 键 + UI 消费键
真值回读；守卫 400 与流式兄弟同文案；save_output 真落盘+清痕。
"""
import base64
import json
import sys
import urllib.error
import urllib.request

BASE = "http://localhost:8090"
FAIL = []


def post(path, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), name, detail if not cond else "")
    if not cond:
        FAIL.append(name)


FAMILY = {"format", "engine", "chart_type", "size_bytes", "elapsed_ms", "output_file", "image_base64"}

cases = [
    (
        "/api/vision/chart/line/json",
        {
            "x": ["1", "2", "3", "4", "5", "6"],
            "series": {"系列A": [10, 15, 13, 18, 22, 20]},
            "title": "",
            "save_output": False,
        },
        "line",
    ),
    (
        "/api/vision/chart/pie/json",
        {
            "labels": ["Python", "JavaScript", "Rust", "Go"],
            "values": [40, 30, 15, 15],
            "title": "",
            "save_output": False,
        },
        "pie",
    ),
    (
        "/api/vision/chart/scatter/json",
        {
            "x": [1, 2, 3, 4, 5, 6, 7, 8],
            "y": [2, 4, 5, 4, 6, 8, 7, 9],
            "title": "",
            "save_output": False,
        },
        "scatter",
    ),
]

for path, body, kind in cases:
    status, data = post(path, body)
    check(f"{kind} 200", status == 200, f"status={status} body={data}")
    if status == 200:
        check(f"{kind} 键集恰 7", set(data.keys()) == FAMILY, str(sorted(data.keys())))
        check(f"{kind} chart_type", data.get("chart_type") == kind, str(data.get("chart_type")))
        check(
            f"{kind} elapsed_ms int",
            isinstance(data.get("elapsed_ms"), int) and data["elapsed_ms"] >= 0,
            str(data.get("elapsed_ms")),
        )
        check(f"{kind} output_file 空", data.get("output_file") == "", str(data.get("output_file")))
        if data.get("format") == "png":
            try:
                png = base64.b64decode(data.get("image_base64", ""))
                check(f"{kind} image_base64 真 PNG", png[:8] == b"\x89PNG\r\n\x1a\n")
            except Exception as e:  # noqa: BLE001
                check(f"{kind} image_base64 真 PNG", False, str(e))

# 守卫 400（与流式兄弟同文案，UI 空输入如实拒）
guards = [
    ("/api/vision/chart/line/json", {"x": [], "series": {}}, "x values and at least one series required"),
    ("/api/vision/chart/pie/json", {"labels": [], "values": []}, "Labels and values required"),
    ("/api/vision/chart/scatter/json", {"x": [], "y": []}, "x and y values required"),
]
for path, body, msg in guards:
    status, data = post(path, body)
    check(f"guard 400 {path.rsplit('/', 2)[-2]}", status == 400 and data.get("detail") == msg,
          f"status={status} detail={data.get('detail')}")

# save_output 真落盘（UI 真实路径 save_output: true）→ 清痕
status, data = post(
    "/api/vision/chart/pie/json",
    {"labels": ["a", "b"], "values": [1, 2], "title": "cd21-e2e", "save_output": True},
)
check("save_output 200", status == 200, str(status))
if status == 200:
    out = data.get("output_file", "")
    check("save_output output_file 非空", bool(out), out)
    if out:
        fname = out.rsplit("/", 1)[-1]
        req = urllib.request.Request(BASE + "/api/vision/outputs/" + fname, method="DELETE")
        with urllib.request.urlopen(req, timeout=30) as r:
            check("清痕 deleted", json.loads(r.read().decode()).get("message") == "deleted")

# 既有面回归：bar/json 与 mindmap/json 照旧
status, data = post("/api/vision/chart/bar/json", {"labels": ["a", "b"], "values": [1, 2], "title": ""})
check("bar/json 回归 200+7 键", status == 200 and set(data.keys()) == FAMILY, f"{status} {sorted(data.keys())}")
status, data = post("/api/vision/mindmap/json", {"root": {"label": "root"}, "title": ""})
check("mindmap/json 回归 200", status == 200 and "elapsed_seconds" in data, str(status))

print("FAILS:", len(FAIL), FAIL)
sys.exit(1 if FAIL else 0)
