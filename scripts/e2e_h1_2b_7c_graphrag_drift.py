#!/usr/bin/env python3
"""H1-②b-⑦c graphrag 同族列漂移收口 live e2e 探针（--pre/--post 双向硬证）。

--pre（部署旧码时跑）：/api/cortex/graphrag/build 与 /query 必炸
（sqlite3.OperationalError: no column named relation_type / user_id）。
--post（重启载新码后跑）：build 真落 relations 行（真源 type 列）+query 读真源
type 输出契约键 "type"+用户边界（跨用户关系行不可见）+形状契约+清痕 remaining=0。

数据面：scratch user e2e-7c-<ts>，knowledge 行直插（被测面是 build/query 扫描链，
knowledge 创建路径非本拍对象；直插避开 ⑦b create_knowledge 自动建图 LLM 后台链）。
取证后全部清痕。
"""

import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8090"
DB = "/home/climbing/opensoul/data/opensoul.db"
MARK = "e2e-7c-%d" % int(time.time())
MODE = sys.argv[1] if len(sys.argv) > 1 else "--post"

results = []


def check(name: str, ok: object, detail: object = ""):
    results.append((name, bool(ok), str(detail)[:200]))
    print(("PASS " if ok else "FAIL ") + name + (" | " + str(detail)[:200] if detail != "" else ""))


def api(method, path, body=None, params=None):
    url = BASE + path
    if params:
        from urllib.parse import urlencode

        url += "?" + urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:300]}


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def main():
    # 0. health
    st, body = api("GET", "/api/cortex/enhanced/health")
    check("health", st == 200, f"{st} {body}")

    # 1. seed scratch knowledge（Acme Corp uses Kubernetes. = 抽取锚定文本）
    c = db()
    kid = MARK + "-k1"
    c.execute(
        "INSERT INTO knowledge (id, user_id, title, content) VALUES (?,?,?,?)",
        (kid, MARK, "e2e", "Acme Corp uses Kubernetes."),
    )
    # 跨用户对照：user-b 实体 X + 关系 Acme Corp→X（build 后 query 应不可见）
    xb = MARK + "-xb"
    acme_id = None
    c.commit()
    c.close()

    # 2. build
    st, body = api("POST", "/api/cortex/graphrag/build", params={"user_id": MARK})
    if MODE == "--pre":
        check("build 必炸 500（修前硬证）", st == 500, f"{st} {body}")
    else:
        check(
            "build 200 真落行",
            st == 200 and body.get("entities_new", 0) >= 2 and body.get("relations_new") == 1,
            f"{st} {body}",
        )

    if MODE == "--pre":
        st, body = api("POST", "/api/cortex/graphrag/query", {"entity_name": "Acme Corp", "depth": 2},
                       params={"user_id": MARK})
        check("query 必炸 500（修前硬证）", st == 500, f"{st} {body}")

    if MODE == "--post":
        c = db()
        rels = c.execute("SELECT * FROM relations").fetchall()
        probe_rels = [r for r in rels if r["source_id"] and r["target_id"]]
        check("relations 行恰真源列集", probe_rels and list(probe_rels[0].keys()) == [
            "id", "source_id", "target_id", "type", "properties", "created_at",
        ], [r["type"] for r in probe_rels])
        check("关系 type=uses 落行", any(r["type"] == "uses" for r in probe_rels), [r["type"] for r in probe_rels])
        ents = c.execute("SELECT * FROM entities WHERE user_id = ?", (MARK,)).fetchall()
        check("entities 行归属 scratch user", len(ents) >= 2 and all(e["user_id"] == MARK for e in ents),
              [e["name"] for e in ents])
        acme_id = next((e["id"] for e in ents if e["name"] == "Acme Corp"), None)

        # 3. query 契约
        st, body = api("POST", "/api/cortex/graphrag/query", {"entity_name": "Acme Corp", "depth": 2},
                       params={"user_id": MARK})
        ok_shape = st == 200 and set(body.keys()) == {
            "center", "entities", "relations", "depth", "total_entities", "total_relations",
        }
        rel0 = (body.get("relations") or [{}])[0]
        check("query 200+形状契约", ok_shape, f"{st} {sorted(body.keys()) if isinstance(body, dict) else body}")
        check("query 关系键 type=uses 契约键不变", rel0.get("type") == "uses" and rel0.get("direction") == "out", rel0)
        check("query 关系恰 1 行（去重）", body.get("total_relations") == 1, body.get("total_relations"))

        # 4. 用户边界：跨用户关系不可见（⑦a「跨用户邻居不暴露」）
        if acme_id:
            c.execute(
                "INSERT INTO entities (id, user_id, name, type, description, properties) VALUES (?,?,?,?,?,?)",
                (xb, MARK + "-other", "X", "person", "", "{}"),
            )
            c.execute(
                "INSERT INTO relations (id, source_id, target_id, type, properties) VALUES (?,?,?,?,?)",
                (MARK + "-xr", acme_id, xb, "cross_user_probe", "{}"),
            )
            c.commit()
            st, body = api("POST", "/api/cortex/graphrag/query", {"entity_name": "Acme Corp", "depth": 2},
                           params={"user_id": MARK})
            types = [r.get("type") for r in body.get("relations", [])]
            names = [e.get("name") for e in body.get("entities", [])]
            check("跨用户关系不可见", "cross_user_probe" not in types and "X" not in names, (types, names))
        c.close()

        # 5. 未知实体 404 契约
        st, body = api("POST", "/api/cortex/graphrag/query", {"entity_name": "NoSuchEntity"},
                       params={"user_id": MARK})
        check("未知实体 404 契约", st == 404, f"{st} {body}")

    # 6. 清痕
    c = db()
    # relations：任一端属于探针实体的行
    ents = [r["id"] for r in c.execute("SELECT id FROM entities WHERE user_id IN (?,?)", (MARK, MARK + "-other"))]
    if ents:
        q = ",".join("?" * len(ents))
        c.execute(f"DELETE FROM relations WHERE source_id IN ({q}) OR target_id IN ({q})", ents + ents)
    c.execute("DELETE FROM entities WHERE user_id IN (?,?)", (MARK, MARK + "-other"))
    c.execute("DELETE FROM knowledge WHERE user_id = ?", (MARK,))
    c.commit()
    remaining = (
        c.execute("SELECT COUNT(*) FROM knowledge WHERE user_id = ?", (MARK,)).fetchone()[0]
        + c.execute("SELECT COUNT(*) FROM entities WHERE user_id IN (?,?)", (MARK, MARK + "-other")).fetchone()[0]
    )
    c.close()
    check("清痕 remaining=0", remaining == 0, remaining)

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n== {MODE} {passed}/{len(results)} ==")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
