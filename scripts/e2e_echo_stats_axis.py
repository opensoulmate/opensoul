#!/usr/bin/env python3
"""DEC-⑦-⑭ echo /stats 聚合面加轴 live e2e 探针（修前/修后同探针双向硬证）。

用法：python3 scripts/e2e_echo_stats_axis.py [tag]
产出：data/e2e_echo_stats_<tag>.json（事实工件）+ stdout 摘要。
清痕：探针自建 nest 租户/enterprise 用户全部删除，回读 remaining=0。
"""

import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://localhost:8090"
REPO = Path(__file__).resolve().parent.parent
MARK = "e2e-echo-stats"


def _req(method, path, body=None, token=None, params=None):
    url = BASE + path
    if params:
        from urllib.parse import urlencode

        url += "?" + urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def main(tag):
    ts = str(int(time.time()))
    facts = {"tag": tag, "ts": ts, "steps": {}}
    created_tenants = []
    created_users = []

    # 1. 真租户×2 + enterprise register→login→token
    tokens = {}
    for side in ("a", "b"):
        name = f"{MARK}-{side}-{ts}"
        st, t = _req("POST", "/api/nest/tenants", {"name": name, "tier": "free"})
        tid = t.get("tenant_id") or t.get("id")
        facts["steps"][f"nest_create_{side}"] = {"status": st, "tenant_id": tid}
        created_tenants.append(tid)
        st, reg = _req(
            "POST",
            "/api/enterprise/auth/register",
            {"username": name, "password": "e2e-pass-1", "tenant_id": tid},
        )
        facts["steps"][f"register_{side}"] = {"status": st}
        created_users.append(name)
        st, login = _req(
            "POST", "/api/enterprise/auth/login", {"username": name, "password": "e2e-pass-1"}
        )
        tokens[side] = login.get("access_token")
        facts["steps"][f"login_{side}"] = {"status": st, "has_token": bool(tokens[side])}

    # 2. 播种消息行：A×2 B×1 匿名×1
    for i in range(2):
        st, _ = _req(
            "POST",
            "/api/echo/send",
            {"channel": "console", "title": f"{MARK}-a-{i}-{ts}", "content": "e2e body"},
            token=tokens["a"],
        )
        facts["steps"][f"send_a{i}"] = {"status": st}
    st, _ = _req(
        "POST",
        "/api/echo/send",
        {"channel": "console", "title": f"{MARK}-b-0-{ts}", "content": "e2e body"},
        token=tokens["b"],
    )
    facts["steps"]["send_b0"] = {"status": st}
    st, _ = _req(
        "POST",
        "/api/echo/send",
        {"channel": "console", "title": f"{MARK}-anon-0-{ts}", "content": "e2e body"},
    )
    facts["steps"]["send_anon0"] = {"status": st}

    # 3. 读 /stats 四面 + /history 对账 + /health 系统视图
    def _snap(d):
        return {
            k: d.get(k)
            for k in ("total_messages", "sent", "failed", "tenant_id")
        } | {"has_tenant_key": "tenant_id" in d}

    st, sa = _req("GET", "/api/echo/stats", token=tokens["a"])
    facts["steps"]["stats_A"] = {"status": st, **_snap(sa)}
    st, sb = _req("GET", "/api/echo/stats", token=tokens["b"])
    facts["steps"]["stats_B"] = {"status": st, **_snap(sb)}
    st, sanon = _req("GET", "/api/echo/stats")
    facts["steps"]["stats_anon"] = {"status": st, **_snap(sanon)}
    st, sbad = _req("GET", "/api/echo/stats", token="not-a-jwt")
    facts["steps"]["stats_bad_token"] = {"status": st}
    st, sself = _req(
        "GET", "/api/echo/stats", token=tokens["a"], params={"tenant_id": "default"}
    )
    facts["steps"]["stats_selfreport_ignored"] = {"status": st, **_snap(sself)}
    st, ha = _req("GET", "/api/echo/history", token=tokens["a"])
    facts["steps"]["history_A_rows"] = {
        "status": st,
        "n": len(ha.get("messages", [])),
        "titles": sorted(m.get("title", "") for m in ha.get("messages", [])),
    }
    st, hb = _req("GET", "/api/echo/health")
    facts["steps"]["health"] = {
        "status": st,
        "total_messages": hb.get("total_messages"),
    }
    st, sp = _req("GET", "/api/echo/stats", params={"tenant_id": "tenant-a"}, token=tokens["b"])
    facts["steps"]["stats_b_with_a_param"] = {"status": st, **_snap(sp)}

    # 4. 清痕
    for tid in created_tenants:
        st, _ = _req("DELETE", f"/api/nest/tenants/{tid}")
        facts["steps"].setdefault("cleanup_tenants", []).append({"tenant_id": tid, "status": st})
    ent = REPO / "data" / "enterprise.db"
    con = sqlite3.connect(ent)
    users_deleted = 0
    for name in created_users:
        cur = con.execute("DELETE FROM users WHERE username = ?", (name,))
        users_deleted += cur.rowcount
    con.commit()
    # 审计行随用户清（user_id 关联）
    remaining_users = con.execute(
        "SELECT COUNT(*) FROM users WHERE username LIKE ?", (MARK + "-%",)
    ).fetchone()[0]
    con.close()
    nest = REPO / "data" / "nest.db"
    con = sqlite3.connect(nest)
    remaining_tenants = con.execute(
        "SELECT COUNT(*) FROM tenants WHERE name LIKE ?", (MARK + "-%",)
    ).fetchone()[0]
    con.close()
    facts["cleanup"] = {
        "users_deleted": users_deleted,
        "remaining_users": remaining_users,
        "remaining_tenants": remaining_tenants,
    }

    out = REPO / "data" / f"e2e_echo_stats_{tag}.json"
    out.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(facts["steps"], ensure_ascii=False, indent=2))
    print("CLEANUP:", facts["cleanup"])
    print("FACTS:", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "run")
