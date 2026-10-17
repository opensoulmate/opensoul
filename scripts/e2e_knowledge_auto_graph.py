#!/usr/bin/env python3
"""H1-②b-⑦b 知识入库自动建图 e2e 探针（修前/修后同探针双向硬证）。

--pre  在旧服务（未接线）上跑：create 200 但图面零行=缺口硬证；
--post 在载新码服务上跑：create→后台建图→entities 行真落库（真 LLM 抽取）。

隔离/清痕：探针独占 user UUID，图行/知识行按 user/knowledge id 精确清，
remaining=0 验收。纯 urllib+sqlite3，零外部依赖。
"""

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://localhost:8090"
DB = "/home/climbing/opensoul/data/opensoul.db"
PROBE_USER = "e2e9b000-0000-4000-8000-000000000001"
CONTENT = (
    "张三是北京大学的教授，专注于量子计算研究。"
    "李四是他的研究同事，也在北京大学工作。"
    "北京大学位于北京。"
)


def api(method, path, payload=None):
    url = f"{BASE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body) if body else {}
        except ValueError:
            return e.code, {"raw": body}


def db_counts(user):
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as c:
        ent = c.execute(
            "SELECT COUNT(*) FROM entities WHERE user_id=?", (user,)
        ).fetchone()[0]
        rel = c.execute(
            "SELECT COUNT(*) FROM relations r JOIN entities e ON r.source_id=e.id "
            "WHERE e.user_id=?",
            (user,),
        ).fetchone()[0]
        kn = c.execute(
            "SELECT COUNT(*) FROM knowledge WHERE user_id=?", (user,)
        ).fetchone()[0]
    return ent, rel, kn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre", action="store_true")
    ap.add_argument("--post", action="store_true")
    ap.add_argument("--timeout", type=int, default=0)
    args = ap.parse_args()
    mode = "pre" if args.pre else "post"
    timeout = args.timeout or (20 if args.pre else 90)

    ok = []
    fail = []

    def check(name, cond, detail=""):
        (ok if cond else fail).append((name, detail))
        print(f"{'PASS' if cond else 'FAIL'} | {name} | {detail}")

    status, health = api("GET", "/api/health")
    check("health 200", status == 200, f"{status}")

    ent0, rel0, kn0 = db_counts(PROBE_USER)
    check("baseline clean", (ent0, rel0, kn0) == (0, 0, 0), f"{ent0}/{rel0}/{kn0}")

    status, row = api(
        "POST",
        f"/api/knowledge/?user_id={PROBE_USER}",
        {"title": f"e2e-auto-graph-{mode}", "content": CONTENT, "source": "e2e", "content_type": "text"},
    )
    kid = row.get("id")
    check("create 200 + contract", status == 200 and kid and "title" in row and "tags" in row, f"{status} id={kid}")

    deadline = time.time() + timeout
    ent1 = rel1 = 0
    while time.time() < deadline:
        ent1, rel1, _ = db_counts(PROBE_USER)
        if ent1 > 0:
            break
        time.sleep(2)
    ent1, rel1, kn1 = db_counts(PROBE_USER)

    if args.pre:
        check(
            "pre: create 后图面零行（未接线缺口硬证）",
            ent1 == 0 and rel1 == 0,
            f"entities={ent1} relations={rel1} waited={timeout}s",
        )
    else:
        check(
            "post: 自动建图真落 entities 行",
            ent1 >= 1,
            f"entities={ent1} relations={rel1} waited<={timeout}s",
        )
        check("post: 图行归属探针 user", ent1 >= 1, f"user={PROBE_USER}")
        print(f"INFO | relations extracted (informational) | {rel1}")

    # 清痕
    status, _ = api("DELETE", f"/api/knowledge/{kid}?user_id={PROBE_USER}")
    check("cleanup knowledge 200", status == 200, str(status))
    with sqlite3.connect(DB) as c:
        c.execute(
            "DELETE FROM relations WHERE source_id IN (SELECT id FROM entities WHERE user_id=?) "
            "OR target_id IN (SELECT id FROM entities WHERE user_id=?)",
            (PROBE_USER, PROBE_USER),
        )
        c.execute("DELETE FROM entities WHERE user_id=?", (PROBE_USER,))
        c.execute("DELETE FROM knowledge WHERE user_id=?", (PROBE_USER,))
        c.execute("DELETE FROM knowledge_chunks WHERE knowledge_id=?", (kid,))
        c.commit()
    ent2, rel2, kn2 = db_counts(PROBE_USER)
    check("remaining=0", (ent2, rel2, kn2) == (0, 0, 0), f"{ent2}/{rel2}/{kn2}")

    print(f"\n== {mode} summary: {len(ok)} ok / {len(fail)} fail ==")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
