"""KN-② knowledge meili 索引写面收口——update 真重索引 + doc 单一构造口（真 socket 桩）。

修前（HEAD 旧代码同测实证非推断）：
① update 写面零索引刷新——content/title/tags 变更后检索面仍旧文（KN-① 诚实
   边界留档「不重刷 meili 索引」）。
② doc 构造双源：create 内联 doc（6 键、tags=请求体）与 meili_indexer._row_to_doc
   （8 键、tags=knowledge.tags 列）双形状双 tags 源=同一索引内容随写入路径漂移。
③ _row_to_doc tags 读 knowledge.tags 列——该列无写点恒 '[]'，全量重索引
   （lifespan/index_all_knowledge）把已索引条目 tags 洗空。

数据面全 tmp（tmp opensoul.db + tmp SQLitePool + stub 索引面），绝不写生产
data/opensoul.db 与生产 meilisearch。Meilisearch 边界=真 socket 桩（真 HTTP
请求记录+可编程失败），非方法级 monkeypatch。
"""

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

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

_TASK = (
    '{"taskUid": 1, "indexUid": "opensoul_knowledge", "status": "enqueued",'
    ' "type": "documentAdditionOrUpdate", "enqueuedAt": "2026-10-14T00:00:00.000Z"}'
)
_INDEX_INFO = (
    '{"uid": "opensoul_knowledge", "primaryKey": "id",'
    ' "createdAt": "2026-10-14T00:00:00.000Z", "updatedAt": "2026-10-14T00:00:00.000Z"}'
)
_ERR = (
    '{"message": "stub failure", "code": "stub_error",'
    ' "type": "invalid_request", "link": "https://docs.meilisearch.com/errors"}'
)


class _MeiliStub:
    """真 socket Meilisearch 桩：documents 按主键 id 替换存储（同真索引语义），
    记录全部请求；fail_documents 可编程 500（失败面测 fire-and-forget 语义）。"""

    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []
        self.docs: dict[str, dict] = {}
        self.fail_documents = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, code: int, body: str):
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body.encode())

            def _rec(self, body: str = ""):
                outer.calls.append((self.command, self.path, body))

            def do_GET(self):
                self._rec()
                parts = self.path.strip("/").split("/")
                # indexes/{uid}/documents/{id}
                if len(parts) == 4 and parts[2] == "documents":
                    doc = outer.docs.get(parts[3])
                    if doc is None:
                        self._reply(404, _ERR)
                    else:
                        self._reply(200, json.dumps(doc))
                    return
                self._reply(200, _INDEX_INFO)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
                self._rec(body)
                if outer.fail_documents:
                    self._reply(500, _ERR)
                    return
                for doc in json.loads(body):
                    outer.docs[str(doc["id"])] = doc
                self._reply(202, _TASK)

            def do_DELETE(self):
                self._rec()
                parts = self.path.strip("/").split("/")
                if len(parts) == 4 and parts[2] == "documents":
                    outer.docs.pop(parts[3], None)
                self._reply(202, _TASK)

            def log_message(self, *args):  # 静默
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def doc_posts(self) -> list[dict]:
        out = []
        for method, path, body in self.calls:
            if method == "POST" and path.endswith("/documents"):
                out.extend(json.loads(body))
        return out

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def mk(tmp_path, monkeypatch):
    stub = _MeiliStub()
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import src.api.knowledge as knowledge_api
        import src.api.search as search_api
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
        monkeypatch.setattr(settings, "meili_url", stub.url)
        tmp_pool = SQLitePool()
        monkeypatch.setattr(postgres_mod, "db_pool", tmp_pool)
        monkeypatch.setattr(knowledge_mod, "db_pool", tmp_pool)
        monkeypatch.setattr(knowledge_api, "db_pool", tmp_pool)

        async def _no_embeddings(_texts):
            return [[] for _ in _texts]

        monkeypatch.setattr(knowledge_mod, "get_embeddings_batch", _no_embeddings)
        # 索引面=真 socket 桩（AVAILABLE 恒 True + 懒 client 随 stub URL 新建，
        # teardown 经 monkeypatch 复位防跨用例泄漏）
        monkeypatch.setattr(knowledge_mod.meili_client, "AVAILABLE", True)
        monkeypatch.setattr(knowledge_mod.meili_client, "_client", None)

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _lifespan(_app):
            await tmp_pool.connect()
            yield
            await tmp_pool.disconnect()

        app = FastAPI(lifespan=_lifespan)
        app.include_router(knowledge_api.router, prefix="/api/knowledge")
        app.include_router(search_api.router, prefix="/api/search")
        app.state.knowledge_client_db = str(tmp_db)
        with TestClient(app) as c:
            yield SimpleNamespace(client=c, stub=stub, db=str(tmp_db))
    finally:
        stub.close()


def _seed(db_path: str, kid: str, tags_column: str, link_tags: list[str], uid: str = UID_A):
    with sqlite3.connect(db_path) as _c:
        _c.execute(
            "INSERT INTO knowledge(id,user_id,title,content,tags) VALUES(?,?,?,?,?)",
            (kid, uid, "seed-" + kid, "seed-content-" + kid, tags_column),
        )
        for i, name in enumerate(link_tags):
            tid = f"{kid}-t{i}"
            _c.execute("INSERT INTO tags(id,user_id,name) VALUES(?,?,?)", (tid, uid, name))
            _c.execute("INSERT INTO knowledge_tags(knowledge_id,tag_id) VALUES(?,?)", (kid, tid))
        _c.commit()


class TestNormalizeTagsSingleSource:
    """normalize_tags 形状契约（KnowledgeResponse.tags 与 meili doc tags 共用）。"""

    def test_list_shape(self):
        from src.models.knowledge import normalize_tags

        assert normalize_tags(["x", 1]) == ["x", "1"]

    def test_json_text_shape(self):
        from src.models.knowledge import normalize_tags

        assert normalize_tags('["a", "b"]') == ["a", "b"]

    def test_comma_concat_shape(self):
        from src.models.knowledge import normalize_tags

        assert normalize_tags("a,b") == ["a", "b"]

    def test_none_and_empty(self):
        from src.models.knowledge import normalize_tags

        assert normalize_tags(None) == []
        assert normalize_tags("") == []
        assert normalize_tags("   ") == []

    def test_legacy_fallback_shapes(self):
        from src.models.knowledge import normalize_tags

        # 坏 JSON（'[' 开头解析失败）走逗号拆分（_row_out 既有语义逐字保留）
        assert normalize_tags("[broken") == ["[broken"]
        assert normalize_tags(42) == []


class TestCreateIndexFace:
    def test_create_doc_shape_and_tags_from_links(self, mk):
        resp = mk.client.post(
            "/api/knowledge/",
            params={"user_id": UID_A},
            json={
                "title": "kn2-t1",
                "content": "kn2-c1",
                "tags": ["b", "a"],
                "source": "e2e",
                "content_type": "text/plain",
            },
        )
        assert resp.status_code == 200, resp.text
        kid = resp.json()["id"]
        posts = mk.stub.doc_posts()
        assert len(posts) == 1
        doc = posts[0]
        # doc 形状=与全量重索引同一构造口（8 键恰集，禁双源漂移）
        assert set(doc) == {
            "id",
            "title",
            "content",
            "tags",
            "user_id",
            "content_type",
            "source",
            "created_at",
        }
        assert doc["id"] == kid
        assert doc["content"] == "kn2-c1"
        assert doc["user_id"] == UID_A
        assert doc["source"] == "e2e"
        assert doc["content_type"] == "text/plain"
        # tags 真源=链接面（与响应同一读真源）
        assert sorted(doc["tags"]) == ["a", "b"]
        assert sorted(doc["tags"]) == sorted(resp.json()["tags"])

    def test_create_index_failure_not_blocking(self, mk):
        # 行为收口：meili 故障绝不吃掉业务写入（修前 create 内联 add_documents
        # 异常逃逸=行已落库后 500 静默部分写）
        mk.stub.fail_documents = True
        resp = mk.client.post(
            "/api/knowledge/",
            params={"user_id": UID_A},
            json={"title": "kn2-fail", "content": "c-fail", "tags": ["t"]},
        )
        assert resp.status_code == 200, resp.text
        kid = resp.json()["id"]
        assert len(mk.stub.doc_posts()) == 1  # 真尝试（接线了）
        got = mk.client.get(f"/api/knowledge/{kid}", params={"user_id": UID_A})
        assert got.status_code == 200 and got.json()["content"] == "c-fail"


class TestUpdateReindexFace:
    def test_update_content_reindexes(self, mk):
        kid = mk.client.post(
            "/api/knowledge/",
            params={"user_id": UID_A},
            json={"title": "t", "content": "original-alpha", "tags": ["old"]},
        ).json()["id"]
        mk.stub.calls.clear()
        resp = mk.client.put(
            f"/api/knowledge/{kid}",
            params={"user_id": UID_A},
            json={"content": "updated-beta"},
        )
        assert resp.status_code == 200, resp.text
        assert len(mk.stub.doc_posts()) == 1  # update 写面后真重索引（修前 0）
        doc = mk.stub.docs[kid]
        assert doc["content"] == "updated-beta"  # 同主键替换=旧文不再可搜
        assert doc["tags"] == ["old"]  # 未声明字段不洗

    def test_update_tags_only_reindexes_from_link_face(self, mk):
        kid = mk.client.post(
            "/api/knowledge/",
            params={"user_id": UID_A},
            json={"title": "t", "content": "c", "tags": ["old"]},
        ).json()["id"]
        mk.stub.calls.clear()
        resp = mk.client.put(
            f"/api/knowledge/{kid}",
            params={"user_id": UID_A},
            json={"tags": ["fresh-x", "fresh-y"]},
        )
        assert resp.status_code == 200, resp.text
        assert len(mk.stub.doc_posts()) == 1  # tags-only 也真重索引
        doc = mk.stub.docs[kid]
        # 修前 _row_to_doc 读 knowledge.tags 列（无写点恒 '[]'）→ tags 洗空；
        # 现读链接面=与 get/list/响应同一真源
        assert sorted(doc["tags"]) == ["fresh-x", "fresh-y"]
        assert sorted(doc["tags"]) == sorted(resp.json()["tags"])

    def test_update_title_reindexes(self, mk):
        kid = mk.client.post(
            "/api/knowledge/",
            params={"user_id": UID_A},
            json={"title": "old-title", "content": "c"},
        ).json()["id"]
        mk.stub.calls.clear()
        resp = mk.client.put(
            f"/api/knowledge/{kid}", params={"user_id": UID_A}, json={"title": "new-title"}
        )
        assert resp.status_code == 200, resp.text
        assert mk.stub.docs[kid]["title"] == "new-title"

    def test_update_metadata_only_reindexes(self, mk):
        kid = mk.client.post(
            "/api/knowledge/",
            params={"user_id": UID_A},
            json={"title": "t", "content": "c"},
        ).json()["id"]
        mk.stub.calls.clear()
        resp = mk.client.put(
            f"/api/knowledge/{kid}", params={"user_id": UID_A}, json={"metadata": {"k": "v"}}
        )
        assert resp.status_code == 200, resp.text
        assert len(mk.stub.doc_posts()) == 1  # 任何写面后恒重索引（行为锁死）
        assert mk.stub.docs[kid]["content"] == "c"

    def test_unowned_update_zero_side_effects(self, mk):
        kid = mk.client.post(
            "/api/knowledge/",
            params={"user_id": UID_A},
            json={"title": "t", "content": "victim", "tags": ["keep"]},
        ).json()["id"]
        mk.stub.calls.clear()
        resp = mk.client.put(
            f"/api/knowledge/{kid}", params={"user_id": UID_B}, json={"content": "hacked"}
        )
        assert resp.status_code == 404, resp.text
        assert mk.stub.doc_posts() == []  # 零索引写
        assert mk.stub.docs.get(kid) is None or mk.stub.docs[kid]["content"] == "victim"
        got = mk.client.get(f"/api/knowledge/{kid}", params={"user_id": UID_A})
        assert got.json()["content"] == "victim"  # 行零改写

    def test_empty_update_zero_calls(self, mk):
        kid = mk.client.post(
            "/api/knowledge/",
            params={"user_id": UID_A},
            json={"title": "t", "content": "c"},
        ).json()["id"]
        mk.stub.calls.clear()
        resp = mk.client.put(f"/api/knowledge/{kid}", params={"user_id": UID_A}, json={})
        assert resp.status_code == 200, resp.text
        assert mk.stub.calls == []  # 无写面=无索引调用

    def test_index_failure_not_blocking_update(self, mk):
        kid = mk.client.post(
            "/api/knowledge/",
            params={"user_id": UID_A},
            json={"title": "t", "content": "before", "tags": ["x"]},
        ).json()["id"]
        mk.stub.calls.clear()
        mk.stub.fail_documents = True
        resp = mk.client.put(
            f"/api/knowledge/{kid}", params={"user_id": UID_A}, json={"content": "after"}
        )
        assert resp.status_code == 200, resp.text  # 索引失败不阻断业务（fire-and-forget）
        assert len(mk.stub.doc_posts()) == 1  # 真尝试（接线了≠摆设）
        assert resp.json()["content"] == "after"  # 行真更新

    def test_meili_dead_port_not_blocking(self, mk, monkeypatch):
        from src.config import settings

        kid = mk.client.post(
            "/api/knowledge/",
            params={"user_id": UID_A},
            json={"title": "t", "content": "before"},
        ).json()["id"]
        monkeypatch.setattr(settings, "meili_url", "http://127.0.0.1:9")
        mk.stub.calls.clear()
        resp = mk.client.put(
            f"/api/knowledge/{kid}", params={"user_id": UID_A}, json={"content": "after"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["content"] == "after"


class TestOtherWriteFaces:
    def test_delete_removes_document(self, mk):
        kid = mk.client.post(
            "/api/knowledge/",
            params={"user_id": UID_A},
            json={"title": "t", "content": "c"},
        ).json()["id"]
        assert kid in mk.stub.docs
        mk.stub.calls.clear()
        resp = mk.client.delete(f"/api/knowledge/{kid}", params={"user_id": UID_A})
        assert resp.status_code == 200, resp.text
        assert kid not in mk.stub.docs
        deletes = [c for c in mk.stub.calls if c[0] == "DELETE"]
        assert len(deletes) == 1 and deletes[0][1].endswith(f"/documents/{kid}")

    def test_star_zero_reindex(self, mk):
        kid = mk.client.post(
            "/api/knowledge/",
            params={"user_id": UID_A},
            json={"title": "t", "content": "c"},
        ).json()["id"]
        mk.stub.calls.clear()
        resp = mk.client.post(f"/api/knowledge/{kid}/star", params={"user_id": UID_A})
        assert resp.status_code == 200, resp.text
        assert mk.stub.calls == []  # starred 不在索引 doc 面=零调用（行为锁死）


class TestFullReindexTagsSource:
    def test_full_reindex_tags_from_link_face_not_column(self, mk):
        # 列=stale、链接面=真源：全量重索引必须带链接面 tags（修前读列=洗空/错值）
        _seed(mk.db, "k-link", '["stale"]', ["real-a", "real-b"])
        _seed(mk.db, "k-nolink", '["stale2"]', [])
        resp = mk.client.post("/api/search/reindex")
        assert resp.status_code == 200, resp.text
        assert resp.json()["indexed"] == 2
        doc = mk.stub.docs["k-link"]
        assert sorted(doc["tags"]) == ["real-a", "real-b"]
        assert mk.stub.docs["k-nolink"]["tags"] == []

    def test_full_reindex_keeps_create_shape(self, mk):
        kid = mk.client.post(
            "/api/knowledge/",
            params={"user_id": UID_A},
            json={"title": "t", "content": "c", "tags": ["z"]},
        ).json()["id"]
        resp = mk.client.post("/api/search/reindex")
        assert resp.status_code == 200, resp.text
        # 全量重索引与 create/update 同一 doc 形状（同主键替换后零键丢失）
        assert set(mk.stub.docs[kid]) == {
            "id",
            "title",
            "content",
            "tags",
            "user_id",
            "content_type",
            "source",
            "created_at",
        }
        assert mk.stub.docs[kid]["tags"] == ["z"]
