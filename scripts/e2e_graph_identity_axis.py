#!/usr/bin/env python3
"""e2e: graph 面 user_id 轴 bind_identity 收口 live 探针（读面为主，写=探针自建
行清痕归零；零事件行面=TR-② 纪律）。

真数据行实证：seed user A/B + A 名下 2 实体 + 1 关系，验证派生键真流入数据过滤。

探针项（修后行为面）：
  A 匿名自报保留（既有本地多档案语义，F3b-⑥ 同款契约）
  B 匿名缺省契约不变
  C A token + user_id=B → 403 "user mismatch" ×7 端点冒读面收口电池
  D A token 无 user_id（派生）→ 200 ×7 端点（含 A 名下真实体真命中）
  E A token + user_id=A（确认绑定）→ 200
  F 坏 token → 401
  G health 200
  J B token 派生键与 A 行隔离（A 名下实体对 B 不可见 404）
  H timeline total_events 前后 delta==0（TR-② 纪律零事件行面）
  I 清痕 users/relations/entities 标记行 remaining=0

用法：python3 scripts/e2e_graph_identity_axis.py --base http://127.0.0.1:8090
"""

import argparse
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import httpx

DB_PATH = Path.home() / "opensoul" / "data" / "opensoul.db"
MARKER = f"e2e-gaxis-{int(time.time())}"
E1_ID = str(uuid.uuid4())
E2_ID = str(uuid.uuid4())

PASS = 0
FAIL = 0
RESULTS: list = []


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        RESULTS.append(f"  P{name} PASS {detail}")
    else:
        FAIL += 1
        RESULTS.append(f"  P{name} FAIL {detail}")


def seed():
    a_id, b_id = str(uuid.uuid4()), str(uuid.uuid4())
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB_PATH) as c:
        for uid, uname in ((a_id, f"{MARKER}-a"), (b_id, f"{MARKER}-b")):
            c.execute(
                "INSERT INTO users (id, username, email, password_hash, role, is_active, "
                "created_at, updated_at, tenant_id) VALUES (?, ?, ?, 'x', 'user', 1, ?, ?, 'default')",
                (uid, uname, f"{uname}@e2e.local", now, now),
            )
        # A 名下 2 实体 + 1 关系（派生键真流入数据过滤的真数据行）
        for eid, name in ((E1_ID, f"{MARKER}-ent1"), (E2_ID, f"{MARKER}-ent2")):
            c.execute(
                "INSERT INTO entities (id, user_id, name, type, description, properties, "
                "created_at, updated_at) VALUES (?, ?, ?, 'concept', 'e2e', '{}', ?, ?)",
                (eid, a_id, name, now, now),
            )
        c.execute(
            "INSERT INTO relations (id, source_id, target_id, type, properties, created_at) "
            "VALUES (?, ?, ?, 'related', '{}', ?)",
            (str(uuid.uuid4()), E1_ID, E2_ID, now),
        )
        c.commit()
    return a_id, b_id


def cleanup() -> int:
    with sqlite3.connect(DB_PATH) as c:
        c.execute("DELETE FROM relations WHERE source_id IN (?, ?) OR target_id IN (?, ?)",
                  (E1_ID, E2_ID, E1_ID, E2_ID))
        c.execute("DELETE FROM entities WHERE name LIKE ?", (f"{MARKER}%",))
        c.execute("DELETE FROM users WHERE username LIKE ?", (f"{MARKER}%",))
        c.commit()
        remaining = 0
        for sql, args in (
            ("SELECT COUNT(*) FROM relations WHERE source_id IN (?, ?) OR target_id IN (?, ?)",
             (E1_ID, E2_ID, E1_ID, E2_ID)),
            ("SELECT COUNT(*) FROM entities WHERE name LIKE ?", (f"{MARKER}%",)),
            ("SELECT COUNT(*) FROM users WHERE username LIKE ?", (f"{MARKER}%",)),
        ):
            remaining += c.execute(sql, args).fetchone()[0]
    return remaining


def make_token(uid: str) -> str:
    sys.path.insert(0, str(Path.home() / "opensoul"))
    from src.services.auth import create_access_token

    return create_access_token(user_id=uuid.UUID(uid), role="user")


def timeline_total(client: httpx.Client) -> int:
    r = client.get("/api/timeline/stats", timeout=30)
    return int(r.json().get("total_events", -1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8090")
    args = ap.parse_args()

    client = httpx.Client(base_url=args.base, timeout=30)
    t0 = timeline_total(client)
    a_id, b_id = seed()
    tok_a = make_token(a_id)
    tok_b = make_token(b_id)
    hdr_a = {"Authorization": f"Bearer {tok_a}"}
    hdr_b = {"Authorization": f"Bearer {tok_b}"}

    ENDPOINTS = [
        ("entities", "/api/graph/entities", {}),
        ("entity_detail", f"/api/graph/entities/{E1_ID}", {}),
        ("neighbors", f"/api/graph/entities/{E1_ID}/neighbors", {}),
        ("paths", "/api/graph/paths", {"source_id": E1_ID, "target_id": E2_ID}),
        ("relations", "/api/graph/relations", {}),
        ("full", "/api/graph/full", {}),
        ("root", "/api/graph/", {}),
    ]

    try:
        # A 匿名自报保留（F3b-⑥ 同款契约：匿名 user_id 保留自报）
        r = client.get("/api/graph/entities", params={"user_id": f"{MARKER}-a"})
        check("A", r.status_code == 200, f"anon self-report 200 (got {r.status_code})")

        # B 匿名缺省契约
        r = client.get("/api/graph/entities")
        check("B", r.status_code == 200, f"anon default 200 (got {r.status_code})")

        # C 冒读 403 电池 ×7（修前 200 直读 victim 档案）
        c_ok = []
        for name, path, params in ENDPOINTS:
            merged = dict(params)
            merged["user_id"] = b_id
            rr = client.get(path, params=merged, headers=hdr_a)
            detail = rr.json().get("detail", "") if rr.status_code == 403 else ""
            c_ok.append(rr.status_code == 403 and "user mismatch" in detail)
            if not (rr.status_code == 403 and "user mismatch" in detail):
                RESULTS.append(f"    [C-{name}] got {rr.status_code} detail={detail!r}")
        check("C", all(c_ok), f"7/7 conflict 403 (got {sum(c_ok)}/7)")

        # D 派生（未声明）200 ×7 + A 名下真实体真命中（派生键真流入数据过滤）
        d_ok = []
        for name, path, params in ENDPOINTS:
            rr = client.get(path, params=params, headers=hdr_a)
            d_ok.append(rr.status_code == 200)
            if rr.status_code != 200:
                RESULTS.append(f"    [D-{name}] got {rr.status_code}")
        check("D", all(d_ok), f"7/7 derived 200 (got {sum(d_ok)}/7)")

        r = client.get(f"/api/graph/entities/{E1_ID}", headers=hdr_a)
        hit = r.status_code == 200 and r.json().get("name") == f"{MARKER}-ent1"
        check("D2", hit, f"A derived key really matches row user_id (got {r.status_code})")

        r = client.get("/api/graph/entities", headers=hdr_a)
        names = [e.get("name") for e in r.json()] if r.status_code == 200 else []
        check("D3", f"{MARKER}-ent1" in names and f"{MARKER}-ent2" in names,
              f"list filtered by derived key shows A's rows ({len(names)} rows)")

        # E 确认绑定（自报==派生）
        r = client.get(f"/api/graph/entities/{E1_ID}", params={"user_id": a_id}, headers=hdr_a)
        check("E", r.status_code == 200, f"matching declared 200 (got {r.status_code})")

        # F 坏 token 401
        r = client.get("/api/graph/entities", headers={"Authorization": "Bearer garbage"})
        check("F", r.status_code == 401, f"bad token 401 (got {r.status_code})")

        # G health
        r = client.get("/api/graph/health")
        check("G", r.status_code == 200 and r.json().get("status") == "ok", "health 200 ok")

        # J B 派生键隔离：A 名下实体对 B 不可见
        r = client.get(f"/api/graph/entities/{E1_ID}", headers=hdr_b)
        check("J", r.status_code == 404, f"A's entity hidden from B (got {r.status_code})")
    finally:
        remaining = cleanup()

    # H timeline delta
    t1 = timeline_total(client)
    check("H", t1 == t0, f"timeline delta=0 (before={t0} after={t1})")

    # I 清痕
    check("I", remaining == 0, f"users/relations/entities cleanup remaining={remaining}")

    for line in RESULTS:
        print(line)
    print(f"== e2e graph identity axis: {PASS} passed / {FAIL} failed ==")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
