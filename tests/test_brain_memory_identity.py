"""brain 记忆面（/remember·/recall + think/verify/feedback 写点）身份轴收口测试。

F3b-㉛：brain.py 自有 MemoryStore 实例的记忆读写面（独立 store 非 /memories
CRUD 面）修前 /remember·/recall 五缺口——①冒写（任意人写任意桶）②显式桶冒读
③缺省=全量泄露（任意人看全部记忆）④写点行零归属（think/verify/feedback 行恒
无主）⑤桶引用无归属闸。

语义与 F3b-⑳ 逐字：认证=派生、自报只可确认绑定、矛盾=403「user mismatch」、
'default'/空/占位零 UUID（NIL_UUID）=未声明→取派生、匿名=保留自报（既有本地
多档案语义契约零变化）、坏 token=401 零副作用；显式桶引用越权=与缺行同契约
404 禁存在性探测；记忆行归属=父会话行继承（判定单一源=src/hippo/ownership）。

数据面=tmp 全隔离（USERS_DB/ENTERPRISE_DB/tmp SQLitePool/brain store·sessions
换 tmp 实例），绝不写生产库。
"""

import ast
import sqlite3
import uuid
from pathlib import Path

import pytest

NIL_UUID = "00000000-0000-0000-0000-000000000000"
UID_A = "11111111-1111-4111-8111-111111111111"
UID_B = "22222222-2222-4222-8222-222222222222"

_USERS_DDL = (
    "CREATE TABLE IF NOT EXISTS users ("
    "id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "email TEXT DEFAULT '', password_hash TEXT NOT NULL, "
    "role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1, "
    "tenant_id TEXT DEFAULT 'default', "
    "created_at TEXT DEFAULT (datetime('now')), "
    "updated_at TEXT DEFAULT (datetime('now')));"
)

_ENTERPRISE_DDL = (
    "CREATE TABLE IF NOT EXISTS users ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "username TEXT UNIQUE NOT NULL, "
    "password TEXT NOT NULL, "
    "is_active INTEGER NOT NULL DEFAULT 1, "
    "created_at TEXT NOT NULL DEFAULT (datetime('now')));"
)


def _seed_user(db_path, uid, username):
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, 'default')",
            (uid, username),
        )
        c.commit()
    return uid


def _token(uid):
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid), role="user")
    return {"Authorization": "Bearer " + tok}


BAD_TOKEN = {"Authorization": "Bearer not-a-real-token"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """brain router 进程内测试面（test_brain_emit_tenant brain_env 同款路线）+
    brain store/sessions 换 tmp 实例 + 身份库 tmp 隔离。"""
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.brain as brain_api
    import src.database.postgres as pg_mod
    from src.config import settings
    from src.database.postgres import SQLitePool
    from src.hippo.memory_store import MemoryStore
    from src.hippo.session import SessionManager

    users_db = tmp_path / "opensoul.db"
    with sqlite3.connect(users_db) as c:
        c.executescript(_USERS_DDL)
        c.commit()
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    with sqlite3.connect(tmp_path / "enterprise.db") as c:
        c.executescript(_ENTERPRISE_DDL)
        c.commit()
    _seed_user(users_db, UID_A, "alice")
    _seed_user(users_db, UID_B, "bob")

    # brain 记忆面数据面换 tmp 实例（模块单例调用时取真源）
    monkeypatch.setattr(brain_api, "store", MemoryStore())
    monkeypatch.setattr(brain_api, "sessions", SessionManager())

    tmp_db = tmp_path / "brain_data.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_db}")
    tmp_pool = SQLitePool()
    monkeypatch.setattr(pg_mod, "db_pool", tmp_pool)

    # think/verify emit：timeline 换 tmp + 死端口（绝不打 live/生产 timeline）
    from src.nerve import event_bridge
    from src.timeline.store import TimelineStore

    monkeypatch.setattr(
        event_bridge.emit, "_timeline", TimelineStore(str(tmp_path / "timeline.db")), raising=False
    )
    monkeypatch.setattr(event_bridge, "_BASE", "http://127.0.0.1:9")

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()
        yield
        await tmp_pool.disconnect()

    app = FastAPI(lifespan=_lifespan)
    app.include_router(brain_api.router, prefix="/api/brain")
    with TestClient(app) as c:
        yield c, brain_api


def _remember(client, headers=None, params=None):
    p = {"session_id": "s-1", "content": "记忆内容"}
    if params:
        p.update(params)
    return client.post("/api/brain/remember", params=p, headers=headers)


def _contents(resp):
    return sorted(m["content"] for m in resp.json()["memories"])


# ── /remember：冒写面收口 + 自动建会话行归属=派生身份 ─────────────────


class TestRememberAxis:
    def test_own_write_registers_parent_session_owned_by_derived(self, env):
        client, api = env
        resp = _remember(client, _token(UID_A), {"session_id": "s-own", "content": "A 的记忆"})
        assert resp.status_code == 200, resp.text
        held = api.sessions.peek("s-own")
        assert held is not None and held.user_id == UID_A
        rows = [m for m in api.store.search(session_id="s-own")]
        assert len(rows) == 1 and api._memory_owner(rows[0]) == UID_A

    def test_cross_user_write_denied_zero_side_effect(self, env):
        client, api = env
        assert (
            _remember(client, _token(UID_A), {"session_id": "s-x", "content": "A 原行"}).status_code
            == 200
        )
        resp = _remember(client, _token(UID_B), {"session_id": "s-x", "content": "B 冒写"})
        assert resp.status_code == 404 and "Session not found" in resp.text
        rows = api.store.search(session_id="s-x")
        assert [m.content for m in rows] == ["A 原行"]  # 行零改写零新增
        assert api.sessions.peek("s-x").user_id == UID_A  # 会话行零改写

    def test_anonymous_default_contract(self, env):
        client, api = env
        resp = _remember(client, None, {"session_id": "s-anon", "content": "匿名记忆"})
        assert resp.status_code == 200 and resp.json() == {
            "memory_id": resp.json()["memory_id"],
            "stored": True,
        }
        assert api.sessions.peek("s-anon").user_id == "default"
        assert api._memory_owner(api.store.search(session_id="s-anon")[0]) == "default"

    def test_anonymous_self_report_preserved(self, env):
        client, api = env
        resp = _remember(
            client, None, {"session_id": "s-self", "content": "自报", "user_id": UID_A}
        )
        assert resp.status_code == 200
        assert api.sessions.peek("s-self").user_id == UID_A  # 匿名保留自报（契约零变化）

    def test_declared_mismatch_403_zero_side_effect(self, env):
        client, api = env
        resp = _remember(
            client, _token(UID_A), {"session_id": "s-m", "content": "x", "user_id": UID_B}
        )
        assert resp.status_code == 403 and "user mismatch" in resp.text
        assert api.sessions.peek("s-m") is None and not api.store.search(session_id="s-m")

    def test_bad_token_401_zero_side_effect(self, env):
        client, api = env
        resp = _remember(client, BAD_TOKEN, {"session_id": "s-bad", "content": "x"})
        assert resp.status_code == 401
        assert api.sessions.peek("s-bad") is None and not api.store.search(session_id="s-bad")

    def test_nil_uuid_takes_derived(self, env):
        client, api = env
        resp = _remember(
            client, _token(UID_A), {"session_id": "s-nil", "content": "x", "user_id": NIL_UUID}
        )
        assert resp.status_code == 200
        assert api.sessions.peek("s-nil").user_id == UID_A


# ── /recall：「用户看自己的」+ 显式桶归属闸 ──────────────────────────


class TestRecallAxis:
    def test_default_scan_shows_own_only(self, env):
        client, api = env
        assert (
            _remember(client, _token(UID_A), {"session_id": "ra", "content": "A 行"}).status_code
            == 200
        )
        assert (
            _remember(client, _token(UID_B), {"session_id": "rb", "content": "B 行"}).status_code
            == 200
        )
        assert _remember(client, None, {"session_id": "rd", "content": "匿名行"}).status_code == 200
        # 修前=三行全量泄露（任意人看全部记忆）；修后各看各的
        assert _contents(client.get("/api/brain/recall", headers=_token(UID_A))) == ["A 行"]
        assert _contents(client.get("/api/brain/recall", headers=_token(UID_B))) == ["B 行"]
        assert _contents(client.get("/api/brain/recall")) == ["匿名行"]

    def test_explicit_session_own_ok(self, env):
        client, api = env
        _remember(client, _token(UID_A), {"session_id": "s-ok", "content": "own"})
        resp = client.get("/api/brain/recall", params={"session_id": "s-ok"}, headers=_token(UID_A))
        assert resp.status_code == 200 and _contents(resp) == ["own"]

    def test_explicit_session_cross_user_404_rows_unchanged(self, env):
        client, api = env
        _remember(client, _token(UID_A), {"session_id": "s-gate", "content": "A 行"})
        resp = client.get(
            "/api/brain/recall", params={"session_id": "s-gate"}, headers=_token(UID_B)
        )
        assert resp.status_code == 404 and "Session not found" in resp.text
        assert [m.content for m in api.store.search(session_id="s-gate")] == ["A 行"]

    def test_explicit_session_missing_404_same_contract(self, env):
        client, api = env
        # 越权=与缺行同契约（禁存在性探测：两形态同 404 不可差分）
        resp = client.get("/api/brain/recall", params={"session_id": "nope"}, headers=_token(UID_A))
        assert resp.status_code == 404 and "Session not found" in resp.text

    def test_bad_token_401(self, env):
        client, api = env
        assert client.get("/api/brain/recall", headers=BAD_TOKEN).status_code == 401
        assert (
            client.get(
                "/api/brain/recall", params={"session_id": "s-1"}, headers=BAD_TOKEN
            ).status_code
            == 401
        )

    def test_declared_mismatch_403(self, env):
        client, api = env
        _remember(client, _token(UID_A), {"session_id": "s-d", "content": "x"})
        resp = client.get(
            "/api/brain/recall",
            params={"session_id": "s-d", "user_id": UID_B},
            headers=_token(UID_A),
        )
        assert resp.status_code == 403 and "user mismatch" in resp.text

    def test_response_shape_unchanged(self, env):
        client, api = env
        _remember(client, _token(UID_A), {"session_id": "s-shape", "content": "c", "tags": "t1,t2"})
        resp = client.get("/api/brain/recall", headers=_token(UID_A))
        data = resp.json()
        assert set(data) == {"memories", "total"}
        item = data["memories"][0]
        assert set(item) == {"id", "content", "importance", "tags", "retention"}
        assert item["tags"] == ["t1", "t2"] and data["total"] == 1

    def test_limit_applies_to_visible_set(self, env):
        client, api = env
        for i in range(3):
            _remember(client, _token(UID_A), {"session_id": f"sl{i}", "content": f"A{i}"})
        _remember(client, None, {"session_id": "sl-anon", "content": "anon"})
        resp = client.get("/api/brain/recall", params={"limit": 2}, headers=_token(UID_A))
        assert resp.status_code == 200 and len(resp.json()["memories"]) == 2
        assert all(c.startswith("A") for c in _contents(resp))  # 过滤先于截断


# ── 写点（think/verify/feedback）行归属=派生身份 + 越权不写 ─────────


class TestWritePointOwnership:
    def test_think_row_owned_by_derived(self, env):
        client, api = env
        resp = client.post(
            "/api/brain/think",
            json={"action": "修改 main.py 加日志", "session_id": "task-t1"},
            headers=_token(UID_A),
        )
        assert resp.status_code == 200, resp.text
        assert api.sessions.peek("task-t1").user_id == UID_A
        rows = api.store.search(session_id="task-t1")
        assert rows and all(api._memory_owner(m) == UID_A for m in rows)

    def test_think_cross_user_bucket_skipped(self, env):
        client, api = env
        _remember(client, _token(UID_A), {"session_id": "task-own", "content": "A 桶行"})
        resp = client.post(
            "/api/brain/think",
            json={"action": "改文件", "session_id": "task-own"},
            headers=_token(UID_B),
        )
        assert resp.status_code == 200  # 记忆写入跳过不阻断思考主流程
        assert [m.content for m in api.store.search(session_id="task-own")] == ["A 桶行"]

    def test_verify_row_owned_by_derived(self, env):
        client, api = env
        resp = client.post(
            "/api/brain/verify",
            json={"action": "改文件", "task_id": "task-v1", "result": {"success": True}},
            headers=_token(UID_A),
        )
        assert resp.status_code == 200, resp.text
        rows = api.store.search(session_id="task-v1")
        assert rows and all(api._memory_owner(m) == UID_A for m in rows)

    def test_anonymous_think_row_default(self, env):
        client, api = env
        resp = client.post("/api/brain/think", json={"action": "改文件", "session_id": "task-anon"})
        assert resp.status_code == 200, resp.text
        assert api.sessions.peek("task-anon").user_id == "default"
        assert api._memory_owner(api.store.search(session_id="task-anon")[0]) == "default"

    def test_feedback_row_owned_by_derived(self, env):
        client, api = env
        resp = client.post(
            "/api/brain/feedback",
            json={"action": "回复太长", "feedback": "请精简", "rating": 3},
            headers=_token(UID_A),
        )
        assert resp.status_code == 200, resp.text
        rows = api.store.search(session_id=f"user:{UID_A}")
        assert rows and all(api._memory_owner(m) == UID_A for m in rows)

    def test_orphan_row_default_fallback_visible_to_anonymous(self, env):
        client, api = env
        # 键派生故障兜底语义：无父会话行=孤儿记忆 'default' 兜底（③b-2 旁路纪律），
        # 缺省扫描（'default' 身份可见）真兜底；显式桶引用=by-ID 语义缺行同契约 404
        api.store.add(session_id="orphan", content="孤儿行")
        assert api._memory_owner(api.store.search(session_id="orphan")[0]) == "default"
        resp = client.get("/api/brain/recall")
        assert resp.status_code == 200 and _contents(resp) == ["孤儿行"]
        resp2 = client.get("/api/brain/recall", params={"session_id": "orphan"})
        assert resp2.status_code == 404


# ── 静态接线普查（写了≠接线了） ──────────────────────────────────


class TestStaticWiring:
    def test_endpoints_and_write_points_wired(self):
        src = Path("src/api/brain.py").read_text()
        tree = ast.parse(src)
        targets = {"remember", "recall", "think", "verify", "feedback"}
        found = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                calls = [
                    n.func.id
                    for n in ast.walk(node)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                ]
                if node.name in targets:
                    found[node.name] = calls
        assert set(found) == targets
        assert "bind_identity" in found["remember"] and "bind_identity" in found["recall"]
        # 四个写点（/remember + think/verify/feedback）全走 _memory_write 单一收口
        for name in ("remember", "think", "verify", "feedback"):
            assert "_memory_write" in found[name], name
        # 显式桶闸/归属过滤真消费
        assert "_gate_session_owner" in found["recall"] and "_memory_owner" in found["recall"]
        # store.add 唯一残点=_memory_write 收口内（写点旁路清零）
        assert src.count("store.add(") == 1
        # 判定单一源：两面共用 src/hippo/ownership（禁两套判定漂移）
        hippo_src = Path("src/api/hippo.py").read_text()
        assert "_derive_memory_owner" in hippo_src and "_derive_session_owner" in hippo_src
        assert "_derive_memory_owner" in src and "_derive_session_owner" in src
        own_src = Path("src/hippo/ownership.py").read_text()
        assert "def memory_owner(" in own_src and "def session_owner(" in own_src
