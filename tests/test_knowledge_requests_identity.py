"""knowledge_requests 面身份轴收口测试（user 从认证派生 + admin 门，禁冒读/冒写）。

F3b-⑯：F3b-⑮ 复核登记「knowledge_requests list（docstring「用户看自己的」未实现
+ review「管理员审批」零管理员门）」执行件（F3b-⑥ 已裁决语义同族既有面延伸，
F3b-⑦/⑩/⑫/⑬/⑭/⑮ 先例）。修前四缺口：
① review 零管理员门：任意认证用户可审批他人申请，approved 直写申请人 knowledge
   行=冒写他人知识档案（含自审自批）；
② list docstring「用户看全部/用户看自己的」未实现：user_id 自报过滤+缺省=全量
   泄露（任意认证用户看全库申请）；
③ get by-ID 零归属闸：任意认证用户读他人申请；
④ 写读键=str(token sub) 裸自报串，与档案键家族正典（_resolve_user_id）漂移。

语义（F3b-⑥ 逐字）：认证=派生档案键、自报只可确认绑定、矛盾=403、
'default'/空/占位零 UUID（NIL_UUID）=未声明→取派生；admin=role_at_least 单一判法
（services.auth=middleware require_role 同源）。读面越权=与缺行同契约 404（禁存在性
探测），写面越权=403 带真因（F3b 写面惯例）。

数据面：tmp opensoul.db 真 schema（users/knowledge/knowledge_requests 三表）
+ tmp SQLitePool 三绑定（postgres/services.auth/middleware.auth/api.knowledge_requests）
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
CREATE TABLE IF NOT EXISTS knowledge_requests (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, user_name TEXT DEFAULT '',
    kb_name TEXT NOT NULL, kb_description TEXT DEFAULT '',
    status TEXT DEFAULT 'pending', reviewer_id TEXT, review_note TEXT DEFAULT '',
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
def kr_client(tmp_path, monkeypatch):
    """knowledge_requests API 隔离测试面：真实 router + tmp 独立数据面。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.knowledge_requests as kr_api
    import src.database.postgres as postgres_mod
    import src.middleware.auth as mw_auth
    import src.services.auth as services_auth
    from src.config import settings
    from src.database.postgres import SQLitePool

    tmp_db = tmp_path / "opensoul.db"
    with sqlite3.connect(tmp_db) as c:
        c.executescript(_DDL)
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, role) "
            "VALUES (?, ?, 'x', 1, ?)",
            (UID_A, "alice", "user"),
        )
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, role) "
            "VALUES (?, ?, 'x', 1, ?)",
            (UID_B, "bob", "user"),
        )
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, role) "
            "VALUES (?, ?, 'x', 1, ?)",
            (UID_ADMIN, "root", "admin"),
        )
        c.commit()
    monkeypatch.setenv("USERS_DB", str(tmp_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_db}")
    tmp_pool = SQLitePool()
    monkeypatch.setattr(postgres_mod, "db_pool", tmp_pool)
    monkeypatch.setattr(services_auth, "db_pool", tmp_pool)
    monkeypatch.setattr(mw_auth, "db_pool", tmp_pool)
    monkeypatch.setattr(kr_api, "db_pool", tmp_pool)

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()
        yield
        await tmp_pool.disconnect()

    app = FastAPI(lifespan=_lifespan)
    app.include_router(kr_api.router, prefix="/api/knowledge-requests")
    app.state.kr_db = str(tmp_db)
    with TestClient(app) as c:
        yield c


def _create(client, headers, name="kb1"):
    r = client.post(
        "/api/knowledge-requests/",
        headers=headers,
        json={"kb_name": name, "kb_description": f"desc-{name}"},
    )
    assert r.status_code == 200, r.text
    return r.json()


# ── 档案键家族正典（_resolve_user_id 同形纯函数） ─────────────────────────


def test_resolve_user_id_uuid_passthrough():
    from src.api.knowledge_requests import _resolve_user_id

    assert _resolve_user_id(UID_A) == uuid.UUID(UID_A)


def test_resolve_user_id_md5_normalization():
    from src.api.knowledge_requests import _resolve_user_id

    # 非 UUID 形（数字 id/用户名）按 md5 归一为档案 UUID（家族正典同形）
    assert _resolve_user_id("42") == _md5_uuid("42")
    assert _resolve_user_id("alice") == _md5_uuid("alice")


# ── review 管理员门（① 修前任意认证用户可审批=冒写面） ──────────────────


def test_non_admin_review_rejected_zero_side_effect(kr_client):
    db = kr_client.app.state.kr_db
    req = _create(kr_client, _headers(UID_A), "kb-a")
    r = kr_client.post(
        f"/api/knowledge-requests/{req['id']}/review",
        headers=_headers(UID_B),
        json={"status": "approved", "review_note": "n"},
    )
    assert r.status_code == 403, r.text
    assert "admin" in r.json()["detail"]
    rows = _rows(db, "knowledge_requests")
    assert rows[0]["status"] == "pending"
    assert rows[0]["reviewer_id"] is None
    # 零副作用：approved 分支不得写 knowledge 行（修前直写申请人档案）
    assert _rows(db, "knowledge") == []


def test_self_review_blocked_for_non_admin(kr_client):
    db = kr_client.app.state.kr_db
    req = _create(kr_client, _headers(UID_A), "kb-self")
    r = kr_client.post(
        f"/api/knowledge-requests/{req['id']}/review",
        headers=_headers(UID_A),
        json={"status": "approved"},
    )
    assert r.status_code == 403, r.text
    assert _rows(db, "knowledge") == []


def test_admin_approve_creates_kb_under_requester_archive_key(kr_client):
    db = kr_client.app.state.kr_db
    req = _create(kr_client, _headers(UID_A), "kb-approve")
    r = kr_client.post(
        f"/api/knowledge-requests/{req['id']}/review",
        headers=_headers(UID_ADMIN, role="admin"),
        json={"status": "approved", "review_note": "ok"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["reviewer_id"] == UID_ADMIN
    krows = _rows(db, "knowledge")
    assert len(krows) == 1
    # 行归属=申请人档案键家族正典（非审批人、非裸自报串）
    assert krows[0]["user_id"] == UID_A
    assert krows[0]["title"] == "kb-approve"
    assert krows[0]["source"] == "auto-created"


def test_admin_reject_no_kb(kr_client):
    db = kr_client.app.state.kr_db
    req = _create(kr_client, _headers(UID_A), "kb-reject")
    r = kr_client.post(
        f"/api/knowledge-requests/{req['id']}/review",
        headers=_headers(UID_ADMIN, role="admin"),
        json={"status": "rejected", "review_note": "no"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"
    assert _rows(db, "knowledge") == []


def test_admin_invalid_status_still_400(kr_client):
    req = _create(kr_client, _headers(UID_A), "kb-bad")
    r = kr_client.post(
        f"/api/knowledge-requests/{req['id']}/review",
        headers=_headers(UID_ADMIN, role="admin"),
        json={"status": "weird"},
    )
    assert r.status_code == 400, r.text


def test_review_gate_precedes_status_validation(kr_client):
    """身份/授权先于载荷校验（F3b-⑤「身份证明先于一切授权检查」同纪律）：
    非 admin 携非法 status 也先 403（不借 400/403 差分探测端点校验面）。"""
    req = _create(kr_client, _headers(UID_A), "kb-order")
    r = kr_client.post(
        f"/api/knowledge-requests/{req['id']}/review",
        headers=_headers(UID_B),
        json={"status": "weird"},
    )
    assert r.status_code == 403, r.text


def test_review_missing_request_404_admin(kr_client):
    r = kr_client.post(
        "/api/knowledge-requests/no-such-id/review",
        headers=_headers(UID_ADMIN, role="admin"),
        json={"status": "approved"},
    )
    assert r.status_code == 404, r.text


def test_review_already_reviewed_400_admin(kr_client):
    req = _create(kr_client, _headers(UID_A), "kb-twice")
    h = _headers(UID_ADMIN, role="admin")
    r1 = kr_client.post(
        f"/api/knowledge-requests/{req['id']}/review",
        headers=h,
        json={"status": "approved"},
    )
    assert r1.status_code == 200, r1.text
    r2 = kr_client.post(
        f"/api/knowledge-requests/{req['id']}/review",
        headers=h,
        json={"status": "rejected"},
    )
    assert r2.status_code == 400, r2.text
    assert "Already reviewed" in r2.json()["detail"]


# ── list 面（② 修前自报过滤+缺省全量泄露） ───────────────────────────────


def test_non_admin_sees_own_only(kr_client):
    _create(kr_client, _headers(UID_A), "kb-a")
    _create(kr_client, _headers(UID_B), "kb-b")
    ra = kr_client.get("/api/knowledge-requests/", headers=_headers(UID_A))
    assert ra.status_code == 200, ra.text
    assert [x["kb_name"] for x in ra.json()] == ["kb-a"]
    rb = kr_client.get("/api/knowledge-requests/", headers=_headers(UID_B))
    assert [x["kb_name"] for x in rb.json()] == ["kb-b"]


def test_non_admin_self_report_confirm_and_mismatch_403(kr_client):
    _create(kr_client, _headers(UID_A), "kb-a")
    # 确认绑定=放行（自报自己的键）
    r_ok = kr_client.get(
        "/api/knowledge-requests/", headers=_headers(UID_A), params={"user_id": UID_A}
    )
    assert r_ok.status_code == 200, r_ok.text
    assert [x["kb_name"] for x in r_ok.json()] == ["kb-a"]
    # 矛盾=403 冒读面收口（修前 200 直读他人档案）
    r_bad = kr_client.get(
        "/api/knowledge-requests/", headers=_headers(UID_A), params={"user_id": UID_B}
    )
    assert r_bad.status_code == 403, r_bad.text
    assert "user mismatch" in r_bad.json()["detail"]


def test_non_admin_undeclared_sentinels_take_derived(kr_client):
    _create(kr_client, _headers(UID_A), "kb-a")
    _create(kr_client, _headers(UID_B), "kb-b")
    for sentinel in ("", "default", NIL_UUID):
        r = kr_client.get(
            "/api/knowledge-requests/",
            headers=_headers(UID_A),
            params={"user_id": sentinel},
        )
        assert r.status_code == 200, (sentinel, r.text)
        assert [x["kb_name"] for x in r.json()] == ["kb-a"], sentinel


def test_admin_sees_all_and_filters(kr_client):
    _create(kr_client, _headers(UID_A), "kb-a")
    _create(kr_client, _headers(UID_B), "kb-b")
    h = _headers(UID_ADMIN, role="admin")
    rall = kr_client.get("/api/knowledge-requests/", headers=h)
    assert rall.status_code == 200, rall.text
    assert sorted(x["kb_name"] for x in rall.json()) == ["kb-a", "kb-b"]
    rfilt = kr_client.get("/api/knowledge-requests/", headers=h, params={"user_id": UID_B})
    assert [x["kb_name"] for x in rfilt.json()] == ["kb-b"]


def test_status_filter_contract(kr_client):
    a = _create(kr_client, _headers(UID_A), "kb-a")
    _create(kr_client, _headers(UID_A), "kb-a2")
    r = kr_client.post(
        f"/api/knowledge-requests/{a['id']}/review",
        headers=_headers(UID_ADMIN, role="admin"),
        json={"status": "approved"},
    )
    assert r.status_code == 200, r.text
    got = kr_client.get(
        "/api/knowledge-requests/",
        headers=_headers(UID_A),
        params={"status": "approved"},
    )
    assert [x["kb_name"] for x in got.json()] == ["kb-a"]


# ── get by-ID 面（③ 修前零归属闸） ───────────────────────────────────────


def test_get_owner_and_admin_ok(kr_client):
    req = _create(kr_client, _headers(UID_A), "kb-a")
    r_owner = kr_client.get(f"/api/knowledge-requests/{req['id']}", headers=_headers(UID_A))
    assert r_owner.status_code == 200, r_owner.text
    assert r_owner.json()["id"] == req["id"]
    r_admin = kr_client.get(
        f"/api/knowledge-requests/{req['id']}", headers=_headers(UID_ADMIN, role="admin")
    )
    assert r_admin.status_code == 200, r_admin.text


def test_get_foreign_404_same_as_missing(kr_client):
    req = _create(kr_client, _headers(UID_A), "kb-a")
    r_foreign = kr_client.get(f"/api/knowledge-requests/{req['id']}", headers=_headers(UID_B))
    r_missing = kr_client.get("/api/knowledge-requests/no-such-id", headers=_headers(UID_B))
    assert r_foreign.status_code == 404, r_foreign.text
    assert r_missing.status_code == 404, r_missing.text
    # 与缺行同契约（禁存在性探测）：响应形状一致
    assert r_foreign.json() == r_missing.json()


# ── my 面 + create/delete 行归属（④ 写读键家族正典） ─────────────────────


def test_my_own_only(kr_client):
    _create(kr_client, _headers(UID_A), "kb-a")
    _create(kr_client, _headers(UID_B), "kb-b")
    r = kr_client.get("/api/knowledge-requests/my", headers=_headers(UID_A))
    assert r.status_code == 200, r.text
    assert [x["kb_name"] for x in r.json()] == ["kb-a"]


def test_create_row_attribution_and_user_name(kr_client):
    db = kr_client.app.state.kr_db
    body = _create(kr_client, _headers(UID_A), "kb-a")
    rows = _rows(db, "knowledge_requests")
    assert rows[0]["user_id"] == UID_A  # 家族正典档案键（UUID 形原样）
    assert rows[0]["user_name"] == "alice"  # users 行真源 username
    assert body["kb_name"] == "kb-a"


def test_delete_owner_ok_foreign_403(kr_client):
    db = kr_client.app.state.kr_db
    req = _create(kr_client, _headers(UID_A), "kb-a")
    r_foreign = kr_client.delete(f"/api/knowledge-requests/{req['id']}", headers=_headers(UID_B))
    assert r_foreign.status_code == 403, r_foreign.text
    assert "Not your request" in r_foreign.json()["detail"]
    assert len(_rows(db, "knowledge_requests")) == 1  # 行存活
    r_owner = kr_client.delete(f"/api/knowledge-requests/{req['id']}", headers=_headers(UID_A))
    assert r_owner.status_code == 200, r_owner.text
    assert _rows(db, "knowledge_requests") == []


# ── 认证契约不变（401 面） ───────────────────────────────────────────────


def test_no_token_401_battery(kr_client):
    r_list = kr_client.get("/api/knowledge-requests/")
    r_create = kr_client.post(
        "/api/knowledge-requests/", json={"kb_name": "x", "kb_description": ""}
    )
    r_review = kr_client.post("/api/knowledge-requests/any-id/review", json={"status": "approved"})
    r_get = kr_client.get("/api/knowledge-requests/any-id")
    r_my = kr_client.get("/api/knowledge-requests/my")
    for r in (r_list, r_create, r_review, r_get, r_my):
        assert r.status_code == 401, (r.url, r.status_code, r.text)


def test_bad_token_401(kr_client):
    r = kr_client.get("/api/knowledge-requests/", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401, r.text
