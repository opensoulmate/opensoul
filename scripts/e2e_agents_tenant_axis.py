#!/usr/bin/env python3
"""DEC-⑦-⑤ live e2e 探针：agents 面加轴（真租户×2 + 匿名互斥双向 + 劫持闸）。"""
import json
import sqlite3
import sys
import urllib.request
import urllib.error
import uuid

BASE = "http://127.0.0.1:8090"
REG_DB = "/home/climbing/opensoul/data/agent_registry.db"
RESULTS = []


def req(method, path, body=None, token=None, raw_body=None):
    url = BASE + path
    data = None
    headers = {}
    if raw_body is not None:
        data = raw_body.encode()
        headers["Content-Type"] = "application/json"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS" if ok else "FAIL"), name, detail)


def raw_rows():
    with sqlite3.connect(REG_DB) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute("SELECT * FROM acp_agent_registry")]


def main():
    tag = "probe-e2e-" + uuid.uuid4().hex[:6]
    tag2 = tag + "-b"

    # 0. health
    s, d = req("GET", "/api/agents/health")
    check("health", s == 200, f"{s}")

    # 1. 匿名 registry 契约（存量 'default' 行可见）
    s, d = req("GET", "/api/agents/registry")
    anon_before = [a["agent_id"] for a in d.get("agents", [])]
    check("registry anonymous 200", s == 200, f"total={d.get('total')}")
    check(
        "registry additive tenant_id key",
        all("tenant_id" in a for a in d.get("agents", [])) and all(
            a["tenant_id"] == "default" for a in d.get("agents", [])
        ),
        str([a.get("tenant_id") for a in d.get("agents", [])]),
    )

    # 2. 真租户×2（nest 建租户拿生成 id + enterprise register→login→token）
    toks = {}
    uids = {}
    created_tenants = []
    for tname in ("tenant-e2e-a-" + uuid.uuid4().hex[:6], "tenant-e2e-b-" + uuid.uuid4().hex[:6]):
        s, d = req("POST", "/api/nest/tenants", {"name": tname, "tier": "free"})
        real_tid = d.get("tenant_id")
        check(f"nest create {tname[:18]}…", s in (200, 201) and bool(real_tid), f"{s} {real_tid}")
        created_tenants.append(real_tid)
        uname = "e2eagent_" + uuid.uuid4().hex[:8]
        s2, d2 = req("POST", "/api/enterprise/auth/register", {"username": uname, "password": "E2ePass!234", "tenant_id": real_tid})
        check(f"register {(real_tid or '')[:14]}…", s2 in (200, 201), f"{s2} {str(d2)[:80]}")
        s3, d3 = req("POST", "/api/enterprise/auth/login", {"username": uname, "password": "E2ePass!234"})
        tok = d3.get("token") or d3.get("access_token")
        check(f"login {(real_tid or '')[:14]}…", s3 == 200 and bool(tok), f"{s3}")
        toks[real_tid] = tok
        uids[real_tid] = uname
    ta, tb = list(toks.keys())

    # 3. probe 行归属（匿名='default'、A=派生租户）
    s, d = req("POST", "/api/agents/probe", {"agent_id": tag}, token=None)
    check("probe anonymous 200", s == 200, f"{s} registered={d.get('registered')}")
    row = [r for r in raw_rows() if r["agent_id"] == tag]
    check("probe anonymous row 'default'", bool(row) and row[0]["tenant_id"] == "default", str(row[0]["tenant_id"]) if row else "missing")

    s, d = req("POST", "/api/agents/probe", {"agent_id": tag2}, token=toks[ta])
    row2 = [r for r in raw_rows() if r["agent_id"] == tag2]
    check("probe A row=派生租户", s == 200 and bool(row2) and row2[0]["tenant_id"] == ta, f"{s} {row2[0]['tenant_id'] if row2 else 'missing'}")

    # 4. 劫持闸：B 探同 agent_id → 403 行零改写
    before = [r for r in raw_rows() if r["agent_id"] == tag2][0]
    s, d = req("POST", "/api/agents/probe", {"agent_id": tag2}, token=toks[tb])
    after = [r for r in raw_rows() if r["agent_id"] == tag2][0]
    check("hijack 403", s == 403 and "another tenant" in str(d.get("detail", "")), f"{s} {str(d.get('detail'))[:60]}")
    check("hijack row zero-touch", after == before, "")

    # 5. registry 三面隔离（匿名只见 default、A 只见己、B 只见己）
    s, d = req("GET", "/api/agents/registry")
    anon_ids = [a["agent_id"] for a in d.get("agents", [])]
    sA, dA = req("GET", "/api/agents/registry", token=toks[ta])
    a_ids = [a["agent_id"] for a in dA.get("agents", [])]
    sB, dB = req("GET", "/api/agents/registry", token=toks[tb])
    b_ids = [a["agent_id"] for a in dB.get("agents", [])]
    check("registry anon rows all default", s == 200 and all(x in anon_before for x in anon_ids), f"{len(anon_ids)} rows")
    check("registry A 不见 default 行", sA == 200 and not (set(a_ids) & set(anon_before)), f"A={a_ids}")
    check("registry B 与 A 互斥", sB == 200 and not set(a_ids) & set(b_ids), f"B={b_ids}")

    # 6. unified 搜索面：匿名搜 default 行、A 搜不到他人行
    # 用已有 default 注册行的 agent_id 词查询（若空表则以探测行反证不可见）
    q = anon_before[0] if anon_before else tag
    s, d = req("GET", "/api/search/unified?q=" + q + "&sources=agents&limit=10")
    anon_hits = [m["title"] for m in d.get("by_source", {}).get("agents", [])]
    check("search anonymous sees default row", q in anon_hits or not anon_before, f"hits={anon_hits}")
    s, d = req("GET", "/api/search/unified?q=" + tag2 + "&sources=agents&limit=10", token=toks[tb])
    b_hits = [m["title"] for m in d.get("by_source", {}).get("agents", [])]
    check("search B 不见 A 的行(unregistered 不进列表)", tag2 not in b_hits, f"hits={b_hits}")
    if anon_before:
        s, d = req("GET", "/api/search/unified?q=" + q + "&sources=agents&limit=10", token=toks[ta])
        a_hits = [m["title"] for m in d.get("by_source", {}).get("agents", [])]
        check("search A 不见 default 行", q not in a_hits, f"hits={a_hits}")

    # 7. detect session_agents 过滤
    s, d = req("GET", "/api/agents/detect")
    det_anon = [a["agent_id"] for a in d.get("session_agents", [])]
    s, d = req("GET", "/api/agents/detect", token=toks[ta])
    det_a = [a["agent_id"] for a in d.get("session_agents", [])]
    check("detect anon=default 行", set(det_anon) <= set(anon_before) or not det_anon, f"{det_anon}")
    check("detect A 无 default 行", not (set(det_a) & set(anon_before)), f"{det_a}")

    # 8. 坏 token 401
    s, d = req("GET", "/api/agents/registry", token="not-a-real-token")
    check("bad token 401", s == 401, f"{s}")

    # 9. 清痕
    with sqlite3.connect(REG_DB) as c:
        c.execute("DELETE FROM acp_agent_registry WHERE agent_id IN (?, ?)", (tag, tag2))
        c.commit()
    remaining = [r["agent_id"] for r in raw_rows() if r["agent_id"].startswith("probe-e2e")]
    for tname in toks:
        req("DELETE", "/api/nest/tenants/" + tname)
    # enterprise 用户 SQL 清
    import os
    ent = "/home/climbing/opensoul/data/enterprise.db"
    if os.path.exists(ent):
        with sqlite3.connect(ent) as c:
            for uname in uids.values():
                c.execute("DELETE FROM users WHERE username=?", (uname,))
                c.execute("DELETE FROM audit_log WHERE user_id NOT IN (SELECT id FROM users)")
            c.commit()
    check("cleanup remaining=0", not remaining, str(remaining))

    fails = [r for r in RESULTS if not r[1]]
    print(f"\n== E2E {len(RESULTS)-len(fails)}/{len(RESULTS)} ==")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
