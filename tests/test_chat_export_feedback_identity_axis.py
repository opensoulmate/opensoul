"""chat·export·feedback 面 user_id 轴 bind_identity 收口测试（user 从认证派生，禁自报冒读/冒写）。

F3b-⑥ 已裁决语义（user_id 自报轴改认证派生）同族既有面延伸（F3b-④b 先例，
F3b-⑦ entity/F3b-⑩ cortex/F3b-⑪ capture/F3b-⑫ pipeline 同款）：/api/chat/、
/api/export/* 、/api/feedback/* 修前 user_id 完全客户端自报直接进读写档案键
——认证请求可冒读他人导出/反馈、冒写他人反馈知识档案（F3b-⑥ search 同型）。

语义与 F3b-⑥ 逐字一致：认证=派生档案键、自报只可确认绑定、矛盾=403、
'default'/空/占位零 UUID（NIL_UUID，admin-ui 占位形）=未声明→取派生、
匿名=保留自报（既有本地多档案语义契约零变化）、坏 token=401 零副作用。

写键=档案键家族正典 str(_resolve_user_id(identity.user_id))（F3b-⑫ 同源）。

本文件 cef_client tmp 全隔离（tmp opensoul.db 真 schema + USERS_DB/
ENTERPRISE_DB tmp + db_pool 换 tmp SQLitePool），绝不写生产库。数据面=真
db_pool 真 SQL 落库回读（非接缝桩——反馈提取是纯规则面，落库回读即真实产出
证据）；chat 面 semantic_search 收集桩捕获真 uid 流入（identity 派生真消费）。
"""

import hashlib
import sqlite3
import uuid
from contextlib import asynccontextmanager

import pytest

NIL_UUID = "00000000-0000-0000-0000-000000000000"

# tmp 库预建 schema（conftest._PIPELINE_KNOWLEDGE_DDL 家族同形；conftest 不可
# import 时内联同源 DDL，禁漂移——users 带 tenant_id 列对齐 F3b-② 真源列集）
_PIPELINE_KNOWLEDGE_DDL = """CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
    email TEXT DEFAULT '', password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1,
    tenant_id TEXT DEFAULT 'default',
    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS knowledge (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    title TEXT NOT NULL, content TEXT DEFAULT '',
    tags TEXT DEFAULT '[]', source TEXT DEFAULT '',
    content_type TEXT DEFAULT '', metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id TEXT PRIMARY KEY, knowledge_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL, content TEXT NOT NULL,
    embedding_id TEXT, token_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
    color TEXT DEFAULT '#6366f1', created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS knowledge_tags (
    knowledge_id TEXT NOT NULL, tag_id TEXT NOT NULL);"""


def _md5_uuid(s: str) -> uuid.UUID:
    h = hashlib.md5(s.encode()).hexdigest()
    return uuid.UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


def _seed_user(db_path, uid: str, username: str, tenant: str = "default") -> str:
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
def cef_client(tmp_path, monkeypatch):
    """chat+export+feedback 三 router 隔离测试面：真实 router + tmp 知识库
    真 schema（_PIPELINE_KNOWLEDGE_DDL 家族同源），绝不写生产 data/opensoul.db。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.chat as chat_api
    import src.api.export as export_api
    import src.api.feedback as feedback_api
    import src.database.postgres as postgres_mod
    import src.services.feedback as feedback_service
    from src.config import settings
    from src.database.postgres import SQLitePool

    tmp_os_db = tmp_path / "opensoul.db"
    with sqlite3.connect(tmp_os_db) as _c:
        _c.executescript(_PIPELINE_KNOWLEDGE_DDL)
        # entities 表（export 面真触达；对齐 src/main.py:291 lifespan DDL）
        _c.execute(
            "CREATE TABLE IF NOT EXISTS entities ("
            "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
            "name TEXT NOT NULL, type TEXT DEFAULT '', "
            "description TEXT DEFAULT '', properties TEXT DEFAULT '{}', "
            "created_at TEXT DEFAULT (datetime('now')), "
            "updated_at TEXT DEFAULT (datetime('now')))"
        )
        _c.commit()
    monkeypatch.setenv("USERS_DB", str(tmp_os_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_os_db}")
    tmp_pool = SQLitePool()
    monkeypatch.setattr(postgres_mod, "db_pool", tmp_pool)
    monkeypatch.setattr(export_api, "db_pool", tmp_pool)
    monkeypatch.setattr(feedback_service, "db_pool", tmp_pool)

    captured = []

    async def _stub_semantic_search(question, user_id=None, limit=5):
        captured.append(user_id)
        return []

    monkeypatch.setattr(chat_api, "semantic_search", _stub_semantic_search)

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()
        yield
        await tmp_pool.disconnect()

    app = FastAPI(lifespan=_lifespan)
    app.include_router(chat_api.router, prefix="/api/chat")
    app.include_router(export_api.router, prefix="/api/export")
    app.include_router(feedback_api.router, prefix="/api/feedback")
    app.state.cef_db = str(tmp_os_db)
    app.state.cef_captured = captured
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def env(tmp_path, monkeypatch, cef_client):
    db_path = cef_client.app.state.cef_db
    user_a = _seed_user(db_path, str(uuid.uuid4()), "alice-cef")
    user_b = _seed_user(db_path, str(uuid.uuid4()), "bob-cef")
    return {
        "client": cef_client,
        "db": db_path,
        "user_a": user_a,
        "user_b": user_b,
        "captured": cef_client.app.state.cef_captured,
    }


def _chat(env, headers=None, user_id=None):
    params = {}
    if user_id is not None:
        params["user_id"] = user_id
    return env["client"].post(
        "/api/chat/", json={"question": "hi"}, params=params, headers=headers or {}
    )


def _seed_knowledge(env, key: str, title: str, content_type: str = "") -> None:
    with sqlite3.connect(env["db"]) as c:
        c.execute(
            "INSERT INTO knowledge (id, user_id, title, content, content_type, metadata) "
            "VALUES (?, ?, ?, ?, ?, '{}')",
            (str(uuid.uuid4()), key, title, f"content of {title}", content_type),
        )
        c.commit()


def _knowledge_rows(env) -> list:
    with sqlite3.connect(env["db"]) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute("SELECT * FROM knowledge")]


class TestChatIdentityAxis:
    def test_anon_default_canonical_key(self, env):
        """匿名缺省：档案键=家族正典 str(_resolve_user_id('default'))。"""
        resp = _chat(env)
        assert resp.status_code == 200
        assert env["captured"][-1] == _md5_uuid("default")

    def test_anon_self_report_preserved(self, env):
        """匿名保留自报（既有本地多档案语义，契约零变化）→ 键=自报档案正典键。"""
        resp = _chat(env, user_id="alice-chat")
        assert resp.status_code == 200
        assert env["captured"][-1] == _md5_uuid("alice-chat")

    def test_anon_nil_uuid_placeholder_preserved(self, env):
        """匿名+占位零 UUID：占位值原样保留（admin-ui 占位形契约零变化）。"""
        resp = _chat(env, user_id=NIL_UUID)
        assert resp.status_code == 200
        assert env["captured"][-1] == uuid.UUID(NIL_UUID)

    def test_authed_undeclared_derives_user(self, env):
        """认证+未声明→键=派生（users.id），修前自报直接进档案键（冒读面）。"""
        resp = _chat(env, headers=_token(env["user_a"]))
        assert resp.status_code == 200
        assert env["captured"][-1] == uuid.UUID(env["user_a"])

    def test_authed_default_and_nil_uuid_are_undeclared(self, env):
        """ "'default'/占位零 UUID=未声明→取派生（不误伤传占位符的调用方）。"""
        for declared in ("default", NIL_UUID):
            resp = _chat(env, headers=_token(env["user_a"]), user_id=declared)
            assert resp.status_code == 200
            assert env["captured"][-1] == uuid.UUID(env["user_a"])

    def test_authed_matching_declared_ok(self, env):
        resp = _chat(env, headers=_token(env["user_a"]), user_id=env["user_a"])
        assert resp.status_code == 200
        assert env["captured"][-1] == uuid.UUID(env["user_a"])

    def test_authed_conflict_403_zero_side_effect(self, env):
        """认证请求自报他人档案=403 冒读面收口，检索零调用（零副作用硬反证）。"""
        n = len(env["captured"])
        resp = _chat(env, headers=_token(env["user_a"]), user_id=env["user_b"])
        assert resp.status_code == 403
        assert "user mismatch" in resp.json()["detail"]
        assert len(env["captured"]) == n

    def test_bad_token_401_zero_side_effect(self, env):
        n = len(env["captured"])
        resp = _chat(env, headers={"Authorization": "Bearer garbage"})
        assert resp.status_code == 401
        assert len(env["captured"]) == n


class TestExportIdentityAxis:
    def test_json_anon_default_sees_own_rows_only(self, env):
        """匿名缺省面只见 'default' 正典键行（修前 user_id 必填 UUID 直通查询）。"""
        _seed_knowledge(env, str(_md5_uuid("default")), "row-default")
        _seed_knowledge(env, env["user_a"], "row-a")
        resp = env["client"].get("/api/export/json")
        assert resp.status_code == 200
        titles = [r["title"] for r in resp.json()["knowledge"]]
        assert titles == ["row-default"]

    def test_json_authed_derives_and_isolates(self, env):
        """认证派生：A 只见 A 行、B 行不可见（跨档案隔离真过滤）。"""
        _seed_knowledge(env, env["user_a"], "row-a")
        _seed_knowledge(env, env["user_b"], "row-b")
        resp = env["client"].get("/api/export/json", headers=_token(env["user_a"]))
        assert resp.status_code == 200
        titles = [r["title"] for r in resp.json()["knowledge"]]
        assert titles == ["row-a"]

    def test_json_conflict_403(self, env):
        resp = env["client"].get(
            "/api/export/json", params={"user_id": env["user_b"]}, headers=_token(env["user_a"])
        )
        assert resp.status_code == 403
        assert "user mismatch" in resp.json()["detail"]

    def test_markdown_json_axis(self, env):
        """/markdown-json 修前零收口面：现在同轴（派生隔离+矛盾 403）。"""
        _seed_knowledge(env, env["user_a"], "row-a")
        _seed_knowledge(env, env["user_b"], "row-b")
        resp = env["client"].get("/api/export/markdown-json", headers=_token(env["user_a"]))
        assert resp.status_code == 200
        titles = [r["title"] for r in resp.json()["knowledge"]]
        assert titles == ["row-a"]
        bad = env["client"].get(
            "/api/export/markdown-json",
            params={"user_id": env["user_b"]},
            headers=_token(env["user_a"]),
        )
        assert bad.status_code == 403

    def test_markdown_sqlite_aggregate_regression(self, env):
        """SQLite 后端回归钉（Gate3 逼出的挡路真断点根因修）：/markdown、
        /markdown-json、/csv 的 knowledge 聚合查询修前用 PG 专属
        array_agg ... FILTER（SQLite 生产后端必 500 'no such function:
        array_agg'），收口=GROUP_CONCAT 家族正典（knowledge.py list 同真源）
        + normalize_tags 归一（KN-② 单一源）。"""
        _seed_knowledge(env, env["user_a"], "row-a")
        for path in ("/api/export/markdown", "/api/export/markdown-json", "/api/export/csv"):
            resp = env["client"].get(
                path,
                params={"table": "knowledge"} if path.endswith("/csv") else None,
                headers=_token(env["user_a"]),
            )
            assert resp.status_code == 200, path
            assert "row-a" in resp.text

    def test_csv_axis(self, env):
        """/csv 修前零收口面：现在同轴（派生隔离+矛盾 403+坏 token 401）。"""
        _seed_knowledge(env, env["user_a"], "row-a")
        _seed_knowledge(env, env["user_b"], "row-b")
        resp = env["client"].get(
            "/api/export/csv", params={"table": "knowledge"}, headers=_token(env["user_a"])
        )
        assert resp.status_code == 200
        body = resp.text
        assert "row-a" in body
        assert "row-b" not in body
        bad = env["client"].get(
            "/api/export/csv",
            params={"table": "knowledge", "user_id": env["user_b"]},
            headers=_token(env["user_a"]),
        )
        assert bad.status_code == 403
        assert (
            env["client"]
            .get(
                "/api/export/csv",
                params={"table": "knowledge"},
                headers={"Authorization": "Bearer garbage"},
            )
            .status_code
            == 401
        )

    def test_anon_self_report_preserved(self, env):
        """匿名保留自报：自报 UUID 档案照旧可见（契约零变化）。"""
        _seed_knowledge(env, env["user_b"], "row-b")
        resp = env["client"].get("/api/export/json", params={"user_id": env["user_b"]})
        assert resp.status_code == 200
        assert [r["title"] for r in resp.json()["knowledge"]] == ["row-b"]

    def test_bad_token_401(self, env):
        resp = env["client"].get("/api/export/json", headers={"Authorization": "Bearer garbage"})
        assert resp.status_code == 401


class TestFeedbackIdentityAxis:
    def _extract(self, env, headers=None, user_id=None):
        params = {}
        if user_id is not None:
            params["user_id"] = user_id
        return env["client"].post(
            "/api/feedback/extract",
            json={
                "conversation": "发现 SQLite 嵌套连接会死锁，外层未提交禁开内层写库",
                "source": "probe",
            },
            params=params,
            headers=headers or {},
        )

    def test_extract_anon_default_canonical_write_key(self, env):
        """写键=家族正典（修前裸自报串直通 knowledge.user_id=孤儿键面）。"""
        resp = self._extract(env)
        assert resp.status_code == 200
        assert resp.json()["stored_count"] >= 1
        rows = _knowledge_rows(env)
        assert rows and all(r["user_id"] == str(_md5_uuid("default")) for r in rows)

    def test_extract_anon_self_report_canonical(self, env):
        resp = self._extract(env, user_id="alice-anon")
        assert resp.status_code == 200
        rows = _knowledge_rows(env)
        assert rows and all(r["user_id"] == str(_md5_uuid("alice-anon")) for r in rows)

    def test_extract_authed_undeclared_derives(self, env):
        resp = self._extract(env, headers=_token(env["user_a"]))
        assert resp.status_code == 200
        rows = _knowledge_rows(env)
        assert rows and all(r["user_id"] == env["user_a"] for r in rows)

    def test_extract_authed_conflict_403_zero_rows(self, env):
        """认证请求自报他人档案=403 冒写面收口，零知识行落库硬反证。"""
        resp = self._extract(env, headers=_token(env["user_a"]), user_id=env["user_b"])
        assert resp.status_code == 403
        assert "user mismatch" in resp.json()["detail"]
        assert _knowledge_rows(env) == []

    def test_extract_bad_token_401_zero_rows(self, env):
        resp = self._extract(env, headers={"Authorization": "Bearer garbage"})
        assert resp.status_code == 401
        assert _knowledge_rows(env) == []

    def test_entries_axis(self, env):
        """/entries 读面：A 只见 A 的反馈行、矛盾 403（修前自报直读他人反馈）。"""
        self._extract(env, headers=_token(env["user_a"]))
        self._extract(env, headers=_token(env["user_b"]))
        resp = env["client"].get("/api/feedback/entries", headers=_token(env["user_a"]))
        assert resp.status_code == 200
        rows = resp.json()
        assert rows and all(r["user_id"] == env["user_a"] for r in rows)
        bad = env["client"].get(
            "/api/feedback/entries",
            params={"user_id": env["user_b"]},
            headers=_token(env["user_a"]),
        )
        assert bad.status_code == 403

    def test_stats_axis(self, env):
        """/stats 聚合面：A 的 total 只计 A 行（修前自报直聚他人反馈）。"""
        self._extract(env, headers=_token(env["user_a"]))
        self._extract(env, headers=_token(env["user_b"]))
        resp = env["client"].get("/api/feedback/stats", headers=_token(env["user_a"]))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["by_type"]  # 真规则提取真产出
        bad = env["client"].get(
            "/api/feedback/stats", params={"user_id": env["user_b"]}, headers=_token(env["user_a"])
        )
        assert bad.status_code == 403
        assert (
            env["client"]
            .get("/api/feedback/stats", headers={"Authorization": "Bearer garbage"})
            .status_code
            == 401
        )


class TestStaticWiring:
    def test_chat_wiring(self):
        from pathlib import Path

        src = Path("src/api/chat.py").read_text(encoding="utf-8")
        assert src.count('bind_identity(authorization, "", user_id)') == 1
        import_line = "from src.services.tenant_binding import bind_identity"
        assert import_line in src
        assert src.index(import_line) < src.index("@router")
        assert src.index("def _resolve_user_id") < src.index("@router")

    def test_export_wiring(self):
        from pathlib import Path

        src = Path("src/api/export.py").read_text(encoding="utf-8")
        assert src.count('bind_identity(authorization, "", user_id)') == 4
        assert "user_id: UUID" not in src  # 修前必填自报形清零
        assert "WHERE k.user_id = $1" in src  # SQL 列名不被改写（回归钉）
        import_line = "from src.services.tenant_binding import bind_identity"
        assert import_line in src
        assert src.index(import_line) < src.index("@router")

    def test_feedback_wiring(self):
        from pathlib import Path

        src = Path("src/api/feedback.py").read_text(encoding="utf-8")
        assert src.count('bind_identity(authorization, "", user_id)') == 3
        assert "user_id: UUID = Query" not in src  # 修前必填自报形清零
        assert src.count("str(uid)") == 5  # 写键+读键全走家族正典
        assert 'user_id: str = Query("default")' in src

    def test_nil_uuid_semantics_locked(self):
        """ "占位零 UUID=未声明"语义在 bind_identity 单一判定口锁死。"""
        from src.services.tenant_binding import NIL_UUID, bind_identity

        assert NIL_UUID == "00000000-0000-0000-0000-000000000000"
        ident = bind_identity(None, "", NIL_UUID)
        assert ident.user_id == NIL_UUID  # 匿名占位原样保留（契约零变化）
