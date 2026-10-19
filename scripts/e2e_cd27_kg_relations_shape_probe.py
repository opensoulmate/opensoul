#!/usr/bin/env python3
"""CD-㉗ live 真栈探针：GET /api/graph/entities/{id} relations 元素键真源形状 + 部署 chunk 回读。

真 e2e（urllib 真 HTTP 栈打 live :8090 真服务）：
1. 真建实体×2 + 真建关系（POST /api/entity/ + POST /api/graph/relations）；
2. GET /api/graph/entities/{id} 回读 relations 元素键集——恰真源行键
   {id,source_id,target_id,type,properties,created_at,source_name,target_name}、
   source_entity_id/target_entity_id/relation_type 硬缺席（修前 UI 消费的漂移键）；
3. live :8090 HTTP 回读部署 chunk（与 deploy_cd27_admin.py 同判据）；
4. 取证后清痕（DELETE 实体 + SQLite 直删关系行），remaining=0。
"""
import json
import sqlite3
import sys
import urllib.error
import urllib.request

BASE = "http://localhost:8090"
DB = "/home/climbing/opensoul/data/opensoul.db"
TRUE_ELEMENT_KEYS = {
    "id", "source_id", "target_id", "type", "properties",
    "created_at", "source_name", "target_name",
}
DRIFT_KEYS = {"source_entity_id", "target_entity_id", "relation_type"}

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS" if ok else "FAIL"), name, detail)


def req(method: str, path: str, body=None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


created_entities = []
try:
    # 1. 真建实体×2 + 关系
    s, a = req("POST", "/api/entity/?user_id=probe-cd27", {"name": "probe-cd27-A", "entity_type": "concept"})
    check("create entity A", s == 200 and "id" in a, f"status={s}")
    s, b = req("POST", "/api/entity/?user_id=probe-cd27", {"name": "probe-cd27-B", "entity_type": "concept"})
    check("create entity B", s == 200 and "id" in b, f"status={s}")
    created_entities = [a.get("id"), b.get("id")]

    s, rel = req(
        "POST",
        "/api/graph/relations",
        {
            "source_entity_id": a["id"],
            "target_entity_id": b["id"],
            "relation_type": "related",
            "properties": {},
        },
    )
    check("create relation", s == 200 and "id" in rel, f"status={s} id={rel.get('id')}")

    # 2. entity detail relations 元素键真源形状
    s, detail = req("GET", f"/api/graph/entities/{a['id']}?user_id=probe-cd27")
    check("entity detail 200", s == 200, f"status={s}")
    rels = detail.get("relations", [])
    check("relations non-empty", len(rels) >= 1, f"n={len(rels)}")
    if rels:
        keys = set(rels[0].keys())
        check("element keys == true shape", keys == TRUE_ELEMENT_KEYS, f"keys={sorted(keys)}")
        check("drift keys absent", not (keys & DRIFT_KEYS), f"hit={sorted(keys & DRIFT_KEYS)}")
        check(
            "UI consumed keys present",
            {"id", "source_id", "target_id", "type"} <= keys,
            f"keys={sorted(keys)}",
        )
        check(
            "source_id row value",
            rels[0].get("source_id") == a["id"] and rels[0].get("type") == "related",
            f"source_id={rels[0].get('source_id')} type={rels[0].get('type')}",
        )

    # 3. live :8090 部署 chunk 回读（漂移串残留=0/真源消费在场）
    chunk = "/static/admin/_next/static/chunks/1s6zdr2nzuorp.js"
    s2 = urllib.request.urlopen(BASE + chunk, timeout=30)
    text = s2.read().decode("utf-8", errors="ignore")
    check("chunk served", s2.status == 200 and "添加关系" in text, f"status={s2.status} len={len(text)}")
    for k in ("source_entity_id===", ".target_entity_id.slice", ".source_entity_id.slice"):
        check(f"chunk drift residue {k!r}=0", text.count(k) == 0, f"count={text.count(k)}")
    for k in ("target_id.slice", "source_id===", "source_entity_id:"):
        check(f"chunk true-source {k!r} in place", text.count(k) >= 1, f"count={text.count(k)}")
finally:
    # 4. 清痕：SQLite 直删关系行（无 DELETE /relations 端点，delete_entity 不级联）+ DELETE 实体
    con = sqlite3.connect(DB)
    if created_entities:
        qmarks = ",".join("?" for _ in created_entities)
        before = con.execute(
            f"SELECT COUNT(*) FROM relations WHERE source_id IN ({qmarks}) OR target_id IN ({qmarks})",
            [*created_entities, *created_entities],
        ).fetchone()[0]
        print("probe relations before cleanup:", before)
        con.execute(
            f"DELETE FROM relations WHERE source_id IN ({qmarks}) OR target_id IN ({qmarks})",
            [*created_entities, *created_entities],
        )
        con.commit()
    left = con.execute(
        "SELECT COUNT(*) FROM relations WHERE source_id IN (SELECT id FROM entities WHERE name LIKE 'probe-cd27%')"
        " OR target_id IN (SELECT id FROM entities WHERE name LIKE 'probe-cd27%')"
    ).fetchone()[0]
    con.close()
    for eid in created_entities:
        if eid:
            s, d = req("DELETE", f"/api/entity/{eid}?user_id=probe-cd27")
            print("cleanup entity", eid, s)
    con2 = sqlite3.connect(DB)
    ent_left = con2.execute(
        "SELECT COUNT(*) FROM entities WHERE name LIKE 'probe-cd27%'"
    ).fetchone()[0]
    con2.close()
    check("cleanup remaining=0", left == 0 and ent_left == 0, f"relations={left} entities={ent_left}")

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
