"""search/unified 端点身份轴收口测试（user/tenant 从认证派生，禁自报冒读）。

修前（HEAD 源码实证）：GET/POST /api/search/ 的 user_id 完全客户端自报直接进
档案键（认证请求可冒读他人档案）、/api/search/unified 知识子搜索硬编码
'default' 档案键、课程子搜索走 CourseEngine.list_courses() 系统视图=跨租户
课程泄露（F3b-③c-2②b 留档「search.py 跨模块系统视图（身份轴另拍）」）。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB/LEARN_DB），绝不写生产库。
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


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 身份库 + 搜索函数捕获桩（真 router 真 bind_identity，数据面全隔离）。"""
    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    import src.api.search as search_api

    calls: list = []
    results = [{"id": "k1", "title": "t", "content": "c", "source": "fulltext", "score": 1.0}]

    async def _capture(query, user_id, limit=10):
        calls.append({"query": query, "uid": user_id, "limit": limit})
        return list(results)

    monkeypatch.setattr(search_api, "hybrid_search", _capture)
    monkeypatch.setattr(search_api, "semantic_search", _capture)
    monkeypatch.setattr(search_api, "fulltext_search", _capture)

    app = FastAPI()
    app.include_router(search_api.router, prefix="/api/search")
    client = TestClient(app)

    user_a = _seed_user(users_db, str(uuid.uuid4()), "alice-a")
    user_b = _seed_user(users_db, str(uuid.uuid4()), "bob-b")
    return {
        "client": client,
        "calls": calls,
        "user_a": user_a,
        "user_b": user_b,
        "users_db": users_db,
        "search_api": search_api,
    }


class TestSearchIdentityAxis:
    def test_anon_self_report_preserved(self, env):
        """匿名保留自报（既有本地多档案语义，F3b-② 契约零变化）。"""
        resp = env["client"].get("/api/search/", params={"q": "x", "user_id": "alice"})
        assert resp.status_code == 200
        assert env["calls"][0]["uid"] == _md5_uuid("alice")

    def test_anon_default_contract_unchanged(self, env):
        """匿名缺省 user_id='default' 的搜索键与修前逐值一致（GET+POST）。"""
        r1 = env["client"].get("/api/search/", params={"q": "x"})
        r2 = env["client"].post("/api/search/", json={"query": "x"})
        assert r1.status_code == r2.status_code == 200
        assert env["calls"][0]["uid"] == _md5_uuid("default")
        assert env["calls"][1]["uid"] == _md5_uuid("default")

    def test_authed_undeclared_derives_user(self, env):
        """认证+未声明→搜索键=派生档案键（users.id），修前自报 'default' 顶替。"""
        resp = env["client"].get("/api/search/", params={"q": "x"}, headers=_token(env["user_a"]))
        assert resp.status_code == 200
        assert env["calls"][0]["uid"] == uuid.UUID(env["user_a"])

    def test_authed_declare_default_is_undeclared(self, env):
        """ "default"/空=未声明→取派生（不误伤不传参调用方）。"""
        resp = env["client"].get(
            "/api/search/",
            params={"q": "x", "user_id": "default"},
            headers=_token(env["user_a"]),
        )
        assert resp.status_code == 200
        assert env["calls"][0]["uid"] == uuid.UUID(env["user_a"])

    def test_authed_conflict_403_zero_side_effect(self, env):
        """认证请求自报他人档案=403 冒读面收口（修前 200 直接搜 victim 档案）。"""
        resp = env["client"].get(
            "/api/search/",
            params={"q": "x", "user_id": env["user_b"]},
            headers=_token(env["user_a"]),
        )
        assert resp.status_code == 403
        assert "user mismatch" in resp.json()["detail"]
        assert env["calls"] == []  # 零副作用：搜索函数未被调用

    def test_authed_matching_declared_ok(self, env):
        """自报与派生一致=确认绑定，照常搜索。"""
        resp = env["client"].get(
            "/api/search/",
            params={"q": "x", "user_id": env["user_a"]},
            headers=_token(env["user_a"]),
        )
        assert resp.status_code == 200
        assert env["calls"][0]["uid"] == uuid.UUID(env["user_a"])

    def test_bad_token_401(self, env):
        resp = env["client"].get(
            "/api/search/", params={"q": "x"}, headers={"Authorization": "Bearer garbage"}
        )
        assert resp.status_code == 401
        assert env["calls"] == []

    def test_post_same_axis(self, env):
        """POST 面同轴：匿名自报照旧、认证冒读 403。"""
        r1 = env["client"].post("/api/search/", json={"query": "x"}, params={"user_id": "alice"})
        assert r1.status_code == 200
        assert env["calls"][0]["uid"] == _md5_uuid("alice")
        r2 = env["client"].post(
            "/api/search/",
            json={"query": "x"},
            params={"user_id": env["user_b"]},
            headers=_token(env["user_a"]),
        )
        assert r2.status_code == 403
        assert len(env["calls"]) == 1


class TestUnifiedIdentityAxis:
    def test_shape_unchanged_and_knowledge_uses_derived(self, env):
        """unified 知识子搜索用派生档案键（修前硬编码 'default'）+响应形状逐键不变。"""
        resp = env["client"].get(
            "/api/search/unified",
            params={"q": "x", "sources": "knowledge"},
            headers=_token(env["user_a"]),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"query", "total", "by_source", "sources_searched"}
        assert env["calls"][0]["uid"] == uuid.UUID(env["user_a"])
        assert data["by_source"]["knowledge"][0]["source"] == "knowledge"

    def test_unified_anon_default_contract_unchanged(self, env):
        """匿名缺省与修前硬编码 _resolve_user_id('default') 逐值一致。"""
        resp = env["client"].get("/api/search/unified", params={"q": "x", "sources": "knowledge"})
        assert resp.status_code == 200
        assert env["calls"][0]["uid"] == _md5_uuid("default")

    def test_unified_conflict_403(self, env):
        resp = env["client"].get(
            "/api/search/unified",
            params={"q": "x", "user_id": env["user_b"]},
            headers=_token(env["user_a"]),
        )
        assert resp.status_code == 403
        assert env["calls"] == []

    def test_unified_courses_tenant_from_identity(self, env, monkeypatch):
        """课程子搜索按身份派生租户过滤（修前 list_courses() 系统视图=跨租户泄露）。"""
        from src.learn import course_engine as ce

        seen: list = []
        real = ce.CourseEngine.list_courses

        def _spy(self, status=None, tenant_id=None):
            seen.append(tenant_id)
            return real(self, status=status, tenant_id=tenant_id)

        monkeypatch.setattr(ce.CourseEngine, "list_courses", _spy)

        # 匿名→'default'；认证→users.tenant_id 派生租户
        with sqlite3.connect(env["users_db"]) as c:
            c.execute("UPDATE users SET tenant_id='tenant-a' WHERE id=?", (env["user_a"],))
            c.commit()
        r1 = env["client"].get("/api/search/unified", params={"q": "marker", "sources": "courses"})
        r2 = env["client"].get(
            "/api/search/unified",
            params={"q": "marker", "sources": "courses"},
            headers=_token(env["user_a"]),
        )
        assert r1.status_code == r2.status_code == 200
        assert seen == ["default", "tenant-a"]

    def test_unified_courses_cross_tenant_closed(self, env):
        """跨租户课程泄露收口：真 CourseEngine 面，各租户只见自己的课程行。"""
        from src.learn.course_engine import CourseEngine

        engine = CourseEngine()
        engine.create_course("alpha course marker", tenant_id="tenant-a")
        engine.create_course("beta course marker", tenant_id="tenant-b")
        engine.create_course("default course marker", tenant_id="default")

        with sqlite3.connect(env["users_db"]) as c:
            c.execute("UPDATE users SET tenant_id='tenant-a' WHERE id=?", (env["user_a"],))
            c.execute("UPDATE users SET tenant_id='tenant-b' WHERE id=?", (env["user_b"],))
            c.commit()

        def titles(resp):
            assert resp.status_code == 200
            return sorted(m["title"] for m in resp.json()["by_source"]["courses"])

        r_a = env["client"].get(
            "/api/search/unified",
            params={"q": "marker", "sources": "courses"},
            headers=_token(env["user_a"]),
        )
        r_b = env["client"].get(
            "/api/search/unified",
            params={"q": "marker", "sources": "courses"},
            headers=_token(env["user_b"]),
        )
        r_anon = env["client"].get(
            "/api/search/unified", params={"q": "marker", "sources": "courses"}
        )
        assert titles(r_a) == ["alpha course marker"]  # 修前系统视图=三条全见
        assert titles(r_b) == ["beta course marker"]
        assert titles(r_anon) == ["default course marker"]
