"""unified files 子搜索存储层加轴测试（DEC-⑦-④：files 面最小件）。

修前（HEAD 源码实证）：_search_files 读 FileStore.list_files 零租户过滤=任意租户
可见全部文件（与 timeline ③c-2① / events DEC-⑦-① / echo ⑦-② / gene ⑦-③ 已收
的查询过滤不对称=跨租户泄露）；且 File 行无租户键、写点（upload/update/chunked
complete/pipeline）零身份轴=行归属无从谈起，update/delete/rollback 按 file_id
跨租户改删零闸。收口=file_row_tenant 单一判法（文件自带键缺失/空白→'default'
兜底，⑦-① _event_row_tenant / ② message_row_tenant / ③ template_row_tenant
同款；写读同一判法）+files.tenant_id 行键（DDL+ALTER 双轨幂等迁移回填
'default'，③c-2① 索引补列之后建坑延续）+写点行归属（store(tenant_id=) =
bind_identity 派生租户、缺省→'default' 系统写方）+劫持闸（跨租户按 file_id
覆盖=FileTenantDenied→403 行零改写，⑦-③ 同款）+delete/rollback 归属闸（跨租户
=零副作用与 not-found 同契约不差分泄露）+list_files(tenant_id=) 恒按行租户键
过滤（None=系统视图字节不变；过滤先于截断=limit 只计可见命中）+_search_files
恒按派生租户过滤+读真源=src.api.vein 模块单例 store。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB/HOME 归 tmp——FileStore 缺省路径
expanduser 随 HOME，控制组旧代码函数内 new FileStore() 也读同一 tmp 真源，
泄露暴露在行为断言而非空集假象；绝不碰生产 ~/opensoul/data/vein）。
"""

import hashlib
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

USERS_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "password_hash TEXT, is_active INTEGER NOT NULL DEFAULT 1, "
    "tenant_id TEXT NOT NULL DEFAULT 'default')"
)

OLD_FILES_DDL = (
    "CREATE TABLE files (file_id TEXT PRIMARY KEY, name TEXT NOT NULL, "
    "content_hash TEXT NOT NULL, size INTEGER NOT NULL, "
    "mime_type TEXT DEFAULT 'application/octet-stream', tags TEXT DEFAULT '', "
    "created_at REAL NOT NULL, ref_count INTEGER DEFAULT 1)"
)


def _seed_user(db_path, uid, username, tenant="default"):
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, ?)",
            (uid, username, tenant),
        )
        c.commit()
    return uid


def _token(uid):
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid), role="user")
    return {"Authorization": "Bearer " + tok}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 身份库 + HOME 归 tmp（FileStore 缺省数据面随 tmp）+ src.api.vein
    模块单例 store=tmp 实例 + 真 search/vein router 真 bind_identity。
    搜索与管理面写口读同一真源实例。"""
    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    # HOME 对齐：旧代码 _search_files 函数内 new FileStore() 解析
    # ~/opensoul/data/vein（expanduser 随 HOME）——默认存储归 tmp，控制组
    # 旧代码才读得到 seed 行（泄露暴露在行为断言而非空集假象）。
    monkeypatch.setenv("HOME", str(tmp_path))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    import src.api.search as search_api
    import src.api.vein as vein_api
    from src.vein.file_store import FileStore

    fresh = FileStore()  # 缺省路径随 HOME=tmp → tmp 数据面（禁碰生产）
    monkeypatch.setattr(vein_api, "store", fresh)

    app = FastAPI()
    app.include_router(search_api.router, prefix="/api/search")
    app.include_router(vein_api.router, prefix="/api/vein")
    client = TestClient(app)

    user_tenant_a = _seed_user(users_db, str(uuid.uuid4()), "alice-a", "tenant-a")
    user_tenant_b = _seed_user(users_db, str(uuid.uuid4()), "bob-b", "tenant-b")
    user_default = _seed_user(users_db, str(uuid.uuid4()), "carol-default", "default")

    yield {
        "client": client,
        "store": fresh,
        "meta_db": str(tmp_path / "opensoul" / "data" / "vein" / "vein_meta.db"),
        "user_tenant_a": user_tenant_a,
        "user_tenant_b": user_tenant_b,
        "user_default": user_default,
    }


def _upload(client, headers, name, content):
    r = client.post(
        "/api/vein/upload",
        files={"file": (name, content, "text/plain")},
        headers=headers or {},
    )
    assert r.status_code == 200
    return r.json()["file_id"]


def _set_row_tenant(meta_db, file_id, tenant):
    """直写行键（写点归属另有专测；⑦-③ setattr 直写同款控制组纪律）。

    旧代码无租户列→OperationalError：行即「无键」=修前真态，让泄露暴露在
    行为断言而非 fixture TypeError。
    """
    try:
        with sqlite3.connect(meta_db) as c:
            c.execute("UPDATE files SET tenant_id=? WHERE file_id=?", (tenant, file_id))
            c.commit()
    except sqlite3.OperationalError:
        pass


def _row_tenant(meta_db, file_id):
    with sqlite3.connect(meta_db) as c:
        row = c.execute("SELECT tenant_id FROM files WHERE file_id=?", (file_id,)).fetchone()
    return row[0] if row else None


def _row_alive(meta_db, file_id):
    with sqlite3.connect(meta_db) as c:
        row = c.execute("SELECT COUNT(*) FROM files WHERE file_id=?", (file_id,)).fetchone()
    return row[0] == 1


def _search(client, headers, params=None):
    p = {"q": "probe", "sources": "files", "limit": 10}
    p.update(params or {})
    r = client.get("/api/search/unified", params=p, headers=headers or {})
    assert r.status_code == 200
    return [m["title"] for m in r.json()["by_source"]["files"]]


# ── 迁移与单一判法 ─────────────────────────────────────────


class TestMigrationAndRowTenant:
    def test_old_schema_backfill_and_index(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        from src.vein.file_store import FileStore

        db = tmp_path / "old" / "vein_meta.db"
        db.parent.mkdir(parents=True)
        with sqlite3.connect(db) as c:
            c.execute(OLD_FILES_DDL)
            c.execute(
                "INSERT INTO files (file_id, name, content_hash, size, created_at) "
                "VALUES ('f1', 'legacy.txt', 'h1', 3, 1.0)"
            )
            c.commit()
        store = FileStore(root=str(tmp_path / "old" / "files"), db_path=str(db))
        cols = {r[1] for r in store._db.execute("PRAGMA table_info(files)")}
        assert "tenant_id" in cols  # ALTER 自愈补列
        meta = store.get_meta("f1")
        assert meta is not None
        assert meta.tenant_id == "default"  # 存量行回填 'default'
        idx = {r[1] for r in store._db.execute("PRAGMA index_list(files)")}
        assert "idx_files_tenant" in idx  # 索引补列之后建（③c-2① 坑延续）
        # 幂等：二次构造零报错零重复列
        store2 = FileStore(root=str(tmp_path / "old" / "files"), db_path=str(db))
        cols2 = {r[1] for r in store2._db.execute("PRAGMA table_info(files)")}
        assert cols2 == cols

    def test_file_row_tenant_fallback(self):
        from src.vein.file_store import FileMeta, file_row_tenant

        m_missing = FileMeta("f", "n", "h", 1, "t", "", 1.0, 1)
        assert file_row_tenant(m_missing) == "default"
        m_blank = FileMeta("f", "n", "h", 1, "t", "", 1.0, 1, "   ")
        assert file_row_tenant(m_blank) == "default"
        m_real = FileMeta("f", "n", "h", 1, "t", "", 1.0, 1, "tenant-a")
        assert file_row_tenant(m_real) == "tenant-a"


# ── 存储层：写点归属/劫持闸/归属闸/过滤 ─────────────────────


class TestStoreAxis:
    def test_store_row_attribution_and_dedup(self, env):
        store = env["store"]
        m1 = store.store(data=b"same-bytes", name="a.txt", tenant_id="tenant-a")
        assert m1.tenant_id == "tenant-a"
        assert _row_tenant(env["meta_db"], m1.file_id) == "tenant-a"
        m2 = store.store(data=b"same-bytes", name="b.txt", tenant_id="tenant-b")
        assert m2.tenant_id == "tenant-b"
        assert m2.file_id != m1.file_id
        assert m2.content_hash == m1.content_hash  # blob 共享=内容寻址既有语义
        assert _row_tenant(env["meta_db"], m2.file_id) == "tenant-b"  # 各落各键
        m3 = store.store(data=b"no-tenant", name="c.txt")
        assert m3.tenant_id == "default"  # 缺省兜底=系统写方（键派生故障不掉落库）

    def test_update_sticky_and_hijack_gate(self, env):
        from src.vein.file_store import FileTenantDenied

        store = env["store"]
        m = store.store(data=b"v1", name="k.txt", tenant_id="tenant-a")
        m2 = store.store(data=b"v2x", name="k2.txt", file_id=m.file_id, tenant_id="tenant-a")
        assert _row_tenant(env["meta_db"], m.file_id) == "tenant-a"  # 粘性不洗键
        assert m2.tenant_id == "tenant-a"
        with pytest.raises(FileTenantDenied):
            store.store(data=b"evil", name="hijack.txt", file_id=m.file_id, tenant_id="tenant-b")
        meta = store.get_meta(m.file_id)
        assert meta.name == "k2.txt"  # 行零改写
        assert meta.content_hash == hashlib.sha256(b"v2x").hexdigest()

    def test_delete_gate(self, env):
        store = env["store"]
        m = store.store(data=b"del", name="d.txt", tenant_id="tenant-a")
        assert store.delete(m.file_id, tenant_id="tenant-b") is False
        assert store.get_meta(m.file_id) is not None  # 行存活零副作用
        assert store.delete(m.file_id, tenant_id="tenant-a") is True
        assert store.get_meta(m.file_id) is None

    def test_rollback_gate(self, env):
        store = env["store"]
        m = store.store(data=b"v1", name="r.txt", tenant_id="tenant-a")
        store.store(data=b"v2xx", name="r.txt", file_id=m.file_id, tenant_id="tenant-a")
        store.store(data=b"v3xxx", name="r.txt", file_id=m.file_id, tenant_id="tenant-a")
        # 版本记的是每次更新的新内容（v2xx=版本1、v3xxx=版本2；v1 初始无版本行）
        assert store.rollback_to_version(m.file_id, 1, tenant_id="tenant-b") is None
        meta = store.get_meta(m.file_id)
        assert meta.content_hash == hashlib.sha256(b"v3xxx").hexdigest()  # 零副作用
        assert store.rollback_to_version(m.file_id, 1, tenant_id="tenant-a") is not None
        assert store.get_meta(m.file_id).content_hash == hashlib.sha256(b"v2xx").hexdigest()

    def test_list_filter_before_limit(self, env):
        store = env["store"]
        store.store(data=b"d1", name="probe-td-1.txt")  # default 先（旧，降序在后）
        store.store(data=b"a1", name="probe-ta-1.txt", tenant_id="tenant-a")  # 后（新在前）
        visible_default = store.list_files(name_filter="probe", limit=1, tenant_id="default")
        # 过滤先于截断：修前名额被更新的跨租户行占掉（降序首条=ta 行）
        assert [f.name for f in visible_default] == ["probe-td-1.txt"]
        visible_a = store.list_files(name_filter="probe", limit=1, tenant_id="tenant-a")
        assert [f.name for f in visible_a] == ["probe-ta-1.txt"]
        assert len(store.list_files(name_filter="probe")) == 2  # None=系统视图字节不变


# ── API 写点归属 + 闸门全链（bind_identity 派生） ────────────


class TestApiAxis:
    def test_upload_attribution_and_additive_keys(self, env):
        client, meta_db = env["client"], env["meta_db"]
        fa = _upload(client, _token(env["user_tenant_a"]), "probe-ta-up.txt", b"a")
        assert _row_tenant(meta_db, fa) == "tenant-a"
        fd = _upload(client, None, "probe-td-up.txt", b"d")
        assert _row_tenant(meta_db, fd) == "default"  # 匿名 'default' 契约不变
        r = client.get("/api/vein/files", headers=_token(env["user_tenant_a"]))
        assert r.status_code == 200
        entry = next(x for x in r.json()["files"] if x["file_id"] == fa)
        assert entry["tenant_id"] == "tenant-a"  # 加性键=行归属可观测（归一值）

    def test_list_three_way_isolation(self, env):
        client = env["client"]
        _upload(client, _token(env["user_tenant_a"]), "probe-ta-l.txt", b"a")
        _upload(client, _token(env["user_tenant_b"]), "probe-tb-l.txt", b"b")
        _upload(client, None, "probe-td-l.txt", b"d")

        def names(headers):
            r = client.get("/api/vein/files", params={"name": "probe"}, headers=headers)
            assert r.status_code == 200
            return sorted(x["name"] for x in r.json()["files"])

        assert names(_token(env["user_tenant_a"])) == ["probe-ta-l.txt"]
        assert names(_token(env["user_tenant_b"])) == ["probe-tb-l.txt"]
        assert names(None) == ["probe-td-l.txt"]

    def test_update_hijack_403(self, env):
        client, meta_db = env["client"], env["meta_db"]
        fa = _upload(client, _token(env["user_tenant_a"]), "probe-ta-upd.txt", b"v1")
        r = client.put(
            "/api/vein/files/" + fa + "/content",
            files={"file": ("probe-ta-upd.txt", b"evil", "text/plain")},
            headers=_token(env["user_tenant_b"]),
        )
        assert r.status_code == 403  # 劫持闸（⑦-③ 同款 403 非 400）
        with sqlite3.connect(meta_db) as c:
            row = c.execute("SELECT content_hash FROM files WHERE file_id=?", (fa,)).fetchone()
        assert row[0] == hashlib.sha256(b"v1").hexdigest()  # 行零改写
        r = client.put(
            "/api/vein/files/" + fa + "/content",
            files={"file": ("probe-ta-upd.txt", b"v2ok", "text/plain")},
            headers=_token(env["user_tenant_a"]),
        )
        assert r.status_code == 200
        assert _row_tenant(meta_db, fa) == "tenant-a"  # 同租户更新粘性

    def test_delete_cross_tenant_404(self, env):
        client, meta_db = env["client"], env["meta_db"]
        fa = _upload(client, _token(env["user_tenant_a"]), "probe-ta-del.txt", b"a")
        r = client.delete("/api/vein/files/" + fa, headers=_token(env["user_tenant_b"]))
        assert r.status_code == 404  # 与 not-found 同契约不差分泄露
        assert _row_alive(meta_db, fa)  # 行存活零副作用
        r = client.delete("/api/vein/files/" + fa, headers=_token(env["user_tenant_a"]))
        assert r.status_code == 200
        assert not _row_alive(meta_db, fa)

    def test_rollback_cross_tenant_404(self, env):
        client, meta_db = env["client"], env["meta_db"]
        fa = _upload(client, _token(env["user_tenant_a"]), "probe-ta-rb.txt", b"v1")
        r = client.put(
            "/api/vein/files/" + fa + "/content",
            files={"file": ("probe-ta-rb.txt", b"v2xx", "text/plain")},
            headers=_token(env["user_tenant_a"]),
        )
        assert r.status_code == 200
        r = client.put(
            "/api/vein/files/" + fa + "/content",
            files={"file": ("probe-ta-rb.txt", b"v3xxx", "text/plain")},
            headers=_token(env["user_tenant_a"]),
        )
        assert r.status_code == 200
        r = client.post(
            "/api/vein/files/" + fa + "/rollback/1",
            headers=_token(env["user_tenant_b"]),
        )
        assert r.status_code == 404  # 跨租户=not-found 同契约
        with sqlite3.connect(meta_db) as c:
            row = c.execute("SELECT content_hash FROM files WHERE file_id=?", (fa,)).fetchone()
        assert row[0] == hashlib.sha256(b"v3xxx").hexdigest()  # 零副作用
        r = client.post(
            "/api/vein/files/" + fa + "/rollback/1",
            headers=_token(env["user_tenant_a"]),
        )
        assert r.status_code == 200
        with sqlite3.connect(meta_db) as c:
            row = c.execute("SELECT content_hash FROM files WHERE file_id=?", (fa,)).fetchone()
        # 版本1=第一次更新的新内容 v2xx（版本记每次更新的新内容）
        assert row[0] == hashlib.sha256(b"v2xx").hexdigest()

    def test_bad_token_401(self, env):
        r = env["client"].get(
            "/api/vein/files", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert r.status_code == 401


# ── unified files 子搜索：恒按派生租户过滤 ──────────────────


class TestUnifiedFilesSearch:
    def test_cross_tenant_hidden(self, env):
        client, meta_db = env["client"], env["meta_db"]
        fa = _upload(client, _token(env["user_tenant_a"]), "probe-ta-s.txt", b"a")
        fb = _upload(client, _token(env["user_tenant_b"]), "probe-tb-s.txt", b"b")
        fd = _upload(client, None, "probe-td-s.txt", b"d")
        _set_row_tenant(meta_db, fa, "tenant-a")
        _set_row_tenant(meta_db, fb, "tenant-b")
        _set_row_tenant(meta_db, fd, "default")

        assert _search(client, _token(env["user_tenant_a"])) == ["probe-ta-s.txt"]
        assert _search(client, _token(env["user_tenant_b"])) == ["probe-tb-s.txt"]
        assert _search(client, None) == ["probe-td-s.txt"]  # 匿名 'default' 契约不变

    def test_filter_before_limit(self, env):
        client, meta_db = env["client"], env["meta_db"]
        fd = _upload(client, None, "probe-td-q.txt", b"d")  # 先（旧，降序在后）
        fa = _upload(client, _token(env["user_tenant_a"]), "probe-ta-q.txt", b"a")  # 后（新在前）
        _set_row_tenant(meta_db, fd, "default")
        _set_row_tenant(meta_db, fa, "tenant-a")
        # limit=1：修前降序首条=ta 行，default 询问被跨租户行占名额（泄露+挤占）
        assert _search(client, None, {"limit": 1}) == ["probe-td-q.txt"]
        assert _search(client, _token(env["user_tenant_a"]), {"limit": 1}) == ["probe-ta-q.txt"]

    def test_result_shape_unchanged(self, env):
        client, meta_db = env["client"], env["meta_db"]
        fa = _upload(client, None, "probe-td-shape.txt", b"d")
        _set_row_tenant(meta_db, fa, "default")
        r = client.get(
            "/api/search/unified",
            params={"q": "probe", "sources": "files", "limit": 10},
        )
        assert r.status_code == 200
        entries = r.json()["by_source"]["files"]
        assert entries, "files 子搜索应有命中"
        assert set(entries[0].keys()) == {
            "source",
            "icon",
            "title",
            "snippet",
            "file_id",
            "size",
            "mime_type",
            "created_at",
        }

    def test_search_reads_module_singleton(self, env, monkeypatch):
        """读真源收口：_search_files 禁再函数内 new FileStore()（⑦-②③ 同款）。

        修前逐调用构造（旧代码在此探针下炸出空集=收口前真态）。
        """
        client, meta_db = env["client"], env["meta_db"]
        fd = _upload(client, None, "probe-td-src.txt", b"d")
        _set_row_tenant(meta_db, fd, "default")

        import src.vein.file_store as vein_store_mod

        def _boom(*args, **kwargs):
            raise AssertionError("修前 _search_files 函数内 new FileStore()——收口后禁再构造")

        monkeypatch.setattr(vein_store_mod, "FileStore", _boom)
        assert _search(client, None) == ["probe-td-src.txt"]
