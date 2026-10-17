"""F3b-⑨b-②b-1 vein 8 调用点接派生——push_event 带 bind_identity 派生租户键。

F3b-⑨b-② 留档候选「其余 41 HTTP 调用点分 organ 拆拍（vein 8/immune 6/mirror 5/…）」
拆件首件=vein 模块 8 调用点（F3b-⑨ census：vein 8 全在 src/api/vein.py，
upload/download/delete/update/rollback/promote/auto-process/batch 各 1）。

修前：8 调用点不传 tenant_id→事件 dict 无键→record() 'default' 兜底
（timeline/store.py record tenant_val），非 default 租户身份用户的 vein 文件
动作事件落 'default'→被自己的租户过滤查询挡掉=自动作自我不可见
（F3b-⑨ live 硬证同型、F3b-⑨b-② brain 面同型）。

数据面全 tmp（USERS_DB/ENTERPRISE_DB/tmp FileStore/tmp TimelineStore/死端口
_BASE/OCR 桩），绝不写生产 opensoul.db/enterprise.db/vein/timeline.db、
不打 live :8090。
"""

import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from jose import jwt

from src.config import settings
from src.nerve import event_bridge
from src.services import tenant_binding as tb

ENTERPRISE_DDL = """CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')))"""

KNOWLEDGE_DDL = [
    """CREATE TABLE knowledge (
        id TEXT PRIMARY KEY, user_id TEXT, title TEXT, content TEXT,
        source TEXT, content_type TEXT, metadata TEXT,
        created_at REAL, updated_at REAL)""",
    "CREATE TABLE tags (id TEXT PRIMARY KEY, name TEXT, user_id TEXT)",
    "CREATE TABLE knowledge_tags (knowledge_id TEXT, tag_id TEXT)",
]


def _ent_token(uid, username="u"):
    payload = {
        "sub": str(uid),
        "username": username,
        "scope": "enterprise",
        "exp": datetime.now(UTC) + timedelta(minutes=10),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _insert_ent_user(db, username, tenant, is_active=1):
    tb.ensure_user_tenant_columns(str(db))
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password, is_active, tenant_id) VALUES (?, ?, ?, ?)",
            (username, "hashed", is_active, tenant),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _rows(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    out = [dict(r) for r in con.execute("SELECT * FROM events ORDER BY timestamp, event_id")]
    con.close()
    return out


class _StubOCR:
    """OCR 桩：auto-process 面不需要真 OCR，只要非空文本走到 push_event。"""

    async def smart_image_to_text(self, data, language=None):
        return SimpleNamespace(text="stub ocr text", engine="stub-ocr")

    def image_to_text(self, data, lang=None):
        return SimpleNamespace(text="stub ocr text", engine="stub-ocr")


@pytest.fixture()
def vein_env(tmp_path, monkeypatch):
    """vein router + tmp 文件库/身份库/知识库/TimelineStore（绝不碰生产数据面）。"""
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.vein as vein_api
    import src.database.postgres as pg_mod
    from src.database.postgres import SQLitePool
    from src.timeline.store import TimelineStore
    from src.vein.cache import CacheManager
    from src.vein.chunked import ChunkedUploader
    from src.vein.file_store import FileStore

    # 文件库隔离（module 单例换 tmp 实例）
    monkeypatch.setattr(
        vein_api,
        "store",
        FileStore(root=str(tmp_path / "vein_files"), db_path=str(tmp_path / "vein_meta.db")),
    )
    monkeypatch.setattr(vein_api, "cache", CacheManager(max_size_mb=16, default_ttl=60))
    monkeypatch.setattr(vein_api, "uploader", ChunkedUploader(temp_dir=str(tmp_path / "uploads")))

    # 身份库隔离（bind_identity 真源绝不碰生产）
    monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    ent_db = tmp_path / "enterprise.db"
    conn = sqlite3.connect(ent_db)
    conn.execute(ENTERPRISE_DDL)
    conn.commit()
    conn.close()

    # 知识库隔离（promote/auto-process 落库面）
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'kb.db'}")
    conn = sqlite3.connect(tmp_path / "kb.db")
    for ddl in KNOWLEDGE_DDL:
        conn.execute(ddl)
    conn.commit()
    conn.close()
    tmp_pool = SQLitePool()
    monkeypatch.setattr(pg_mod, "db_pool", tmp_pool)

    # meilisearch 索引调度换成 no-op（绝不打网络）
    import src.services.meili_indexer as meili

    monkeypatch.setattr(meili, "schedule_index", lambda *a, **k: None)

    # OCR/网关桩（auto-process 面）
    import src.api.sense as sense_api
    import src.sense.ocr as ocr_mod

    monkeypatch.setattr(ocr_mod, "OCREngine", _StubOCR)
    monkeypatch.setattr(sense_api, "_get_gateway", lambda: None)

    # timeline 行归属真源=tmp（push_event._timeline 注入，绝不写生产 timeline.db）
    tl_db = str(tmp_path / "timeline.db")
    store = TimelineStore(tl_db)
    monkeypatch.setattr(event_bridge.push_event, "_timeline", store, raising=False)
    monkeypatch.setattr(event_bridge.emit, "_timeline", store, raising=False)
    # 死端口：nerve publish 快速连接失败（被 push_event/emit 自身吞掉），绝不打 live
    monkeypatch.setattr(event_bridge, "_BASE", "http://127.0.0.1:9")

    from src.api import event_stream

    event_stream._event_buffer.clear()

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()
        yield
        await tmp_pool.disconnect()

    app = FastAPI(lifespan=_lifespan)
    app.include_router(vein_api.router, prefix="/api/vein")
    with TestClient(app) as c:
        yield c, tl_db, ent_db, event_stream
    event_stream._event_buffer.clear()


def _auth(token):
    return {"Authorization": f"Bearer {token}"} if token else {}


def _upload(c, token=None, name="a.txt", content=b"hello vein", mime="text/plain"):
    return c.post(
        "/api/vein/upload",
        files={"file": (name, content, mime)},
        data={"tags": ""},
        headers=_auth(token),
    )


# ── ① 8 调用点真接派生 → record 行归属 ───────────────────────────


class TestVeinEmitTenantWiring:
    def test_upload_event_row_tenant_derived(self, vein_env):
        """认证派生租户→upload 事件行落派生租户（修前恒 'default' 自我不可见）。"""
        c, tl_db, ent_db, _es = vein_env
        uid = _insert_ent_user(ent_db, "alice", "tenant-vein-a")
        r = _upload(c, _ent_token(uid, "alice"))
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "file_uploaded"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-vein-a"  # 非 'default'=真接派生
        assert rows[0]["organ"] == "vein"

    def test_download_event_row_tenant_derived(self, vein_env):
        c, tl_db, ent_db, _es = vein_env
        uid = _insert_ent_user(ent_db, "bob", "tenant-vein-b")
        token = _ent_token(uid, "bob")
        fid = _upload(c, token).json()["file_id"]
        r = c.get(f"/api/vein/files/{fid}/download", headers=_auth(token))
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "file_downloaded"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-vein-b"

    def test_update_event_row_tenant_derived(self, vein_env):
        c, tl_db, ent_db, _es = vein_env
        uid = _insert_ent_user(ent_db, "carol", "tenant-vein-c")
        token = _ent_token(uid, "carol")
        fid = _upload(c, token).json()["file_id"]
        time.sleep(0.02)  # evt id 含 ms 时间戳，防同 ms 去重假阴
        r = c.put(
            f"/api/vein/files/{fid}/content",
            files={"file": ("a.txt", b"v2 content", "text/plain")},
            data={"change_summary": "v2"},
            headers=_auth(token),
        )
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "file_updated"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-vein-c"

    def test_rollback_event_row_tenant_derived(self, vein_env):
        c, tl_db, ent_db, _es = vein_env
        uid = _insert_ent_user(ent_db, "dave", "tenant-vein-d")
        token = _ent_token(uid, "dave")
        fid = _upload(c, token).json()["file_id"]
        c.put(
            f"/api/vein/files/{fid}/content",
            files={"file": ("a.txt", b"v2 content", "text/plain")},
            data={"change_summary": "v2"},
            headers=_auth(token),
        )
        time.sleep(0.02)
        r = c.post(f"/api/vein/files/{fid}/rollback/1", headers=_auth(token))
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "file_rollback"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-vein-d"

    def test_delete_event_row_tenant_derived(self, vein_env):
        c, tl_db, ent_db, _es = vein_env
        uid = _insert_ent_user(ent_db, "eve", "tenant-vein-e")
        token = _ent_token(uid, "eve")
        fid = _upload(c, token).json()["file_id"]
        time.sleep(0.02)
        r = c.delete(f"/api/vein/files/{fid}", headers=_auth(token))
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "file_deleted"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-vein-e"

    def test_promote_event_row_tenant_derived(self, vein_env):
        c, tl_db, ent_db, _es = vein_env
        uid = _insert_ent_user(ent_db, "frank", "tenant-vein-f")
        token = _ent_token(uid, "frank")
        fid = _upload(c, token).json()["file_id"]
        time.sleep(0.02)
        r = c.post(
            f"/api/vein/files/{fid}/promote", json={"user_id": "default"}, headers=_auth(token)
        )
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "vein.promoted"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-vein-f"

    def test_auto_process_event_row_tenant_derived(self, vein_env):
        c, tl_db, ent_db, _es = vein_env
        uid = _insert_ent_user(ent_db, "gina", "tenant-vein-g")
        token = _ent_token(uid, "gina")
        fid = _upload(c, token, name="x.png", content=b"\x89PNG stub", mime="image/png").json()[
            "file_id"
        ]
        time.sleep(0.02)
        r = c.post(f"/api/vein/files/{fid}/auto-process", json={}, headers=_auth(token))
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "file_auto_processed"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-vein-g"

    def test_batch_event_row_tenant_derived(self, vein_env):
        c, tl_db, ent_db, _es = vein_env
        uid = _insert_ent_user(ent_db, "hank", "tenant-vein-h")
        token = _ent_token(uid, "hank")
        _upload(c, token)  # text/plain 不在批处理资格内=0 processed 也照发事件
        time.sleep(0.02)
        r = c.post("/api/vein/auto-process/batch", json={"limit": 50}, headers=_auth(token))
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "batch_auto_processed"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-vein-h"

    def test_buffer_dict_carries_tenant_key(self, vein_env):
        """事件 dict 自带键（bus/缓冲/record 同源注入）——⑨b-① 三处注入真流经调用点。"""
        c, _tl, ent_db, es = vein_env
        uid = _insert_ent_user(ent_db, "iris", "tenant-vein-i")
        _upload(c, _ent_token(uid, "iris"))
        buffered = [e for e in es._event_buffer if e.get("type") == "file_uploaded"]
        assert len(buffered) == 1
        assert buffered[0]["tenant_id"] == "tenant-vein-i"

    def test_dual_tenant_rows_isolated(self, vein_env):
        """双租户各自落行互不串键。"""
        c, tl_db, ent_db, _es = vein_env
        uid_a = _insert_ent_user(ent_db, "u_a", "tenant-dual-a")
        uid_b = _insert_ent_user(ent_db, "u_b", "tenant-dual-b")
        assert _upload(c, _ent_token(uid_a, "u_a"), name="a.txt").status_code == 200
        time.sleep(0.02)
        assert _upload(c, _ent_token(uid_b, "u_b"), name="b.txt").status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "file_uploaded"]
        assert sorted(x["tenant_id"] for x in rows) == ["tenant-dual-a", "tenant-dual-b"]


# ── ② 不变面（契约锁死） ─────────────────────────────────────────


class TestVeinEmitTenantInvariants:
    def test_anonymous_default_contract_unchanged(self, vein_env):
        """匿名='default' 行值不变+响应契约逐键不变（接派生不改响应面）。"""
        c, tl_db, _ent, _es = vein_env
        r = _upload(c)
        assert r.status_code == 200
        data = r.json()
        for key in ("file_id", "name", "content_hash", "size", "mime_type", "tags"):
            assert key in data
        rows = [x for x in _rows(tl_db) if x["event_type"] == "file_uploaded"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "default"

    def test_bad_token_401_zero_event(self, vein_env):
        """坏 token=401（bind_identity 闸门），零事件行落库（身份证明先于一切）。"""
        c, tl_db, _ent, _es = vein_env
        r = _upload(c, "not-a-jwt-token")
        assert r.status_code == 401
        assert _rows(tl_db) == []

    def test_all_eight_call_sites_wired(self):
        """静态接线普查：handler 全带派生、调用点全传租户键（写了必须接线）。

        契约随更新（DEC-⑦-④ files 子搜索存储层加轴）：修前 8/8（emit 面）；
        本拍新增 list_files 与 chunked complete 两 handler 落 bind_identity（+2）
        + store 写点/过滤/闸门 6 处带键（upload/update/complete 落行、list 过滤、
        delete/rollback 归属闸）——push_event emit 面 8 处带键零变化（+6 全在
        store 面），总数 10 handler / 14 键。
        契约随更新（DEC-⑦-⑩ by-ID 读面归属闸）：get_file_meta/versions×2 三
        handler 落 bind_identity（+3），by-ID 读面闸 12 处带键（meta/download×2/
        versions×2/promote×2/auto-process×2/batch list+逐行 retrieve——push_event
        emit 面零变化），总数 13 handler / 26 键。
        契约随更新（DEC-⑦-㉕ chunked 上传会话面）：init/chunk/status/list/cancel
        五 handler 落 bind_identity（+5，complete 既有），会话面闸 8 处带键
        （init 落行、chunk get+upload 双、complete get+assemble 双、status、
        list 过滤、cancel——push_event emit 面零变化），总数 18 handler / 34 键。
        契约随更新（DEC-⑦-㉕b 缓存 API 键面）：get/put/delete/clear/cleanup
        五 cache handler 落 bind_identity（+5，stats 系统视图不带），键 7 处
        （五 handler 各 1 + download 缓存读/delete 缓存失效两内部维护点带行
        租户键——push_event emit 面零变化），总数 23 handler / 41 键。
        契约随更新（T3-⑤ version_count 计数面接线）：update 响应 version_count
        计数走 get_version_count 读面带行租户键（+1，修前 len(get_version_history)
        系统视图零键）——push_event emit 面零变化，总数 23 handler / 42 键。
        """
        src = (Path(__file__).parent.parent / "src" / "api" / "vein.py").read_text(encoding="utf-8")
        assert src.count("identity = bind_identity(authorization)") == 23
        assert src.count("tenant_id=identity.tenant_id") == 42
