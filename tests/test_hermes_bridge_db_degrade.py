"""hermes 读面 DatabaseError 同族收口测试（SESS-⑥）。

修前（--pre live 真损坏库实证 + 控制组）：hermes_bridge 三读面 + sessions_api
search 第 4 面对 ~/.hermes/state.db not-a-db 形态的 DatabaseError 各给一种
error 形态（messages 500 裸 str(e)、delete 200 success:False 裸 str(e)、
两个 search 200 裸 str(e)）——「3 处不同形」+同病第 4 面，裸 sqlite 内部文案
直出、无降级标记、客户端无从分辨"真无消息/无结果"与"store 不可读"。

收口=SESS-④/⑤ 家族语义搬进纯 hermes 读面（无 agent 回落面）：
- 只认 sqlite3.DatabaseError 家族不宽泛吞（自身 bug 不静默，测试锁死）
- 统一降级形态：error「hermes session store unreadable」+ 加性键
  hermes_unreadable=True（只降级出场）+ 消息/搜索空载荷
- 日志带真因（warning 带 sqlite 错误文本）
- 健康面形状/文案字节不变（含 delete 幂等删除、空 q、缺库文案）

本文件 tmp 全隔离（hermes 库换 tmp 路径），绝不写生产库。
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
    """tmp hermes 库 + 真 router 真鉴权链（数据面全隔离）。

    raise_server_exceptions=False：未捕获异常在生产=500 响应，测试同形态取 500
    而非把异常抛进断言（控制组旧码同口径可比）。
    """
    hermes_dir = tmp_path / ".hermes"
    hermes_dir.mkdir()
    hermes_db = hermes_dir / "state.db"

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
            "INSERT INTO sessions (id, title, started_at, last_activity_at, source,"
            " message_count) VALUES ('hs-2', 'alpha-needle-title', 5.0, 6.0, 'cli', 1)"
        )
        c.execute(
            "INSERT INTO sessions (id, title, started_at, last_activity_at, source,"
            " message_count) VALUES ('hs-3', 'plain-title', 7.0, 8.0, 'wx', 1)"
        )
        c.execute(
            "INSERT INTO messages (id, session_id, role, content, timestamp)"
            " VALUES (1, 'hs-1', 'user', 'hermes-msg', 1.0)"
        )
        c.execute(
            "INSERT INTO messages (id, session_id, role, content, timestamp)"
            " VALUES (2, 'hs-2', 'user', 'hello world', 5.0)"
        )
        c.execute(
            "INSERT INTO messages (id, session_id, role, content, timestamp)"
            " VALUES (3, 'hs-3', 'user', 'find the needle here', 7.0)"
        )
        c.commit()

    import src.api.hermes_bridge as hermes_bridge
    import src.api.sessions_api as sessions_api

    # HOME 重定向：旧码内联 expanduser("~/.hermes/state.db") 也解析进 tmp（控制组
    # 绝不触生产库）；raising=False 容缺（旧码无 _DB_PATH 常量），失败落行为断言。
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(hermes_bridge, "_DB_PATH", str(hermes_db), raising=False)
    monkeypatch.setattr(sessions_api, "_DB_PATH", str(hermes_db), raising=False)

    app = FastAPI()
    app.include_router(hermes_bridge.router, prefix="/api/hermes")
    app.include_router(sessions_api.router, prefix="/api/sessions")
    client = TestClient(app, raise_server_exceptions=False)

    def corrupt_hermes():
        """把 hermes 库换成非 SQLite 字节（~/.hermes/state.db not-a-db 同形态）。"""
        hermes_db.write_bytes(b"\x0d\x00\x00\x00\x01\x01\x61\x00" + b"garbage" * 64)

    def row_count(table, sid):
        with sqlite3.connect(hermes_db) as c:
            try:
                return c.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE session_id = ?", (sid,)
                ).fetchone()[0]
            except sqlite3.Error:
                return c.execute(f"SELECT COUNT(*) FROM {table} WHERE id = ?", (sid,)).fetchone()[0]

    return {
        "client": client,
        "corrupt_hermes": corrupt_hermes,
        "row_count": row_count,
        "hermes_bridge": hermes_bridge,
        "sessions_api": sessions_api,
        "tmp_path": tmp_path,
    }


def _warn_text(caplog):
    return " ".join(r.getMessage() for r in caplog.records if r.levelname == "WARNING")


class TestBridgeMessagesDegraded:
    def test_corrupt_store_unified_degraded_shape(self, env):
        """hermes 库损坏 -> 200 空载荷+真因+加性键（修前 500 裸 str(e) 不同形硬反证）。"""
        env["corrupt_hermes"]()
        resp = env["client"].get("/api/hermes/sessions/no-such/messages", headers=_token())
        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {"messages", "total", "error", "hermes_unreadable"}
        assert data["messages"] == []
        assert data["total"] == 0
        assert data["error"] == "hermes session store unreadable"
        assert data["hermes_unreadable"] is True

    def test_non_db_error_not_swallowed(self, env, monkeypatch):
        """只认 DatabaseError 家族：非 DB 故障保持修前形态（500 裸真因），禁宽泛吞。"""

        def boom(*a, **kw):
            raise ValueError("bridge-bug")

        monkeypatch.setattr(sqlite3, "connect", boom)
        resp = env["client"].get("/api/hermes/sessions/hs-1/messages", headers=_token())
        assert resp.status_code == 500
        assert "hermes_unreadable" not in resp.text

    def test_healthy_shape_byte_stable_no_marker(self, env):
        """健康不变面：真消息回读，形状恰 {messages,total}，加性键只降级出场。"""
        resp = env["client"].get("/api/hermes/sessions/hs-1/messages", headers=_token())
        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {"messages", "total"}
        assert data["total"] == 1
        assert data["messages"][0]["content"] == "hermes-msg"
        assert data["messages"][0]["source"] == "hermes-db"

    def test_healthy_missing_session_empty_no_marker(self, env):
        """健康不变面：缺会话照旧 200 空载荷（既有契约），无降级标记。"""
        resp = env["client"].get("/api/hermes/sessions/no-such/messages", headers=_token())
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"messages": [], "total": 0}


class TestBridgeDeleteDegraded:
    def test_corrupt_store_unified_degraded_shape(self, env):
        """hermes 库损坏 -> 200 success:False 契约不变+真因+加性键（修前 error 裸 str(e)）。"""
        env["corrupt_hermes"]()
        resp = env["client"].delete("/api/hermes/sessions/no-such", headers=_token())
        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {"success", "error", "hermes_unreadable"}
        assert data["success"] is False
        assert data["error"] == "hermes session store unreadable"
        assert data["hermes_unreadable"] is True

    def test_healthy_delete_shape_byte_stable(self, env):
        """健康不变面：真删，响应恰 {success,deleted_session} 逐字节不变。"""
        resp = env["client"].delete("/api/hermes/sessions/hs-1", headers=_token())
        assert resp.status_code == 200
        assert resp.json() == {"success": True, "deleted_session": "hs-1"}
        assert env["row_count"]("messages", "hs-1") == 0
        assert env["row_count"]("sessions", "hs-1") == 0

    def test_healthy_delete_missing_idempotent_contract(self, env):
        """健康不变面：缺行幂等删除照旧 success True（既有契约锁死，本拍不改语义）。"""
        resp = env["client"].delete("/api/hermes/sessions/no-such", headers=_token())
        assert resp.status_code == 200
        assert resp.json() == {"success": True, "deleted_session": "no-such"}


class TestBridgeSearchDegraded:
    def test_corrupt_store_unified_degraded_shape(self, env):
        """hermes 库损坏 -> error 键集修前形状+真因+加性键（禁空列表冒充"无结果"）。"""
        env["corrupt_hermes"]()
        resp = env["client"].get("/api/hermes/sessions/search?q=needle", headers=_token())
        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {"sessions", "error", "hermes_unreadable"}
        assert data["sessions"] == []
        assert data["error"] == "hermes session store unreadable"
        assert data["hermes_unreadable"] is True

    def test_healthy_search_shape_byte_stable(self, env):
        """健康不变面：标题+内容双面命中，形状恰 {sessions,query,total} 无标记。"""
        resp = env["client"].get("/api/hermes/sessions/search?q=needle", headers=_token())
        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {"sessions", "query", "total"}
        assert data["query"] == "needle"
        assert {r["match_type"] for r in data["sessions"]} == {"title", "content"}
        ids = [r["id"] for r in data["sessions"]]
        assert "hs-2" in ids and "hs-3" in ids

    def test_empty_q_contract_unchanged(self, env):
        """健康不变面：空 q 早退契约字节不变。"""
        resp = env["client"].get("/api/hermes/sessions/search?q=", headers=_token())
        assert resp.status_code == 200
        assert resp.json() == {"sessions": [], "query": ""}


class TestSessionsSearchDegraded:
    def test_corrupt_store_unified_degraded_shape(self, env):
        """同病第 4 面：sessions search 损坏 -> 同款统一降级形态（修前 error 裸 str(e)）。"""
        env["corrupt_hermes"]()
        resp = env["client"].get("/api/sessions/search?q=needle", headers=_token())
        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {"sessions", "error", "hermes_unreadable"}
        assert data["sessions"] == []
        assert data["error"] == "hermes session store unreadable"
        assert data["hermes_unreadable"] is True

    def test_connect_seam_degraded(self, env, monkeypatch):
        """connect 缝（修前 _get_db() 在 try 外直通）：打不开也走同款降级。"""
        monkeypatch.setattr(env["sessions_api"], "_DB_PATH", str(env["tmp_path"] / "a-directory-x"))
        (env["tmp_path"] / "a-directory-x").mkdir()
        resp = env["client"].get("/api/sessions/search?q=needle", headers=_token())
        assert resp.status_code == 200
        data = resp.json()
        assert data["hermes_unreadable"] is True
        assert data["error"] == "hermes session store unreadable"

    def test_non_db_error_not_swallowed(self, env, monkeypatch):
        """只认 DatabaseError 家族：非 DB 故障保持修前形态（500），禁宽泛吞。"""

        def boom():
            raise ValueError("search-bug")

        monkeypatch.setattr(env["sessions_api"], "_get_db", boom)
        resp = env["client"].get("/api/sessions/search?q=needle", headers=_token())
        assert resp.status_code == 500
        assert "hermes_unreadable" not in resp.text

    def test_healthy_search_shape_byte_stable(self, env):
        """健康不变面：标题+内容双面命中，形状恰 {sessions,query,total} 无标记。"""
        resp = env["client"].get("/api/sessions/search?q=needle", headers=_token())
        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {"sessions", "query", "total"}
        assert {r["match_type"] for r in data["sessions"]} == {"title", "content"}
        for r in data["sessions"]:
            assert set(r) == {
                "id",
                "title",
                "created_at",
                "updated_at",
                "source",
                "match_type",
                "snippet",
            }

    def test_missing_db_shape_unchanged(self, env, monkeypatch):
        """健康不变面：库文件缺失照旧「No session database found」文案字节不变。"""
        monkeypatch.setattr(env["sessions_api"], "_DB_PATH", str(env["tmp_path"] / "nope.db"))
        resp = env["client"].get("/api/sessions/search?q=needle", headers=_token())
        assert resp.status_code == 200
        assert resp.json() == {
            "sessions": [],
            "query": "needle",
            "error": "No session database found",
        }

    def test_empty_q_contract_unchanged(self, env):
        """健康不变面：空 q 早退契约字节不变。"""
        resp = env["client"].get("/api/sessions/search?q=", headers=_token())
        assert resp.status_code == 200
        assert resp.json() == {"sessions": [], "query": ""}


class TestWarningCarriesRealCause:
    def test_bridge_warning_real_cause(self, env, caplog):
        """降级可见非静默：warning 带真因（sqlite 错误文本）+统一家族文案。"""
        import logging

        env["corrupt_hermes"]()
        with caplog.at_level(logging.WARNING, logger="src.api.hermes_bridge"):
            env["client"].get("/api/hermes/sessions/hs-1/messages", headers=_token())
            env["client"].delete("/api/hermes/sessions/hs-1", headers=_token())
            env["client"].get("/api/hermes/sessions/search?q=needle", headers=_token())
        warn = _warn_text(caplog)
        assert warn.count("hermes store unreadable") == 3
        assert "not a database" in warn

    def test_sessions_search_warning_real_cause(self, env, caplog):
        """同病第 4 面同款：warning 带真因非静默。"""
        import logging

        env["corrupt_hermes"]()
        with caplog.at_level(logging.WARNING, logger="src.api.sessions_api"):
            env["client"].get("/api/sessions/search?q=needle", headers=_token())
        warn = _warn_text(caplog)
        assert "hermes store unreadable" in warn
        assert "not a database" in warn
