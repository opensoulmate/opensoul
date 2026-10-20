"""POST /relations 写面归属闸测试——两端实体须属派生 user。

H1-②b-⑦a-⑤ 诚实边界②留档件收口（「POST /relations 写面归属闸（两端实体须属
派生 user）=另拍候选」）。修前（HEAD 旧代码同测控制组实证）：add_relation 零
身份轴、create_relation 零归属检查——任意人可给任意实体 id 挂关系（含他人实体
=跨用户边污染受害方关系列表、不存在实体=悬挂关系行占库）。

隔离纪律（TL-②/H1-②b 系先例）：tmp opensoul.db 真 schema（与 src/main.py
lifespan DDL 同形，relations 真源列=type）+ db_pool 多绑定换 tmp SQLitePool +
tmp USERS_DB/ENTERPRISE_DB（认证轴），绝不写生产库。
"""

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

import pytest

_GRAPH_DDL = """CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    name TEXT NOT NULL, type TEXT DEFAULT '',
    description TEXT DEFAULT '', properties TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS relations (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
    target_id TEXT NOT NULL, type TEXT NOT NULL,
    properties TEXT DEFAULT '{}', created_at TEXT DEFAULT (datetime('now')));"""

USERS_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "password_hash TEXT, is_active INTEGER NOT NULL DEFAULT 1, "
    "tenant_id TEXT NOT NULL DEFAULT 'default')"
)


def _md5_uuid(s: str) -> uuid.UUID:
    h = hashlib.md5(s.encode()).hexdigest()
    return uuid.UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


def _uid(user_id: str = "user-a"):
    from src.api.graph import _resolve_user_id

    return _resolve_user_id(user_id)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """真实 router + tmp opensoul.db（真 schema）+ tmp 身份库，绝不写生产库。"""
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.graph as graph_api
    import src.database.postgres as postgres_mod
    import src.services.entity as entity_mod
    import src.services.graph as graph_mod
    from src.config import settings
    from src.database.postgres import SQLitePool

    tmp_os_db = tmp_path / "opensoul.db"
    with sqlite3.connect(tmp_os_db) as _c:
        _c.executescript(_GRAPH_DDL)
        _c.commit()

    users_db = tmp_path / "users.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    with sqlite3.connect(users_db) as _c:
        _c.execute(USERS_DDL)
        _c.commit()

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_os_db}")
    tmp_pool = SQLitePool()
    monkeypatch.setattr(postgres_mod, "db_pool", tmp_pool)
    monkeypatch.setattr(graph_mod, "db_pool", tmp_pool)
    monkeypatch.setattr(entity_mod, "db_pool", tmp_pool)
    monkeypatch.setattr(graph_api, "db_pool", tmp_pool)

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()
        yield
        await tmp_pool.disconnect()

    app = FastAPI(lifespan=_lifespan)
    app.include_router(graph_api.router, prefix="/api/graph")
    with TestClient(app) as client:
        yield {"client": client, "db": str(tmp_os_db), "users_db": users_db}


def _seed_entity(db_path, user_id: str, name: str) -> str:
    eid = str(uuid.uuid4())
    with sqlite3.connect(db_path) as _c:
        _c.execute(
            "INSERT INTO entities (id, user_id, name, type, description, properties) "
            "VALUES (?, ?, ?, 'person', '', ?)",
            (eid, str(_uid(user_id)), name, json.dumps({})),
        )
        _c.commit()
    return eid


def _seed_user(db_path: Path, uid: str, username: str) -> str:
    with sqlite3.connect(db_path) as _c:
        _c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, 'default')",
            (uid, username),
        )
        _c.commit()
    return uid


def _token(uid: str) -> dict:
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid), role="user")
    return {"Authorization": f"Bearer {tok}"}


def _relation_count(db_path) -> int:
    with sqlite3.connect(db_path) as _c:
        return _c.execute("SELECT COUNT(*) FROM relations").fetchone()[0]


def _post(client, a, b, **kw):
    params = kw.pop("params", None)
    headers = kw.pop("headers", None)
    return client.post(
        "/api/graph/relations",
        params=params,
        headers=headers,
        json={
            "source_entity_id": a,
            "target_entity_id": b,
            "relation_type": "knows",
            "properties": {"w": 1},
        },
    )


class TestOwnershipGate:
    """两端实体须属派生 user（写面归属闸本体，真实数据面）。"""

    def test_same_owner_roundtrip(self, env):
        a = _seed_entity(env["db"], "user-a", "A")
        b = _seed_entity(env["db"], "user-a", "B")
        resp = _post(env["client"], a, b, params={"user_id": "user-a"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["relation_type"] == "knows"
        assert data["properties"] == {"w": 1}  # json.dumps/loads 往返契约不变
        assert _relation_count(env["db"]) == 1
        listed = env["client"].get("/api/graph/relations", params={"user_id": "user-a"})
        assert [r["relation_type"] for r in listed.json()] == ["knows"]

    def test_cross_user_end_rejected_zero_side_effect(self, env):
        """跨用户端=404 缺/跨用户同形（无存在性探测面）+ 零新行硬反证。"""
        a = _seed_entity(env["db"], "user-a", "A")
        b = _seed_entity(env["db"], "user-b", "B")
        resp = _post(env["client"], a, b, params={"user_id": "user-a"})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Entity not found"
        assert _relation_count(env["db"]) == 0  # 零副作用：一行未写

    def test_missing_end_rejected_zero_side_effect(self, env):
        """不存在实体 id=404（修前照写=悬挂关系行占库）。"""
        a = _seed_entity(env["db"], "user-a", "A")
        resp = _post(env["client"], a, str(uuid.uuid4()), params={"user_id": "user-a"})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Entity not found"
        assert _relation_count(env["db"]) == 0

    def test_target_owned_source_foreign_rejected(self, env):
        """反向混端同样拒（归属判法对两端对称）。"""
        a = _seed_entity(env["db"], "user-a", "A")
        b = _seed_entity(env["db"], "user-b", "B")
        resp = _post(env["client"], b, a, params={"user_id": "user-a"})
        assert resp.status_code == 404
        assert _relation_count(env["db"]) == 0

    def test_anon_self_report_preserved(self, env):
        """匿名保留自报（既有本地多档案语义契约零变化）：自报档案键两端真属该键
        → 照常可建。"""
        a = _seed_entity(env["db"], "user-a", "A")
        b = _seed_entity(env["db"], "user-a", "B")
        resp = _post(env["client"], a, b, params={"user_id": "user-a"})
        assert resp.status_code == 200
        assert _relation_count(env["db"]) == 1

    def test_anon_default_profile_gated(self, env):
        """匿名缺省 'default' 档案键不得写 user-a 的实体（修前 200 直写=幽灵写
        硬反证，控制组旧代码同测必炸）。"""
        a = _seed_entity(env["db"], "user-a", "A")
        b = _seed_entity(env["db"], "user-a", "B")
        resp = _post(env["client"], a, b)
        assert resp.status_code == 404
        assert _relation_count(env["db"]) == 0


class TestIdentityAxisOnWrite:
    """写面身份轴（bind_identity 同轴）：认证派生禁自报冒写。"""

    def test_authed_derived_key_creates(self, env):
        user_a = _seed_user(env["users_db"], str(uuid.uuid4()), "alice-rel")
        a = _seed_entity(env["db"], user_a, "A")
        b = _seed_entity(env["db"], user_a, "B")
        resp = _post(env["client"], a, b, headers=_token(user_a))
        assert resp.status_code == 200
        assert _relation_count(env["db"]) == 1

    def test_authed_foreign_end_rejected(self, env):
        """认证请求端点实体属他人档案=404（派生键只达自己的实体）。"""
        user_a = _seed_user(env["users_db"], str(uuid.uuid4()), "alice-rel2")
        user_b = _seed_user(env["users_db"], str(uuid.uuid4()), "bob-rel2")
        a = _seed_entity(env["db"], user_a, "A")
        b = _seed_entity(env["db"], user_b, "B")
        resp = _post(env["client"], a, b, headers=_token(user_a))
        assert resp.status_code == 404
        assert _relation_count(env["db"]) == 0

    def test_authed_conflict_403_zero_side_effect(self, env):
        user_a = _seed_user(env["users_db"], str(uuid.uuid4()), "alice-rel3")
        user_b = _seed_user(env["users_db"], str(uuid.uuid4()), "bob-rel3")
        a = _seed_entity(env["db"], user_a, "A")
        b = _seed_entity(env["db"], user_a, "B")
        resp = _post(env["client"], a, b, params={"user_id": user_b}, headers=_token(user_a))
        assert resp.status_code == 403
        assert "user mismatch" in resp.json()["detail"]
        assert _relation_count(env["db"]) == 0

    def test_bad_token_401(self, env):
        a = _seed_entity(env["db"], "user-a", "A")
        b = _seed_entity(env["db"], "user-a", "B")
        resp = _post(env["client"], a, b, headers={"Authorization": "Bearer garbage"})
        assert resp.status_code == 401
        assert _relation_count(env["db"]) == 0
