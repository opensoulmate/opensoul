"""DEC-⑦-㉕b vein 缓存 API 键面（CacheManager + /api/vein/cache 全面）行租户键加轴测试。

修前（DEC-⑦-⑩/㉕ 留档「chunked 上传会话与缓存 API 键面无行键」拆件第二件
+HEAD 源码实证）：缓存全局单键空间、零行键零归属闸——
① PUT /cache 写点零身份轴（任意人写任意 key）
② GET /cache/{key} 全局可读（file:{file_id} 热缓存行=他人文件内容 hex
   前 100 字符直接可读，绕开 ⑦-⑩ 文件行闸的旁路面）
③ DELETE /cache/{key} 全局可删（删他人缓存行）
④ POST /cache/clear 全局清（变相清全史）
⑤ 同 key 异租户互覆盖（全局单键空间）

收口=DEC-⑦ 家族同款（⑦-㉕ upload_session_row_tenant 先例）：
① cache_row_tenant 行租户键单一判法（缺失/空白→'default' 兜底）
② CacheEntry.tenant_id 行键 + 内建复合索引键 (tenant, key)（同 key 异租户
   =两个独立行互不覆盖 ③a 幂等键隔离同款）+ 写点归属（bind_identity 派生、
   自动 file: 热缓存=文件行租户键）
③ by-ID 归属闸（结构性复合键精确查）：跨租户与 miss 同 404 同 detail
   不差分泄露（③c-1）、删闸行零破坏（③b-3）
④ clear/cleanup bulk 清理恒按行租户键（防变相清全史 ③a purge 纪律）
⑤ stats 系统视图（health 计数字节不变）。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB/文件库/缓存/上传临时面），绝不碰
生产数据面，不打 live :8090。
"""

import base64
import sqlite3
import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

USERS_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "password_hash TEXT, is_active INTEGER NOT NULL DEFAULT 1, "
    "tenant_id TEXT NOT NULL DEFAULT 'default')"
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
    return {"Authorization": f"Bearer {tok}"}


BAD_TOKEN = {"Authorization": "Bearer not-a-real-token"}

CACHE_MISS_DETAIL = "Cache miss or expired"
CACHE_ABSENT_DETAIL = "Key not in cache"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 文件库/缓存/上传临时面 + tmp 身份库 + 真 router 真 bind_identity。"""
    import src.api.vein as vein_api
    from src.vein.cache import CacheManager
    from src.vein.chunked import ChunkedUploader
    from src.vein.file_store import FileStore

    monkeypatch.setattr(
        vein_api,
        "store",
        FileStore(root=str(tmp_path / "vein_files"), db_path=str(tmp_path / "vein_meta.db")),
    )
    monkeypatch.setattr(vein_api, "cache", CacheManager(max_size_mb=16, default_ttl=60))
    monkeypatch.setattr(vein_api, "uploader", ChunkedUploader(temp_dir=str(tmp_path / "uploads")))

    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    app = FastAPI()
    app.include_router(vein_api.router, prefix="/api/vein")
    client = TestClient(app)

    user_a = _seed_user(users_db, str(uuid.uuid4()), "cache-alice", "tenant-a")
    user_b = _seed_user(users_db, str(uuid.uuid4()), "cache-bob", "tenant-b")
    user_default = _seed_user(users_db, str(uuid.uuid4()), "cache-carol", "default")

    yield {
        "client": client,
        "cache": vein_api.cache,
        "user_a": user_a,
        "user_b": user_b,
        "user_default": user_default,
        "h_a": _token(user_a),
        "h_b": _token(user_b),
        "h_default": _token(user_default),
    }


def _put(c, key, raw: bytes, headers):
    return c.put(
        "/api/vein/cache",
        json={"key": key, "data": base64.b64encode(raw).decode()},
        headers=headers,
    )


def _get(c, key, headers=None):
    return c.get(f"/api/vein/cache/{key}", headers=headers)


def _del(c, key, headers=None):
    return c.delete(f"/api/vein/cache/{key}", headers=headers)


# ── ① 行租户键单一判法 ─────────────────────────────────────


def test_cache_row_tenant_single_source():
    from src.vein.cache import CacheEntry, cache_row_tenant

    assert cache_row_tenant({"tenant_id": "  tenant-a  "}) == "tenant-a"
    assert cache_row_tenant("tenant-b") == "tenant-b"
    entry = CacheEntry(key="k", data=b"x", size=1, created_at=0.0, ttl=1.0, tenant_id="tenant-c")
    assert cache_row_tenant(entry) == "tenant-c"
    assert cache_row_tenant({}) == "default"
    assert cache_row_tenant({"tenant_id": ""}) == "default"
    assert cache_row_tenant({"tenant_id": "   "}) == "default"
    assert cache_row_tenant(None) == "default"


# ── ② 写点归属 ─────────────────────────────────────────────


def test_put_write_attribution_additive_key(env):
    c = env["client"]
    r = _put(c, "k-attr", b"hello", env["h_a"])
    assert r.status_code == 200
    assert r.json()["tenant_id"] == "tenant-a"
    entry = env["cache"].get_entry("k-attr", tenant_id="tenant-a")
    assert entry is not None
    assert entry.tenant_id == "tenant-a"

    r2 = _put(c, "k-anon", b"anon", None)
    assert r2.status_code == 200
    assert r2.json()["tenant_id"] == "default"
    assert env["cache"].get_entry("k-anon", tenant_id="default") is not None


def test_put_same_tenant_overwrite_unchanged(env):
    c = env["client"]
    _put(c, "k-ow", b"first", env["h_a"])
    r = _put(c, "k-ow", b"second", env["h_a"])
    assert r.status_code == 200
    entry = env["cache"].get_entry("k-ow", tenant_id="tenant-a")
    assert entry.data == b"second"
    rows = [ik for ik in env["cache"]._cache if ik == ("tenant-a", "k-ow")]
    assert len(rows) == 1


# ── ③ by-ID 归属闸（get）────────────────────────────────────


def test_get_gate_cross_tenant_same_detail_as_miss(env):
    c = env["client"]
    r = _put(c, "k-get", b"secret-bytes", env["h_a"])
    assert r.status_code == 200

    owner = _get(c, "k-get", env["h_a"])
    assert owner.status_code == 200
    body = owner.json()
    assert body["key"] == "k-get"
    assert body["data"].startswith(b"secret-bytes".hex())
    assert body["tenant_id"] == "tenant-a"

    intruder = _get(c, "k-get", env["h_b"])
    assert intruder.status_code == 404
    miss = _get(c, "k-nope", env["h_b"])
    assert miss.status_code == 404
    # 不差分泄露：跨租户与真 miss 同 detail（③c-1）
    assert intruder.json()["detail"] == miss.json()["detail"] == CACHE_MISS_DETAIL

    # 匿名 default 租户同样看不到 tenant-a 行
    assert _get(c, "k-get", None).status_code == 404


def test_get_bad_token_401(env):
    c = env["client"]
    _put(c, "k-bt", b"x", env["h_a"])
    assert _get(c, "k-bt", BAD_TOKEN).status_code == 401


# ── ③ 删闸：同 detail + 行零破坏 ─────────────────────────────


def test_delete_gate_cross_tenant_zero_destruction(env):
    c = env["client"]
    _put(c, "k-del", b"keep-me", env["h_a"])

    intruder = _del(c, "k-del", env["h_b"])
    assert intruder.status_code == 404
    miss = _del(c, "k-nope", env["h_b"])
    assert miss.status_code == 404
    assert intruder.json()["detail"] == miss.json()["detail"] == CACHE_ABSENT_DETAIL

    # 行零破坏：他人缓存行一个字节不动（③b-3）
    assert _get(c, "k-del", env["h_a"]).status_code == 200

    owner = _del(c, "k-del", env["h_a"])
    assert owner.status_code == 200
    assert _get(c, "k-del", env["h_a"]).status_code == 404


# ── ④ 同 key 异租户独立行 ───────────────────────────────────


def test_same_key_two_tenants_independent_rows(env):
    c = env["client"]
    _put(c, "k-shared", b"AAA", env["h_a"])
    _put(c, "k-shared", b"BBB", env["h_b"])

    # 修前全局单键空间：B 覆盖 A，A 读到 BBB；现=两独立行互不覆盖
    a = _get(c, "k-shared", env["h_a"])
    b = _get(c, "k-shared", env["h_b"])
    assert a.status_code == 200 and a.json()["data"].startswith(b"AAA".hex())
    assert b.status_code == 200 and b.json()["data"].startswith(b"BBB".hex())
    rows = [ik for ik in env["cache"]._cache if ik[1] == "k-shared"]
    assert sorted(rows) == [("tenant-a", "k-shared"), ("tenant-b", "k-shared")]

    # 跨租户删=行零改写（B 删不动 A 行）
    assert _del(c, "k-shared", env["h_b"]).status_code == 200
    assert _get(c, "k-shared", env["h_a"]).status_code == 200
    assert _get(c, "k-shared", env["h_b"]).status_code == 404


# ── ⑤ bulk 清理恒按行租户键 ─────────────────────────────────


def test_clear_scoped_to_caller_tenant(env):
    c = env["client"]
    _put(c, "k-5", b"1", env["h_a"])
    _put(c, "k-6", b"2", env["h_a"])
    _put(c, "k-7", b"3", env["h_b"])
    _put(c, "k-8", b"4", None)

    r = c.post("/api/vein/cache/clear", headers=env["h_a"])
    assert r.status_code == 200
    body = r.json()
    assert body["cleared"] == 2
    assert body["tenant_id"] == "tenant-a"

    # 防"只给租户"变相清全史：他租户行一律不动（③a purge 同款）
    assert _get(c, "k-5", env["h_a"]).status_code == 404
    assert _get(c, "k-6", env["h_a"]).status_code == 404
    assert _get(c, "k-7", env["h_b"]).status_code == 200
    assert _get(c, "k-8", None).status_code == 200

    # 匿名清=default 范围，tenant-b 行仍在
    r2 = c.post("/api/vein/cache/clear", headers=None)
    assert r2.status_code == 200 and r2.json()["cleared"] == 1
    assert _get(c, "k-7", env["h_b"]).status_code == 200


def test_cleanup_expired_scoped_to_caller_tenant(env):
    c = env["client"]
    cache = env["cache"]
    cache.put("e-a", b"x", ttl=0.001, tenant_id="tenant-a")
    cache.put("e-b", b"y", ttl=0.001, tenant_id="tenant-b")
    cache.put("f-a", b"z", ttl=60, tenant_id="tenant-a")
    time.sleep(0.02)

    r = c.post("/api/vein/cache/cleanup", headers=env["h_a"])
    assert r.status_code == 200
    assert r.json()["removed"] == 1
    # 跨租户过期行不代删（bulk 恒按行租户键）
    assert ("tenant-b", "e-b") in cache._cache
    assert ("tenant-a", "f-a") in cache._cache
    assert ("tenant-a", "e-a") not in cache._cache

    r2 = c.post("/api/vein/cache/cleanup", headers=env["h_b"])
    assert r2.json()["removed"] == 1
    assert ("tenant-b", "e-b") not in cache._cache


# ── ⑥ file: 热缓存行归属（⑦-⑩ 文件行闸的缓存旁路面收口）──────


def test_file_autocache_row_attribution_and_cache_face_gate(env):
    c = env["client"]
    up = c.post(
        "/api/vein/upload",
        files={"file": ("hot.bin", b"file-payload-xyz", "application/octet-stream")},
        headers=env["h_a"],
    )
    assert up.status_code == 200
    file_id = up.json()["file_id"]

    entry = env["cache"].get_entry(f"file:{file_id}", tenant_id="tenant-a")
    assert entry is not None
    assert entry.tenant_id == "tenant-a"

    # 修前：GET /cache/file:{id} 全局可读=他人文件内容 hex 泄露（绕 ⑦-⑩ 行闸）
    assert _get(c, f"file:{file_id}", env["h_b"]).status_code == 404
    assert _get(c, f"file:{file_id}", None).status_code == 404
    owner = _get(c, f"file:{file_id}", env["h_a"])
    assert owner.status_code == 200
    assert owner.json()["data"].startswith(b"file-payload-xyz".hex())

    # ⑦-⑩ download 行闸回归钉：跨租户 404，拥有者缓存命中面照常
    assert c.get(f"/api/vein/files/{file_id}/download", headers=env["h_b"]).status_code == 404
    dl = c.get(f"/api/vein/files/{file_id}/download", headers=env["h_a"])
    assert dl.status_code == 200 and dl.content == b"file-payload-xyz"

    # 删文件=删自己缓存行（既有语义），行归属下只动自己行
    assert c.delete(f"/api/vein/files/{file_id}", headers=env["h_a"]).status_code == 200
    assert env["cache"].get_entry(f"file:{file_id}", tenant_id="tenant-a") is None


def test_delete_file_cache_invalidate_tenant_scoped(env):
    c = env["client"]
    # 同 file_id 不可能跨租户存在（文件行唯一），此例钉 invalidate 带行租户键后
    # 拥有者删除路径缓存照清（不因行键化误留垃圾）
    up = c.post(
        "/api/vein/upload",
        files={"file": ("d.bin", b"dd", "application/octet-stream")},
        headers=env["h_a"],
    )
    file_id = up.json()["file_id"]
    assert env["cache"].get_entry(f"file:{file_id}", tenant_id="tenant-a") is not None
    c.delete(f"/api/vein/files/{file_id}", headers=env["h_a"])
    assert env["cache"].get_entry(f"file:{file_id}", tenant_id="tenant-a") is None
    assert env["cache"].get(f"file:{file_id}") is None  # 系统视图也不残留


# ── ⑦ stats 系统视图 + 既有契约不变 ─────────────────────────


def test_stats_system_view_shape_unchanged(env):
    c = env["client"]
    _put(c, "s-1", b"1", env["h_a"])
    _put(c, "s-2", b"2", env["h_b"])
    r = c.get("/api/vein/cache/stats")
    assert r.status_code == 200
    body = r.json()
    # health 消费计数字节不变：键集逐键不增不减
    assert set(body.keys()) == {
        "entries",
        "current_size_bytes",
        "max_size_bytes",
        "usage_percent",
        "hits",
        "misses",
        "hit_rate",
        "default_ttl_seconds",
    }
    assert body["entries"] == 2  # 系统视图=全部租户行


def test_miss_contracts_unchanged(env):
    c = env["client"]
    assert _get(c, "k-none", None).status_code == 404
    assert _get(c, "k-none", None).json()["detail"] == CACHE_MISS_DETAIL
    assert _del(c, "k-none", None).status_code == 404
    assert _del(c, "k-none", None).json()["detail"] == CACHE_ABSENT_DETAIL


def test_bad_token_write_zero_side_effect(env):
    c = env["client"]
    before = len(env["cache"]._cache)
    r = c.put(
        "/api/vein/cache",
        json={"key": "k-evil", "data": base64.b64encode(b"x").decode()},
        headers=BAD_TOKEN,
    )
    assert r.status_code == 401
    assert len(env["cache"]._cache) == before
    assert _del(c, "k-evil", BAD_TOKEN).status_code == 401
    assert c.post("/api/vein/cache/clear", headers=BAD_TOKEN).status_code == 401
