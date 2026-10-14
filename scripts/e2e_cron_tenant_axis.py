"""DEC-⑦-⑦ cron 面 live e2e 探针（真 :8090 栈，取证后清痕 remaining=0）。

覆盖：真源收口（修前幽灵表空集→修后真命中）/写点行归属（create→
stamp_job_tenant→jobs.json 回读行键）/三面检索隔离/改删闸（403/404 行存活）
/hermes 未知键往返存活（CLI 重写 store 后行键仍在）/级联清痕。
"""

import json
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

BASE = "http://localhost:8090"
STORE = Path.home() / ".hermes" / "cron" / "jobs.json"
EVID = []


def ev(name, ok, detail=""):
    EVID.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(f"{'PASS' if ok else 'FAIL'} {name}: {str(detail)[:160]}")


def store_rows():
    return json.loads(STORE.read_text(encoding="utf-8"))["jobs"]


def row(job_id):
    for r in store_rows():
        if str(r.get("id")) == str(job_id):
            return r
    return None


def main():
    c = httpx.Client(base_url=BASE, timeout=30)
    suffix = uuid.uuid4().hex[:6]
    uname_a, uname_b = f"e2e-cron-a-{suffix}", f"e2e-cron-b-{suffix}"
    pwd = "E2eCronPass!42"

    # 0. health
    r = c.get("/api/health")
    ev("health 200", r.status_code == 200, r.status_code)

    # 1. 真租户×2 + enterprise register→login→token
    ta = c.post("/api/nest/tenants", json={"name": f"e2e-cron-ta-{suffix}"}).json()
    tb = c.post("/api/nest/tenants", json={"name": f"e2e-cron-tb-{suffix}"}).json()
    ta_id, tb_id = ta["tenant_id"], tb["tenant_id"]
    ev("tenants created", ta_id.startswith("tenant") and tb_id.startswith("tenant"), f"{ta_id} {tb_id}")

    tokens = {}
    for uname, tid in ((uname_a, ta_id), (uname_b, tb_id)):
        rr = c.post(
            "/api/user/register",
            json={
                "username": uname,
                "email": f"{uname}@e2e.local",
                "password": pwd,
                "tenant_id": tid,
            },
        )
        if rr.status_code != 200:
            ev(f"register {uname}", False, rr.text)
            return
        lr = c.post(
            "/api/user/login",
            data={"username": uname, "password": pwd},
        )
        assert lr.status_code == 200, lr.text
        tokens[uname] = {"Authorization": "Bearer " + lr.json()["access_token"]}
    ev("register→login tokens", len(tokens) == 2, list(tokens))

    # 2. 修后真源：匿名 default 视图真命中（修前同探针 []=幽灵表）
    r = c.get("/api/search/unified", params={"q": "update", "sources": "cron", "limit": 10})
    hits = r.json()["by_source"]["cron"]
    ev("true-source hits (anon default)", len(hits) >= 1, [h["job_id"] for h in hits][:5])
    ev(
        "result shape unchanged",
        all(set(h.keys()) == {"source", "icon", "title", "snippet", "job_id", "enabled"} for h in hits),
        hits[0] if hits else "none",
    )

    # 3. A create→写点行归属落键（jobs.json 回读）
    marker = f"e2e-cron-probe-alpha-{suffix}"
    r = c.post(
        "/api/cron/create",
        json={"name": "e2e cron probe alpha", "schedule": "0 12 * * *", "prompt": marker},
        headers=tokens[uname_a],
    )
    ok = r.status_code == 200
    data = r.json() if ok else {}
    ja = data.get("job_id")
    ev("A create 200 + stamp", ok and data.get("tenant_stamped") is True, data)
    row_a = row(ja) if ja else None
    ev("store read-back row key", bool(row_a) and row_a.get("tenant_id") == ta_id,
       (row_a or {}).get("tenant_id"))

    # 4. 三面检索隔离
    ra = c.get("/api/search/unified", params={"q": marker, "sources": "cron"},
               headers=tokens[uname_a]).json()["by_source"]["cron"]
    rb = c.get("/api/search/unified", params={"q": marker, "sources": "cron"},
               headers=tokens[uname_b]).json()["by_source"]["cron"]
    rn = c.get("/api/search/unified", params={"q": marker, "sources": "cron"}).json()["by_source"]["cron"]
    ev("A sees own", [h["job_id"] for h in ra] == [ja], ra)
    ev("B hidden", rb == [], rb)
    ev("anon hidden", rn == [], rn)

    # 5. 改闸：A 对 default 行（生产作业 4b0cb16e1e99）→403 行零改写
    ops_row_before = json.dumps(row("4b0cb16e1e99"), sort_keys=True)
    r = c.post("/api/cron/4b0cb16e1e99/pause", headers=tokens[uname_a])
    ev("cross-tenant pause 403", r.status_code == 403, r.json().get("detail", ""))
    ev("row untouched after 403", json.dumps(row("4b0cb16e1e99"), sort_keys=True) == ops_row_before)

    # 6. 删闸：A 删 default 行→404 行存活（与 not-found 同契约）
    r = c.delete("/api/cron/4b0cb16e1e99", headers=tokens[uname_a])
    ev("cross-tenant delete 404", r.status_code == 404, r.status_code)
    ev("row alive after 404", row("4b0cb16e1e99") is not None)

    # 7. own 行管理 + hermes CLI 未知键往返存活
    r = c.post(f"/api/cron/{ja}/pause", headers=tokens[uname_a])
    ev("owner pause 200", r.status_code == 200, r.status_code)
    cli_rc = -1
    if ja:
        p = subprocess.run(["hermes", "cron", "resume", ja], capture_output=True, text=True, timeout=60)
        cli_rc = p.returncode
    row_after_cli = row(ja) if ja else None
    ev("hermes CLI roundtrip preserves tenant key",
       cli_rc == 0 and bool(row_after_cli) and row_after_cli.get("tenant_id") == ta_id,
       f"rc={cli_rc} key={(row_after_cli or {}).get('tenant_id')}")
    r = c.delete(f"/api/cron/{ja}", headers=tokens[uname_a])
    ev("owner delete 200 + row gone", r.status_code == 200 and row(ja) is None, r.status_code)

    # 8. B 侧对称 + A 碰 B 行被闸
    r = c.post("/api/cron/create", json={"name": "e2e cron probe beta", "schedule": "0 13 * * *",
                                         "prompt": f"e2e-cron-probe-beta-{suffix}"},
               headers=tokens[uname_b])
    jb = r.json().get("job_id")
    ev("B create stamped", r.json().get("tenant_stamped") is True and (row(jb) or {}).get("tenant_id") == tb_id,
       (row(jb) or {}).get("tenant_id"))
    r = c.post(f"/api/cron/{jb}/pause", headers=tokens[uname_a])
    ev("A touch B row 403", r.status_code == 403, r.status_code)
    r = c.delete(f"/api/cron/{jb}", headers=tokens[uname_b])
    ev("B delete own 200 + gone", r.status_code == 200 and row(jb) is None, r.status_code)

    # 9. 坏 token 401
    r = c.post("/api/cron/create", json={"name": "x", "schedule": "0 * * * *", "prompt": "x"},
               headers={"Authorization": "Bearer not-a-token"})
    ev("bad token 401", r.status_code == 401, r.status_code)

    # 10. 清痕：租户+用户（双库）+审计行，remaining=0
    for tid in (ta_id, tb_id):
        c.delete(f"/api/nest/tenants/{tid}")
    users_db = Path("data/enterprise.db")
    with sqlite3.connect(users_db) as conn:
        conn.execute("DELETE FROM users WHERE username LIKE 'e2e-cron-%'")
        conn.commit()
    os_db = Path("data/opensoul.db")
    with sqlite3.connect(os_db) as conn:
        conn.execute("DELETE FROM users WHERE username LIKE 'e2e-cron-%'")
        conn.commit()
    remaining_jobs = [j for j in store_rows() if "e2e-cron-probe" in json.dumps(j)]
    with sqlite3.connect(users_db) as conn:
        remaining_users = conn.execute(
            "SELECT COUNT(*) FROM users WHERE username LIKE 'e2e-cron-%'"
        ).fetchone()[0]
    with sqlite3.connect(os_db) as conn:
        remaining_users += conn.execute(
            "SELECT COUNT(*) FROM users WHERE username LIKE 'e2e-cron-%'"
        ).fetchone()[0]
    tenants_left = c.get("/api/nest/tenants").json()
    # 响应为 dict 形（tenant_id→tenant）——按整载荷串查残留（修探针解析 nit）
    n_left = json.dumps(tenants_left).count(suffix) if tenants_left else 0
    ev("cleanup remaining=0", not remaining_jobs and remaining_users == 0 and n_left == 0,
       f"jobs={len(remaining_jobs)} users={remaining_users} tenants={n_left}")

    failed = [e for e in EVID if not e["ok"]]
    print(json.dumps({"total": len(EVID), "passed": len(EVID) - len(failed),
                      "failed": len(failed)}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
