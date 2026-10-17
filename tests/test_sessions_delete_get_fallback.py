"""delete/get/messages hermes 路径 DatabaseError 回落韧性测试（SESS-④ 同族收口）。

修前（SESS-①/④ 记档+控制组实证）：get_session / delete_session /
get_session_messages 的 hermes 面 SELECT 抛 sqlite3.DatabaseError（~/.hermes/
state.db 损坏 not-a-db 即此形态）经 finally 直通 500，agent 回落整条路被堵死
——docstring「works for both Hermes and agent sessions」契约破（SESS-④ 只收了
rename_session 单口，本拍收同族三口）。收口=hermes 库不可读只跳过 hermes 面
继续查 agent 面（与 rename_session/list_sessions 既有降级语义同款，只认
sqlite3.DatabaseError 家族不宽泛吞）；delete 行命中后写失败不回落（防两库
分叉）；缺行 404/error 诚实真因（健康面裸文案字节不变）；messages 降级下
响应带加性键 hermes_unreadable=True（不谎报"真无消息"）。

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

    with sqlite3.connect(hermes_db) as c:
        c.execute(
            """CREATE TABLE sessions (
                id TEXT PRIMARY KEY, title TEXT, started_at REAL, last_activity_at REAL,
                source TEXT, message_count INTEGER, input_tokens INTEGER,
                output_tokens INTEGER, estimated_cost_usd REAL, archived INTEGER DEFAULT 0
            )"""
        )
        c.execute(
            """CREATE TABLE messages (
                id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
                tool_calls TEXT, tool_name TEXT, timestamp REAL,
                active INTEGER DEFAULT 1, compacted INTEGER DEFAULT 0
            )"""
        )
        c.execute("CREATE TABLE session_lineage (session_id TEXT)")
        c.execute(
            "INSERT INTO sessions (id, title, started_at, last_activity_at, source,"
            " message_count) VALUES ('hs-1', 'hermes-old', 1.0, 2.0, 'cli', 1)"
        )
        c.execute(
            "INSERT INTO messages (id, session_id, role, content, timestamp)"
            " VALUES (1, 'hs-1', 'user', 'hermes-msg', 1.0)"
        )
        c.commit()

    with sqlite3.connect(opensoul_db) as c:
        c.execute(
            """CREATE TABLE agent_sessions (
                id TEXT PRIMARY KEY, agent_id TEXT, title TEXT, created_at REAL,
                last_activity_at REAL, message_count INTEGER, archived INTEGER DEFAULT 0
            )"""
        )
        c.execute(
            """CREATE TABLE agent_messages (
                id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp REAL
            )"""
        )
        c.execute(
            "INSERT INTO agent_sessions (id, agent_id, title, created_at,"
            " last_activity_at, message_count) VALUES ('as-1', 'soulmate', 'agent-old', 1.0, 2.0, 1)"
        )
        # hermes hs-1 的 agent 影子行（delete「行命中不回落」语义观测用）
        c.execute(
            "INSERT INTO agent_sessions (id, agent_id, title, created_at,"
            " last_activity_at, message_count) VALUES ('hs-1', 'soulmate', 'shadow-old', 1.0, 2.0, 0)"
        )
        c.execute(
            "INSERT INTO agent_messages (id, session_id, role, content, timestamp)"
            " VALUES (1, 'as-1', 'user', 'agent-msg', 1.0)"
        )
        c.commit()

    import src.api.sessions_api as sessions_api

    monkeypatch.setattr(sessions_api, "_DB_PATH", str(hermes_db))
    monkeypatch.setattr(sessions_api, "_OPENSOUL_DB", str(opensoul_db))

    app = FastAPI()
    app.include_router(sessions_api.router, prefix="/api/sessions")
    client = TestClient(app, raise_server_exceptions=False)

    def hermes_row_count(table, sid):
        with sqlite3.connect(hermes_db) as c:
            try:
                return c.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE session_id = ?", (sid,)
                ).fetchone()[0]
            except sqlite3.Error:
                return c.execute(f"SELECT COUNT(*) FROM {table} WHERE id = ?", (sid,)).fetchone()[0]

    def agent_title(sid):
        with sqlite3.connect(opensoul_db) as c:
            row = c.execute("SELECT title FROM agent_sessions WHERE id = ?", (sid,)).fetchone()
        return row[0] if row else None

    def agent_row_count(table, sid):
        with sqlite3.connect(opensoul_db) as c:
            return c.execute(
                f"SELECT COUNT(*) FROM {table} WHERE session_id = ?", (sid,)
            ).fetchone()[0]

    def corrupt_hermes():
        """把 hermes 库换成非 SQLite 字节（~/.hermes/state.db not-a-db 同形态）。"""
        hermes_db.write_bytes(b"\x0d\x00\x00\x00\x01\x01\x61\x00" + b"garbage" * 64)

    return {
        "client": client,
        "hermes_row_count": hermes_row_count,
        "agent_title": agent_title,
        "agent_row_count": agent_row_count,
        "corrupt_hermes": corrupt_hermes,
        "sessions_api": sessions_api,
    }


def _warn_text(caplog):
    return " ".join(r.getMessage() for r in caplog.records if r.levelname == "WARNING")


class TestGetSessionDegradedFallback:
    def test_corrupt_hermes_get_falls_back_to_agent(self, env):
        """hermes 库损坏 + agent 行命中 → 200 agent 会话（修前 500=回落整条路被堵死硬反证）。"""
        env["corrupt_hermes"]()
        resp = env["client"].get("/api/sessions/as-1", headers=_token())
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "as-1"
        assert data["title"] == "agent-old"
        assert data["source"] == "soulmate"

    def test_corrupt_hermes_get_missing_honest_404(self, env):
        """hermes 库损坏 + agent 未命中 → 404 带真因（不谎报裸 'Session not found'）。"""
        env["corrupt_hermes"]()
        resp = env["client"].get("/api/sessions/no-such", headers=_token())
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert "unreadable" in detail
        assert detail != "Session not found"

    def test_get_warning_carries_real_cause_not_silent(self, env, caplog):
        """降级回落可见非静默：warning 带真因（sqlite 错误文本）。"""
        import logging

        env["corrupt_hermes"]()
        with caplog.at_level(logging.WARNING, logger="src.api.sessions_api"):
            env["client"].get("/api/sessions/as-1", headers=_token())
        warn = _warn_text(caplog)
        assert "get_session:" in warn
        assert "falling back to agent sessions" in warn
        assert "not a database" in warn

    def test_healthy_hermes_get_unchanged(self, env):
        """健康不变面：hermes 行命中回 hermes 形状，缺行裸 404 文案字节不变。"""
        resp = env["client"].get("/api/sessions/hs-1", headers=_token())
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "hermes-old"
        assert data["source"] == "cli"

        miss = env["client"].get("/api/sessions/no-such", headers=_token())
        assert miss.status_code == 404
        assert miss.json()["detail"] == "Session not found"

    def test_healthy_agent_fallback_unchanged(self, env):
        """健康不变面：hermes 无此行照旧回落 agent 面（既有双面契约零变化）。"""
        resp = env["client"].get("/api/sessions/as-1", headers=_token())
        assert resp.status_code == 200
        assert resp.json()["title"] == "agent-old"


class TestDeleteSessionDegradedFallback:
    def test_corrupt_hermes_delete_falls_back_to_agent(self, env):
        """hermes 库损坏 + agent 行命中 → 真删 agent 行（修前 500=回落整条路被堵死硬反证）。"""
        env["corrupt_hermes"]()
        resp = env["client"].delete("/api/sessions/as-1", headers=_token())
        assert resp.status_code == 200
        assert resp.json() == {"success": True, "deleted_session": "as-1"}
        assert env["agent_title"]("as-1") is None
        assert env["agent_row_count"]("agent_messages", "as-1") == 0

    def test_corrupt_hermes_delete_missing_honest_error(self, env):
        """hermes 库损坏 + agent 未命中 → success False 带真因（不谎报裸 'Session not found'）。"""
        env["corrupt_hermes"]()
        resp = env["client"].delete("/api/sessions/no-such", headers=_token())
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "unreadable" in body["error"]
        assert body["error"] != "Session not found"

    def test_delete_warning_carries_real_cause_not_silent(self, env, caplog):
        """降级回落可见非静默：warning 带真因。"""
        import logging

        env["corrupt_hermes"]()
        with caplog.at_level(logging.WARNING, logger="src.api.sessions_api"):
            env["client"].delete("/api/sessions/as-1", headers=_token())
        warn = _warn_text(caplog)
        assert "delete_session:" in warn
        assert "falling back to agent sessions" in warn
        assert "not a database" in warn

    def test_healthy_hermes_delete_row_hit_no_fallback_shadow_survives(self, env):
        """健康不变面+行命中不回落：删 hermes 行，agent 影子行一字不动（防两库分叉）。"""
        resp = env["client"].delete("/api/sessions/hs-1", headers=_token())
        assert resp.status_code == 200
        assert resp.json() == {"success": True, "deleted_session": "hs-1"}
        assert env["hermes_row_count"]("sessions", "hs-1") == 0
        assert env["hermes_row_count"]("messages", "hs-1") == 0
        assert env["agent_title"]("hs-1") == "shadow-old"

    def test_healthy_delete_missing_bare_error_unchanged(self, env):
        """健康不变面：缺行裸 error 文案字节不变。"""
        resp = env["client"].delete("/api/sessions/no-such", headers=_token())
        assert resp.status_code == 200
        assert resp.json() == {"success": False, "error": "Session not found"}


class TestMessagesDegradedFallback:
    def test_corrupt_hermes_messages_falls_back_to_agent(self, env):
        """hermes 库损坏 + agent 有消息 → agent 消息回读 + 加性键 hermes_unreadable。"""
        env["corrupt_hermes"]()
        resp = env["client"].get("/api/sessions/as-1/messages", headers=_token())
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages"][0]["content"] == "agent-msg"
        assert data["messages"][0]["source"] == "agent-db"
        assert data["hermes_unreadable"] is True

    def test_corrupt_hermes_messages_empty_honest_marker(self, env):
        """hermes 库损坏 + agent 无消息 → 空列表 + hermes_unreadable（禁谎报"真无消息"）。"""
        env["corrupt_hermes"]()
        resp = env["client"].get("/api/sessions/no-such/messages", headers=_token())
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages"] == []
        assert data["total"] == 0
        assert data["hermes_unreadable"] is True

    def test_messages_warning_carries_real_cause_not_silent(self, env, caplog):
        """降级回落可见非静默：warning 带真因。"""
        import logging

        env["corrupt_hermes"]()
        with caplog.at_level(logging.WARNING, logger="src.api.sessions_api"):
            env["client"].get("/api/sessions/as-1/messages", headers=_token())
        warn = _warn_text(caplog)
        assert "get_session_messages:" in warn
        assert "falling back to agent sessions" in warn
        assert "not a database" in warn

    def test_healthy_hermes_messages_contract_bytes_unchanged(self, env):
        """健康不变面：hermes 消息契约恰 3 键（messages/total/branch_summaries），
        hermes_unreadable 键不出场（健康面字节不变）。"""
        resp = env["client"].get("/api/sessions/hs-1/messages", headers=_token())
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"messages", "total", "branch_summaries"}
        assert data["messages"][0]["content"] == "hermes-msg"
        assert data["messages"][0]["source"] == "hermes-db"

    def test_healthy_agent_fallback_no_marker(self, env):
        """健康不变面：hermes 无此会话消息照旧回落 agent 面，零标记键。"""
        resp = env["client"].get("/api/sessions/as-1/messages", headers=_token())
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"messages", "total", "branch_summaries"}
        assert data["messages"][0]["content"] == "agent-msg"
