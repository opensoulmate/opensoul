"""rename_session hermes 路径 DatabaseError 回落韧性测试（SESS-④）。

修前（SESS-①/② 记档+控制组实证）：rename_session 的 hermes 面 SELECT 抛
sqlite3.DatabaseError（~/.hermes/state.db 损坏 not-a-db 即此形态）经 finally
直通 500，agent 回落整条路被堵死——docstring「works for both Hermes and agent
sessions」契约破。收口=hermes 库不可读只跳过 hermes 面继续查 agent 面（与
list_sessions 既有降级语义同款），读失败降级回落+日志带真因；行命中后的写失败
不回落（hermes 行可读=归属 hermes，回落改 agent 影子行会两库分叉）。

本文件 tmp 全隔离（hermes sessions 库/opensoul agent 库/身份 token），绝不写生产库。
"""

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _token() -> dict:
    import uuid

    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(str(uuid.uuid4())), role="user")
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 双会话库 + 真 router 真鉴权链（数据面全隔离）。

    raise_server_exceptions=False：未捕获 sqlite3.DatabaseError 在生产=500 响应，
    测试里同形态取 500 而非把异常抛进断言（控制组旧码同口径可比）。
    """
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
    client = TestClient(app, raise_server_exceptions=False)

    def hermes_title(sid="hs-1"):
        with sqlite3.connect(hermes_db) as c:
            row = c.execute("SELECT title FROM sessions WHERE id = ?", (sid,)).fetchone()
        return row[0] if row else None

    def agent_title(sid="as-1"):
        with sqlite3.connect(opensoul_db) as c:
            row = c.execute("SELECT title FROM agent_sessions WHERE id = ?", (sid,)).fetchone()
        return row[0] if row else None

    def corrupt_hermes():
        """把 hermes 库换成非 SQLite 字节（~/.hermes/state.db not-a-db 同形态）。"""
        hermes_db.write_bytes(b"\x0d\x00\x00\x00\x01\x01\x61\x00" + b"garbage" * 64)

    return {
        "client": client,
        "hermes_title": hermes_title,
        "agent_title": agent_title,
        "corrupt_hermes": corrupt_hermes,
        "sessions_api": sessions_api,
    }


class TestDegradedHermesFallback:
    def test_corrupt_hermes_falls_back_to_agent(self, env):
        """hermes 库损坏 + agent 行命中 → 200 真改 agent 行（修前 500=回落整条路被堵死硬反证）。"""
        env["corrupt_hermes"]()
        resp = env["client"].patch(
            "/api/sessions/as-1", json={"title": "degraded-renamed"}, headers=_token()
        )
        assert resp.status_code == 200
        assert resp.json() == {"success": True, "id": "as-1", "title": "degraded-renamed"}
        assert env["agent_title"]() == "degraded-renamed"

    def test_corrupt_hermes_missing_session_honest_404(self, env, caplog):
        """hermes 库损坏 + agent 未命中 → 404 带真因（不谎报裸 'Session not found'）。"""
        env["corrupt_hermes"]()
        import logging

        with caplog.at_level(logging.WARNING, logger="src.api.sessions_api"):
            resp = env["client"].patch(
                "/api/sessions/no-such", json={"title": "x"}, headers=_token()
            )
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert "unreadable" in detail
        assert detail != "Session not found"

    def test_warning_carries_real_cause_not_silent(self, env, caplog):
        """降级回落可见非静默：warning 带真因（sqlite 错误文本）。"""
        import logging

        env["corrupt_hermes"]()
        with caplog.at_level(logging.WARNING, logger="src.api.sessions_api"):
            env["client"].patch("/api/sessions/as-1", json={"title": "w"}, headers=_token())
        warn = " ".join(r.getMessage() for r in caplog.records if r.levelname == "WARNING")
        assert "falling back to agent sessions" in warn
        assert "not a database" in warn

    def test_corrupt_hermes_shadow_row_degraded_semantics(self, env):
        """降级语义（接线前定形）：hermes 不可读时 hermes 优先无从保证，agent 影子行
        被改——hermes 优先需要可读前提，降级下如实记录该语义（若未来要更强语义须先改
        本契约）。"""
        env["corrupt_hermes"]()
        resp = env["client"].patch(
            "/api/sessions/hs-1", json={"title": "shadow-renamed"}, headers=_token()
        )
        assert resp.status_code == 200
        assert env["agent_title"]("hs-1") == "shadow-renamed"

    def test_connect_failure_falls_back(self, env, monkeypatch):
        """connect 级失败（unable to open）同降级回落：agent 行照常真改。"""

        def boom():
            raise sqlite3.OperationalError("unable to open database file")

        monkeypatch.setattr(env["sessions_api"], "_get_db", boom)
        resp = env["client"].patch(
            "/api/sessions/as-1", json={"title": "conn-fail-renamed"}, headers=_token()
        )
        assert resp.status_code == 200
        assert env["agent_title"]() == "conn-fail-renamed"


class TestWriteFailureNoShadowDivergence:
    def test_write_failure_does_not_fall_through(self, env, monkeypatch):
        """hermes 行命中后的写失败不回落：agent 影子行零改写（宁如实失败不两库分叉）。"""

        class _StubCursor:
            def fetchone(self):
                return ("hs-1",)

        class _StubConn:
            def execute(self, sql, params=None):
                if sql.strip().upper().startswith("SELECT"):
                    return _StubCursor()
                raise sqlite3.DatabaseError("disk I/O error")

            def commit(self):
                pass

            def close(self):
                pass

        monkeypatch.setattr(env["sessions_api"], "_get_db", lambda: _StubConn())
        resp = env["client"].patch(
            "/api/sessions/hs-1", json={"title": "should-not-land"}, headers=_token()
        )
        assert resp.status_code == 500
        assert env["agent_title"]("hs-1") == "shadow-old"


class TestHealthyPathZeroChange:
    def test_hermes_precedence_unchanged(self, env):
        """不变面：hermes 可读时 hermes 优先——只改 hermes 行，agent 影子行不动。"""
        resp = env["client"].patch(
            "/api/sessions/hs-1", json={"title": "only-hermes"}, headers=_token()
        )
        assert resp.status_code == 200
        assert env["hermes_title"]() == "only-hermes"
        assert env["agent_title"]("hs-1") == "shadow-old"

    def test_healthy_miss_404_detail_bytes_unchanged(self, env):
        """不变面：hermes 可读 + 双面未命中 → 404 裸文案逐字节不变。"""
        resp = env["client"].patch("/api/sessions/no-such", json={"title": "x"}, headers=_token())
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Session not found"

    def test_healthy_success_shape_unchanged(self, env):
        """不变面：成功响应恰 3 键 {success,id,title}。"""
        resp = env["client"].patch(
            "/api/sessions/as-1", json={"title": "shape-check"}, headers=_token()
        )
        assert resp.status_code == 200
        assert set(resp.json().keys()) == {"success", "id", "title"}

    def test_auth_gate_unchanged(self, env):
        """不变面：鉴权门零回归（SESS-② 契约）——匿名 401 行零写入。"""
        resp = env["client"].patch("/api/sessions/as-1", json={"title": "hacked"})
        assert resp.status_code == 401
        assert env["agent_title"]() == "agent-old"


class TestStaticWiring:
    def test_degraded_detail_wired_into_404(self, env):
        """静态接线：诚实 detail 真在 404 分支（防只改注释不改面）。"""
        import inspect

        src = inspect.getsource(env["sessions_api"].rename_session)
        assert "hermes_unreadable" in src
        assert "hermes session store unreadable" in src
        assert src.count("falling back to agent sessions") == 2
