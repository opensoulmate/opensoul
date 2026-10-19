#!/usr/bin/env python3
"""CD-31 live 真栈探针：GET /api/soma/discovery/scan services 元素键真源形状 + 部署 chunk 回读。

真 e2e（urllib 真 HTTP 栈打 live :8090 真服务）：
1. 真 GET /api/soma/discovery/scan（页面真取数路径）——services 元素键集恰真源 7 字段
   {protocol,local_address,local_port,state,process_name,pid,description}、
   name/host/port 硬缺席（修前 UI 消费的漂移键）、UI 真消费键 ⊆ 元素键；
2. 真 GET /api/soma/discovery/services（同形直取面）元素键集一致；
3. UI 修后渲染表达式吃真元素逐值对账（服务名/地址段==真源值，禁 undefined 假渲染）；
4. live :8090 HTTP 回读部署 chunk（与 deploy_cd31_admin.py 同判据）；
5. GET /admin/connectors.html 200。
"""
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://localhost:8090"
TRUE_ELEMENT_KEYS = {
    "protocol", "local_address", "local_port", "state", "process_name", "pid", "description",
}
DRIFT_KEYS = {"name", "host", "port"}
CONSUMED = {"process_name", "local_address", "local_port", "protocol"}

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
svcs = (scan or {}).get("services") if isinstance(scan, dict) else None
check("scan.services 是非空列表", isinstance(svcs, list) and len(svcs) > 0,
      f"n={len(svcs) if isinstance(svcs, list) else None}")
elem_ok = all(set(e.keys()) == TRUE_ELEMENT_KEYS for e in svcs or [])
check("services 元素键集恰真源 7 字段", elem_ok,
      f"sample={sorted((svcs or [{}])[0].keys())}")
drift_free = all(not (DRIFT_KEYS & set(e.keys())) for e in svcs or [])
check("name/host/port 硬缺席", drift_free)
consumed_ok = all(CONSUMED <= set(e.keys()) for e in svcs or [])
check("UI 真消费键 ⊆ 元素键", consumed_ok)

# 3. UI 修后渲染表达式吃真元素逐值对账（服务名回落语义 + 地址段）
render_ok = True
detail = []
for e in (svcs or [])[:5]:
    name_line = e.get("process_name") or e.get("local_address")  # s.process_name || s.local_address
    addr_line = f'{e.get("local_address")}:{e.get("local_port")}'  # {s.local_address}:{s.local_port}
    if not isinstance(name_line, str) or not name_line or "undefined" in name_line.lower():
        render_ok = False
    if "undefined" in addr_line.lower() or ":" not in addr_line:
        render_ok = False
    detail.append(f"{name_line!r}@{addr_line}")
check("渲染对账：服务名/地址段==真源值零 undefined", render_ok, "; ".join(detail[:3]))
port_ok = all(isinstance(e.get("local_port"), int) for e in svcs or [])
check("local_port 真 int", port_ok)

# 2. 同形直取面 GET /services
s2, svcs2 = req("/api/soma/discovery/services")
check("GET /services 200", s2 == 200, f"status={s2}")
check(
    "/services 元素键集与 scan 同形",
    isinstance(svcs2, list) and all(set(e.keys()) == TRUE_ELEMENT_KEYS for e in svcs2),
)

# 4. live HTTP 回读部署 chunk（与 deploy_cd31_admin.py 同判据）
admin = Path("/home/climbing/opensoul/src/static/admin")
MARK = "监听服务"
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
    for k in (".host", ".port"):
        if k in body:
            ok = False
            cdetail.append(f"{p.name}:{k} 残留")
    for pat in (r"\.name\|\|\w*\.host", r"\.host.{0,12}\.port"):
        if re.search(pat, body):
            ok = False
            cdetail.append(f"{p.name}:{pat} 残留")
    for k in (".process_name", ".local_address", ".local_port"):
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
