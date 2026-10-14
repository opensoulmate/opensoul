"""DEC-⑦-⑧ cron 管理读面 live e2e 探针（真 :8090 栈，取证后清痕 remaining=0）。

覆盖：/list 按行租户键过滤（A 见己行/B 不见/加性键 tenant_id）/ /get /history
归属闸（跨租户=404 与 not-found 同契约）/name 引用绕写闸双向硬证（修前 200
真改 vs 修后 403 行零改写）/引用判法与 CLI 对齐（name/独一前缀读面可达）。

用法：修前后各跑一次同探针（before=旧代码现场、after=重启新代码），对照取证。
"""

import json
import sqlite3
import sys
import uuid
from pathlib import Path

import httpx

BASE = "http://localhost:8090"
STORE = Path.home() / ".hermes" / "cron" / "jobs.json"
EVID = []


def ev(name: str, ok, detail: object = ""):
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
    uname_a, uname_b = f"e2e-cr8-a-{suffix}", f"e2e-cr8-b-{suffix}"
    pwd = "E2eCr8Pass!42"
    marker = f"e2e-cr8-probe-{suffix}"

    # 0. health + 基线
    r = c.get("/api/health")
    ev("health 200", r.status_code == 200, r.status_code)
    baseline_jobs = len(store_rows())
    ev("baseline jobs count", baseline_jobs > 0, baseline_jobs)

    # 1. 真租户×2 + register→login→token
    ta = c.post("/api/nest/tenants", json={"name": f"e2e-cr8-ta-{suffix}"}).json()
    tb = c.post("/api/nest/tenants", json={"name": f"e2e-cr8-tb-{suffix}"}).json()
    ta_id, tb_id = ta["tenant_id"], tb["tenant_id"]
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
            return 1
        lr = c.post("/api/user/login", data={"username": uname, "password": pwd})
        assert lr.status_code == 200, lr.text
        tokens[uname] = {"Authorization": "Bearer " + lr.json()["access_token"]}
    ev("register→login tokens", len(tokens) == 2, list(tokens))

    # 2. A create 探针作业（远期排程防触发）→ 行归属落键
    r = c.post(
        "/api/cron/create",
        json={"name": "e2e cr8 probe", "schedule": "0 0 1 1 *", "prompt": marker},
        headers=tokens[uname_a],
    )
    data = r.json() if r.status_code == 200 else {}
    ja = data.get("job_id")
    ev("A create 200 + stamp", r.status_code == 200 and data.get("tenant_stamped") is True, data)
    row_a = row(ja) if ja else None
    ev("store row key", bool(row_a) and row_a.get("tenant_id") == ta_id,
       (row_a or {}).get("tenant_id"))

    # 3. /list 隔离 + 加性键
    ra = c.get("/api/cron/list", headers=tokens[uname_a])
    rb = c.get("/api/cron/list", headers=tokens[uname_b])
    ok_a = ra.status_code == 200
    a_jobs = ra.json().get("jobs", []) if ok_a else []
    a_ids = [j.get("id") for j in a_jobs]
    b_ids = [j.get("id") for j in rb.json().get("jobs", [])] if rb.status_code == 200 else []
    ev("A list sees own", ja in a_ids, f"{ja} in {len(a_ids)} rows")
    ev("B list hides A row", ja not in b_ids, f"B sees {len(b_ids)} rows")
    probe_row = next((j for j in a_jobs if j.get("id") == ja), None)
    ev("list additive tenant_id", bool(probe_row) and probe_row.get("tenant_id") == ta_id,
       (probe_row or {}).get("tenant_id"))
    ev("list total==visible", ok_a and ra.json().get("total") == len(a_jobs),
       (ra.json().get("total") if ok_a else None, len(a_jobs)))

    # 4. /get 归属闸 + 引用判法
    r = c.get(f"/api/cron/{ja}", headers=tokens[uname_a])
    ev("A get own 200", r.status_code == 200, r.status_code)
    ev("get additive tenant_id",
       r.status_code == 200 and r.json().get("tenant_id") == ta_id,
       r.json().get("tenant_id") if r.status_code == 200 else r.status_code)
    r = c.get(f"/api/cron/{ja}", headers=tokens[uname_b])
    ev("B get cross 404", r.status_code == 404, r.status_code)
    r = c.get("/api/cron/e2e cr8 probe", headers=tokens[uname_a])
    ev("A get by name 200", r.status_code == 200 and r.json().get("id") == ja,
       r.status_code)
    r = c.get("/api/cron/e2e cr8 probe", headers=tokens[uname_b])
    ev("B get by name 404", r.status_code == 404, r.status_code)
    prefix = None
    if ja:
        for n in (6, 7, 8, 9, 10):
            if sum(1 for j in store_rows() if str(j.get("id")).startswith(ja[:n])) == 1:
                prefix = ja[:n]
                break
    if prefix:
        r = c.get(f"/api/cron/{prefix}", headers=tokens[uname_a])
        ev("A get unique prefix 200", r.status_code == 200 and r.json().get("id") == ja,
           (prefix, r.status_code))
    else:
        ev("A get unique prefix 200", False, "no unique prefix found")

    # 5. /history 归属闸
    r = c.get(f"/api/cron/{ja}/history", headers=tokens[uname_a])
    ev("A history 200", r.status_code == 200, r.status_code)
    r = c.get(f"/api/cron/{ja}/history", headers=tokens[uname_b])
    ev("B history cross 404", r.status_code == 404, r.status_code)

    # 6. name 引用绕写闸双向硬证（本探针核心）：
    #    B 用 A 行的 name 引用 pause——修前闸查无行放行→CLI 真 pause（200+
    #    行 enabled 被改）；修后 403 行零改写。
    row_before = json.dumps(row(ja), sort_keys=True) if ja else ""
    r = c.post("/api/cron/e2e cr8 probe/pause", headers=tokens[uname_b])
    row_after = json.dumps(row(ja), sort_keys=True) if ja else ""
    ev("B pause-by-name blocked 403", r.status_code == 403,
       f"{r.status_code} {r.json().get('detail', '') if r.status_code != 200 else 'BYPASS'}")
    ev("row untouched after name-gate", row_before == row_after,
       "same" if row_before == row_after else "MUTATED(修前 bypass 实证)")
    r = c.post("/api/cron/e2e cr8 probe/pause", headers=tokens[uname_a])
    ev("A pause-by-name own 200", r.status_code == 200, r.status_code)

    # 7. 清痕：探针作业删+租户删+用户删（双库），remaining=0
    if ja:
        c.post(f"/api/cron/{ja}/resume", headers=tokens[uname_a])
        rd = c.delete(f"/api/cron/{ja}", headers=tokens[uname_a])
        ev("probe job deleted", rd.status_code == 200 and row(ja) is None, rd.status_code)
    for tid in (ta_id, tb_id):
        c.delete(f"/api/nest/tenants/{tid}")
    users_db = Path("data/enterprise.db")
    os_db = Path("data/opensoul.db")
    with sqlite3.connect(users_db) as conn:
        conn.execute("DELETE FROM users WHERE username LIKE 'e2e-cr8-%'")
        conn.commit()
    with sqlite3.connect(os_db) as conn:
        conn.execute("DELETE FROM users WHERE username LIKE 'e2e-cr8-%'")
        conn.commit()
    remaining_jobs = [j for j in store_rows() if "e2e-cr8" in json.dumps(j)]
    with sqlite3.connect(users_db) as conn:
        ru = conn.execute(
            "SELECT COUNT(*) FROM users WHERE username LIKE 'e2e-cr8-%'"
        ).fetchone()[0]
    with sqlite3.connect(os_db) as conn:
        ru += conn.execute(
            "SELECT COUNT(*) FROM users WHERE username LIKE 'e2e-cr8-%'"
        ).fetchone()[0]
    tenants_left = json.dumps(c.get("/api/nest/tenants").json()).count(suffix)
    ev("cleanup remaining=0", not remaining_jobs and ru == 0 and tenants_left == 0,
       f"jobs={len(remaining_jobs)} users={ru} tenants={tenants_left}")
    ev("jobs count back to baseline", len(store_rows()) == baseline_jobs,
       (baseline_jobs, len(store_rows())))

    failed = [e for e in EVID if not e["ok"]]
    print(json.dumps({"total": len(EVID), "passed": len(EVID) - len(failed),
                      "failed": len(failed)}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
