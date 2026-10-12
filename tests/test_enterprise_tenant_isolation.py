"""F3b-③c-2②c enterprise 面（enterprise.db users/audit_log 表）数据面隔离接线测试。

覆盖四层（全部 tmp 独立库 + NEST_DB/USERS_DB/ENTERPRISE_DB 环境隔离，测试行
绝不写生产 data/enterprise.db / data/opensoul.db）：
①users/audit_log.tenant_id 列迁移（老库补列+审计行随 users 行归属回填+租户索引
  补列之后建）与粘性（重跑 _init_db 不洗租户键）
②审计行归属（显式键优先/users 行派生/关联不到→'default' 兜底）与 API 写路径
  （register/login 审计行真落行归属租户）
③查询过滤（users/list、audit 恒按请求者租户，修前全量列表=跨租户泄露）
④IsolationEngine.check_access 真执行（by-ID 闸门跨租户 EnterpriseAccessDenied→
  API 403 带真因、bulk 审计留痕、审计行 ns 对账）
⑤API 层契约（响应形状加性 tenant_id 键、缺行 404、匿名/坏 token 契约不变、
  register 未知租户 400 契约不变）

注：src.api.enterprise 模块级 _DB_PATH 在 import 期计算——fixture 内延迟 import +
monkeypatch._DB_PATH + 幂等 _init_db()，无论模块此前是否已 import 都落 tmp。
"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from src.config import settings

# 生产 enterprise.db F3b-③c-2②c 迁移前形状（users/audit_log 均无 tenant_id）
OLD_USERS_DDL = """CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')))"""

OLD_AUDIT_DDL = """CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    action TEXT NOT NULL,
    resource TEXT NOT NULL,
    detail TEXT,
    ip TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')))"""


def _ent_token(uid, username="u"):
    payload = {
        "sub": str(uid),
        "username": username,
        "scope": "enterprise",
        "exp": datetime.now(UTC) + timedelta(minutes=10),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _rows(db, sql, params=()):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


@pytest.fixture()
def ent_env(tmp_path, monkeypatch):
    """enterprise API 隔离数据面：tmp enterprise.db/opensoul.db（NEST_DB 由
    conftest autouse 隔离）。import 延迟+路径双保险，绝不碰生产库。"""
    db = tmp_path / "enterprise.db"
    monkeypatch.setenv("ENTERPRISE_DB", str(db))
    monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))

    import src.api.enterprise as ent

    monkeypatch.setattr(ent, "_DB_PATH", db)  # import 期缓存路径同步换 tmp
    ent._init_db()  # 幂等：tmp 库建表+迁移
    return ent


@pytest.fixture()
def ent_client(ent_env):
    app = FastAPI()
    app.include_router(ent_env.router)
    with TestClient(app) as c:
        yield c


def _insert_user(ent, username, tenant):
    with ent._db() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password, tenant_id) VALUES (?, ?, ?)",
            (username, "hashed", tenant),
        )
        return cur.lastrowid


def _seed_role(ent, user_id, perms, role_name="ops"):
    with ent._db() as conn:
        cur = conn.execute("INSERT INTO roles (name) VALUES (?)", (role_name,))
        role_id = cur.lastrowid
        for res, act in perms:
            conn.execute(
                "INSERT INTO permissions (role_id, resource, action) VALUES (?, ?, ?)",
                (role_id, res, act),
            )
        conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))


def _mk_tenants():
    """NestStore 登记真租户（validate_tenant 要求非 default 租户已登记）。"""
    from src.nest.tenant import TenantManager

    m = TenantManager()
    m.ensure_default_tenant()
    return m.create(name="A").tenant_id, m.create(name="B").tenant_id


# ── ① 迁移 + 粘性 ─────────────────────────────────────────────────────


class TestMigrationAndAttribution:
    def test_columns_backfilled_from_users_and_indexed(self, tmp_path, monkeypatch):
        db = tmp_path / "enterprise.db"
        conn = sqlite3.connect(db)
        conn.execute(OLD_USERS_DDL)
        conn.execute(OLD_AUDIT_DDL)
        conn.execute("INSERT INTO users (username, password) VALUES ('alice', 'h')")
        conn.execute("INSERT INTO users (username, password) VALUES ('bob', 'h')")
        conn.execute(
            "INSERT INTO audit_log (user_id, action, resource) VALUES (1, 'login', 'auth')"
        )
        conn.execute(
            "INSERT INTO audit_log (user_id, action, resource) VALUES (2, 'login', 'auth')"
        )
        conn.execute(
            "INSERT INTO audit_log (user_id, action, resource) VALUES (NULL, 'login_failed', 'auth')"
        )
        conn.commit()
        conn.close()

        # users 行先绑非 default 租户（模拟 F3b-② 已绑租户的存量库），
        # 审计行回填必须随 users 行归属（非一律 'default'）
        from src.services import tenant_binding as tb

        tb.ensure_user_tenant_columns(str(db))
        conn = sqlite3.connect(db)
        conn.execute("UPDATE users SET tenant_id = 'tenant-a' WHERE id = 1")
        conn.commit()
        conn.close()

        import src.api.enterprise as ent

        monkeypatch.setattr(ent, "_DB_PATH", db)
        ent._init_db()

        cols_u = [r[1] for r in _rows(db, "PRAGMA table_info(users)")]
        cols_a = [r[1] for r in _rows(db, "PRAGMA table_info(audit_log)")]
        assert "tenant_id" in cols_u and "tenant_id" in cols_a
        # 审计行随 users 行归属：user 1→tenant-a、user 2→default、NULL user→default
        assert _rows(db, "SELECT tenant_id FROM audit_log ORDER BY id") == [
            ("tenant-a",),
            ("default",),
            ("default",),
        ]
        # 租户索引真建（补列之后建不炸——③c-2① 坑）
        assert _rows(db, "PRAGMA index_info(idx_users_tenant)")
        assert _rows(db, "PRAGMA index_info(idx_audit_tenant)")

    def test_tenant_keys_sticky_across_reinit(self, ent_env):
        db = ent_env._DB_PATH
        uid = _insert_user(ent_env, "alice", "tenant-a")
        ent_env._record_audit(uid, "login", "auth")
        assert _rows(db, "SELECT tenant_id FROM audit_log") == [("tenant-a",)]
        # 手工改绑后的键在重跑 _init_db（重启）后不被洗回（回填只在迁移时跑一次）
        conn = sqlite3.connect(db)
        conn.execute("UPDATE audit_log SET tenant_id = 'tenant-x'")
        conn.commit()
        conn.close()
        ent_env._init_db()
        assert _rows(db, "SELECT tenant_id FROM audit_log") == [("tenant-x",)]

    def test_record_audit_explicit_derived_fallback(self, ent_env):
        db = ent_env._DB_PATH
        uid = _insert_user(ent_env, "alice", "tenant-a")
        ent_env._record_audit(uid, "a1", "r", tenant_id="explicit-tenant")  # 显式优先
        ent_env._record_audit(uid, "a2", "r")  # users 行派生
        ent_env._record_audit(999999, "a3", "r")  # 未知 user→default 兜底
        ent_env._record_audit(None, "a4", "r")  # 匿名→default 兜底
        got = _rows(db, "SELECT action, tenant_id FROM audit_log ORDER BY id")
        assert got == [
            ("a1", "explicit-tenant"),
            ("a2", "tenant-a"),
            ("a3", "default"),
            ("a4", "default"),
        ]


# ── ② API 写路径审计行归属 ────────────────────────────────────────────


class TestApiAuditAttribution:
    def test_register_login_audit_rows_attributed(self, ent_env, ent_client):
        a, _ = _mk_tenants()
        r = ent_client.post(
            "/api/enterprise/auth/register",
            json={"username": "alice", "password": "pw123456", "tenant_id": a},
        )
        assert r.status_code == 200
        r = ent_client.post(
            "/api/enterprise/auth/login", json={"username": "alice", "password": "wrong"}
        )
        assert r.status_code == 401
        r = ent_client.post(
            "/api/enterprise/auth/login", json={"username": "alice", "password": "pw123456"}
        )
        assert r.status_code == 200
        r = ent_client.post(
            "/api/enterprise/auth/login",
            json={"username": "ghost_user_xyz", "password": "x"},
        )
        assert r.status_code == 401

        got = _rows(ent_env._DB_PATH, "SELECT action, tenant_id FROM audit_log ORDER BY id")
        # register→租户 a、login 失败（行租户已知）→a、login 成功→a、未知用户→default
        assert got == [
            ("register", a),
            ("login_failed", a),
            ("login", a),
            ("login_failed", "default"),
        ]

    def test_register_unknown_tenant_400_contract(self, ent_client):
        r = ent_client.post(
            "/api/enterprise/auth/register",
            json={"username": "eve", "password": "pw123456", "tenant_id": "tenant-ghost-xyz"},
        )
        assert r.status_code == 400  # validate_tenant 禁悬空绑定（既有契约不变）


# ── ③ 查询过滤 ────────────────────────────────────────────────────────


class TestQueryFiltering:
    def test_users_list_isolated(self, ent_env, ent_client):
        a, b = _mk_tenants()
        alice = _insert_user(ent_env, "alice", a)
        _insert_user(ent_env, "bob", b)
        _insert_user(ent_env, "dave", "default")
        _seed_role(ent_env, alice, [("users", "list")])
        tok = _ent_token(alice, "alice")

        r = ent_client.get("/api/enterprise/users/list", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200
        names = [u["username"] for u in r.json()]
        assert names == ["alice"]  # 修前=全量三行（跨租户身份泄露）
        assert r.json()[0]["tenant_id"] == a  # 加性契约键

    def test_audit_isolated(self, ent_env, ent_client):
        a, b = _mk_tenants()
        alice = _insert_user(ent_env, "alice", a)
        bob = _insert_user(ent_env, "bob", b)
        _seed_role(ent_env, alice, [("audit", "read")], role_name="r1")
        _seed_role(ent_env, bob, [("audit", "read")], role_name="r2")
        ent_env._record_audit(alice, "act-a", "res", tenant_id=a)
        ent_env._record_audit(bob, "act-b", "res", tenant_id=b)
        ent_env._record_audit(None, "act-default", "res")

        r = ent_client.get(
            "/api/enterprise/audit",
            headers={"Authorization": f"Bearer {_ent_token(alice, 'alice')}"},
        )
        assert r.status_code == 200
        acts = [e["action"] for e in r.json()]
        assert "act-a" in acts and "act-b" not in acts and "act-default" not in acts
        assert all(e["tenant_id"] == a for e in r.json())  # 加性契约键

        r = ent_client.get(
            "/api/enterprise/audit", headers={"Authorization": f"Bearer {_ent_token(bob, 'bob')}"}
        )
        acts = [e["action"] for e in r.json()]
        assert acts == ["act-b"]  # B 只见自己租户（含与 default 面互不可见）


# ── ④ IsolationEngine 闸门真执行 ──────────────────────────────────────


class TestEngineGates:
    def test_cross_tenant_assign_role_denied_with_audit(self, ent_env, ent_client):
        a, b = _mk_tenants()
        alice = _insert_user(ent_env, "alice", a)
        bob = _insert_user(ent_env, "bob", b)
        _seed_role(ent_env, bob, [("users", "manage")], role_name="mgr")
        with ent_env._db() as conn:
            conn.execute("INSERT INTO roles (name) VALUES ('ops')")

        r = ent_client.post(
            f"/api/enterprise/users/{alice}/roles",
            json={"name": "ops"},
            headers={"Authorization": f"Bearer {_ent_token(bob, 'bob')}"},
        )
        assert r.status_code == 403
        assert "different tenant" in r.json()["detail"]  # 带真因，禁静默 not_found
        # 行未被写（跨租户零破坏；bob 自己的 seed 角色链接行不算）
        assert _rows(
            ent_env._DB_PATH, "SELECT COUNT(*) FROM user_roles WHERE user_id = ?", (alice,)
        ) == [(0,)]
        # 引擎审计硬对账：resource_id=行归属方 ns（alice 的租户 a），访问者=b 的 ns
        logs = ent_env._iso().get_access_log()
        deny = [e for e in logs if e["resource_id"] == f"ns-{a}:{alice}" and e["allowed"] is False]
        assert deny and "namespace mismatch" in deny[0]["reason"]
        assert any(e["tenant_id"] == f"ns-{b}" for e in deny)
        st = ent_env._iso().stats()
        assert st["total_access_checks"] > 0 and st["blocked_attempts"] > 0

    def test_same_tenant_assign_role_allowed(self, ent_env, ent_client):
        a, _ = _mk_tenants()
        alice = _insert_user(ent_env, "alice", a)
        carol = _insert_user(ent_env, "carol", a)
        _seed_role(ent_env, alice, [("users", "manage")], role_name="mgr")
        with ent_env._db() as conn:
            conn.execute("INSERT INTO roles (name) VALUES ('ops')")
            role_id = conn.execute("SELECT id FROM roles WHERE name='ops'").fetchone()[0]

        r = ent_client.post(
            f"/api/enterprise/users/{carol}/roles",
            json={"name": "ops"},
            headers={"Authorization": f"Bearer {_ent_token(alice, 'alice')}"},
        )
        assert r.status_code == 200
        rows = _rows(ent_env._DB_PATH, "SELECT user_id, role_id FROM user_roles")
        assert (carol, role_id) in rows  # alice 的 seed 链接行并存，按目标行对账
        # 同租户放行也留痕（audit_access=True）
        logs = ent_env._iso().get_access_log()
        assert any(e["resource_id"] == f"ns-{a}:{carol}" and e["allowed"] is True for e in logs)

    def test_bulk_faces_audited(self, ent_env, ent_client):
        a, _ = _mk_tenants()
        alice = _insert_user(ent_env, "alice", a)
        _seed_role(ent_env, alice, [("users", "list"), ("audit", "read")])
        tok = _ent_token(alice, "alice")
        ent_client.get("/api/enterprise/users/list", headers={"Authorization": f"Bearer {tok}"})
        ent_client.get("/api/enterprise/audit", headers={"Authorization": f"Bearer {tok}"})
        logs = ent_env._iso().get_access_log()
        bulk = [e for e in logs if e["resource_id"] == f"ns-{a}:*"]
        assert any(e["resource_type"] == "enterprise_user" for e in bulk)
        assert any(e["resource_type"] == "enterprise_audit" for e in bulk)

    def test_missing_target_404_unchanged(self, ent_env, ent_client):
        a, _ = _mk_tenants()
        alice = _insert_user(ent_env, "alice", a)
        _seed_role(ent_env, alice, [("users", "manage")], role_name="mgr")
        with ent_env._db() as conn:
            conn.execute("INSERT INTO roles (name) VALUES ('ops')")
        r = ent_client.post(
            "/api/enterprise/users/999999/roles",
            json={"name": "ops"},
            headers={"Authorization": f"Bearer {_ent_token(alice, 'alice')}"},
        )
        assert r.status_code == 404  # 缺行契约不变


# ── ⑤ API 契约 ────────────────────────────────────────────────────────


class TestApiContract:
    def test_anonymous_and_bad_token_contract(self, ent_env, ent_client):
        # 匿名（无 Authorization）：HTTPBearer 拒绝（既有契约不变）
        r = ent_client.get("/api/enterprise/users/list")
        assert r.status_code in (401, 403)
        r = ent_client.get(
            "/api/enterprise/users/list", headers={"Authorization": "Bearer not-a-jwt"}
        )
        assert r.status_code == 401

    def test_login_response_shape_unchanged(self, ent_env, ent_client):
        _insert_user(ent_env, "alice", "default")
        with ent_env._db() as conn:
            from src.api.enterprise import _hash_password

            conn.execute(
                "UPDATE users SET password = ? WHERE username = 'alice'",
                (_hash_password("pw123456"),),
            )
        r = ent_client.post(
            "/api/enterprise/auth/login", json={"username": "alice", "password": "pw123456"}
        )
        assert r.status_code == 200
        assert set(r.json().keys()) == {"access_token", "token_type", "user_id", "username"}
