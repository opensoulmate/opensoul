"""entity CRUD 面 user_id 轴 bind_identity 收口测试（user 从认证派生，禁自报冒写/冒读）。

F3b-⑥ 已裁决语义（user_id 自报轴改认证派生）同族既有面延伸（F3b-④b 先例
「已裁决语义的同族既有面延伸=无需拍板可立即执行件」）：/api/entity 的 5 个
带 user_id Query 端点（create/list/get/update/delete）修前 user_id 完全客户
端自报直接进档案键——认证请求可冒读/冒写/冒删他人实体（F3b-⑥ search 面、
H1-②b-⑦a-⑤ graph 读面、-⑥ graph 写面同型，本件=该族 CRUD 面收口）。

语义与 F3b-⑥ 逐字一致：认证=派生档案键、自报只可确认绑定、矛盾=403、
'default'/空=未声明→取派生、匿名=保留自报（既有本地多档案语义契约零变化）、
坏 token=401 零副作用。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB），绝不写生产库。数据面走
api.entity 模块接缝捕获桩（真 router 真 bind_identity）。
"""

import hashlib
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

USERS_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "password_hash TEXT, is_active INTEGER NOT NULL DEFAULT 1, "
    "tenant_id TEXT NOT NULL DEFAULT 'default')"
)


def _md5_uuid(s: str) -> uuid.UUID:
    h = hashlib.md5(s.encode()).hexdigest()
    return uuid.UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


def _seed_user(db_path: Path, uid: str, username: str, tenant: str = "default") -> str:
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, ?)",
            (uid, username, tenant),
        )
        c.commit()
    return uid


def _token(uid: str) -> dict:
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid), role="user")
    return {"Authorization": f"Bearer {tok}"}


ENT_ID = str(uuid.uuid4())
CREATE_BODY = {"name": "probe", "entity_type": "concept"}
UPDATE_BODY = {"name": "probe2"}


def _entity_payload(uid) -> dict:
    return {
        "name": "probe",
        "entity_type": "concept",
        "description": "",
        "properties": {},
        "id": ENT_ID,
        "user_id": str(uid),
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 身份库 + entity 服务捕获桩（真 router 真 bind_identity，数据面全隔离）。"""
    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    import src.api.entity as entity_api

    calls: dict = {
        "create_entity": [],
        "list_entities": [],
        "get_entity": [],
        "update_entity": [],
        "delete_entity": [],
    }

    async def _create_entity(data, uid):
        calls["create_entity"].append({"uid": uid})
        return _entity_payload(uid)

    async def _list_entities(uid, entity_type=None, offset=0, limit=50):
        calls["list_entities"].append({"uid": uid})
        return []

    async def _get_entity(entity_id, uid):
        calls["get_entity"].append({"entity_id": entity_id, "uid": uid})
        return _entity_payload(uid)

    async def _update_entity(entity_id, data, uid):
        calls["update_entity"].append({"entity_id": entity_id, "uid": uid})
        return _entity_payload(uid)

    async def _delete_entity(entity_id, uid):
        calls["delete_entity"].append({"entity_id": entity_id, "uid": uid})
        return True

    monkeypatch.setattr(entity_api.entity_service, "create_entity", _create_entity)
    monkeypatch.setattr(entity_api.entity_service, "list_entities", _list_entities)
    monkeypatch.setattr(entity_api.entity_service, "get_entity", _get_entity)
    monkeypatch.setattr(entity_api.entity_service, "update_entity", _update_entity)
    monkeypatch.setattr(entity_api.entity_service, "delete_entity", _delete_entity)

    app = FastAPI()
    app.include_router(entity_api.router, prefix="/api/entity")
    client = TestClient(app)

    user_a = _seed_user(users_db, str(uuid.uuid4()), "alice-e")
    user_b = _seed_user(users_db, str(uuid.uuid4()), "bob-e")
    return {
        "client": client,
        "calls": calls,
        "user_a": user_a,
        "user_b": user_b,
    }


def _all_uids(calls: dict):
    """回收全部捕获桩收到的 uid（跨桩对账）。"""
    out = []
    for entries in calls.values():
        for e in entries:
            if "uid" in e:
                out.append(e["uid"])
    return out


class TestEntityIdentityAxis:
    def test_anon_self_report_preserved(self, env):
        """匿名保留自报（既有本地多档案语义，F3b-② 契约零变化）。"""
        resp = env["client"].post("/api/entity/", params={"user_id": "alice"}, json=CREATE_BODY)
        assert resp.status_code == 200
        assert env["calls"]["create_entity"][0]["uid"] == _md5_uuid("alice")

    def test_anon_default_contract_unchanged(self, env):
        """匿名缺省 user_id='default' 档案键与修前逐值一致（create+list 两面）。"""
        r1 = env["client"].post("/api/entity/", json=CREATE_BODY)
        r2 = env["client"].get("/api/entity/")
        assert r1.status_code == r2.status_code == 200
        assert env["calls"]["create_entity"][0]["uid"] == _md5_uuid("default")
        assert env["calls"]["list_entities"][0]["uid"] == _md5_uuid("default")

    def test_authed_undeclared_derives_user(self, env):
        """认证+未声明→档案键=派生（users.id），修前自报 'default' 顶替。"""
        resp = env["client"].post("/api/entity/", json=CREATE_BODY, headers=_token(env["user_a"]))
        assert resp.status_code == 200
        assert env["calls"]["create_entity"][0]["uid"] == uuid.UUID(env["user_a"])

    def test_authed_declare_default_is_undeclared(self, env):
        """'default'/空=未声明→取派生（不误伤不传参调用方）。"""
        resp = env["client"].get(
            "/api/entity/",
            params={"user_id": "default"},
            headers=_token(env["user_a"]),
        )
        assert resp.status_code == 200
        assert env["calls"]["list_entities"][0]["uid"] == uuid.UUID(env["user_a"])

    def test_authed_conflict_403_zero_side_effect(self, env):
        """认证请求自报他人档案=403 冒写/冒读面收口（修前 200 直接读写 victim 实体）。"""
        resp = env["client"].get(
            "/api/entity/",
            params={"user_id": env["user_b"]},
            headers=_token(env["user_a"]),
        )
        assert resp.status_code == 403
        assert "user mismatch" in resp.json()["detail"]
        assert _all_uids(env["calls"]) == []  # 零副作用：服务函数未被调用

    def test_authed_matching_declared_ok(self, env):
        """自报与派生一致=确认绑定，照常查询。"""
        resp = env["client"].get(
            "/api/entity/",
            params={"user_id": env["user_a"]},
            headers=_token(env["user_a"]),
        )
        assert resp.status_code == 200
        assert env["calls"]["list_entities"][0]["uid"] == uuid.UUID(env["user_a"])

    def test_bad_token_401_zero_side_effect(self, env):
        resp = env["client"].get("/api/entity/", headers={"Authorization": "Bearer garbage"})
        assert resp.status_code == 401
        assert _all_uids(env["calls"]) == []


class TestAllEndpointsSameAxis:
    """5 个带 user_id 自报轴端点同轴收口（派生流入+冒读冒写 403 逐点）。"""

    ENDPOINTS = [
        ("create_entity", "POST", "/api/entity/", CREATE_BODY),
        ("list_entities", "GET", "/api/entity/", None),
        ("get_entity", "GET", f"/api/entity/{ENT_ID}", None),
        ("update_entity", "PATCH", f"/api/entity/{ENT_ID}", UPDATE_BODY),
        ("delete_entity", "DELETE", f"/api/entity/{ENT_ID}", None),
    ]

    def _req(self, env, method, path, body, headers=None, params=None):
        kw = {"params": params or {}}
        if body is not None:
            kw["json"] = body
        return env["client"].request(method, path, headers=headers or {}, **kw)

    @pytest.mark.parametrize("seam,method,path,body", ENDPOINTS)
    def test_derived_uid_reaches_seam(self, env, seam, method, path, body):
        resp = self._req(env, method, path, body, headers=_token(env["user_a"]))
        assert resp.status_code == 200
        assert env["calls"][seam], f"seam {seam} 未被调用"
        assert env["calls"][seam][0]["uid"] == uuid.UUID(env["user_a"])

    @pytest.mark.parametrize("seam,method,path,body", ENDPOINTS)
    def test_conflict_403_on_every_endpoint(self, env, seam, method, path, body):
        resp = self._req(
            env,
            method,
            path,
            body,
            headers=_token(env["user_a"]),
            params={"user_id": env["user_b"]},
        )
        assert resp.status_code == 403
        assert "user mismatch" in resp.json()["detail"]
        assert _all_uids(env["calls"]) == []  # 零副作用硬反证（含 delete 禁冒删）


class TestContractUnchanged:
    """既有契约零变化面（身份轴之外的行为锁死）。"""

    def test_get_one_404_contract(self, env, monkeypatch):
        import src.api.entity as entity_api

        async def _none(entity_id, uid):
            return None

        monkeypatch.setattr(entity_api.entity_service, "get_entity", _none)
        resp = env["client"].get(f"/api/entity/{ENT_ID}")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Entity not found"

    def test_update_404_contract(self, env, monkeypatch):
        import src.api.entity as entity_api

        async def _none(entity_id, data, uid):
            return None

        monkeypatch.setattr(entity_api.entity_service, "update_entity", _none)
        resp = env["client"].patch(f"/api/entity/{ENT_ID}", json=UPDATE_BODY)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Entity not found"

    def test_delete_contracts(self, env, monkeypatch):
        """delete 成功契约 {deleted: True} + 缺行 404 契约逐字不变。"""
        resp = env["client"].delete(f"/api/entity/{ENT_ID}")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}

        import src.api.entity as entity_api

        async def _false(entity_id, uid):
            return False

        monkeypatch.setattr(entity_api.entity_service, "delete_entity", _false)
        resp2 = env["client"].delete(f"/api/entity/{ENT_ID}")
        assert resp2.status_code == 404
        assert resp2.json()["detail"] == "Entity not found"

    def test_health_system_view_no_auth(self, env):
        """/health 系统视图不闸（F3b-⑥ 边界③同款）。"""
        resp = env["client"].get("/api/entity/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "component": "EntitySystem"}


class TestStaticWiring:
    def test_wiring_shape(self):
        """静态接线：恰 5 处 bind_identity + 5 个 authorization Header 形参、
        旧自报直通形（_resolve_user_id(user_id)）清零、import 在文件顶部。"""
        from pathlib import Path

        src = Path("src/api/entity.py").read_text(encoding="utf-8")
        assert src.count("bind_identity(authorization") == 5
        assert src.count("authorization: str " + chr(124) + " None = Header(None)") == 5
        assert "uid = _resolve_user_id(user_id)" not in src
        assert src.count("uid = _resolve_user_id(identity.user_id)") == 5
        import_line = "from src.services.tenant_binding import bind_identity"
        assert import_line in src
        assert src.index(import_line) < src.index("@router")
