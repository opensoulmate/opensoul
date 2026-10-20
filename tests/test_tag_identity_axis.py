"""tag.py 面 user_id 轴 bind_identity 收口测试（user 从认证派生，禁自报冒读/冒写/冒删）。

F3b-⑮ 同族剩余面复核揭出（F3b-⑭「user_id 自报轴全族收口」信念被复核推翻）：
/api/tags/ 四端点（create/list/update/delete）修前 user_id: UUID 完全客户端自报
且**零鉴权**（无任何 Depends/authorization 形参）——任意人可自报任意 user_id
冒读他人标签列表、冒写/冒删他人标签（比 F3b-⑥~⑭ 各面更弱：那批至少有匿名语义
可辩，本面连 token 都不要）。

语义与 F3b-⑥ 逐字一致：认证=派生档案键、自报只可确认绑定、矛盾=403、
'default'/空/占位零 UUID（NIL_UUID）=未声明→取派生、匿名=保留自报（既有
本地多档案语义契约零变化）、坏 token=401 零副作用。

写键/读键=档案键家族正典 str(_resolve_user_id(identity.user_id))（F3b-⑫ 同源）。

数据面=tmp 全隔离（USERS_DB/ENTERPRISE_DB 随 tmp，绝不写生产库）；真 SQL
落库回读非接缝桩。
"""

import ast
import hashlib
import sqlite3
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

NIL_UUID = "00000000-0000-0000-0000-000000000000"
UID_A = "11111111-1111-4111-8111-111111111111"
UID_B = "22222222-2222-4222-8222-222222222222"


def _md5_uuid(s: str) -> uuid.UUID:
    h = hashlib.md5(s.encode()).hexdigest()
    return uuid.UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


_TAG_DDL = (
    "CREATE TABLE IF NOT EXISTS users ("
    "id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "email TEXT DEFAULT '', password_hash TEXT NOT NULL, "
    "role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1, "
    "tenant_id TEXT DEFAULT 'default', "
    "created_at TEXT DEFAULT (datetime('now')), "
    "updated_at TEXT DEFAULT (datetime('now'))); "
    "CREATE TABLE IF NOT EXISTS tags ("
    "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, "
    "color TEXT DEFAULT '#6366f1', created_at TEXT DEFAULT (datetime('now'))); "
    "CREATE TABLE IF NOT EXISTS knowledge_tags ("
    "knowledge_id TEXT NOT NULL, tag_id TEXT NOT NULL, "
    "PRIMARY KEY (knowledge_id, tag_id));"
)


@pytest.fixture()
def tag_axis_client(tmp_path, monkeypatch):
    """tag API 隔离测试面：真实 router + tmp 独立数据面（knowledge_client 同款
    路线），绝不写生产 data/opensoul.db。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.tag as tag_api
    import src.database.postgres as postgres_mod
    from src.config import settings
    from src.database.postgres import SQLitePool

    db_path = tmp_path / "opensoul.db"
    with sqlite3.connect(db_path) as c:
        c.executescript(_TAG_DDL)
        c.commit()
    monkeypatch.setenv("USERS_DB", str(db_path))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path}")
    pool = SQLitePool()
    monkeypatch.setattr(postgres_mod, "db_pool", pool)
    monkeypatch.setattr(tag_api, "db_pool", pool)

    @asynccontextmanager
    async def _lifespan(_app):
        await pool.connect()
        yield
        await pool.disconnect()

    app = FastAPI(lifespan=_lifespan)
    app.include_router(tag_api.router, prefix="/api/tags")
    app.state.tag_client_db = str(db_path)
    with TestClient(app) as c:
        yield c


def _seed_user(db_path, uid: str, username: str) -> str:
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, 'default')",
            (uid, username),
        )
        c.commit()
    return uid


def _token(uid: str) -> dict:
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid), role="user")
    return {"Authorization": f"Bearer {tok}"}


def _enterprise_token(ent_id: int) -> dict:
    """enterprise scope token（sub=数字 id，bind_identity 企业分支真身份）。"""
    from datetime import UTC, datetime, timedelta

    from jose import jwt

    from src.config import settings

    payload = {
        "sub": str(ent_id),
        "scope": "enterprise",
        "role": "user",
        "exp": datetime.now(UTC) + timedelta(minutes=30),
        "iat": datetime.now(UTC),
    }
    tok = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {tok}"}


def _seed_enterprise_user(tmp_path, ent_id: int) -> None:
    db = tmp_path / "enterprise.db"
    with sqlite3.connect(db) as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
            "email TEXT DEFAULT '', password_hash TEXT NOT NULL, "
            "role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1, "
            "tenant_id TEXT DEFAULT 'default', "
            "created_at TEXT DEFAULT (datetime('now')), "
            "updated_at TEXT DEFAULT (datetime('now')))"
        )
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, 'default')",
            (ent_id, f"ent{ent_id}"),
        )
        c.commit()


def _payload(name="t1"):
    return {"name": name, "color": "#22c55e"}


def _sql_rows(db_path, where="", *args):
    with sqlite3.connect(db_path) as c:
        c.row_factory = sqlite3.Row
        q = "SELECT * FROM tags"
        if where:
            q += " WHERE " + where
        return [dict(r) for r in c.execute(q, args).fetchall()]


# ── 轴语义（create 面） ─────────────────────────────────────────────────


def test_anon_self_report_preserved(tag_axis_client):
    """匿名自报保留：修前 ?user_id=UID 直写该档案键，契约零变化。"""
    db = tag_axis_client.app.state.tag_client_db
    r = tag_axis_client.post("/api/tags/", params={"user_id": UID_A}, json=_payload())
    assert r.status_code == 200, r.text
    rows = _sql_rows(db)
    assert len(rows) == 1
    assert rows[0]["user_id"] == UID_A


def test_anon_missing_user_id_family_default_key(tag_axis_client):
    """缺省 user_id 修前 422（必填自报面缺省不可用）；现=匿名 default 档案家族正典键。"""
    db = tag_axis_client.app.state.tag_client_db
    r = tag_axis_client.post("/api/tags/", json=_payload())
    assert r.status_code == 200, r.text
    rows = _sql_rows(db)
    assert rows[0]["user_id"] == str(_md5_uuid("default"))


def test_anon_nonuuid_key_family_canonical(tag_axis_client):
    """匿名非 UUID 自报：修前 422（UUID 类型校验拒收），现=md5 家族正典键。"""
    db = tag_axis_client.app.state.tag_client_db
    r = tag_axis_client.post("/api/tags/", params={"user_id": "alice"}, json=_payload())
    assert r.status_code == 200, r.text
    rows = _sql_rows(db)
    assert rows[0]["user_id"] == str(_md5_uuid("alice"))


def test_authed_derived_key(tag_axis_client):
    """认证缺省=未声明→取派生：写键=A 派生档案键（identity 派生真消费）。"""
    db = tag_axis_client.app.state.tag_client_db
    _seed_user(db, UID_A, "alice")
    r = tag_axis_client.post("/api/tags/", json=_payload(), headers=_token(UID_A))
    assert r.status_code == 200, r.text
    rows = _sql_rows(db)
    assert rows[0]["user_id"] == UID_A


@pytest.mark.parametrize("placeholder", ["default", NIL_UUID])
def test_authed_placeholder_is_undeclared(tag_axis_client, placeholder):
    """'default'/占位零 UUID=未声明→取派生（admin-ui tags 页占位形不误伤）。"""
    db = tag_axis_client.app.state.tag_client_db
    _seed_user(db, UID_A, "alice")
    r = tag_axis_client.post(
        "/api/tags/", params={"user_id": placeholder}, json=_payload(), headers=_token(UID_A)
    )
    assert r.status_code == 200, r.text
    rows = _sql_rows(db)
    assert rows[0]["user_id"] == UID_A


def test_authed_confirm_binding(tag_axis_client):
    """自报==派生=确认绑定放行。"""
    db = tag_axis_client.app.state.tag_client_db
    _seed_user(db, UID_A, "alice")
    r = tag_axis_client.post(
        "/api/tags/", params={"user_id": UID_A}, json=_payload(), headers=_token(UID_A)
    )
    assert r.status_code == 200, r.text
    assert _sql_rows(db)[0]["user_id"] == UID_A


def test_conflict_403_zero_side_effect(tag_axis_client):
    """矛盾=403 冒写面收口+零副作用（修前 200 直写他人档案）。"""
    db = tag_axis_client.app.state.tag_client_db
    _seed_user(db, UID_A, "alice")
    _seed_user(db, UID_B, "bob")
    r = tag_axis_client.post(
        "/api/tags/", params={"user_id": UID_A}, json=_payload("intruder"), headers=_token(UID_B)
    )
    assert r.status_code == 403
    assert "user mismatch" in r.json()["detail"]
    assert _sql_rows(db) == []


def test_bad_token_401_zero_side_effect(tag_axis_client):
    db = tag_axis_client.app.state.tag_client_db
    r = tag_axis_client.post(
        "/api/tags/",
        params={"user_id": UID_A},
        json=_payload(),
        headers={"Authorization": "Bearer not-a-token"},
    )
    assert r.status_code == 401
    assert _sql_rows(db) == []


# ── 写键家族正典（enterprise 数字 id → md5 档案键） ────────────────────


def test_enterprise_write_key_family_canonical(tag_axis_client, tmp_path):
    """enterprise 用户（users.id 数字）写键=md5 家族正典，写读同键。"""
    db = tag_axis_client.app.state.tag_client_db
    _seed_enterprise_user(tmp_path, 7)
    hdrs = _enterprise_token(7)
    r = tag_axis_client.post("/api/tags/", json=_payload(), headers=hdrs)
    assert r.status_code == 200, r.text
    rows = _sql_rows(db)
    assert rows[0]["user_id"] == str(_md5_uuid("7"))
    lst = tag_axis_client.get("/api/tags/", headers=hdrs)
    assert lst.status_code == 200
    assert len(lst.json()) == 1


# ── 读隔离（冒读面收口） ───────────────────────────────────────────────


def test_read_isolation_list(tag_axis_client):
    """B 恒读不到 A 的行：list 0 行（派生键不达他人行）。"""
    db = tag_axis_client.app.state.tag_client_db
    _seed_user(db, UID_A, "alice")
    _seed_user(db, UID_B, "bob")
    r = tag_axis_client.post("/api/tags/", json=_payload(), headers=_token(UID_A))
    assert r.status_code == 200, r.text
    lst = tag_axis_client.get("/api/tags/", headers=_token(UID_B))
    assert lst.status_code == 200
    assert lst.json() == []


# ── 四端点同轴电池（B 自报 A=403 零副作用 + A 正对照） ─────────────────


def _seed_owned_tag(tag_axis_client, db, uid):
    r = tag_axis_client.post(
        "/api/tags/", params={"user_id": uid}, json=_payload("owned"), headers=_token(uid)
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


CROSS_CASES = [
    ("POST", "/api/tags/", "create"),
    ("GET", "/api/tags/", None),
    ("PATCH", "/api/tags/{tid}", "update"),
    ("DELETE", "/api/tags/{tid}", None),
]


@pytest.mark.parametrize("method,path,shape", CROSS_CASES)
def test_cross_user_battery_403(tag_axis_client, method, path, shape):
    """B token+自报 A 档案：四端点逐一 403（修前 200 冒读/冒写/冒删）+零副作用。"""
    db = tag_axis_client.app.state.tag_client_db
    _seed_user(db, UID_A, "alice")
    _seed_user(db, UID_B, "bob")
    tid = _seed_owned_tag(tag_axis_client, db, UID_A)
    url = path.format(tid=tid)
    kwargs = {"params": {"user_id": UID_A}, "headers": _token(UID_B)}
    if shape == "create":
        kwargs["json"] = _payload("intruder")
    elif shape == "update":
        kwargs["json"] = {"name": "intruder"}
    r = tag_axis_client.request(method, url, **kwargs)
    assert r.status_code == 403, (method, path, r.status_code, r.text)
    assert "user mismatch" in r.json()["detail"]
    # 零副作用：A 的行一字不动、无新行
    rows = _sql_rows(db)
    assert len(rows) == 1
    assert rows[0]["name"] == "owned"
    assert rows[0]["user_id"] == UID_A


POSITIVE_CASES = [
    ("GET", "/api/tags/", None, 200),
    ("PATCH", "/api/tags/{tid}", "update", 200),
    ("DELETE", "/api/tags/{tid}", None, 200),
]


@pytest.mark.parametrize("method,path,shape,expect", POSITIVE_CASES)
def test_own_row_positive_control(tag_axis_client, method, path, shape, expect):
    """A 正对照：属主操作全通（四端点正向面零回归）。"""
    db = tag_axis_client.app.state.tag_client_db
    _seed_user(db, UID_A, "alice")
    tid = _seed_owned_tag(tag_axis_client, db, UID_A)
    kwargs = {"params": {"user_id": UID_A}, "headers": _token(UID_A)}
    if shape == "update":
        kwargs["json"] = {"name": "t2"}
    r = tag_axis_client.request(method, path.format(tid=tid), **kwargs)
    assert r.status_code == expect, (method, path, r.text)


def test_cross_user_blind_404_row_survives(tag_axis_client):
    """B 不带自报盲操作 A 的 tag：派生键不达行→404，行存活（无存在性探测面同形）。"""
    db = tag_axis_client.app.state.tag_client_db
    _seed_user(db, UID_A, "alice")
    _seed_user(db, UID_B, "bob")
    tid = _seed_owned_tag(tag_axis_client, db, UID_A)
    r = tag_axis_client.delete(f"/api/tags/{tid}", headers=_token(UID_B))
    assert r.status_code == 404
    rows = _sql_rows(db)
    assert len(rows) == 1 and rows[0]["id"] == tid


# ── 契约面（缺省 422→200 既有行为变更如实 + 系统视图零变化） ────────────


def test_missing_user_id_contract_200_default_bucket(tag_axis_client):
    """缺参契约随收口更新（修前 422 必填自报面）：现=200 且按未声明默认桶过滤。"""
    r = tag_axis_client.post("/api/tags/", params={"user_id": UID_A}, json=_payload())
    assert r.status_code == 200
    lst = tag_axis_client.get("/api/tags/")
    assert lst.status_code == 200
    assert lst.json() == []  # UID_A 行不进默认桶
    tag_axis_client.post("/api/tags/", json=_payload("dflt"))
    lst2 = tag_axis_client.get("/api/tags/")
    assert [t["name"] for t in lst2.json()] == ["dflt"]


def test_health_system_view_unchanged(tag_axis_client):
    assert tag_axis_client.get("/api/tags/health").status_code == 200


# ── 静态接线（AST：派生先于服务调用，authorization 形参在场） ─────────


def test_static_wiring_ast():
    src = Path("src/api/tag.py").read_text()
    tree = ast.parse(src)
    targets = {"create", "list_all", "update", "delete"}
    seen = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name not in targets:
            continue
        seen.add(node.name)
        params = [a.arg for a in node.args.args]
        assert "authorization" in params, f"{node.name} 缺 authorization 形参"
        assert "user_id" in params, f"{node.name} 缺 user_id 形参"
        uid_ann = node.args.args[params.index("user_id")].annotation
        assert uid_ann is not None
        ann = ast.unparse(uid_ann)
        assert ann == "str", f"{node.name} user_id 注解={ann}"
        calls = [
            n for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        ]
        bind_calls = [
            n for n in calls if isinstance(n.func, ast.Name) and n.func.id == "bind_identity"
        ]
        assert len(bind_calls) == 1, f"{node.name} bind_identity 调用数={len(bind_calls)}"
        resolve_calls = [
            n for n in calls if isinstance(n.func, ast.Name) and n.func.id == "_resolve_user_id"
        ]
        assert len(resolve_calls) == 1, f"{node.name} _resolve_user_id 调用数={len(resolve_calls)}"
    assert seen == targets, f"端点集缺口: {targets - seen}"
