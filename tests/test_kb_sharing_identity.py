"""kb_sharing 面身份轴收口测试（user 从认证派生 + admin 门，禁冒读/冒写）。

F3b-⑰：F3b-⑯ 留档「kb_sharing list（自报过滤+全列表）」执行件（F3b-⑥ 已裁决语义
同族既有面延伸，F3b-⑯ knowledge_requests 同形）。修前四缺口：
① review「管理员审批」零管理员门：任意认证用户可审批他人申请，approved 直写
   申请人 knowledge 行=冒写他人知识档案（含自审自批）；
② list user_id 自报过滤+缺省=全量泄露（任意认证用户看全库申请）；
③ 写读键=str(token user_id) 裸自报串，与档案键家族正典（_resolve_user_id）漂移；
④ review approved 的 knowledge WHERE 裸行键对家族正典知识行落空。

语义（F3b-⑥ 逐字）：认证=派生档案键、自报只可确认绑定、矛盾=403、
'default'/空/占位零 UUID（NIL_UUID）=未声明→取派生；admin=role_at_least 单一判法
（services.auth=middleware require_role 同源）。

数据面：tmp opensoul.db 真 schema（users/knowledge/kb_sharing_requests 三表）
+ tmp SQLitePool 四绑定（postgres/services.auth/middleware.auth/api.kb_sharing）
+ USERS_DB/ENTERPRISE_DB 随 tmp（绝不写生产库），真 SQL 落库回读非接缝桩。
"""

import hashlib
import sqlite3
import uuid
from contextlib import asynccontextmanager

import pytest

NIL_UUID = "00000000-0000-0000-0000-000000000000"
UID_A = "11111111-1111-4111-8111-111111111111"
UID_B = "22222222-2222-4222-8222-222222222222"
UID_ADMIN = "33333333-3333-4333-8333-333333333333"

_DDL = """CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
    email TEXT DEFAULT '', password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1,
    tenant_id TEXT DEFAULT 'default',
    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS knowledge (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    title TEXT NOT NULL, content TEXT DEFAULT '',
    tags TEXT DEFAULT '[]', source TEXT DEFAULT '',
    content_type TEXT DEFAULT '', metadata TEXT DEFAULT '{}',
    starred INTEGER DEFAULT 0, pinned INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS kb_sharing_requests (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, user_name TEXT DEFAULT '',
    kb_id TEXT NOT NULL, kb_name TEXT NOT NULL, status TEXT DEFAULT 'pending',
    reviewer_id TEXT, review_note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')), reviewed_at TEXT);"""


def _md5_uuid(s: str) -> uuid.UUID:
    h = hashlib.md5(s.encode()).hexdigest()
    return uuid.UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


def _headers(uid: str, role: str = "user") -> dict:
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid), role=role)
    return {"Authorization": f"Bearer {tok}"}


def _rows(db_path, table: str, where: str = "", *args):
    with sqlite3.connect(db_path) as c:
        c.row_factory = sqlite3.Row
        q = f"SELECT * FROM {table}"
        if where:
            q += " WHERE " + where
        return [dict(r) for r in c.execute(q, args).fetchall()]


@pytest.fixture()
def kbs_client(tmp_path, monkeypatch):
    """kb_sharing API 隔离测试面：真实 router + tmp 独立数据面。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.kb_sharing as kbs_api
    import src.database.postgres as postgres_mod
    import src.middleware.auth as mw_auth
    import src.services.auth as services_auth
    from src.config import settings
    from src.database.postgres import SQLitePool

    tmp_db = tmp_path / "opensoul.db"
    with sqlite3.connect(tmp_db) as c:
        c.executescript(_DDL)
        for uid, name, role in (
            (UID_A, "alice", "user"),
            (UID_B, "bob", "user"),
            (UID_ADMIN, "root", "admin"),
        ):
            c.execute(
                "INSERT INTO users (id, username, password_hash, is_active, role) "
                "VALUES (?, ?, 'x', 1, ?)",
                (uid, name, role),
            )
        c.commit()
    monkeypatch.setenv("USERS_DB", str(tmp_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_db}")
    tmp_pool = SQLitePool()
    monkeypatch.setattr(postgres_mod, "db_pool", tmp_pool)
    monkeypatch.setattr(services_auth, "db_pool", tmp_pool)
    monkeypatch.setattr(mw_auth, "db_pool", tmp_pool)
    monkeypatch.setattr(kbs_api, "db_pool", tmp_pool)

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()
        yield
        await tmp_pool.disconnect()

    app = FastAPI(lifespan=_lifespan)
    app.include_router(kbs_api.router, prefix="/api/kb-sharing")
    app.state.kbs_db = str(tmp_db)
    with TestClient(app) as c:
        yield c


def _create(client, headers, kb_id="kb-1", kb_name="kb1"):
    r = client.post(
        "/api/kb-sharing/",
        headers=headers,
        json={"kb_id": kb_id, "kb_name": kb_name},
    )
    assert r.status_code == 200, r.text
    return r.json()


# ── 档案键家族正典（_resolve_user_id 同形纯函数） ─────────────────────────


def test_resolve_user_id_uuid_passthrough():
    from src.api.kb_sharing import _resolve_user_id

    assert _resolve_user_id(UID_A) == uuid.UUID(UID_A)


def test_resolve_user_id_md5_normalization():
    from src.api.kb_sharing import _resolve_user_id

    assert _resolve_user_id("42") == _md5_uuid("42")
    assert _resolve_user_id("alice") == _md5_uuid("alice")


# ── review 管理员门（① 修前任意认证用户可审批=冒写面） ──────────────────


def test_non_admin_review_rejected_zero_side_effect(kbs_client):
    req = _create(kbs_client, _headers(UID_A))
    with sqlite3.connect(kbs_client.app.state.kbs_db) as c:
        c.execute(
            "INSERT INTO knowledge (id, user_id, title) VALUES ('k1', ?, 't1')",
            (UID_A,),
        )
        c.commit()
    r = kbs_client.post(
        f"/api/kb-sharing/{req['id']}/review",
        headers=_headers(UID_B),
        json={"status": "approved"},
    )
    assert r.status_code == 403, r.text
    assert "admin" in r.json()["detail"].lower()
    # 零副作用：行仍 pending、申请人知识行未被冒写标记
    row = _rows(kbs_client.app.state.kbs_db, "kb_sharing_requests", "id = ?", req["id"])[0]
    assert row["status"] == "pending"
    kn = _rows(kbs_client.app.state.kbs_db, "knowledge", "id = 'k1'")[0]
    assert "shared_to_enterprise" not in (kn["metadata"] or "")


def test_self_review_rejected(kbs_client):
    req = _create(kbs_client, _headers(UID_A))
    r = kbs_client.post(
        f"/api/kb-sharing/{req['id']}/review",
        headers=_headers(UID_A),
        json={"status": "approved"},
    )
    assert r.status_code == 403, r.text


def test_admin_approve_marks_applicant_knowledge(kbs_client):
    req = _create(kbs_client, _headers(UID_A))
    with sqlite3.connect(kbs_client.app.state.kbs_db) as c:
        c.execute(
            "INSERT INTO knowledge (id, user_id, title) VALUES ('k1', ?, 't1')",
            (UID_A,),
        )
        c.commit()
    r = kbs_client.post(
        f"/api/kb-sharing/{req['id']}/review",
        headers=_headers(UID_ADMIN, role="admin"),
        json={"status": "approved", "review_note": "ok"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["reviewer_id"] == UID_ADMIN
    # 申请人（非审批人）的知识行被标记企业共享
    kn = _rows(kbs_client.app.state.kbs_db, "knowledge", "id = 'k1'")[0]
    assert "shared_to_enterprise" in kn["metadata"]
    assert UID_A in kn["metadata"]  # shared_by=申请人
    assert UID_ADMIN not in kn["metadata"]


def test_admin_approve_canonical_write_key_for_legacy_row(kbs_client):
    """行键=非家族正典（数字 id 旧形）时，knowledge WHERE 仍命中家族正典知识行。"""
    legacy_key = "42"
    with sqlite3.connect(kbs_client.app.state.kbs_db) as c:
        c.execute(
            "INSERT INTO kb_sharing_requests (id, user_id, kb_id, kb_name) "
            "VALUES ('req-legacy', ?, 'kb-9', 'legacy')",
            (legacy_key,),
        )
        c.execute(
            "INSERT INTO knowledge (id, user_id, title) VALUES ('k2', ?, 't2')",
            (str(_md5_uuid(legacy_key)),),
        )
        c.commit()
    r = kbs_client.post(
        "/api/kb-sharing/req-legacy/review",
        headers=_headers(UID_ADMIN, role="admin"),
        json={"status": "approved"},
    )
    assert r.status_code == 200, r.text
    kn = _rows(kbs_client.app.state.kbs_db, "knowledge", "id = 'k2'")[0]
    assert "shared_to_enterprise" in kn["metadata"]


def test_admin_reject_zero_knowledge_write(kbs_client):
    req = _create(kbs_client, _headers(UID_A))
    with sqlite3.connect(kbs_client.app.state.kbs_db) as c:
        c.execute(
            "INSERT INTO knowledge (id, user_id, title) VALUES ('k1', ?, 't1')",
            (UID_A,),
        )
        c.commit()
    r = kbs_client.post(
        f"/api/kb-sharing/{req['id']}/review",
        headers=_headers(UID_ADMIN, role="admin"),
        json={"status": "rejected", "review_note": "no"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"
    kn = _rows(kbs_client.app.state.kbs_db, "knowledge", "id = 'k1'")[0]
    assert "shared_to_enterprise" not in (kn["metadata"] or "")


def test_admin_invalid_status_400(kbs_client):
    req = _create(kbs_client, _headers(UID_A))
    r = kbs_client.post(
        f"/api/kb-sharing/{req['id']}/review",
        headers=_headers(UID_ADMIN, role="admin"),
        json={"status": "maybe"},
    )
    assert r.status_code == 400, r.text


def test_gate_before_payload_check(kbs_client):
    """身份门先于载荷校验（F3b-⑤ 纪律）：非 admin 带非法 status 仍 403。"""
    req = _create(kbs_client, _headers(UID_A))
    r = kbs_client.post(
        f"/api/kb-sharing/{req['id']}/review",
        headers=_headers(UID_B),
        json={"status": "maybe"},
    )
    assert r.status_code == 403, r.text


def test_review_missing_request_404(kbs_client):
    r = kbs_client.post(
        "/api/kb-sharing/no-such-id/review",
        headers=_headers(UID_ADMIN, role="admin"),
        json={"status": "approved"},
    )
    assert r.status_code == 404, r.text


def test_review_already_reviewed_400(kbs_client):
    req = _create(kbs_client, _headers(UID_A))
    h = _headers(UID_ADMIN, role="admin")
    assert (
        kbs_client.post(
            f"/api/kb-sharing/{req['id']}/review", headers=h, json={"status": "approved"}
        ).status_code
        == 200
    )
    r = kbs_client.post(
        f"/api/kb-sharing/{req['id']}/review", headers=h, json={"status": "rejected"}
    )
    assert r.status_code == 400, r.text


# ── list 轴（② 修前缺省=全量泄露+自报冒读） ─────────────────────────────


def test_non_admin_list_only_own(kbs_client):
    a = _create(kbs_client, _headers(UID_A), kb_id="kb-a", kb_name="a")
    b = _create(kbs_client, _headers(UID_B), kb_id="kb-b", kb_name="b")
    ra = kbs_client.get("/api/kb-sharing/", headers=_headers(UID_A)).json()
    rb = kbs_client.get("/api/kb-sharing/", headers=_headers(UID_B)).json()
    assert [r["id"] for r in ra] == [a["id"]]
    assert [r["id"] for r in rb] == [b["id"]]


def test_declared_mismatch_403_and_confirm_binding(kbs_client):
    _create(kbs_client, _headers(UID_A))
    r = kbs_client.get("/api/kb-sharing/", headers=_headers(UID_B), params={"user_id": UID_A})
    assert r.status_code == 403, r.text
    assert "user mismatch" in r.json()["detail"]
    # 确认绑定放行（声明自己的键）
    r2 = kbs_client.get("/api/kb-sharing/", headers=_headers(UID_A), params={"user_id": UID_A})
    assert r2.status_code == 200, r2.text


def test_undeclared_sentinels_take_derived(kbs_client):
    a = _create(kbs_client, _headers(UID_A))
    _create(kbs_client, _headers(UID_B), kb_id="kb-b", kb_name="b")
    for sentinel in ("", "default", NIL_UUID):
        r = kbs_client.get(
            "/api/kb-sharing/", headers=_headers(UID_A), params={"user_id": sentinel}
        )
        assert r.status_code == 200, (sentinel, r.text)
        assert [x["id"] for x in r.json()] == [a["id"]], sentinel


def test_admin_list_all_and_filter(kbs_client):
    a = _create(kbs_client, _headers(UID_A), kb_id="kb-a", kb_name="a")
    b = _create(kbs_client, _headers(UID_B), kb_id="kb-b", kb_name="b")
    h = _headers(UID_ADMIN, role="admin")
    all_rows = kbs_client.get("/api/kb-sharing/", headers=h).json()
    assert {r["id"] for r in all_rows} == {a["id"], b["id"]}
    filtered = kbs_client.get("/api/kb-sharing/", headers=h, params={"user_id": UID_B}).json()
    assert [r["id"] for r in filtered] == [b["id"]]


def test_status_filter(kbs_client):
    a = _create(kbs_client, _headers(UID_A))
    kbs_client.post(
        f"/api/kb-sharing/{a['id']}/review",
        headers=_headers(UID_ADMIN, role="admin"),
        json={"status": "rejected"},
    )
    _create(kbs_client, _headers(UID_A), kb_id="kb-a2", kb_name="a2")
    pending = kbs_client.get(
        "/api/kb-sharing/", headers=_headers(UID_A), params={"status": "pending"}
    ).json()
    assert all(r["status"] == "pending" for r in pending)
    assert len(pending) == 1


# ── create/my 行归属（③ 写读键家族正典） ────────────────────────────────


def test_create_row_attribution_and_my(kbs_client):
    row = _create(kbs_client, _headers(UID_A))
    assert row["user_id"] == UID_A  # 档案键家族正典（UUID 形原样）
    assert row["user_name"] == "alice"
    my = kbs_client.get("/api/kb-sharing/my", headers=_headers(UID_A)).json()
    assert [r["id"] for r in my] == [row["id"]]
    my_b = kbs_client.get("/api/kb-sharing/my", headers=_headers(UID_B)).json()
    assert my_b == []


def test_create_non_uuid_identity_canonical_key(kbs_client):
    """非 UUID 身份（数字 id）落家族正典档案键（md5 归一）。"""
    from src.api.kb_sharing import _archive_key

    key = _archive_key({"id": 42, "username": "num"})
    assert key == _md5_uuid("42")


# ── 401 电池 ────────────────────────────────────────────────────────────


def test_list_requires_auth(kbs_client):
    r = kbs_client.get("/api/kb-sharing/")
    assert r.status_code == 401, r.text


def test_bad_token_401(kbs_client):
    r = kbs_client.get("/api/kb-sharing/", headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401, r.text


def test_review_requires_auth(kbs_client):
    req = _create(kbs_client, _headers(UID_A))
    r = kbs_client.post(f"/api/kb-sharing/{req['id']}/review", json={"status": "approved"})
    assert r.status_code == 401, r.text


# ── 静态接线 ────────────────────────────────────────────────────────────


def test_static_wiring():
    import ast

    src = open("src/api/kb_sharing.py").read()
    tree = ast.parse(src)
    # 身份轴=middleware get_current_user（非 api.user 的 token 裸 UUID 形）
    assert "from src.middleware.auth import get_current_user" in src
    assert "from src.api.user import get_current_user" not in src
    deps = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in (
            "create_sharing_request",
            "list_sharing_requests",
            "my_sharing_requests",
            "review_sharing_request",
        ):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                    if sub.func.id == "Depends":
                        deps += 1
    assert deps >= 4
    # 三 helper 真消费（定义不等于接线）
    for sym in ("_archive_key(", "_is_admin(", "_resolve_user_id("):
        assert src.count(sym) >= 2, sym
