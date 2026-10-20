"""F3b-㉜ /recall query 参数真消费测试——关键词检索记忆（wired-but-not-functional 收口）。

修前（grep 实证）：/api/brain/recall 声明 query 参数却零消费——store.search 无
query 过滤轴、handler 不传，客户端带检索词被静默忽略（F4b-② LLMRequest.stream
同型「声明了不消费」）。收口：store.search 增 query 过滤轴（字面子串匹配 content，
大小写不敏感；None/空=零过滤契约零变化）+ /recall 两分支真消费（过滤先于截断，
limit top-N 语义在过滤后可见集上取）。

数据面=tmp 全隔离（USERS_DB/ENTERPRISE_DB/tmp SQLitePool/brain store·sessions
换 tmp 实例），绝不写生产库。
"""

import ast
import sqlite3
import uuid
from pathlib import Path

import pytest

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


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """brain router 进程内测试面（test_brain_memory_identity env 同款路线）+ brain
    store/sessions 换 tmp 实例 + 身份库 tmp 隔离。"""
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

    monkeypatch.setattr(brain_api, "store", MemoryStore())
    monkeypatch.setattr(brain_api, "sessions", SessionManager())

    tmp_db = tmp_path / "brain_data.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_db}")
    tmp_pool = SQLitePool()
    monkeypatch.setattr(pg_mod, "db_pool", tmp_pool)

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


# ── store.search query 过滤轴（字面子串，单一过滤源） ─────────────────


class TestStoreQueryFilter:
    def test_ascii_case_insensitive(self):
        from src.hippo.memory_store import MemoryStore

        st = MemoryStore()
        st.add(session_id="s", content="Needle in a Haystack")
        st.add(session_id="s", content="unrelated")
        got = st.search(query="needle")
        assert [m.content for m in got] == ["Needle in a Haystack"]
        assert [m.content for m in st.search(query="HAYSTACK")] == ["Needle in a Haystack"]

    def test_chinese_substring(self):
        from src.hippo.memory_store import MemoryStore

        st = MemoryStore()
        st.add(session_id="s", content="今天复习了概率论")
        st.add(session_id="s", content="明天去爬山")
        assert [m.content for m in st.search(query="概率")] == ["今天复习了概率论"]

    def test_no_match_empty(self):
        from src.hippo.memory_store import MemoryStore

        st = MemoryStore()
        st.add(session_id="s", content="只有一条")
        assert st.search(query="不存在的词") == []

    def test_none_and_empty_query_zero_change(self):
        """None/''=零过滤契约零变化：非命中行照旧在场（修前行为逐字）。"""
        from src.hippo.memory_store import MemoryStore

        st = MemoryStore()
        st.add(session_id="s", content="needle 行")
        st.add(session_id="s", content="普通行")
        baseline = [m.content for m in st.search()]
        assert baseline == [m.content for m in st.search(query=None)]
        assert baseline == [m.content for m in st.search(query="")]
        assert "普通行" in baseline  # 非命中行未被过滤

    def test_filter_before_limit(self):
        """limit top-N 在过滤后可见集上取（禁先截断把命中行挤出窗口）。"""
        from src.hippo.memory_store import MemoryStore

        st = MemoryStore()
        for i in range(3):
            st.add(session_id="s", content=f"needle-{i}")
        for i in range(2):
            st.add(session_id="s", content=f"noise-{i}")
        got2 = st.search(query="needle", limit=2)
        assert len(got2) == 2 and all("needle" in m.content for m in got2)
        assert len(st.search(query="needle", limit=10)) == 3

    def test_composes_with_session_filter(self):
        from src.hippo.memory_store import MemoryStore

        st = MemoryStore()
        st.add(session_id="s-1", content="needle 在一会话")
        st.add(session_id="s-2", content="needle 在二会话")
        got = st.search(session_id="s-1", query="needle")
        assert [m.content for m in got] == ["needle 在一会话"]

    def test_matches_content_only_not_tags(self):
        """query 只匹配 content——tags 是独立过滤轴（刻意分开）。"""
        from src.hippo.memory_store import MemoryStore

        st = MemoryStore()
        st.add(session_id="s", content="内容里没有关键词", tags=["needle"])
        assert st.search(query="needle") == []
        assert [m.content for m in st.search(tags=["needle"])] == ["内容里没有关键词"]


# ── /recall 两分支真消费 ────────────────────────────────────────────


class TestRecallQueryApi:
    def test_query_filters_default_branch(self, env):
        client, _api = env
        for content in ("needle 甲", "无关乙", "needle 丙"):
            assert _remember(client, _token(UID_A), {"content": content}).status_code == 200
        resp = client.get("/api/brain/recall", params={"query": "needle"}, headers=_token(UID_A))
        assert resp.status_code == 200
        assert _contents(resp) == ["needle 丙", "needle 甲"]  # 排序=retention 降序稳定序
        assert resp.json()["total"] == 2

    def test_empty_and_whitespace_query_contract_unchanged(self, env):
        client, _api = env
        for content in ("needle 甲", "无关乙"):
            _remember(client, _token(UID_A), {"content": content})
        for q in ("", "   "):
            resp = client.get("/api/brain/recall", params={"query": q}, headers=_token(UID_A))
            assert resp.status_code == 200
            body = resp.json()
            assert _contents(resp) == ["needle 甲", "无关乙"]  # 零过滤契约逐字（sorted）
            assert set(body) == {"memories", "total"} and body["total"] == 2
            assert set(body["memories"][0]) == {"id", "content", "importance", "tags", "retention"}

    def test_query_with_session_bucket(self, env):
        client, _api = env
        _remember(client, _token(UID_A), {"session_id": "s-b", "content": "needle 桶内"})
        _remember(client, _token(UID_A), {"session_id": "s-b", "content": "桶内无关"})
        resp = client.get(
            "/api/brain/recall",
            params={"session_id": "s-b", "query": "needle"},
            headers=_token(UID_A),
        )
        assert _contents(resp) == ["needle 桶内"] and resp.json()["total"] == 1

    def test_no_match_total_zero(self, env):
        client, _api = env
        _remember(client, _token(UID_A), {"content": "needle 甲"})
        resp = client.get("/api/brain/recall", params={"query": "不存在"}, headers=_token(UID_A))
        assert resp.status_code == 200
        assert resp.json() == {"memories": [], "total": 0}

    def test_owner_isolation_composes_with_query(self, env):
        """query 过滤叠在「用户看自己的」之上：B 带 query 也恒见不到 A 的命中行。"""
        client, _api = env
        _remember(client, _token(UID_A), {"content": "needle A 秘密"})
        _remember(client, _token(UID_B), {"session_id": "s-b", "content": "needle B 自己的"})
        resp = client.get("/api/brain/recall", params={"query": "needle"}, headers=_token(UID_B))
        assert _contents(resp) == ["needle B 自己的"]
        resp_a = client.get("/api/brain/recall", params={"query": "needle"}, headers=_token(UID_A))
        assert _contents(resp_a) == ["needle A 秘密"]

    def test_anonymous_self_report_contract_with_query(self, env):
        client, _api = env
        _remember(client, None, {"user_id": "probe-u", "content": "needle 匿名"})
        _remember(client, None, {"user_id": "probe-u", "content": "匿名无关"})
        resp = client.get("/api/brain/recall", params={"user_id": "probe-u", "query": "needle"})
        assert resp.status_code == 200 and _contents(resp) == ["needle 匿名"]


# ── 静态接线普查（Gate2：写了≠接线了） ──────────────────────────────


class TestWiring:
    def test_recall_passes_query_to_both_store_search_calls(self):
        """recall 体内全部 store.search 调用点必须带 query 关键字实参（两分支）。"""
        src = (Path(__file__).resolve().parent.parent / "src" / "api" / "brain.py").read_text()
        tree = ast.parse(src)
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "recall"
        )
        calls = [
            c
            for c in ast.walk(fn)
            if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Attribute)
            and c.func.attr == "search"
        ]
        assert len(calls) == 2, f"expect 2 store.search calls in recall, got {len(calls)}"
        for c in calls:
            kws = {k.arg for k in c.keywords}
            assert "query" in kws, f"store.search call missing query kwarg: {ast.dump(c)}"
