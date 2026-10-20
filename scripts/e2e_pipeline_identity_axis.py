"""F3b-⑫ live 零副作用探针（TR-② 零事件行面纪律：upload/run 写面刻意不打 live）。

只打零副作用路径：health / 坏 token 401 / 认证自报矛盾 403（bind_identity
先于一切副作用）。前后回读 knowledge/users/timeline 三面行数硬证零写入。
"""
import hashlib
import sqlite3
import uuid

import httpx

BASE = "http://127.0.0.1:8090"
DB = "/home/climbing/opensoul/data/opensoul.db"
TL = "/home/climbing/opensoul/data/timeline.db"


def counts():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    k = c.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
    u = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    ids = {r[0]: r[1] for r in c.execute("SELECT id, username FROM users")}
    c.close()
    t = sqlite3.connect(f"file:{TL}?mode=ro", uri=True)
    ev = t.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    t.close()
    return k, u, ev, ids


k0, u0, ev0, ids = counts()
print(f"baseline knowledge={k0} users={u0} timeline_events={ev0}")

import sys

sys.path.insert(0, "/home/climbing/opensoul")
from src.services.auth import create_access_token

hermes_id = next(i for i, n in ids.items() if n == "hermes")
admin_id = next(i for i, n in ids.items() if n == "admin")
tok = create_access_token(user_id=uuid.UUID(hermes_id), role="user")
auth = {"Authorization": f"Bearer {tok}"}

ok = 0
with httpx.Client(base_url=BASE, timeout=30) as c:
    r = c.get("/api/pipeline/health")
    print("health:", r.status_code, r.json().get("status"))
    assert r.status_code == 200
    ok += 1

    # 坏 token → 401（修前 200 无鉴权轴）
    r = c.post(
        "/api/pipeline/upload",
        files={"file": ("probe.txt", b"probe content for axis", "text/plain")},
        data={"user_id": "victim"},
        headers={"Authorization": "Bearer garbage"},
    )
    print("bad-token upload:", r.status_code)
    assert r.status_code == 401
    ok += 1

    # 认证自报矛盾 → 403 带真因（修前 200 直写 victim 档案）
    r = c.post(
        "/api/pipeline/upload",
        files={"file": ("probe.txt", b"probe content for axis", "text/plain")},
        data={"user_id": admin_id},
        headers=auth,
    )
    print("conflict upload:", r.status_code, r.json().get("detail", "")[:80])
    assert r.status_code == 403 and "user mismatch" in r.json()["detail"]
    ok += 1

    r = c.post(
        "/api/pipeline/run",
        json={"file_id": "nonexistent", "user_id": admin_id},
        headers=auth,
    )
    print("conflict run:", r.status_code, r.json().get("detail", "")[:80])
    assert r.status_code == 403 and "user mismatch" in r.json()["detail"]
    ok += 1

    # 匿名缺省上传不打（写面 TR-②）；/run 缺省自报也不会越过文件 404 前的
    # 身份检查——404 面零副作用可打（证明缺省自报不再 403 误伤）
    r = c.post("/api/pipeline/run", json={"file_id": "nonexistent-probe"})
    print("anon run missing-file:", r.status_code)
    assert r.status_code == 404
    ok += 1

k1, u1, ev1, _ = counts()
print(f"after knowledge={k1} users={u1} timeline_events={ev1}")
assert (k1, u1, ev1) == (k0, u0, ev0), "side effect detected!"
print(f"PROBE OK {ok}/5 zero-write proven (knowledge/users/timeline delta=0)")
