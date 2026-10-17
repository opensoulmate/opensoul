"""PATCH /api/sessions/{id} 身份轴收口测试（SESS-① 留档候选「PATCH sessions 身份轴缺口」）。

修前（HEAD 源码实证）：rename_session 是 sessions_api 鉴权家族（create/search/
import/get/delete/fork/branch-summaries 均带 get_current_user）唯一无鉴权写口——
未认证调用方可改任意会话标题。收口=同款 get_current_user 门（鉴权轴；会话行无
归属列，行级过滤不存在=同 delete 语义，边界由契约测试锁死）。

本文件 tmp 全隔离（hermes sessions 库/opensoul agent 库/身份 token），绝不写生产库。
"""

import inspect
import sqlite3
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _token(uid: str | None = None) -> dict:
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid or str(uuid.uuid4())), role="user")
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 双会话库 + 真 router 真鉴权链（数据面全隔离）。"""
    hermes_db = tmp_path / "state.db"
    opensoul_db = tmp_path / "opensoul.db"
    for p in (hermes_db, opensoul_db):
        with sqlite3.connect(p) as c:
            c.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT)")
            c.execute("CREATE TABLE agent_sessions (id TEXT PRIMARY KEY, title TEXT)")
            c.commit()

    import src.api.sessions_api as sessions_api

    monkeypatch.setattr(sessions_api, "_DB_PATH", str(hermes_db))
    monkeypatch.setattr(sessions_api, "_OPENSOUL_DB", str(opensoul_db))

    with sqlite3.connect(hermes_db) as c:
        c.execute("INSERT INTO sessions (id, title) VALUES ('hs-1', 'hermes-old')")
        c.commit()
    with sqlite3.connect(opensoul_db) as c:
        c.execute("INSERT INTO agent_sessions (id, title) VALUES ('as-1', 'agent-old')")
        c.execute("INSERT INTO agent_sessions (id, title) VALUES ('hs-1', 'shadow-old')")
        c.commit()

    app = FastAPI()
    app.include_router(sessions_api.router, prefix="/api/sessions")
    client = TestClient(app)

    def hermes_title(sid="hs-1"):
        with sqlite3.connect(hermes_db) as c:
            row = c.execute("SELECT title FROM sessions WHERE id = ?", (sid,)).fetchone()
        return row[0] if row else None

    def agent_title(sid="as-1"):
        with sqlite3.connect(opensoul_db) as c:
            row = c.execute("SELECT title FROM agent_sessions WHERE id = ?", (sid,)).fetchone()
        return row[0] if row else None

    return {
        "client": client,
        "hermes_title": hermes_title,
        "agent_title": agent_title,
        "sessions_api": sessions_api,
    }


class TestPatchAuthAxis:
    def test_anon_patch_rejected_no_write(self, env):
        """匿名 PATCH → 401 且行零写入（修前 200 真改=缺口硬反证面）。"""
        resp = env["client"].patch("/api/sessions/hs-1", json={"title": "hacked"})
        assert resp.status_code == 401
        assert env["hermes_title"]() == "hermes-old"

    def test_bad_token_rejected_no_write(self, env):
        """坏 token → 401 且行零写入。"""
        resp = env["client"].patch(
            "/api/sessions/hs-1",
            json={"title": "hacked"},
            headers={"Authorization": "Bearer garbage-token"},
        )
        assert resp.status_code == 401
        assert env["hermes_title"]() == "hermes-old"

    def test_authed_patch_hermes_path(self, env):
        """真 token → hermes 路径真改（响应+DB 回读双证）。"""
        resp = env["client"].patch(
            "/api/sessions/hs-1", json={"title": "renamed-ok"}, headers=_token()
        )
        assert resp.status_code == 200
        assert resp.json() == {"success": True, "id": "hs-1", "title": "renamed-ok"}
        assert env["hermes_title"]() == "renamed-ok"

    def test_authed_patch_agent_path(self, env):
        """真 token → agent_sessions 路径真改（「works for both」契约）。"""
        resp = env["client"].patch(
            "/api/sessions/as-1", json={"title": "agent-renamed"}, headers=_token()
        )
        assert resp.status_code == 200
        assert env["agent_title"]() == "agent-renamed"

    def test_hermes_path_precedence_unchanged(self, env):
        """两库同 id 时 hermes 优先（既有语义锁死）：只改 hermes 行，影子 agent 行不动。"""
        resp = env["client"].patch(
            "/api/sessions/hs-1", json={"title": "only-hermes"}, headers=_token()
        )
        assert resp.status_code == 200
        assert env["hermes_title"]() == "only-hermes"
        assert env["agent_title"]("hs-1") == "shadow-old"

    def test_missing_session_404_contract(self, env):
        """缺行 404 契约不变（带鉴权）。"""
        resp = env["client"].patch(
            "/api/sessions/no-such-session", json={"title": "x"}, headers=_token()
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Session not found"

    def test_empty_title_400_no_write(self, env):
        """空/纯空白 title → 400 且行零写入（既有契约不变）。"""
        for payload in ({"title": ""}, {"title": "   "}):
            resp = env["client"].patch("/api/sessions/hs-1", json=payload, headers=_token())
            assert resp.status_code == 400
            assert resp.json()["detail"] == "Title is required"
        assert env["hermes_title"]() == "hermes-old"

    def test_missing_title_key_400(self, env):
        """body 无 title 键 → 400（body.get('title','') 语义不变）。"""
        resp = env["client"].patch("/api/sessions/hs-1", json={"other": 1}, headers=_token())
        assert resp.status_code == 400
        assert env["hermes_title"]() == "hermes-old"

    def test_response_shape_keys(self, env):
        """响应形状恰 3 键 {success,id,title} 不变。"""
        resp = env["client"].patch(
            "/api/sessions/hs-1", json={"title": "shape-check"}, headers=_token()
        )
        assert set(resp.json().keys()) == {"success", "id", "title"}

    def test_no_ownership_filter_boundary(self, env):
        """边界锁死（诚实边界）：任意认证用户可改任意会话——会话行无归属列，
        行级过滤不存在=同 delete 语义；若未来引入归属须先改本契约。"""
        resp = env["client"].patch(
            "/api/sessions/as-1", json={"title": "by-other-user"}, headers=_token()
        )
        assert resp.status_code == 200
        assert env["agent_title"]() == "by-other-user"

    def test_static_wiring_dependency(self, env):
        """接线静态证：rename_session 签名真带 get_current_user 依赖（修前无）。"""
        sig = inspect.signature(env["sessions_api"].rename_session)
        dep_params = [
            p
            for p in sig.parameters.values()
            if p.default is not inspect.Parameter.empty
            and getattr(p.default, "dependency", None) is not None
        ]
        assert any(
            getattr(p.default.dependency, "__name__", "") == "get_current_user" for p in dep_params
        ), f"get_current_user dependency missing: {sig}"


class TestInvariantFaces:
    def test_health_no_auth(self, env):
        """不变面：/health 无需鉴权。"""
        resp = env["client"].get("/api/sessions/health")
        assert resp.status_code == 200
        assert resp.json()["component"] == "SessionsAPI"

    def test_delete_auth_gate_unchanged(self, env):
        """不变面：delete 既有鉴权门零回归（匿名 401 且行存活）。"""
        resp = env["client"].delete("/api/sessions/hs-1")
        assert resp.status_code == 401
        assert env["hermes_title"]() == "hermes-old"

    def test_get_auth_gate_unchanged(self, env):
        """不变面：get 既有鉴权门零回归（匿名 401）。"""
        resp = env["client"].get("/api/sessions/hs-1")
        assert resp.status_code == 401
