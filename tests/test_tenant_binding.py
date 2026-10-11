"""F3b-② 认证→租户绑定测试——tenant/user 从认证身份派生，禁客户端自报。

覆盖四层（全部 tmp 独立库，绝不写生产 data/*.db）：
①users.tenant_id 缺列自愈（双库、幂等、存量行回填 'default'）
②bind_identity 派生语义（匿名钉 default/自报拒、token 派生、自报只可确认
  不得矛盾、未知/停用身份 401、A1 进程内直调兼容）
③注册绑定写路径（enterprise 真库往返 register→login→token 派生闭环；
  标准 auth 真写 SQLitePool 隔离池回读）
④API 层冒读面收口（/api/brain 真实 router：tenant 自报=403（修前 200）、
  派生 tenant 真流入执行面、/api/mind 声明面收口）
"""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from jose import jwt

from src.config import settings
from src.services import tenant_binding as tb

# 生产 users 表形状（无 tenant_id → 证明自愈补列）
USERS_DDL = """CREATE TABLE users (
    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
    email TEXT DEFAULT '', password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')))"""

ENTERPRISE_DDL = """CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')))"""


def _mk_db(path, ddl):
    conn = sqlite3.connect(path)
    conn.execute(ddl)
    conn.commit()
    conn.close()


def _ent_token(uid, username="u"):
    payload = {
        "sub": str(uid),
        "username": username,
        "scope": "enterprise",
        "exp": datetime.now(UTC) + timedelta(minutes=10),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _user_token(uid):
    payload = {
        "sub": str(uid),
        "role": "user",
        "exp": datetime.now(UTC) + timedelta(minutes=10),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _insert_ent_user(db, username, tenant, is_active=1):
    tb.ensure_user_tenant_columns(str(db))
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password, is_active, tenant_id) VALUES (?, ?, ?, ?)",
            (username, "hashed", is_active, tenant),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _insert_std_user(db, uid, username, tenant, is_active=1):
    tb.ensure_user_tenant_columns(str(db))
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (uid, username, "hashed", is_active, tenant),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 双身份库+tmp nest.db（NEST_DB/USERS_DB/ENTERPRISE_DB 三路环境隔离）。"""
    users_db = tmp_path / "opensoul.db"
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    monkeypatch.setenv("NEST_DB", str(tmp_path / "nest.db"))
    _mk_db(users_db, USERS_DDL)
    _mk_db(ent_db, ENTERPRISE_DDL)
    return SimpleNamespace(users_db=users_db, ent_db=ent_db, tmp=tmp_path)


# ── ① users.tenant_id 缺列自愈 ─────────────────────────────────────────


class TestSelfHeal:
    def test_tenant_column_added_and_backfilled(self, env):
        # 先在无 tenant_id 列的库插行（生产存量形状）→ 自愈后回填 'default'
        conn = sqlite3.connect(env.users_db)
        conn.execute("INSERT INTO users (id, username, password_hash) VALUES ('u1', 'alice', 'h')")
        conn.commit()
        conn.close()

        tb.ensure_user_tenant_columns()  # 缺省=双库都补

        conn = sqlite3.connect(env.users_db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        row = conn.execute("SELECT tenant_id FROM users WHERE id = 'u1'").fetchone()
        conn.close()
        assert "tenant_id" in cols
        assert row[0] == "default"

    def test_idempotent(self, env):
        tb.ensure_user_tenant_columns()
        tb.ensure_user_tenant_columns()  # 重复执行不炸
        conn = sqlite3.connect(env.ent_db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        conn.close()
        assert cols.count("tenant_id") == 1

    def test_missing_db_not_created(self, env):
        ghost = env.tmp / "no_such.db"
        tb.ensure_user_tenant_columns(str(ghost))
        assert not ghost.exists()


# ── ② bind_identity 派生语义 ───────────────────────────────────────────


class TestBindIdentityAnonymous:
    def test_anonymous_pinned_default(self, env):
        ident = tb.bind_identity(None)
        assert (ident.tenant_id, ident.user_id, ident.authed, ident.source) == (
            "default",
            "default",
            False,
            "anonymous",
        )

    def test_anonymous_declared_user_kept(self, env):
        # 既有本地多档案语义保持（/api/brain/feedback 查询面）
        ident = tb.bind_identity(None, "default", "u1")
        assert ident.user_id == "u1"

    def test_anonymous_tenant_self_report_rejected(self, env):
        with pytest.raises(HTTPException) as e:
            tb.bind_identity(None, "tenant-x", "")
        assert e.value.status_code == 403
        assert "self-report" in e.value.detail

    def test_fastapi_default_placeholder_is_anonymous(self, env):
        # A1 进程内直调不带 authorization（FastAPI Header 缺省占位非字符串）→
        # 匿名语义，绝不 AttributeError；同样钉死 default 禁自报
        with pytest.raises(HTTPException) as e:
            tb.bind_identity(object(), "tenant-x")
        assert e.value.status_code == 403
        ident = tb.bind_identity(object(), "default", "u2")
        assert (ident.tenant_id, ident.user_id) == ("default", "u2")


class TestBindIdentityToken:
    def test_invalid_token_401(self, env):
        with pytest.raises(HTTPException) as e:
            tb.bind_identity("Bearer not-a-jwt")
        assert e.value.status_code == 401

    def test_wrong_secret_401(self, env):
        bad = jwt.encode(
            {
                "sub": "1",
                "scope": "enterprise",
                "exp": datetime.now(UTC) + timedelta(minutes=5),
            },
            "wrong-secret",
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(HTTPException) as e:
            tb.bind_identity(f"Bearer {bad}")
        assert e.value.status_code == 401

    def test_enterprise_token_derives_tenant(self, env):
        uid = _insert_ent_user(env.ent_db, "alice", "tenant-x")
        ident = tb.bind_identity(f"Bearer {_ent_token(uid, 'alice')}")
        assert (ident.tenant_id, ident.user_id, ident.authed, ident.source) == (
            "tenant-x",
            str(uid),
            True,
            "token:enterprise",
        )

    def test_declared_default_means_undeclared(self, env):
        # 不传/传缺省=未声明→取派生（不误伤不传参的认证调用方）
        uid = _insert_ent_user(env.ent_db, "bob", "tenant-x")
        ident = tb.bind_identity(f"Bearer {_ent_token(uid)}", "default", "default")
        assert (ident.tenant_id, ident.user_id) == ("tenant-x", str(uid))

    def test_declared_confirm_ok(self, env):
        uid = _insert_ent_user(env.ent_db, "carol", "tenant-x")
        ident = tb.bind_identity(f"Bearer {_ent_token(uid)}", "tenant-x", str(uid))
        assert ident.tenant_id == "tenant-x"

    def test_tenant_mismatch_403(self, env):
        uid = _insert_ent_user(env.ent_db, "dave", "tenant-x")
        with pytest.raises(HTTPException) as e:
            tb.bind_identity(f"Bearer {_ent_token(uid)}", "tenant-y")
        assert e.value.status_code == 403
        assert "tenant-x" in e.value.detail and "tenant-y" in e.value.detail

    def test_user_mismatch_403(self, env):
        uid = _insert_ent_user(env.ent_db, "eve", "tenant-x")
        with pytest.raises(HTTPException) as e:
            tb.bind_identity(f"Bearer {_ent_token(uid)}", "tenant-x", "someone-else")
        assert e.value.status_code == 403

    def test_std_token_derives(self, env):
        _insert_std_user(env.users_db, "uuid-1", "frank", "tenant-y")
        ident = tb.bind_identity(f"Bearer {_user_token('uuid-1')}")
        assert (ident.tenant_id, ident.user_id, ident.source) == (
            "tenant-y",
            "uuid-1",
            "token:user",
        )

    def test_unknown_identity_401(self, env):
        with pytest.raises(HTTPException) as e:
            tb.bind_identity(f"Bearer {_ent_token(99999)}")
        assert e.value.status_code == 401

    def test_inactive_identity_401(self, env):
        uid = _insert_ent_user(env.ent_db, "mallory", "tenant-x", is_active=0)
        with pytest.raises(HTTPException) as e:
            tb.bind_identity(f"Bearer {_ent_token(uid)}")
        assert e.value.status_code == 401

    def test_empty_tenant_defaults(self, env):
        # 自愈列 NOT NULL DEFAULT 'default'（NULL 插不进），空串绑定按 'default' 兜
        uid = _insert_ent_user(env.ent_db, "grace", "")
        ident = tb.bind_identity(f"Bearer {_ent_token(uid)}")
        assert ident.tenant_id == "default"

    def test_bad_subject_401(self, env):
        payload = {
            "sub": "not-int",
            "scope": "enterprise",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        }
        tok = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
        with pytest.raises(HTTPException) as e:
            tb.bind_identity(f"Bearer {tok}")
        assert e.value.status_code == 401


class TestValidateTenant:
    def test_default_always_ok(self, env):
        assert tb.validate_tenant("") == "default"
        assert tb.validate_tenant(None) == "default"
        assert tb.validate_tenant("default") == "default"

    def test_registered_tenant_ok(self, env):
        from src.nest.tenant import TenantManager

        t = TenantManager().create(name="测试租户")
        assert tb.validate_tenant(t.tenant_id) == t.tenant_id

    def test_unknown_tenant_rejected(self, env):
        with pytest.raises(ValueError):
            tb.validate_tenant("tenant-nope")


# ── ③ 注册绑定写路径 ───────────────────────────────────────────────────


class TestRegisterBinding:
    @pytest.fixture()
    def ent_client(self, env, monkeypatch):
        import src.api.enterprise as ent

        monkeypatch.setattr(ent, "_DB_PATH", env.ent_db)
        ent._init_db()  # 真实建库路径：补 roles/user_roles/permissions/audit_log
        app = FastAPI()
        app.include_router(ent.router)  # router 自带 /api/enterprise 前缀
        with TestClient(app) as c:
            yield c

    def test_register_unknown_tenant_400(self, ent_client):
        resp = ent_client.post(
            "/api/enterprise/auth/register",
            json={"username": "u1", "password": "pw", "tenant_id": "tenant-nope"},
        )
        assert resp.status_code == 400
        assert "unknown tenant" in resp.json()["detail"]

    def test_register_default_contract_compat(self, ent_client, env):
        # 既有调用方不带 tenant_id → 契约不变，落 'default'
        resp = ent_client.post(
            "/api/enterprise/auth/register",
            json={"username": "u2", "password": "pw"},
        )
        assert resp.status_code == 200
        assert resp.json()["tenant_id"] == "default"
        conn = sqlite3.connect(env.ent_db)
        row = conn.execute("SELECT tenant_id FROM users WHERE username = 'u2'").fetchone()
        conn.close()
        assert row[0] == "default"

    def test_register_login_token_roundtrip(self, ent_client, env):
        """绑定闭环硬证：register 带租户→login 拿 token→bind_identity 派生同租户。"""
        from src.nest.tenant import TenantManager

        t = TenantManager().create(name="绑定租户")
        resp = ent_client.post(
            "/api/enterprise/auth/register",
            json={"username": "u3", "password": "pw", "tenant_id": t.tenant_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["tenant_id"] == t.tenant_id

        # 真源回读
        conn = sqlite3.connect(env.ent_db)
        row = conn.execute("SELECT id, tenant_id FROM users WHERE username = 'u3'").fetchone()
        conn.close()
        assert row[1] == t.tenant_id

        # login → token → 派生（单一真源=users 行）
        login = ent_client.post(
            "/api/enterprise/auth/login",
            json={"username": "u3", "password": "pw"},
        )
        assert login.status_code == 200
        ident = tb.bind_identity(f"Bearer {login.json()['access_token']}")
        assert (ident.tenant_id, ident.user_id, ident.authed) == (
            t.tenant_id,
            str(row[0]),
            True,
        )

    def test_std_register_binds_tenant_real_write(self, env, monkeypatch):
        """标准 auth 注册真写隔离池：INSERT 真落 tmp 库且 tenant_id 回读一致。"""
        from src.nest.tenant import TenantManager

        t = TenantManager().create(name="标准租户")
        monkeypatch.setattr(settings, "database_url", f"sqlite:///{env.users_db}")
        import src.services.auth as auth
        from src.database.postgres import SQLitePool

        pool = SQLitePool()

        async def run():
            await pool.connect()
            try:
                monkeypatch.setattr(auth, "db_pool", pool)
                return await auth.register_user("stduser", "s@x", "pw", tenant_id=t.tenant_id)
            finally:
                await pool.disconnect()

        user = asyncio.run(run())
        conn = sqlite3.connect(env.users_db)
        row = conn.execute("SELECT tenant_id FROM users WHERE id = ?", (user["id"],)).fetchone()
        conn.close()
        assert row[0] == t.tenant_id

        # 标准 token 派生同一绑定
        ident = tb.bind_identity(f"Bearer {_user_token(user['id'])}")
        assert (ident.tenant_id, ident.user_id) == (t.tenant_id, user["id"])


# ── ④ API 层冒读面收口（真实 router）───────────────────────────────────


class _Rec:
    """LongTermLearning 录制替身：记录构造入参=派生 tenant 是否真流入执行面。"""

    calls: list = []

    def __init__(self, db_pool, tenant_id, agent_id):
        _Rec.calls.append({"tenant_id": tenant_id, "agent_id": agent_id})

    async def get_recommendations(self, intent):
        return [{"pattern_key": "p"}]


class TestBrainAPIBinding:
    @pytest.fixture()
    def brain_client(self, env, monkeypatch):
        import src.api.brain as brain_api
        import src.learn.long_term as lt

        _Rec.calls = []
        monkeypatch.setattr(lt, "LongTermLearning", _Rec)
        app = FastAPI()
        app.include_router(brain_api.router, prefix="/api/brain")
        with TestClient(app) as c:
            yield c

    def test_self_report_tenant_rejected_403(self, brain_client):
        # F3 审计③修前实证：GET ?tenant_id=attacker-tenant 零验证 200（自报即得）→
        # 修后冒读面收口 403
        resp = brain_client.get(
            "/api/brain/recommendations", params={"tenant_id": "attacker-tenant"}
        )
        assert resp.status_code == 403
        assert "self-report" in resp.json()["detail"]
        assert _Rec.calls == []

    def test_anonymous_default_regression(self, brain_client):
        resp = brain_client.get("/api/brain/recommendations")
        assert resp.status_code == 200
        assert resp.json()["recommendations"] == [{"pattern_key": "p"}]
        assert _Rec.calls == [{"tenant_id": "default", "agent_id": "default"}]

    def test_token_tenant_derived_flows_into_execution(self, brain_client, env):
        uid = _insert_ent_user(env.ent_db, "alice", "tenant-x")
        resp = brain_client.get(
            "/api/brain/recommendations",
            headers={"authorization": f"Bearer {_ent_token(uid)}"},
        )
        assert resp.status_code == 200
        # 派生 tenant 真流入 LongTermLearning 构造参数（数据面按绑定隔离）
        assert _Rec.calls == [{"tenant_id": "tenant-x", "agent_id": "default"}]

    def test_token_declared_confirm_ok(self, brain_client, env):
        uid = _insert_ent_user(env.ent_db, "bob", "tenant-x")
        resp = brain_client.get(
            "/api/brain/recommendations",
            params={"tenant_id": "tenant-x"},
            headers={"authorization": f"Bearer {_ent_token(uid)}"},
        )
        assert resp.status_code == 200
        assert _Rec.calls[0]["tenant_id"] == "tenant-x"

    def test_token_declared_mismatch_403(self, brain_client, env):
        uid = _insert_ent_user(env.ent_db, "carol", "tenant-x")
        resp = brain_client.get(
            "/api/brain/recommendations",
            params={"tenant_id": "tenant-y"},
            headers={"authorization": f"Bearer {_ent_token(uid)}"},
        )
        assert resp.status_code == 403

    def test_feedback_user_mismatch_403(self, brain_client, env):
        uid = _insert_ent_user(env.ent_db, "dave", "tenant-x")
        resp = brain_client.get(
            "/api/brain/feedback",
            params={"user_id": "someone-else"},
            headers={"authorization": f"Bearer {_ent_token(uid)}"},
        )
        assert resp.status_code == 403

    def test_think_declaration_surface(self, brain_client):
        # think 不消费 tenant，但声明面收口：匿名自报非 default=403
        resp = brain_client.post(
            "/api/brain/think", json={"tenant_id": "tenant-x", "user_input": "hi"}
        )
        assert resp.status_code == 403

    def test_a1_inprocess_direct_call_compat(self):
        """A1 接线回归硬证：ai_groups 以 brain_verify(VerifyRequest(...)) 直调
        （不带 authorization），FastAPI Header 缺省占位不得炸、匿名语义照旧。"""
        from src.api.brain import VerifyRequest, verify

        result = asyncio.run(
            verify(VerifyRequest(task_id="t1", action="a", result={"success": True}))
        )
        assert result["status"] == "verified"

        # 直调带自报租户同样收口
        with pytest.raises(HTTPException) as e:
            asyncio.run(
                verify(VerifyRequest(task_id="t2", action="a", result={}, tenant_id="tenant-x"))
            )
        assert e.value.status_code == 403


class TestMindDeclarationSurface:
    @pytest.fixture()
    def mind_client(self, env, monkeypatch):
        # PreferenceLearner 默认落 ~/.hermes/opensoul/mind——HOME 换 tmp 免写生产
        monkeypatch.setenv("HOME", str(env.tmp))
        import src.api.mind as mind_api

        app = FastAPI()
        app.include_router(mind_api.router, prefix="/api/mind")
        with TestClient(app) as c:
            yield c

    def test_tenant_self_report_rejected_403(self, mind_client):
        resp = mind_client.post(
            "/api/mind/preference/learn",
            json={"tenant_id": "attacker-tenant", "messages": [{"content": "hi"}]},
        )
        assert resp.status_code == 403

    def test_default_still_works(self, mind_client):
        resp = mind_client.post(
            "/api/mind/preference/learn",
            json={"messages": [{"content": "hi"}]},
        )
        assert resp.status_code == 200
