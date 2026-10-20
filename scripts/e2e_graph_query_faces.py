#!/usr/bin/env python3
"""H1-②b-⑦a graph 查询面出口 live e2e 探针。

用法：
  python3 scripts/e2e_graph_query_faces.py --pre   # 修前（旧服务）：缺口硬证
  python3 scripts/e2e_graph_query_faces.py         # 修后：全链行为

安全纪律：只触碰探针自建实体/关系（user_id=e2e-graph-* / e2e-graphforeign-*）；
生产 entities/relations 行数前后对账；收尾 SQL 清痕 remaining=0。
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
TS = str(int(time.time()))
U1 = "e2e-graph-" + TS
U2 = "e2e-graphforeign-" + TS
PASS = 0
FAIL = 0


def req(method, path, body=None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
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
    with sqlite3.connect(DB) as c:
        e = c.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        r = c.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    return e, r


def _uid_str(user_id: str) -> str:
    """与 api/graph._resolve_user_id 同派生：非 UUID user_id→md5 派生 UUID
    落库——清痕必须按派生键删（探针自伤坑：按字面串 LIKE 匹配不到）。"""
    import hashlib

    h = hashlib.md5(user_id.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def seed_entity(name, user_id):
    st, body = req("POST", f"/api/entity/?user_id={user_id}", {"name": name, "entity_type": "probe"})
    if st != 200:
        raise SystemExit(f"seed entity failed: {st} {body}")
    return body["id"]


def seed_relation(src, dst, user_id, rtype="knows", properties=None):
    """修前 POST /relations 必 500（列漂移）——探针直接落 DB 行保后续面可测。"""
    rid = str(int(time.time() * 1000)) + "-" + src[:6]
    with sqlite3.connect(DB) as c:
        c.execute(
            "INSERT INTO relations (id, source_id, target_id, type, properties) VALUES (?, ?, ?, ?, ?)",
            (rid, src, dst, rtype, json.dumps(properties or {})),
        )
        c.commit()
    return rid


def main():
    pre = "--pre" in sys.argv
    mode = "PRE" if pre else "POST"
    print(f"== e2e_graph_query_faces [{mode}] ==")

    base_e, base_r = db_counts()
    print(f"baseline entities={base_e} relations={base_r}")

    # 探针图（U1）：A→B→C 链 + C→A 环 + C→D；跨用户边 A→X（X 在 U2）
    a = seed_entity("probeA", U1)
    b = seed_entity("probeB", U1)
    c = seed_entity("probeC", U1)
    d = seed_entity("probeD", U1)
    x = seed_entity("probeX", U2)

    # 1) POST /relations 列漂移缺口（修前 500；修后 200 契约）
    st, body = req(
        "POST",
        "/api/graph/relations",
        {
            "source_entity_id": a,
            "target_entity_id": b,
            "relation_type": "knows",
            "properties": {"w": 1},
        },
    )
    if pre:
        check("POST /relations 500（relation_type 列漂移）", st == 500, st)
    else:
        ok = st == 200 and body.get("relation_type") == "knows" and body.get("properties") == {"w": 1}
        check("POST /relations 200 契约（relation_type+properties dict）", ok, (st, body))

    seed_relation(b, c, U1, "mentors")
    seed_relation(c, a, U1, "knows")
    seed_relation(c, d, U1, "knows")
    seed_relation(a, x, U2, "knows")

    # 2) GET /relations 契约键
    st, rows = req("GET", f"/api/graph/relations?user_id={U1}")
    keys_ok = st == 200 and all("relation_type" in r for r in rows) and len(rows) >= 1
    if pre:
        check("GET /relations 回 relation_type 键（修前='type' 裸键）", not keys_ok, (st, rows[:1]))
    else:
        check("GET /relations 回 relation_type 键", keys_ok, (st, rows[:1]))

    # 3) GET /full properties dict + edges relation_type（修前 entities 在场必 500）
    st, g = req("GET", f"/api/graph/full?user_id={U1}")
    if pre:
        check("GET /full 500（properties TEXT 塞 dict 契约）", st == 500, st)
    else:
        props = {n["label"]: n["properties"] for n in g.get("nodes", [])}
        rts = {e.get("relation_type") for e in g.get("edges", [])}
        ok = st == 200 and props.get("probeA") == {} and "knows" in rts and "mentors" in rts
        check("GET /full 200：properties dict+edges relation_type", ok, (st, props, rts))

    # 4) GET / entity 展开出边（修前 UUID/TEXT 比较 bug：B 不被展开）
    st, g = req("GET", f"/api/graph/?user_id={U1}&entity_id={a}&depth=1")
    labels = {n.get("label") for n in g.get("nodes", [])}
    if pre:
        check("GET /?entity_id=A 出边邻居 B 缺席（比较 bug）", "probeB" not in labels, labels)
    else:
        check("GET /?entity_id=A 出边邻居 B 在场", "probeB" in labels, labels)

    # 5) neighbors 出口（修前 404 路由缺失）
    st, nb = req("GET", f"/api/graph/entities/{a}/neighbors?user_id={U1}")
    if pre:
        check("GET neighbors 404（出口缺失）", st == 404, st)
    else:
        dirs = {n["name"]: (n["direction"], n["relation_type"]) for n in nb.get("neighbors", [])}
        ok = (
            st == 200
            and set(nb.keys()) == {"entity", "neighbors"}
            and dirs.get("probeB") == ("outgoing", "knows")
        )
        check("GET neighbors 200：direction+relation_type 契约", ok, (st, dirs))

    # 6) neighbors 跨用户邻居不暴露
    st, nb = req("GET", f"/api/graph/entities/{a}/neighbors?user_id={U1}")
    names = [n["name"] for n in nb.get("neighbors", [])] if st == 200 else []
    if pre:
        check("neighbors 出口缺失（跨用户面不可达）", st == 404, st)
    else:
        check("neighbors 跨用户邻居不暴露", "probeX" not in names, names)

    # 7) neighbors 缺实体 404
    st, _ = req("GET", f"/api/graph/entities/00000000-0000-0000-0000-000000000000/neighbors?user_id={U1}")
    check("neighbors 缺实体 404", st == 404, st)

    # 8) paths 出口（修前 404 路由缺失）：A→C 多跳
    st, pr = req("GET", f"/api/graph/paths?user_id={U1}&source_id={a}&target_id={c}")
    if pre:
        check("GET paths 404（出口缺失）", st == 404, st)
    else:
        seqs = {tuple(s["entity_id"] for s in p) for p in pr.get("paths", [])}
        step_keys_ok = all(
            set(s.keys()) == {"entity_id", "name", "type", "via_relation"}
            for p in pr.get("paths", [])
            for s in p
        )
        ok = (
            st == 200
            # 契约随更新（留档④ 收口）：truncated 加性键进契约（修前恰 5 键）。
            and set(pr.keys())
            == {"source_id", "target_id", "max_depth", "paths", "count", "truncated"}
            and pr.get("truncated") is False
            and (a, b, c) in seqs
            and step_keys_ok
        )
        check("GET paths 200：A→C 多跳+步形状 4 键", ok, (st, pr.get("count"), seqs))

    # 9) paths max_depth=节点上限语义（无向：A—C 因 C→A 边直连，
    # 2 节点 [A,C] 与 3 节点 [A,B,C] 两条——探针期望自伤坑首版按有向写）
    st, pr = req("GET", f"/api/graph/paths?user_id={U1}&source_id={a}&target_id={c}&max_depth=2")
    if pre:
        check("paths 出口缺失（max_depth 面不可达）", st == 404, st)
    else:
        seqs2 = {tuple(s["entity_id"] for s in p) for p in pr.get("paths", [])}
        check("paths max_depth=2 恰 2 节点路径 [A,C]", pr.get("count") == 1 and seqs2 == {(a, c)}, (pr, seqs2))
        st2, pr2 = req("GET", f"/api/graph/paths?user_id={U1}&source_id={a}&target_id={c}&max_depth=3")
        seqs3 = {tuple(s["entity_id"] for s in p) for p in pr2.get("paths", [])}
        check(
            "paths max_depth=3 恰 2 条 [A,C]+[A,B,C]",
            pr2.get("count") == 2 and seqs3 == {(a, c), (a, b, c)},
            (pr2, seqs3),
        )

    # 10) paths 非法 max_depth 422（--pre 出口缺失=404）
    st, _ = req("GET", f"/api/graph/paths?user_id={U1}&source_id={a}&target_id={c}&max_depth=0")
    if pre:
        check("paths 出口缺失（422 面不可达）", st == 404, st)
    else:
        check("paths max_depth=0 → 422", st == 422, st)

    # 11) paths 缺/跨用户 404 同形
    st1, _ = req("GET", f"/api/graph/paths?user_id={U1}&source_id={a}&target_id={x}")
    st2, _ = req("GET", f"/api/graph/paths?user_id={U1}&source_id=00000000-0000-0000-0000-000000000000&target_id={c}")
    check("paths 跨用户终点 404", st1 == 404, st1)
    check("paths 缺实体起点 404", st2 == 404, st2)

    # 12) 跨用户中间节点不穿越：A→X→C 无路（X 在 U2）
    st, pr = req("GET", f"/api/graph/paths?user_id={U1}&source_id={a}&target_id={c}&max_depth=5")
    if not pre:
        # A→C 已有本地路（A-B-C / A-C），改查 A→X 终点已 404；穿越语义由
        # neighbors 跨用户不暴露 + find_path 用户边界（单测锁死）覆盖，live 侧
        # 以 A→X 404 与 probeX 不出现在 neighbors 为证（上面 6/11 项）。
        # 契约随更新（留档④ 收口）：truncated 加性键进契约（修前恰 5 键）。
        check(
            "paths 结构键集恰 6 键（含 truncated）",
            set(pr.keys())
            == {"source_id", "target_id", "max_depth", "paths", "count", "truncated"}
            and pr.get("truncated") is False,
            pr,
        )

    # 清痕：探针实体/关系全删（按 _resolve_user_id 派生键删——字面串删不到，
    # 探针自伤坑；连接对象命名 conn 禁与实体 id c 遮蔽）
    with sqlite3.connect(DB) as conn:
        uids = (_uid_str(U1), _uid_str(U2))
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM entities WHERE user_id IN (?, ?)", uids
            ).fetchall()
        ]
        for i in ids:
            conn.execute("DELETE FROM relations WHERE source_id = ? OR target_id = ?", (i, i))
        conn.execute("DELETE FROM entities WHERE user_id IN (?, ?)", uids)
        conn.commit()
        rem_e = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        rem_r = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    check("清痕 remaining=0（行数回基线）", (rem_e, rem_r) == (base_e, base_r), (rem_e, rem_r, base_e, base_r))

    print(f"== [{mode}] PASS={PASS} FAIL={FAIL} ==")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
