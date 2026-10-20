#!/usr/bin/env python3
"""e2e: POST /relations 写面归属闸 live 探针（写=探针自建行清痕归零；零事件行面
=TR-② 纪律）。

真数据行实证：seed user A/B + A 名下 2 实体 + B 名下 1 实体，验证归属闸两端判定。

探针项（修后行为面）：
  P1 同主两端（匿名自报 a_id）→ 200 + 真落行
  P2 跨用户端（A+B 实体混端）→ 404 "Entity not found" + 零新行
  P3 不存在实体 id → 404 + 零新行（修前照写=悬挂行）
  P4 认证派生键（A token 无 user_id）→ 200 + 真落行
  P5 认证+跨用户端 → 404 + 零新行
  P6 认证+自报他人档案 → 403 "user mismatch" + 零新行
  P7 坏 token → 401（修前 200 无鉴权轴）
  P8 匿名缺省 'default' 写 A 实体 → 404（修前 200 幽灵写）
  H  timeline total_events 前后 delta==0（TR-② 纪律零事件行面）
  I  清痕 users/relations/entities 标记行 remaining=0

用法：python3 scripts/e2e_graph_relation_gate.py --base http://127.0.0.1:8090
"""

import argparse
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import httpx

DB_PATH = Path.home() / "opensoul" / "data" / "opensoul.db"
MARKER = f"e2e-rgate-{int(time.time())}"
EA1 = str(uuid.uuid4())
EA2 = str(uuid.uuid4())
EB1 = str(uuid.uuid4())
ENT_IDS = (EA1, EA2, EB1)

PASS = 0
FAIL = 0
RESULTS: list = []


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        RESULTS.append(f"  {name} PASS {detail}")
    else:
        FAIL += 1
        RESULTS.append(f"  {name} FAIL {detail}")


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
        for eid, uid, name in (
            (EA1, a_id, f"{MARKER}-a1"),
            (EA2, a_id, f"{MARKER}-a2"),
            (EB1, b_id, f"{MARKER}-b1"),
        ):
            c.execute(
                "INSERT INTO entities (id, user_id, name, type, description, properties, "
                "created_at, updated_at) VALUES (?, ?, ?, 'concept', 'e2e', '{}', ?, ?)",
                (eid, uid, name, now, now),
            )
        c.commit()
    return a_id, b_id


def rel_count() -> int:
    with sqlite3.connect(DB_PATH) as c:
        # DISTINCT id：一行关系同时命中 source/target 两端，禁双计（探针自纠）。
        return c.execute(
            "SELECT COUNT(DISTINCT id) FROM relations WHERE "
            "source_id IN (?, ?, ?) OR target_id IN (?, ?, ?)",
            ENT_IDS + ENT_IDS,
        ).fetchone()[0]


def cleanup() -> int:
    with sqlite3.connect(DB_PATH) as c:
        for eid in ENT_IDS:
            c.execute(
                "DELETE FROM relations WHERE source_id = ? OR target_id = ?", (eid, eid)
            )
        c.execute("DELETE FROM entities WHERE name LIKE ?", (f"{MARKER}%",))
        c.execute("DELETE FROM users WHERE username LIKE ?", (f"{MARKER}%",))
        c.commit()
        remaining = rel_count()
        remaining += c.execute(
            "SELECT COUNT(*) FROM entities WHERE name LIKE ?", (f"{MARKER}%",)
        ).fetchone()[0]
        remaining += c.execute(
            "SELECT COUNT(*) FROM users WHERE username LIKE ?", (f"{MARKER}%",)
        ).fetchone()[0]
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
    hdr_a = {"Authorization": f"Bearer {make_token(a_id)}"}
    hdr_bad = {"Authorization": "Bearer garbage"}

    def post(src, dst, **kw):
        params = kw.pop("params", None)
        headers = kw.pop("headers", None)
        return client.post(
            "/api/graph/relations",
            params=params,
            headers=headers,
            json={
                "source_entity_id": src,
                "target_entity_id": dst,
                "relation_type": "related",
                "properties": {"probe": MARKER},
            },
            timeout=30,
        )

    try:
        # P1 同主两端（匿名自报）→ 200 + 真落行
        before = rel_count()
        r = post(EA1, EA2, params={"user_id": a_id})
        check("P1", r.status_code == 200 and rel_count() == before + 1,
              f"same-owner anon create 200 + row (got {r.status_code}, rows {rel_count()})")

        # P2 跨用户混端 → 404 + 零新行
        before = rel_count()
        r = post(EA1, EB1, params={"user_id": a_id})
        check("P2", r.status_code == 404 and rel_count() == before,
              f"cross-user end 404 zero-row (got {r.status_code}, rows {rel_count()})")

        # P3 不存在实体 id → 404 + 零新行
        before = rel_count()
        r = post(EA1, str(uuid.uuid4()), params={"user_id": a_id})
        check("P3", r.status_code == 404 and rel_count() == before,
              f"ghost id 404 zero-row (got {r.status_code}, rows {rel_count()})")

        # P4 认证派生键 → 200 + 真落行
        before = rel_count()
        r = post(EA1, EA2, headers=hdr_a)
        check("P4", r.status_code == 200 and rel_count() == before + 1,
              f"authed derived create 200 + row (got {r.status_code}, rows {rel_count()})")

        # P5 认证+跨用户端 → 404 + 零新行
        before = rel_count()
        r = post(EA1, EB1, headers=hdr_a)
        check("P5", r.status_code == 404 and rel_count() == before,
              f"authed cross-user end 404 zero-row (got {r.status_code}, rows {rel_count()})")

        # P6 认证+自报他人档案 → 403 + 零新行
        before = rel_count()
        r = post(EA1, EA2, params={"user_id": b_id}, headers=hdr_a)
        detail = r.json().get("detail", "") if r.status_code == 403 else ""
        check("P6", r.status_code == 403 and "user mismatch" in detail and rel_count() == before,
              f"conflict 403 zero-row (got {r.status_code}, rows {rel_count()})")

        # P7 坏 token → 401
        before = rel_count()
        r = post(EA1, EA2, headers=hdr_bad)
        check("P7", r.status_code == 401 and rel_count() == before,
              f"bad token 401 zero-row (got {r.status_code}, rows {rel_count()})")

        # P8 匿名缺省 'default' 写 A 实体 → 404（幽灵写面收口）
        before = rel_count()
        r = post(EA1, EA2)
        check("P8", r.status_code == 404 and rel_count() == before,
              f"anon default profile gated 404 zero-row (got {r.status_code}, rows {rel_count()})")
    finally:
        remaining = cleanup()

    t1 = timeline_total(client)
    check("H", t1 == t0, f"timeline delta=0 (before={t0} after={t1})")
    check("I", remaining == 0, f"users/relations/entities cleanup remaining={remaining}")

    for line in RESULTS:
        print(line)
    print(f"== e2e graph relation gate: {PASS} passed / {FAIL} failed ==")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
