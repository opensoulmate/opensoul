#!/usr/bin/env python3
"""e2e probe: DEC-⑦-⑨ trajectory_scores 行租户键加轴（live :8090 真栈）。

修前/修后同探针双向硬证：
- 修前（旧代码）：B 可见 A 会话全部评分（跨租户泄露）、无 tenant_id 加性键、
  坏 token 被当匿名照常 200、trajectory_scores 无 tenant_id 列。
- 修后：三面互斥、父行继承落键、聚合不泄露、401/404 契约。

自清痕：e2e 会话/评分/用户/租户全部回收，remaining=0 校验；只动
e2e-sc-* 前缀数据，绝不碰生产行。
"""

import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8090"
TRAJ_DB = "/home/climbing/opensoul/data/opensoul.db"
ENT_DB = "/home/climbing/opensoul/data/enterprise.db"
PREFIX = f"e2e-sc-{int(time.time())}"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'} | {name} | {detail}")


def req(method, path, body=None, token=None, expect=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            code, payload = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        code, payload = e.code, e.read()
    try:
        js = json.loads(payload) if payload else {}
    except json.JSONDecodeError:
        js = {}
    if expect is not None and code != expect:
        print(f"  !! expected {expect} got {code} for {method} {path}: {js}")
    return code, js


def main():
    code, js = req("GET", "/api/trajectory/health")
    check("health", code == 200 and js.get("status") == "ok", f"{code}")

    # warm-up：ensure_tables 惰性迁移（读面真调用触发补列自愈，schema 断言才确定）
    req("GET", "/api/trajectory/sessions")

    # ── schema 面（修前 FAIL = 预期反证）──
    with sqlite3.connect(TRAJ_DB) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(trajectory_scores)")}
        idx = {r[1] for r in c.execute("PRAGMA index_list(trajectory_scores)")}
    check("schema.tenant_id 列", "tenant_id" in cols, f"cols={sorted(cols)}")
    check("schema.idx_traj_scores_tenant", "idx_traj_scores_tenant" in idx, f"idx={sorted(idx)}")
    with sqlite3.connect(TRAJ_DB) as c:
        legacy = c.execute(
            "SELECT COUNT(*) FROM trajectory_scores WHERE id NOT LIKE 'e2e-sc-%' AND tenant_id='default'"
        ).fetchone()[0] if "tenant_id" in cols else -1
    check("迁移存量行回填 default", legacy == 2, f"legacy_default={legacy}")

    # ── 身份面：真租户×2 + enterprise register→login→token ──
    tokens = {}
    tenant_ids = {}
    for who in ("a", "b"):
        code, js = req("POST", "/api/nest/tenants", {"name": f"{PREFIX}-{who}"})
        tid = js.get("tenant_id")
        check(f"tenant.{who} 建立", code in (200, 201) and bool(tid), f"{code} {tid}")
        tenant_ids[who] = tid
        code, js = req(
            "POST",
            "/api/enterprise/auth/register",
            {"username": f"{PREFIX}-{who}", "password": "e2e-pass-42", "tenant_id": tid},
        )
        check(f"register.{who}", code in (200, 201), f"{code} {js}")
        code, js = req(
            "POST",
            "/api/enterprise/auth/login",
            {"username": f"{PREFIX}-{who}", "password": "e2e-pass-42"},
        )
        tokens[who] = js.get("access_token")
        check(f"login.{who}", code == 200 and bool(tokens[who]), f"{code}")

    ta, tb = tokens["a"], tokens["b"]

    # ── 写点归属：A 会话 + 匿名外部评分方落分→随父行继承 ──
    sid_a = f"{PREFIX}-sess-a"
    code, js = req(
        "POST",
        "/api/trajectory/sessions",
        {"agent_id": "agent-x", "task_description": "e2e alpha", "session_id": sid_a},
        token=ta,
    )
    check("A 建会话", code == 200, f"{code} tenant={js.get('tenant_id')}")
    score_ids = []
    for i, (val, who) in enumerate([(1.0, ta), (0.0, ta), (0.5, None)]):
        code, js = req(
            "POST",
            f"/api/trajectory/sessions/{sid_a}/scores",
            {"name": "quality", "value": val, "source": "api"},
            token=who,
        )
        ok = code == 200
        if ok:
            score_ids.append(js.get("id"))
        check(
            f"落分#{i} ({'A' if who else '匿名外部方'})",
            ok and js.get("tenant_id") == tenant_ids["a"],
            f"{code} row_tenant={js.get('tenant_id')} expect={tenant_ids['a']}",
        )

    # ── 读过滤三面互斥（修前 B 全量泄露=反证）──
    code, js = req("GET", f"/api/trajectory/sessions/{sid_a}/scores", token=ta)
    check("A 可见全部 3 评分", code == 200 and len(js.get("scores", [])) == 3, f"{code} n={len(js.get('scores', []))}")
    code, js = req("GET", f"/api/trajectory/sessions/{sid_a}/scores", token=tb)
    check("B 读 A 评分恒不可见", code == 200 and js.get("scores") == [], f"{code} n={len(js.get('scores', []))}")
    code, js = req("GET", f"/api/trajectory/sessions/{sid_a}/scores")
    check("匿名读 A 评分恒不可见", code == 200 and js.get("scores") == [], f"{code} n={len(js.get('scores', []))}")

    # ── 匿名契约不变（default 租户行匿名可见）──
    sid_d = f"{PREFIX}-sess-d"
    req("POST", "/api/trajectory/sessions", {"agent_id": "a", "task_description": "e2e default", "session_id": sid_d})
    code, js = req("POST", f"/api/trajectory/sessions/{sid_d}/scores", {"name": "q", "value": 1.0})
    check("匿名 default 会话落分", code == 200 and js.get("tenant_id") == "default", f"{code} {js.get('tenant_id')}")
    code, js = req("GET", f"/api/trajectory/sessions/{sid_d}/scores")
    check("匿名 default 会话可见（契约不变）", code == 200 and len(js.get("scores", [])) == 1, f"{code} n={len(js.get('scores', []))}")

    # ── 聚合不泄露 ──
    code, js = req("GET", f"/api/trajectory/sessions/{sid_a}/scores/summary", token=ta)
    cnt = (js.get("scores") or [{}])[0].get("count")
    check("summary A count=3", code == 200 and js.get("total_score_names") == 1 and cnt == 3, f"{code} {js}")
    code, js = req("GET", f"/api/trajectory/sessions/{sid_a}/scores/summary", token=tb)
    check("summary B 空聚合", code == 200 and js == {"scores": [], "total_score_names": 0}, f"{code} {js}")

    # ── 契约面：404/401 ──
    code, _ = req("GET", "/api/trajectory/sessions/no-such/scores", token=ta)
    check("缺行 scores 404", code == 404, f"{code}")
    code, _ = req("GET", "/api/trajectory/sessions/no-such/scores/summary", token=ta)
    check("缺行 summary 404", code == 404, f"{code}")
    code, _ = req("POST", "/api/trajectory/sessions/no-such/scores", {"name": "q"}, token=ta)
    check("缺行 add_score 404", code == 404, f"{code}")
    code, _ = req("GET", f"/api/trajectory/sessions/{sid_a}/scores", token="not-a-token")
    check("坏 token scores 401", code == 401, f"{code}")
    code, _ = req("GET", f"/api/trajectory/sessions/{sid_a}/scores/summary", token="not-a-token")
    check("坏 token summary 401", code == 401, f"{code}")

    # ── DB 回读：行键落盘 ──
    if score_ids:
        try:
            with sqlite3.connect(TRAJ_DB) as c:
                rows = c.execute(
                    f"SELECT tenant_id FROM trajectory_scores WHERE session_id='{sid_a}'"
                ).fetchall()
            keys = {r[0] for r in rows}
            check("DB 回读 A 会话评分行键全派生租户", keys == {tenant_ids["a"]}, f"keys={keys}")
        except sqlite3.OperationalError as exc:
            check("DB 回读 A 会话评分行键全派生租户", False, f"no column (pre-fix): {exc}")

    # ── 清痕 ──
    code, _ = req("DELETE", f"/api/trajectory/sessions/{sid_a}", token=ta)
    check("清痕 A 会话删除", code == 200, f"{code}")
    code, _ = req("DELETE", f"/api/trajectory/sessions/{sid_d}")
    check("清痕 default 会话删除", code == 200, f"{code}")
    for who in ("a", "b"):
        code, _ = req("DELETE", f"/api/nest/tenants/{tenant_ids[who]}")
        check(f"清痕 tenant.{who} 删除", code == 200, f"{code}")
    with sqlite3.connect(ENT_DB) as c:
        c.execute(f"DELETE FROM users WHERE username LIKE '{PREFIX}-%'")
        c.commit()
    with sqlite3.connect(TRAJ_DB) as c:
        remaining = c.execute(
            f"SELECT COUNT(*) FROM trajectory_scores WHERE session_id LIKE '{PREFIX}-%'"
        ).fetchone()[0]
        orphan_sess = c.execute(
            f"SELECT COUNT(*) FROM trajectory_sessions WHERE id LIKE '{PREFIX}-%'"
        ).fetchone()[0]
    check("清痕 remaining=0", remaining == 0 and orphan_sess == 0, f"scores={remaining} sessions={orphan_sess}")

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n== {passed}/{len(results)} PASS ==")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
