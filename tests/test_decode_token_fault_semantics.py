"""DEC-⑭-⑤ decode_token 故障语义收口——故障≠失效凭据（其余消费面）。

修前（控制组实证非推断）：decode_token 企业分支 `except Exception: return None`
把身份库查库故障（DB 瞬断/不可读）吞成 None=故障被当失效凭据——消费面
（src/middleware/auth.get_current_user、src/api/user.get_current_user）回
401 "Invalid or expired token" 谎报凭据无效（客户端误重登、监控把服务故障
记成鉴权失败），与判口家族声明「故障≠失效凭据」（DEC-⑭-②③④）相悖。

收口语义（与 _identity_for_token/bind_identity 家族同判）：
- 凭据无效（签名/过期/畸形、企业身份行缺失/停用、opensoul 侧无对应用户）
  → None（如实「凭据无效」，401 契约字节不变）
- 判定口自身故障（身份库不可读/查询失败）→ TokenLookupFault 原样上抛，
  HTTP 消费面 fail-closed 503 如实（禁 401 谎报），失败原因可见
- 库文件缺失 → None（tenant_binding._lookup_user 同判法，且不空建库文件）

数据面全 tmp（USERS_DB/ENTERPRISE_DB env 单一源），绝不碰生产身份库。
"""

import ast
import inspect
import sqlite3
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jose import JWTError
from jose import jwt as jose_jwt

from src.config import settings
from src.services import auth as auth_mod
from src.services.auth import TokenLookupFault, decode_token

ENT_DDL = "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, is_active INTEGER)"
OS_DDL = "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT)"


def _seed_identity_dbs(tmp_path, monkeypatch):
    """tmp 双身份库（真 schema 真行）：enterprise(1 alice 活跃/2 inactive 停用/3 ghost 活跃
    但 opensoul 侧无对应用户) + opensoul(alice→uuid1)。"""
    ent_db = tmp_path / "enterprise.db"
    os_db = tmp_path / "opensoul.db"
    alice_uid = str(uuid4())
    with sqlite3.connect(ent_db) as conn:
        conn.execute(ENT_DDL)
        conn.execute("INSERT INTO users VALUES (1, 'alice', 1)")
        conn.execute("INSERT INTO users VALUES (2, 'inactive', 0)")
        conn.execute("INSERT INTO users VALUES (3, 'ghost', 1)")
        conn.commit()
    with sqlite3.connect(os_db) as conn:
        conn.execute(OS_DDL)
        conn.execute("INSERT INTO users VALUES (?, 'alice')", (alice_uid,))
        conn.commit()
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    monkeypatch.setenv("USERS_DB", str(os_db))
    return ent_db, os_db, alice_uid


def _ent_token(ent_id, username, secret=None, exp_delta_s=600):
    payload = {
        "sub": str(ent_id),
        "username": username,
        "scope": "enterprise",
        "exp": datetime.now(UTC) + timedelta(seconds=exp_delta_s),
        "iat": datetime.now(UTC),
    }
    return jose_jwt.encode(payload, secret or settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _std_token(user_id=None, secret=None, exp_delta_s=600):
    payload = {
        "sub": str(user_id or uuid4()),
        "role": "user",
        "exp": datetime.now(UTC) + timedelta(seconds=exp_delta_s),
        "iat": datetime.now(UTC),
    }
    return jose_jwt.encode(payload, secret or settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _corrupt(db_path: Path):
    """把身份库写成非 SQLite 内容：查询必抛 sqlite3.DatabaseError（真查库故障）。"""
    db_path.write_bytes(b"this is definitely not a sqlite database")
    assert db_path.exists()


# ── decode_token 本体：故障 vs 失效凭据 ─────────────────────────────────


class TestDecodeTokenFaultSemantics:
    def test_enterprise_lookup_fault_raises_not_none(self, tmp_path, monkeypatch):
        """修前反证：企业分支查库故障被吞成 None（故障被当失效凭据）→ 现=TokenLookupFault。"""
        ent_db, _, _ = _seed_identity_dbs(tmp_path, monkeypatch)
        _corrupt(ent_db)
        with pytest.raises(TokenLookupFault):
            decode_token(_ent_token(1, "alice"))

    def test_users_lookup_fault_raises(self, tmp_path, monkeypatch):
        """opensoul 侧查库故障（第二查库口）同样不吞。"""
        _, os_db, _ = _seed_identity_dbs(tmp_path, monkeypatch)
        _corrupt(os_db)
        with pytest.raises(TokenLookupFault):
            decode_token(_ent_token(1, "alice"))

    def test_enterprise_happy_path_resolves_uuid(self, tmp_path, monkeypatch):
        """契约不变面：企业 token 正常解析回 opensoul UUID。"""
        _, _, alice_uid = _seed_identity_dbs(tmp_path, monkeypatch)
        out = decode_token(_ent_token(1, "alice"))
        assert out is not None
        assert str(out["user_id"]) == alice_uid
        assert set(out.keys()) == {"user_id", "role"}

    def test_missing_row_is_invalid_not_fault(self, tmp_path, monkeypatch):
        _seed_identity_dbs(tmp_path, monkeypatch)
        assert decode_token(_ent_token(999, "alice")) is None

    def test_inactive_row_is_invalid_not_fault(self, tmp_path, monkeypatch):
        _seed_identity_dbs(tmp_path, monkeypatch)
        assert decode_token(_ent_token(2, "inactive")) is None

    def test_opensoul_missing_username_is_invalid(self, tmp_path, monkeypatch):
        """企业侧活跃但 opensoul 侧无对应用户 → 凭据无法解析=无效（非故障）。"""
        _seed_identity_dbs(tmp_path, monkeypatch)
        assert decode_token(_ent_token(3, "ghost")) is None

    def test_missing_db_file_is_invalid_and_not_created(self, tmp_path, monkeypatch):
        """库文件缺失 → None（_lookup_user 同判法）且绝不空建库文件（修前 connect
        缺库即建空库副作用）。"""
        ent_db = tmp_path / "absent-enterprise.db"
        monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
        monkeypatch.setenv("USERS_DB", str(tmp_path / "absent-opensoul.db"))
        assert decode_token(_ent_token(1, "alice")) is None
        assert not ent_db.exists()

    def test_std_token_zero_db_face(self, tmp_path, monkeypatch):
        """标准 UUID token 零查库路径不被身份库故障波及（契约不变）。"""
        ent_db, os_db, _ = _seed_identity_dbs(tmp_path, monkeypatch)
        _corrupt(ent_db)
        _corrupt(os_db)
        out = decode_token(_std_token())
        assert out is not None
        assert out["role"] == "user"

    def test_invalid_credential_contract_unchanged(self, tmp_path, monkeypatch):
        """凭据无效面 401 契约字节不变：坏签名/过期/缺 username/非数字 sub 全 None。"""
        _seed_identity_dbs(tmp_path, monkeypatch)
        assert decode_token(_std_token(secret="wrong-secret")) is None
        assert decode_token(_std_token(exp_delta_s=-600)) is None
        assert decode_token(_ent_token(1, "")) is None  # username 缺失
        assert decode_token(_ent_token("not-a-number", "alice")) is None

    def test_fault_class_distinct_from_credential_errors(self):
        """TokenLookupFault 不被 decode_token 外层 except (JWTError, KeyError,
        ValueError) 吞掉（否则又变回故障→None）。"""
        assert not issubclass(TokenLookupFault, JWTError)
        assert not issubclass(TokenLookupFault, ValueError)
        assert not issubclass(TokenLookupFault, KeyError)

    def test_no_bare_except_swallow_static(self):
        """静态锁死：decode_token/查库口禁 except Exception/bare except 吞故障
        （修前形态禁复活；按 AST 判真，docstring 叙述不计）。"""
        for fn in (auth_mod.decode_token, auth_mod._enterprise_identity_uid):
            tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler):
                    t = node.type
                    swallowed = t is None or (isinstance(t, ast.Name) and t.id == "Exception")
                    assert not swallowed, f"{fn.__name__} 仍吞故障"
        helper_src = inspect.getsource(auth_mod._enterprise_identity_uid)
        assert helper_src.count("TokenLookupFault") >= 2  # 两个查库口各一 raise 点

    def test_lookup_paths_single_source_env(self, tmp_path, monkeypatch):
        """查库路径走 tenant_binding 单一源（enterprise_db_path/users_db_path），
        env 测试隔离生效——修前硬编码 data/enterprise.db 双源漂移。"""
        ent_db, os_db, alice_uid = _seed_identity_dbs(tmp_path, monkeypatch)
        assert auth_mod.enterprise_db_path() == str(ent_db)
        assert auth_mod.users_db_path() == str(os_db)
        out = decode_token(_ent_token(1, "alice"))
        assert out is not None
        assert str(out["user_id"]) == alice_uid


# ── HTTP 消费面：fail-closed 503 如实，禁 401 谎报凭据无效 ──────────────


class _StubPool:
    async def fetchrow(self, *_a, **_kw):
        return {
            "id": "u-1",
            "username": "alice",
            "email": "a@x",
            "role": "user",
            "is_active": True,
        }


@pytest.fixture()
def client(monkeypatch):
    """真 FastAPI 依赖链（Depends(get_current_user) 两消费面各挂一路由）。"""
    import src.api.user as user_api
    import src.middleware.auth as mw_auth

    app = FastAPI()

    @app.get("/mw")
    async def _mw(user=Depends(mw_auth.get_current_user)):
        return {"ok": True, "user": user["username"]}

    @app.get("/userface")
    async def _userface(uid=Depends(user_api.get_current_user)):
        return {"ok": True, "uid": str(uid)}

    monkeypatch.setattr(mw_auth, "db_pool", _StubPool())
    return TestClient(app)


class TestHttpConsumerFaultSemantics:
    def test_middleware_invalid_token_401_contract(self, tmp_path, monkeypatch, client):
        """凭据无效面契约不变：401 "Invalid or expired token"。"""
        _seed_identity_dbs(tmp_path, monkeypatch)
        r = client.get("/mw", headers={"Authorization": "Bearer not-a-jwt"})
        assert r.status_code == 401
        assert r.json()["detail"] == "Invalid or expired token"

    def test_middleware_fault_503_not_401(self, tmp_path, monkeypatch, client):
        """核心断言：身份库故障 → 503（服务故障可见），禁 401 谎报凭据无效。"""
        ent_db, _, _ = _seed_identity_dbs(tmp_path, monkeypatch)
        _corrupt(ent_db)
        r = client.get("/mw", headers={"Authorization": "Bearer " + _ent_token(1, "alice")})
        assert r.status_code == 503
        assert "authentication backend unavailable" in r.json()["detail"]

    def test_middleware_happy_path_unchanged(self, tmp_path, monkeypatch, client):
        """标准 token 正常路径零回归（decode→db_pool 行→用户 dict）。"""
        _seed_identity_dbs(tmp_path, monkeypatch)
        r = client.get("/mw", headers={"Authorization": "Bearer " + _std_token()})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "user": "alice"}

    def test_user_api_invalid_token_401_contract(self, tmp_path, monkeypatch, client):
        _seed_identity_dbs(tmp_path, monkeypatch)
        r = client.get("/userface", headers={"Authorization": "Bearer not-a-jwt"})
        assert r.status_code == 401
        assert r.json()["detail"] == "Invalid or expired token"

    def test_user_api_fault_503_not_401(self, tmp_path, monkeypatch, client):
        """第二消费面（src/api/user.get_current_user，12+ 路由共用）同语义。"""
        _, os_db, _ = _seed_identity_dbs(tmp_path, monkeypatch)
        _corrupt(os_db)
        r = client.get("/userface", headers={"Authorization": "Bearer " + _ent_token(1, "alice")})
        assert r.status_code == 503
        assert "authentication backend unavailable" in r.json()["detail"]

    def test_user_api_happy_path_unchanged(self, tmp_path, monkeypatch, client):
        _seed_identity_dbs(tmp_path, monkeypatch)
        r = client.get("/userface", headers={"Authorization": "Bearer " + _std_token()})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_consumers_wire_token_lookup_fault_static(self):
        """静态接线锁死：两消费面真消费 TokenLookupFault（写了≠接线了）。"""
        for path in ("src/middleware/auth.py", "src/api/user.py"):
            src = Path(path).read_text()
            assert "TokenLookupFault" in src, f"{path} 未接线"
            assert "503" in src
