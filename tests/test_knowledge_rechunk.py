"""KN-③ knowledge update 真重分块/重嵌入——content/content_type 变更后旧分块/向量随换。

修前（HEAD 旧代码同测实证非推断）：update 写面只动 knowledge 行（+KN-② meili），
knowledge_chunks 行与 qdrant 向量点永驻旧文——向量/分块检索命中旧文=向量面谎报
（KN-② 诚实边界留档「update 不重分块/重嵌入=向量面旧文留档」）。

数据面全 tmp（tmp opensoul.db + tmp SQLitePool + 记录器 qdrant/meili 面），
绝不写生产 data/opensoul.db 与生产 qdrant/meilisearch。
"""

import sqlite3

import pytest

DDL = """CREATE TABLE IF NOT EXISTS users (
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
    starred INTEGER DEFAULT 0, pinned INTEGER DEFAULT 0,
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

UID_A = "00000000-0000-4000-8000-00000000000a"
UID_B = "00000000-0000-4000-8000-00000000000b"


def _long_para(n: int, tag: str) -> str:
    """n 字符量级的单段落（>max_chars 800 会自成多块；<800 单块）。"""
    return (tag + "-句子。") * (n // (len(tag) + 3))


class _QdrantRecorder:
    """qdrant 写面记录器（AVAILABLE=True 真走写点分支，捕获 upsert/delete 载荷）。"""

    AVAILABLE = True

    def __init__(self):
        self.upserts = []
        self.deletes = []

    def upsert_points(self, points):
        self.upserts.append(list(points))

    def delete_points(self, ids):
        self.deletes.append(list(ids))

    def upsert_point_ids(self) -> list[str]:
        return [p.id for batch in self.upserts for p in batch]


@pytest.fixture()
def mk(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.knowledge as knowledge_api
    import src.database.postgres as postgres_mod
    import src.services.knowledge as knowledge_mod
    from src.config import settings
    from src.database.postgres import SQLitePool

    tmp_db = tmp_path / "opensoul.db"
    with sqlite3.connect(tmp_db) as _c:
        _c.executescript(DDL)
        _c.commit()
    monkeypatch.setenv("USERS_DB", str(tmp_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_db}")
    tmp_pool = SQLitePool()
    monkeypatch.setattr(postgres_mod, "db_pool", tmp_pool)
    monkeypatch.setattr(knowledge_mod, "db_pool", tmp_pool)
    monkeypatch.setattr(knowledge_api, "db_pool", tmp_pool)

    embed_calls: list[list[str]] = []

    async def _fake_embeddings(texts):
        embed_calls.append(list(texts))
        # 确定性非空向量（每块唯一，便于断言点位与块序对应）
        return [[float(len(t)), float(i + 1)] for i, t in enumerate(texts)]

    monkeypatch.setattr(knowledge_mod, "get_embeddings_batch", _fake_embeddings)
    qrec = _QdrantRecorder()
    monkeypatch.setattr(knowledge_mod, "qdrant_client", qrec)

    indexed: list[str] = []

    async def _fake_index(kid: str) -> bool:
        indexed.append(kid)
        return True

    monkeypatch.setattr(knowledge_mod, "index_knowledge_doc", _fake_index)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()
        yield
        await tmp_pool.disconnect()

    app = FastAPI(lifespan=_lifespan)
    app.include_router(knowledge_api.router, prefix="/api/knowledge")
    with TestClient(app) as c:
        from types import SimpleNamespace

        yield SimpleNamespace(
            client=c,
            db=str(tmp_db),
            qdrant=qrec,
            embed_calls=embed_calls,
            indexed=indexed,
        )


def _chunks(db_path: str, kid: str) -> list[dict]:
    with sqlite3.connect(db_path) as _c:
        _c.row_factory = sqlite3.Row
        rows = _c.execute(
            "SELECT * FROM knowledge_chunks WHERE knowledge_id = ? ORDER BY chunk_index",
            (kid,),
        ).fetchall()
        return [dict(r) for r in rows]


def _create(mk, content: str, content_type: str = "text", uid: str = UID_A, **kw):
    body = {"title": "t", "content": content, "content_type": content_type, **kw}
    resp = mk.client.post("/api/knowledge/", params={"user_id": uid}, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _update(mk, kid: str, uid: str = UID_A, **fields):
    return mk.client.put(f"/api/knowledge/{kid}", params={"user_id": uid}, json=fields)


def _expected(content: str, content_type: str, kid: str, uid: str = UID_A):
    """分块单一源=smart_chunk 本体（禁测试自造期望）。"""
    from src.services.chunking import smart_chunk

    out = []
    for c in smart_chunk(content or "", content_type=content_type or ""):
        out.append(
            {
                "chunk_index": c.index,
                "content": c.content,
                "embedding_id": f"{kid}_{c.index}",
                "token_count": len(c.content.split()),
            }
        )
    return out


def _face(rows: list[dict]) -> list[dict]:
    return [
        {
            "chunk_index": r["chunk_index"],
            "content": r["content"],
            "embedding_id": r["embedding_id"],
            "token_count": r["token_count"],
        }
        for r in rows
    ]


class TestCreateChunkFaceContract:
    """create 分块面契约（_write_chunk_face 收编后行为零变化锁死）。"""

    def test_create_writes_chunks_and_points(self, mk):
        content = _long_para(900, "c1")
        kid = _create(mk, content)
        rows = _chunks(mk.db, kid)
        assert _face(rows) == _expected(content, "text", kid)
        assert rows, "content 非空必有分块行"
        assert mk.qdrant.upsert_point_ids() == [f"{kid}_{r['chunk_index']}" for r in rows]
        assert mk.embed_calls == [[r["content"] for r in rows]]


class TestUpdateRechunk:
    def test_update_content_rechunks_and_drops_old(self, mk):
        c1 = _long_para(900, "old")
        c2 = _long_para(900, "new")
        kid = _create(mk, c1)
        old_rows = _chunks(mk.db, kid)
        old_ids = [r["embedding_id"] for r in old_rows]
        mk.qdrant.upserts.clear()

        resp = _update(mk, kid, content=c2)
        assert resp.status_code == 200, resp.text
        assert resp.json()["content"] == c2

        rows = _chunks(mk.db, kid)
        assert _face(rows) == _expected(c2, "text", kid)
        # 旧分块行全消失（无新旧混驻）
        assert not (set(r["id"] for r in rows) & set(r["id"] for r in old_rows))
        # 旧向量点按行记录 id 真删 + 新点真写（旧文向量面清干净）
        assert mk.qdrant.deletes == [old_ids]
        assert mk.qdrant.upsert_point_ids() == [f"{kid}_{r['chunk_index']}" for r in rows]
        # KN-② meili 重索引零回归
        assert mk.indexed[-1] == kid

    def test_update_shrink_no_stale_chunk_rows(self, mk):
        big = "\n\n".join(_long_para(900, f"p{i}") for i in range(3))
        small = "一段很短的新内容。"
        kid = _create(mk, big)
        old_rows = _chunks(mk.db, kid)
        assert len(old_rows) >= 2
        mk.qdrant.deletes.clear()
        mk.qdrant.upserts.clear()

        assert _update(mk, kid, content=small).status_code == 200
        rows = _chunks(mk.db, kid)
        assert _face(rows) == _expected(small, "text", kid)
        assert len(rows) < len(old_rows)
        # 缩容后无残留高 index 行/点
        assert max(r["chunk_index"] for r in rows) == len(rows) - 1
        assert mk.qdrant.deletes == [[r["embedding_id"] for r in old_rows]]

    def test_update_content_type_triggers_rechunk(self, mk):
        content = "# 标题一\n" + _long_para(300, "md") + "\n# 标题二\n" + _long_para(300, "md")
        kid = _create(mk, content, content_type="text")
        old_rows = _chunks(mk.db, kid)
        mk.qdrant.deletes.clear()
        mk.qdrant.upserts.clear()

        assert _update(mk, kid, content_type="markdown").status_code == 200
        rows = _chunks(mk.db, kid)
        # 分块输入真源=行上 content（本次未传但随换重分块）
        assert _face(rows) == _expected(content, "markdown", kid)
        assert mk.qdrant.deletes == [[r["embedding_id"] for r in old_rows]]
        assert mk.qdrant.upsert_point_ids() == [f"{kid}_{r['chunk_index']}" for r in rows]

    def test_update_to_empty_content_clears_chunk_face(self, mk):
        kid = _create(mk, _long_para(900, "gone"))
        old_rows = _chunks(mk.db, kid)
        assert old_rows
        mk.qdrant.deletes.clear()
        mk.qdrant.upserts.clear()
        assert _update(mk, kid, content="").status_code == 200
        assert _chunks(mk.db, kid) == []
        assert mk.qdrant.deletes == [[r["embedding_id"] for r in old_rows]]
        assert mk.qdrant.upserts == []


class TestNoTouchFaces:
    """不触发面：title/metadata/tags/source 不参与分块构造（宁窄勿滥）。"""

    @pytest.mark.parametrize(
        "fields",
        [
            {"title": "新标题"},
            {"metadata": {"k": "v"}},
            {"tags": ["x", "y"]},
            {"source": "new-source"},
        ],
    )
    def test_non_content_update_zero_chunk_touch(self, mk, fields):
        content = _long_para(900, "keep")
        kid = _create(mk, content, tags=["a"])
        before = _chunks(mk.db, kid)
        mk.qdrant.deletes.clear()
        mk.qdrant.upserts.clear()

        assert _update(mk, kid, **fields).status_code == 200
        after = _chunks(mk.db, kid)
        # 分块行逐字节不变（行 id 都不换）
        assert after == before
        assert mk.qdrant.deletes == []
        assert mk.qdrant.upserts == []
        assert mk.embed_calls == [[r["content"] for r in before]]  # 仅 create 那一次

    def test_non_owner_update_404_zero_chunk_touch(self, mk):
        kid = _create(mk, _long_para(900, "victim"), uid=UID_A)
        before = _chunks(mk.db, kid)
        mk.qdrant.deletes.clear()
        mk.qdrant.upserts.clear()

        resp = _update(mk, kid, uid=UID_B, content="恶意改写")
        assert resp.status_code == 404
        assert _chunks(mk.db, kid) == before
        assert mk.qdrant.deletes == []
        assert mk.qdrant.upserts == []


class TestEmbeddingDegrade:
    def test_degrade_clears_stale_vectors_keeps_rows(self, mk, monkeypatch):
        import src.services.knowledge as knowledge_mod

        kid = _create(mk, _long_para(900, "v1"))
        old_rows = _chunks(mk.db, kid)
        assert old_rows and all(r["embedding_id"] for r in old_rows)
        mk.qdrant.deletes.clear()
        mk.qdrant.upserts.clear()

        async def _degraded(_texts):
            return [[] for _ in _texts]

        monkeypatch.setattr(knowledge_mod, "get_embeddings_batch", _degraded)
        assert _update(mk, kid, content=_long_para(900, "v2")).status_code == 200
        rows = _chunks(mk.db, kid)
        # 分块行照落（embedding_id=NULL），新点不写（空向量禁写），旧点必须已删
        # （旧文向量宁缺勿谎——留着=检索命中旧文）
        assert all(r["embedding_id"] is None for r in rows)
        assert [r["content"] for r in rows] == [
            e["content"] for e in _expected(_long_para(900, "v2"), "text", kid)
        ]
        assert mk.qdrant.upserts == []
        assert mk.qdrant.deletes == [[r["embedding_id"] for r in old_rows]]


class TestSingleSource:
    def test_create_and_update_same_content_identical_face(self, mk):
        content = "\n\n".join(_long_para(900, f"s{i}") for i in range(2))
        kid_a = _create(mk, content)
        kid_b = _create(mk, "占位旧文。")
        assert _update(mk, kid_b, content=content).status_code == 200

        face_a = _face(_chunks(mk.db, kid_a))
        face_b = _face(_chunks(mk.db, kid_b))
        # 同一 _write_chunk_face 口：逐块 content/index/token_count 一致，
        # embedding_id 同构（{kid}_{index}）
        assert [dict(f, embedding_id=None) for f in face_a] == [
            dict(f, embedding_id=None) for f in face_b
        ]
        assert [f["embedding_id"] for f in face_b] == [
            f"{kid_b}_{f['chunk_index']}" for f in face_b
        ]
