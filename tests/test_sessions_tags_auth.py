"""tags 写端点身份轴收口测试（SESS-② 勘误留档候选「tags 写端点身份轴缺口」）。

修前（HEAD 源码实证）：add_session_tag / remove_session_tag 是 sessions_api
鉴权家族最后两个无鉴权写口（SESS-② 已收 PATCH/rename_session）——未认证调用方
可给任意会话打/删标签。收口=同款 get_current_user 门（鉴权轴；会话行无归属列，
行级过滤不存在=同 delete/rename 语义，边界由契约测试锁死）。

读面（GET /{id}/tags、GET /tags/all）保留既有浏览语义不加鉴权（SESS-② 记档
「读面 list·tags 仍无鉴权（既有浏览语义）」边界锁死）。

本文件 tmp 全隔离（opensoul agent 库/身份 token），绝不写生产库。
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
    """tmp 会话库 + 真 router 真鉴权链（数据面全隔离）。"""
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

    app = FastAPI()
    app.include_router(sessions_api.router, prefix="/api/sessions")
    client = TestClient(app)

    def tag_rows(sid: str = "as-1") -> list[str]:
        with sqlite3.connect(opensoul_db) as c:
            try:
                rows = c.execute(
                    "SELECT tag_name FROM session_tags WHERE session_id = ? ORDER BY tag_name",
                    (sid,),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [r[0] for r in rows]

    return {
        "client": client,
        "tag_rows": tag_rows,
        "sessions_api": sessions_api,
    }


class TestTagsWriteAuthAxis:
    def test_anon_add_rejected_no_write(self, env):
        """匿名 POST tag → 401 且零落库（修前 200 真写=缺口硬反证面）。"""
        resp = env["client"].post("/api/sessions/as-1/tags", json={"tag_name": "hacked"})
        assert resp.status_code == 401
        assert env["tag_rows"]() == []

    def test_bad_token_add_rejected_no_write(self, env):
        """坏 token POST → 401 且零落库。"""
        resp = env["client"].post(
            "/api/sessions/as-1/tags",
            json={"tag_name": "hacked"},
            headers={"Authorization": "Bearer garbage-token"},
        )
        assert resp.status_code == 401
        assert env["tag_rows"]() == []

    def test_anon_remove_rejected_row_survives(self, env):
        """匿名 DELETE tag → 401 且行存活（先带鉴权种一行再匿名删）。"""
        env["client"].post("/api/sessions/as-1/tags", json={"tag_name": "keepme"}, headers=_token())
        assert env["tag_rows"]() == ["keepme"]
        resp = env["client"].delete("/api/sessions/as-1/tags/keepme")
        assert resp.status_code == 401
        assert env["tag_rows"]() == ["keepme"]

    def test_bad_token_remove_rejected_row_survives(self, env):
        """坏 token DELETE → 401 且行存活。"""
        env["client"].post("/api/sessions/as-1/tags", json={"tag_name": "keepme"}, headers=_token())
        resp = env["client"].delete(
            "/api/sessions/as-1/tags/keepme",
            headers={"Authorization": "Bearer garbage-token"},
        )
        assert resp.status_code == 401
        assert env["tag_rows"]() == ["keepme"]

    def test_authed_add_writes_row(self, env):
        """真 token → 真落库（响应+DB 回读双证）。"""
        resp = env["client"].post(
            "/api/sessions/as-1/tags", json={"tag_name": "real"}, headers=_token()
        )
        assert resp.status_code == 200
        assert resp.json() == {"session_id": "as-1", "tag": "real", "status": "added"}
        assert env["tag_rows"]() == ["real"]

    def test_authed_remove_deletes_row(self, env):
        """真 token → 真删行（响应+DB 回读双证）。"""
        env["client"].post("/api/sessions/as-1/tags", json={"tag_name": "gone"}, headers=_token())
        resp = env["client"].delete("/api/sessions/as-1/tags/gone", headers=_token())
        assert resp.status_code == 200
        assert resp.json() == {"session_id": "as-1", "tag": "gone", "status": "removed"}
        assert env["tag_rows"]() == []

    def test_tag_normalization_contract_unchanged(self, env):
        """既有归一契约不变：strip().lower()（带鉴权下仍逐字节同语义）。"""
        resp = env["client"].post(
            "/api/sessions/as-1/tags", json={"tag_name": "  MyTag  "}, headers=_token()
        )
        assert resp.status_code == 200
        assert resp.json()["tag"] == "mytag"
        assert env["tag_rows"]() == ["mytag"]

    def test_empty_tag_400_contract_unchanged(self, env):
        """空/纯空白 tag_name → 400 契约不变（鉴权门先于 handler 校验）。"""
        for payload in ({"tag_name": ""}, {"tag_name": "   "}):
            resp = env["client"].post("/api/sessions/as-1/tags", json=payload, headers=_token())
            assert resp.status_code == 400
            assert resp.json()["detail"] == "Tag name must be 1-50 characters"
        assert env["tag_rows"]() == []

    def test_too_long_tag_400_contract_unchanged(self, env):
        """超长 tag_name（>50）→ 400 契约不变。"""
        resp = env["client"].post(
            "/api/sessions/as-1/tags", json={"tag_name": "x" * 51}, headers=_token()
        )
        assert resp.status_code == 400
        assert env["tag_rows"]() == []

    def test_duplicate_add_idempotent_contract_unchanged(self, env):
        """INSERT OR IGNORE 既有幂等契约不变：重复 add 恒 1 行。"""
        for _ in range(2):
            resp = env["client"].post(
                "/api/sessions/as-1/tags", json={"tag_name": "dup"}, headers=_token()
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "added"
        assert env["tag_rows"]() == ["dup"]

    def test_no_ownership_filter_boundary(self, env):
        """边界锁死（诚实边界）：任意认证用户可给任意会话打标签——会话行无归属列，
        行级过滤不存在=同 delete/rename 语义；若未来引入归属须先改本契约。"""
        resp = env["client"].post(
            "/api/sessions/any-session/tags", json={"tag_name": "by-other-user"}, headers=_token()
        )
        assert resp.status_code == 200
        assert env["tag_rows"]("any-session") == ["by-other-user"]

    @pytest.mark.parametrize("fn_name", ["add_session_tag", "remove_session_tag"])
    def test_static_wiring_dependency(self, env, fn_name):
        """接线静态证：两写端点签名真带 get_current_user 依赖（修前无）。"""
        sig = inspect.signature(env["sessions_api"].__dict__[fn_name])
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

    def test_read_faces_no_auth_unchanged(self, env):
        """不变面：tags 读面保留既有浏览语义（无鉴权 200），本拍只收写口。"""
        env["client"].post(
            "/api/sessions/as-1/tags", json={"tag_name": "visible"}, headers=_token()
        )
        resp = env["client"].get("/api/sessions/as-1/tags")
        assert resp.status_code == 200
        assert [t["name"] for t in resp.json()["tags"]] == ["visible"]
        resp = env["client"].get("/api/sessions/tags/all")
        assert resp.status_code == 200
        assert [(t["name"], t["count"]) for t in resp.json()["tags"]] == [("visible", 1)]

    def test_rename_auth_gate_unchanged(self, env):
        """不变面：SESS-② PATCH 既有鉴权门零回归（匿名 401）。"""
        resp = env["client"].patch("/api/sessions/as-1", json={"title": "x"})
        assert resp.status_code == 401
