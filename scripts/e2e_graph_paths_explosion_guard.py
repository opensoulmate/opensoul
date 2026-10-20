#!/usr/bin/env python3
"""H1-②b-⑦a 留档④「paths 路径爆炸防护（dense 图）」live e2e 探针。

用法：
  python3 scripts/e2e_graph_paths_explosion_guard.py   # 修后（live 服务）

安全纪律：只触碰探针自建实体/关系（user_id=e2e-paths-<ts>）；生产
entities/relations/timeline 行数前后对账；收尾 SQL 清痕 remaining=0 含事件行面
（TR-② 纪律）。

修前证据说明（如实记）：修前无界枚举不在 live 跑（--pre 会拖住服务数秒且旧码
不可热装），工作量修前硬证=控制组（git worktree HEAD 旧码）同场景查询计数
22,360（tests/test_graph_paths_explosion_guard.py 断言细节回读）vs 修后预算界
PATH_EXPLORE_BUDGET=2000。
"""

import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE = "http://127.0.0.1:8090"
DB = str(Path.home() / "opensoul" / "data" / "opensoul.db")
TDB = str(Path.home() / "opensoul" / "data" / "timeline.db")
TS = str(int(time.time()))
U = "e2e-paths-" + TS
PASS = 0
FAIL = 0


def req(method, path, body=None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read().decode() or "{}"
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"detail": raw}


def check(name, cond, detail: object = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def db_counts():
    with sqlite3.connect(DB) as conn:
        e = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        r = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    try:
        with sqlite3.connect(TDB) as conn:
            t = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    except sqlite3.Error:
        t = -1
    return e, r, t


def _uid_str(user_id: str) -> str:
    """与 api/graph._resolve_user_id 同派生（清痕按派生键删，字面串删不到）。"""
    import hashlib

    h = hashlib.md5(user_id.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def seed_entity(name):
    st, body = req("POST", f"/api/entity/?user_id={U}", {"name": name, "entity_type": "probe"})
    if st != 200:
        raise SystemExit(f"seed entity failed: {st} {body}")
    return body["id"]


def seed_relation(src, dst, rtype="knows"):
    rid = TS + "-" + src[:6] + "-" + dst[:6] + "-" + str(seed_relation.n)
    seed_relation.n += 1
    with sqlite3.connect(DB) as conn:
        conn.execute(
            "INSERT INTO relations (id, source_id, target_id, type, properties) VALUES (?, ?, ?, ?, '{}')",
            (rid, src, dst, rtype),
        )
        conn.commit()
    return rid


seed_relation.n = 0


def paths_query(source, target, max_depth=5):
    t0 = time.time()
    st, body = req(
        "GET",
        f"/api/graph/paths?user_id={U}&source_id={source}&target_id={target}&max_depth={max_depth}",
    )
    return st, body, time.time() - t0


def main():
    print(f"== e2e_graph_paths_explosion_guard [POST] ==")
    base_e, base_r, base_t = db_counts()
    print(f"baseline entities={base_e} relations={base_r} timeline_events={base_t}")

    # --- 图 A：K8 团（28 边）+ 孤立终点（有实体无边=真无路但须搜完才知）---
    clique = [seed_entity(f"K{i}") for i in range(8)]
    for i in range(8):
        for j in range(i + 1, 8):
            seed_relation(clique[i], clique[j])
    isolated = seed_entity("ISOLATED")

    st, body, elapsed = paths_query(clique[0], isolated, max_depth=7)
    ok = (
        st == 200
        and set(body.keys())
        == {"source_id", "target_id", "max_depth", "paths", "count", "truncated"}
        and body.get("count") == 0
        and body.get("paths") == []
        and body.get("truncated") is True
    )
    check(
        f"dense K8 无路：count=0 + truncated=True 如实（预算耗尽）+ 6 键契约（{elapsed:.2f}s）",
        ok and elapsed < 30.0,
        (st, body, round(elapsed, 2)),
    )

    # --- 图 B：11 条平行 2 跳路→输出满 10 即止（truncated=False=完整非截断）---
    s = seed_entity("S")
    t = seed_entity("T")
    mids = []
    for i in range(11):
        m = seed_entity(f"M{i}")
        mids.append(m)
        seed_relation(s, m)
        seed_relation(m, t)

    st, body, elapsed = paths_query(s, t, max_depth=5)
    seqs = {tuple(step["entity_id"] for step in p) for p in body.get("paths", [])}
    ok = (
        st == 200
        and body.get("count") == 10
        and body.get("truncated") is False
        and len(seqs) == 10
        and seqs <= {(s, m, t) for m in mids}
    )
    check(f"11 平行路→恰 10 条 + truncated=False（输出满即止=完整语义）({elapsed:.2f}s)", ok, (st, body.get("count"), len(seqs)))

    # --- 图 C：恰 2 条路→truncated 恒 bool（形状回归锁）---
    s2 = seed_entity("S2")
    t2 = seed_entity("T2")
    m2 = seed_entity("M2")
    seed_relation(s2, t2)
    seed_relation(s2, m2)
    seed_relation(m2, t2)
    st, body, elapsed = paths_query(s2, t2, max_depth=5)
    ok = st == 200 and body.get("count") == 2 and isinstance(body.get("truncated"), bool) and body["truncated"] is False
    check(f"恰 2 条路 truncated 恒 bool=False ({elapsed:.2f}s)", ok, (st, body))

    # --- 图 D：小图确证无路→truncated=False（与预算截断空结果区分）---
    s3 = seed_entity("S3")
    t3 = seed_entity("T3")
    x3 = seed_entity("X3")
    seed_relation(s3, x3)  # S3-X3 与 T3 不连通
    st, body, elapsed = paths_query(s3, t3, max_depth=5)
    ok = st == 200 and body.get("count") == 0 and body.get("truncated") is False
    check(f"小图确证无路 truncated=False（禁与预算截断混读）({elapsed:.2f}s)", ok, (st, body))

    # --- 清痕：探针实体/关系全删（派生键删）+ timeline 零事件行面（TR-② 纪律）---
    with sqlite3.connect(DB) as conn:
        uid = _uid_str(U)
        ids = [r[0] for r in conn.execute("SELECT id FROM entities WHERE user_id = ?", (uid,)).fetchall()]
        for i in ids:
            conn.execute("DELETE FROM relations WHERE source_id = ? OR target_id = ?", (i, i))
        conn.execute("DELETE FROM entities WHERE user_id = ?", (uid,))
        conn.commit()
        rem_e = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        rem_r = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    _, _, rem_t = db_counts()
    check(
        "清痕 remaining=0（entities/relations 回基线）",
        (rem_e, rem_r) == (base_e, base_r),
        (rem_e, rem_r, base_e, base_r),
    )
    check(
        "timeline 零事件行面（TR-② 纪律 delta=0）",
        rem_t == base_t,
        (base_t, rem_t),
    )

    print(f"== [POST] PASS={PASS} FAIL={FAIL} ==")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
