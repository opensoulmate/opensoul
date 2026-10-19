#!/usr/bin/env python3
"""CD-32 live 真栈探针：GET /api/soma/discovery/scan processes 元素键真源形状 + 部署 chunk 回读。

真 e2e（urllib 真 HTTP 栈打 live :8090 真服务）：
1. 真 GET /api/soma/discovery/scan（页面真取数路径）——processes 元素键集恰真源 7 字段
   {pid,user,cpu,mem,command,executable,description}、name/cmd/memory 硬缺席
   （修前 UI 消费的漂移键）、UI 真消费键 ⊆ 元素键；
2. 真 GET /api/soma/discovery/processes（同形直取面）元素键集一致；
3. UI 修后渲染表达式吃真元素逐值对账（进程名/内存==真源值，禁 undefined 假渲染/禁 toFixed 崩）；
4. live :8090 HTTP 回读部署 chunk（与 deploy_cd32_admin.py 同判据）；
5. GET /admin/connectors.html 200。
"""
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://localhost:8090"
TRUE_ELEMENT_KEYS = {
    "pid", "user", "cpu", "mem", "command", "executable", "description",
}
DRIFT_KEYS = {"name", "cmd", "memory"}
CONSUMED = {"pid", "command", "executable", "cpu", "mem"}

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS" if ok else "FAIL"), name, detail)


def req(path: str) -> tuple[int, object]:
    r = urllib.request.Request(BASE + path)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except ValueError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


# 1. 页面真取数路径 GET /scan
s, scan = req("/api/soma/discovery/scan")
check("GET /scan 200", s == 200, f"status={s}")
procs = (scan or {}).get("processes") if isinstance(scan, dict) else None
check("scan.processes 是非空列表", isinstance(procs, list) and len(procs) > 0,
      f"n={len(procs) if isinstance(procs, list) else None}")
elem_ok = all(set(e.keys()) == TRUE_ELEMENT_KEYS for e in procs or [])
check("processes 元素键集恰真源 7 字段", elem_ok,
      f"sample={sorted((procs or [{}])[0].keys())}")
drift_free = all(not (DRIFT_KEYS & set(e.keys())) for e in procs or [])
check("name/cmd/memory 硬缺席", drift_free)
consumed_ok = all(CONSUMED <= set(e.keys()) for e in procs or [])
check("UI 真消费键 ⊆ 元素键", consumed_ok)

# 3. UI 修后渲染表达式吃真元素逐值对账（进程名回落语义 + 内存段）
render_ok = True
detail = []
for e in (procs or [])[:5]:
    name_line = e.get("command") or e.get("executable")  # p.command || p.executable
    mem_line = "{:.1f}".format(e.get("mem"))  # p.mem.toFixed(1)
    if not isinstance(name_line, str) or not name_line or "undefined" in name_line.lower():
        render_ok = False
    if not isinstance(e.get("mem"), (int, float)) or "undefined" in mem_line.lower():
        render_ok = False
    detail.append(f"{name_line!r}@MEM{mem_line}")
check("渲染对账：进程名/内存==真源值零 undefined 零崩", render_ok, "; ".join(detail[:3]))
cpu_ok = all(isinstance(e.get("cpu"), (int, float)) and isinstance(e.get("mem"), (int, float))
             for e in procs or [])
check("cpu/mem 真数值（toFixed 不炸）", cpu_ok)

# 2. 同形直取面 GET /processes
s2, procs2 = req("/api/soma/discovery/processes")
check("GET /processes 200", s2 == 200, f"status={s2}")
check(
    "/processes 元素键集与 scan 同形",
    isinstance(procs2, list) and all(set(e.keys()) == TRUE_ELEMENT_KEYS for e in procs2),
)

# 4. live HTTP 回读部署 chunk（与 deploy_cd32_admin.py 同判据）
admin = Path("/home/climbing/opensoul/src/static/admin")
MARK = "运行进程"
chunks = []
for p in admin.rglob("*.js"):
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if MARK in t:
        chunks.append(p)
check("connectors 页产物已定位", bool(chunks), f"n={len(chunks)}")
chunk_ok = True
cdetail = []
for p in chunks:
    rel = "/" + str(p.relative_to(admin.parent.parent))  # src/static/admin/... → /static/admin/...
    s3, body = req(rel)
    body = body if isinstance(body, str) else json.dumps(body)
    ok = s3 == 200
    if re.search(r"\.memory\b", body):
        ok = False
        cdetail.append(f"{p.name}:.memory 残留")
    for pat in (r"\.command\|\|\w*\.executable",):
        if not re.search(pat, body):
            ok = False
            cdetail.append(f"{p.name}:{pat} 缺席")
    for k in (".command", ".executable", ".mem", ".process_name", ".local_address", ".local_port"):
        if body.count(k) < 1:
            ok = False
            cdetail.append(f"{p.name}:{k} 缺席")
    if ok:
        cdetail.append(f"{p.name}: OK len={len(body)}")
    chunk_ok = chunk_ok and ok
check("live chunk 回读：漂移残留 0+真源键在场", chunk_ok, "; ".join(cdetail))

# 5. 页面 200
s4, _ = req("/admin/connectors.html")
check("GET /admin/connectors.html 200", s4 == 200, f"status={s4}")

failed = [r for r in results if not r[1]]
print(f"\n=== {len(results) - len(failed)}/{len(results)} PASS ===")
for name, ok, detail in failed:
    print("FAIL:", name, detail)
raise SystemExit(1 if failed else 0)
