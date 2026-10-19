#!/usr/bin/env python3
"""CD-㉘ live 真栈探针：实体类型键真源形状（entity_type 在场/type 硬缺席）+ 部署 chunk 回读。

真 e2e（urllib 真 HTTP 栈打 live :8090 真服务）：
1. 真建实体（POST /api/entity/，entity_type=person）；
2. GET /api/graph/entities/{id} 回读实体键——entity_type 在场=='person'、
   type 硬缺席（修前 UI selectedEntity.type 消费的漂移键=响应面无 type 键的反面）；
3. GET /api/graph/entities 列表元素同形（e.type 列表/选项面收编的真源面）；
4. live :8090 HTTP 回读部署 chunk（与 deploy_cd28_admin.py 同判据）；
5. 取证后清痕（DELETE 实体），remaining=0。
"""
import json
import sqlite3
import sys
import urllib.error
import urllib.request

BASE = "http://localhost:8090"
DB = "/home/climbing/opensoul/data/opensoul.db"
CHUNK = "/static/admin/_next/static/chunks/2ik0nnq8pw0ml.js"

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
    # 1. 真建实体
    s, a = req(
        "POST",
        "/api/entity/?user_id=probe-cd28",
        {"name": "probe-cd28-A", "entity_type": "person"},
    )
    check("create entity", s == 200 and "id" in a, f"status={s}")
    created_entities = [a.get("id")]

    # 2. 实体详情键真源形状
    s, detail = req("GET", f"/api/graph/entities/{a['id']}?user_id=probe-cd28")
    check("entity detail 200", s == 200, f"status={s}")
    check("entity_type present", detail.get("entity_type") == "person", f"entity_type={detail.get('entity_type')}")
    check("type key HARD ABSENT", "type" not in detail, f"keys={sorted(detail.keys())}")

    # 3. 列表元素同形
    s, lst = req("GET", "/api/graph/entities?user_id=probe-cd28&limit=200")
    check("entity list 200", s == 200 and isinstance(lst, list), f"status={s}")
    mine = [e for e in lst if e.get("id") == a.get("id")]
    check("created entity in list", len(mine) == 1, f"n={len(mine)}")
    if mine:
        check("list element entity_type", mine[0].get("entity_type") == "person", f"got={mine[0].get('entity_type')}")
        check("list element type absent", "type" not in mine[0], f"keys={sorted(mine[0].keys())}")

    # 4. live :8090 部署 chunk 回读（漂移串残留=0/真源消费在场）
    s2 = urllib.request.urlopen(BASE + CHUNK, timeout=30)
    text = s2.read().decode("utf-8", errors="ignore")
    check("chunk served", s2.status == 200 and "添加关系" in text, f"status={s2.status} len={len(text)}")
    for k in (".type.toLowerCase", ".type!=="):
        check(f"chunk drift residue {k!r}=0", text.count(k) == 0, f"count={text.count(k)}")
    check("chunk true-source entity_type in place", text.count("entity_type") >= 10, f"count={text.count('entity_type')}")
    import re as _re
    n_type = len(_re.findall(r"\.type\b", text))
    check("chunk .type accesses ==1 (rel.type badge)", n_type == 1, f"count={n_type}")
finally:
    # 5. 清痕
    for eid in created_entities:
        if eid:
            s, d = req("DELETE", f"/api/entity/{eid}?user_id=probe-cd28")
            print("cleanup entity", eid, s)
    con = sqlite3.connect(DB)
    ent_left = con.execute(
        "SELECT COUNT(*) FROM entities WHERE name LIKE 'probe-cd28%'"
    ).fetchone()[0]
    con.close()
    check("cleanup remaining=0", ent_left == 0, f"entities={ent_left}")

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
