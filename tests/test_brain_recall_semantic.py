"""MEM-① /recall 语义检索面测试——mode=embedding 相似度排序（F3b-㉜ 边界①
留档「语义检索另拍」执行件）。

覆盖：cosine 纯函数/semantic_rank 排序·缓存·unranked·degraded/store 缓存失效·
批量回填/API mode 参数（语义排序·排名先于截断·归属同轴·422/503 诚实失败面·
keyword 形状零回归）/静态接线（写了≠接线了）。

数据面=tmp 全隔离（USERS_DB/ENTERPRISE_DB/tmp SQLitePool/brain store·sessions
换 tmp 实例），绝不写生产库；embedding 上游=真 socket 本地桩（真 httpx 栈，
settings.embedding_base_url 指桩，失败路径=monkeypatch 注入）。
"""

import ast
import json
import sqlite3
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


# ── 确定性向量桩：关键词→固定向量，真 socket /embeddings 服务 ──────────


def _stub_vec(text: str) -> list[float]:
    t = text.lower()
    if "爬山" in t or "hike" in t:
        return [1.0, 0.0, 0.0]
    if "半" in t:
        return [0.7, 0.7, 0.0]
    return [0.0, 1.0, 0.0]


class _EmbeddingStub:
    """真 HTTP 桩（embedding.py 走真 httpx POST {base}/embeddings）。"""

    def __init__(self):
        self.requests = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                texts = payload.get("input", [])
                if isinstance(texts, str):
                    texts = [texts]
                outer.requests += 1
                data = [{"index": i, "embedding": _stub_vec(t)} for i, t in enumerate(texts)]
                body = json.dumps({"data": data}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # 静音
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}/v1"

    def close(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture()
def embed_stub(monkeypatch):
    from src.config import settings

    stub = _EmbeddingStub()
    monkeypatch.setattr(settings, "embedding_base_url", stub.base_url)
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    yield stub
    stub.close()


def _seed_user(db_path, uid, username):
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, 'default')",
            (uid, username),
        )
        c.commit()
    return uid


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """brain router 进程内测试面（test_brain_recall_query env 同款路线）。"""
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


# ── cosine 纯函数 ─────────────────────────────────────────────────────


class TestCosine:
    def test_identical_and_opposite(self):
        from src.hippo.memory_semantic import cosine_similarity

        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == -1.0

    def test_orthogonal(self):
        from src.hippo.memory_semantic import cosine_similarity

        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_zero_norm_returns_zero(self):
        """零范数→0.0 如实：禁除零炸也禁 fabricate 1.0。"""
        from src.hippo.memory_semantic import cosine_similarity

        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
        assert cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0

    def test_dimension_mismatch_raises(self):
        """维度不齐→ValueError：zip 静默截断=假相似度，禁。"""
        from src.hippo.memory_semantic import cosine_similarity

        with pytest.raises(ValueError, match="dimension mismatch"):
            cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0])


# ── semantic_rank（真 socket 桩 + 失败注入） ──────────────────────────


class TestSemanticRank:
    def _rows(self):
        from src.hippo.memory_store import MemoryStore

        st = MemoryStore()
        st.add(session_id="s", content="周末计划去爬山")  # [1,0,0]
        st.add(session_id="s", content="半相关主题")  # [0.7,0.7,0]
        st.add(session_id="s", content="复习概率论")  # [0,1,0]
        return st

    def test_rank_order(self, embed_stub):
        import asyncio

        from src.hippo.memory_semantic import semantic_rank

        st = self._rows()
        rows = st.search(limit=100)
        rank = asyncio.run(semantic_rank("爬山", rows, store=st))
        assert [m.content for m, _ in rank.ranked] == [
            "周末计划去爬山",
            "半相关主题",
            "复习概率论",
        ]
        sims = [round(s, 3) for _, s in rank.ranked]
        assert sims == [1.0, 0.707, 0.0]
        assert (rank.candidates, rank.embedded, rank.unranked) == (3, 3, 0)
        assert rank.degraded is False

    def test_cache_second_call_skips_row_embed(self, embed_stub):
        import asyncio

        from src.hippo.memory_semantic import semantic_rank

        st = self._rows()
        rows = st.search(limit=100)
        asyncio.run(semantic_rank("爬山", rows, store=st))
        after_first = embed_stub.requests
        asyncio.run(semantic_rank("爬山", rows, store=st))
        # 第二轮：query 1 次，行向量已缓存零批量调用
        assert embed_stub.requests - after_first == 1
        assert all(m.embedding for m in rows)

    def test_row_embed_failure_unranked_not_silent(self, embed_stub, monkeypatch):
        import asyncio

        import src.hippo.memory_semantic as ms

        st = self._rows()
        rows = st.search(limit=100)

        async def _all_empty(texts):
            return [[] for _ in texts]

        monkeypatch.setattr(ms, "get_embeddings_batch", _all_empty)
        rank = asyncio.run(ms.semantic_rank("爬山", rows, store=st))
        assert rank.ranked == []
        assert rank.unranked == 3  # 如实计数禁静默丢行
        assert rank.degraded is False  # query 向量正常=非整体降级

    def test_query_embed_failure_degraded(self, embed_stub, monkeypatch):
        import asyncio

        import src.hippo.memory_semantic as ms

        st = self._rows()
        rows = st.search(limit=100)

        async def _boom(text):
            raise RuntimeError("Embedding API failed after 3 retries: upstream boom")

        monkeypatch.setattr(ms, "get_embedding", _boom)
        rank = asyncio.run(ms.semantic_rank("爬山", rows, store=st))
        assert rank.degraded is True
        assert "upstream boom" in rank.reason

    def test_empty_rows_shortcut(self, embed_stub):
        import asyncio

        from src.hippo.memory_semantic import semantic_rank

        rank = asyncio.run(semantic_rank("爬山", []))
        assert (rank.candidates, rank.embedded, rank.unranked, rank.degraded) == (0, 0, 0, False)
        assert embed_stub.requests == 0  # 零候选不打上游

    def test_tie_break_retention_then_id(self, embed_stub):
        import asyncio

        from src.hippo.memory_semantic import semantic_rank

        st = self._rows()
        rows = st.search(limit=100)
        for m in rows:
            if m.content == "半相关主题":
                m.retention = 0.2
            if m.content == "复习概率论":
                m.retention = 0.3
        # 两条 [0,1,0] 同相似度 0.0：retention 高者在前
        st.add(session_id="s", content="另一条概率论")  # [0,1,0]
        rows = st.search(limit=100)
        for m in rows:
            if m.content == "另一条概率论":
                m.retention = 0.9
        rank = asyncio.run(semantic_rank("爬山", rows, store=st))
        tail = [m.content for m, _ in rank.ranked[-2:]]
        assert tail == ["另一条概率论", "复习概率论"]


# ── store 缓存回填/失效 ──────────────────────────────────────────────


class TestStoreEmbeddingCache:
    def test_apply_embeddings_skips_empty_and_missing(self):
        from src.hippo.memory_store import MemoryStore

        st = MemoryStore()
        m1 = st.add(session_id="s", content="a")
        applied = st.apply_embeddings(
            {m1.memory_id: [1.0, 0.0], "no-such-id": [1.0], m1.memory_id + "x": []}
        )
        assert applied == 1
        assert st.peek(m1.memory_id).embedding == [1.0, 0.0]

    def test_apply_embeddings_skips_empty_vector(self):
        from src.hippo.memory_store import MemoryStore

        st = MemoryStore()
        m1 = st.add(session_id="s", content="a")
        st.apply_embeddings({m1.memory_id: [1.0]})
        assert st.apply_embeddings({m1.memory_id: []}) == 0  # 空向量=失败禁缓存
        assert st.peek(m1.memory_id).embedding == [1.0]  # 旧缓存不被空向量洗掉

    def test_update_content_invalidates_cache(self):
        from src.hippo.memory_store import MemoryStore

        st = MemoryStore()
        m1 = st.add(session_id="s", content="旧内容")
        st.apply_embeddings({m1.memory_id: [1.0, 0.0]})
        assert st.peek(m1.memory_id).embedding == [1.0, 0.0]
        st.update(m1.memory_id, content="新内容")
        assert st.peek(m1.memory_id).embedding is None  # 陈旧向量禁留

    def test_update_without_content_keeps_cache(self):
        from src.hippo.memory_store import MemoryStore

        st = MemoryStore()
        m1 = st.add(session_id="s", content="内容不变")
        st.apply_embeddings({m1.memory_id: [1.0, 0.0]})
        st.update(m1.memory_id, importance=0.9)
        assert st.peek(m1.memory_id).embedding == [1.0, 0.0]


# ── API：/recall mode 参数（keyword 零回归 + semantic 加性面） ────────


class TestRecallModeAPI:
    def _write(self, client, content, session="s-1", uid="default"):
        return client.post(
            "/api/brain/remember",
            params={"session_id": session, "content": content, "user_id": uid},
        )

    def test_keyword_shape_zero_regression(self, env, embed_stub):
        client, _ = env
        self._write(client, "周末计划去爬山")
        resp = client.get("/api/brain/recall", params={"query": "爬山"})
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"memories", "total"}  # 形状逐键不变
        assert set(data["memories"][0].keys()) == {
            "id",
            "content",
            "importance",
            "tags",
            "retention",
        }

    def test_semantic_ranked_with_additive_keys(self, env, embed_stub):
        client, _ = env
        self._write(client, "周末计划去爬山")
        self._write(client, "半相关主题")
        self._write(client, "复习概率论")
        resp = client.get("/api/brain/recall", params={"query": "爬山", "mode": "semantic"})
        assert resp.status_code == 200
        data = resp.json()
        assert [m["content"] for m in data["memories"]] == [
            "周末计划去爬山",
            "半相关主题",
            "复习概率论",
        ]
        assert round(data["memories"][0]["similarity"], 3) == 1.0
        assert round(data["memories"][1]["similarity"], 3) == 0.707
        assert data["retrieval"] == {
            "mode": "semantic",
            "candidates": 3,
            "ranked": 3,
            "unranked": 0,
        }
        assert data["total"] == 3

    def test_semantic_limit_after_rank(self, env, embed_stub):
        """排名先于截断：top-N 在全候选排序上取（keyword 同纪律）。"""
        client, _ = env
        self._write(client, "周末计划去爬山")
        self._write(client, "半相关主题")
        self._write(client, "复习概率论")
        resp = client.get(
            "/api/brain/recall", params={"query": "爬山", "mode": "semantic", "limit": 2}
        )
        got = [m["content"] for m in resp.json()["memories"]]
        assert got == ["周末计划去爬山", "半相关主题"]  # 低相似度行被排名挤出非插入序挤出

    def test_semantic_session_bucket_gate(self, env, embed_stub):
        client, _ = env
        self._write(client, "周末计划去爬山", session="s-mine")
        resp = client.get(
            "/api/brain/recall",
            params={"query": "爬山", "mode": "semantic", "session_id": "s-mine"},
        )
        assert [m["content"] for m in resp.json()["memories"]] == ["周末计划去爬山"]
        resp = client.get(
            "/api/brain/recall",
            params={"query": "爬山", "mode": "semantic", "session_id": "s-missing"},
        )
        assert resp.status_code == 404  # 缺桶同契约，语义模式不绕闸

    def test_semantic_owner_isolation(self, env, embed_stub):
        """归属过滤先于排名：他人记忆绝不进语义结果（F3b-㉛ 同轴）。"""
        client, _ = env
        self._write(client, "周末计划去爬山", uid="owner-x")
        resp = client.get(
            "/api/brain/recall",
            params={"query": "爬山", "mode": "semantic", "user_id": "owner-x"},
        )
        assert len(resp.json()["memories"]) == 1
        resp = client.get(
            "/api/brain/recall",
            params={"query": "爬山", "mode": "semantic", "user_id": "attacker"},
        )
        data = resp.json()
        assert data["memories"] == []
        assert data["retrieval"]["candidates"] == 0  # 过滤后候选=0，无泄露

    def test_semantic_empty_query_422(self, env, embed_stub):
        client, _ = env
        resp = client.get("/api/brain/recall", params={"query": "  ", "mode": "semantic"})
        assert resp.status_code == 422
        assert "semantic" in resp.json()["detail"]

    def test_invalid_mode_422(self, env, embed_stub):
        client, _ = env
        resp = client.get("/api/brain/recall", params={"query": "x", "mode": "vector"})
        assert resp.status_code == 422
        assert "invalid mode" in resp.json()["detail"]

    def test_semantic_embedding_down_503_no_silent_fallback(self, env, embed_stub, monkeypatch):
        """embedding 上游不可用→503 带真因；禁静默降级成关键词结果（F4b-②）。"""
        import src.hippo.memory_semantic as ms

        client, _ = env
        self._write(client, "周末计划去爬山")

        async def _boom(text):
            raise RuntimeError("Embedding API failed after 3 retries: upstream boom")

        monkeypatch.setattr(ms, "get_embedding", _boom)
        resp = client.get("/api/brain/recall", params={"query": "爬山", "mode": "semantic"})
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert "semantic retrieval unavailable" in detail and "upstream boom" in detail
        assert "memories" not in resp.json()  # 非关键词结果冒充

    def test_keyword_path_unaffected_by_embed_stub(self, env, monkeypatch):
        """keyword 路径零 embedding 依赖（修前契约）：桩不打上游也可用。"""
        import src.hippo.memory_semantic as ms

        client, _ = env
        self._write(client, "needle 行")

        async def _no_upstream(text):
            raise AssertionError("keyword 路径绝不触 embedding")

        monkeypatch.setattr(ms, "get_embedding", _no_upstream)
        resp = client.get("/api/brain/recall", params={"query": "needle"})
        assert resp.status_code == 200
        assert [m["content"] for m in resp.json()["memories"]] == ["needle 行"]


# ── 静态接线（写了≠接线了） ─────────────────────────────────────────


class TestStaticWiring:
    def test_recall_calls_semantic_rank(self):
        src = (Path(__file__).resolve().parent.parent / "src" / "api" / "brain.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(src)
        fns = [
            n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "recall"
        ]
        assert len(fns) == 1
        fn = fns[0]
        params = [a.arg for a in fn.args.args]
        assert "mode" in params  # mode 形参真在签名里
        called = {
            n.func.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "semantic_rank" in called  # handler 真调用排序口

    def test_memory_semantic_imported_at_top(self):
        src = (Path(__file__).resolve().parent.parent / "src" / "api" / "brain.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(src)
        top_imports = [
            n
            for n in tree.body
            if isinstance(n, ast.ImportFrom) and n.module == "src.hippo.memory_semantic"
        ]
        assert top_imports and any(a.name == "semantic_rank" for n in top_imports for a in n.names)
