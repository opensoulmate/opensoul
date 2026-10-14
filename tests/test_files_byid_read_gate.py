"""files 面 by-ID 读面归属闸测试（DEC-⑦-⑩：DEC-⑦-④ 留档「by-ID 读面（download/
get_meta/versions/promote/pipeline_run retrieve）未按租户闸（另拍）」收口件）。

修前（HEAD 源码实证）：by-ID 读面零租户闸——持有 file_id 的跨租户方可读他人文件
元数据/内容/版本（download 缓存命中面更直接：缓存键全局，闸后置于缓存=借缓存读
他人文件内容）、promote/auto-process/pipeline_run 拿他人文件内容跑处理/入知识库、
batch 全租户枚举+逐行 retrieve=批量跨租户读。收口=get_meta/retrieve/stream_retrieve/
get_version_history/get_version 增 tenant_id 可选闸（delete/rollback 归属闸同款：
显式给出时跨租户回 None/[] 与 not-found 同一契约不差分泄露、None=系统视图字节
不变）+API by-ID 读面 bind_identity 派生租户流入（匿名 'default' 契约不变）+
download 归属闸前置于缓存命中+batch 列表/读点同闸。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB/HOME 归 tmp，FileStore 缺省路径随
HOME；绝不碰生产 ~/opensoul/data/vein）。
"""

import sqlite3
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
    return {"Authorization": "Bearer " + tok}


class _DbRecorder:
    """db_pool 桩——记录 execute/fetchrow 调用（promote 零副作用硬证用）。"""

    def __init__(self):
        self.calls = []

    async def execute(self, *args, **kwargs):
        self.calls.append(args[0] if args else "")
        return "DELETE 0"

    async def fetchrow(self, *args, **kwargs):
        self.calls.append(args[0] if args else "")
        return None


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 身份库 + HOME 归 tmp + src.api.vein 模块单例 store=tmp 实例 + 真
    vein/search/pipeline router 真 bind_identity（DEC-⑦-④ env 同款路线）。"""
    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    monkeypatch.setenv("HOME", str(tmp_path))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    import src.api.pipeline as pipeline_api
    import src.api.vein as vein_api
    from src.vein.cache import CacheManager
    from src.vein.file_store import FileStore

    fresh = FileStore()  # 缺省路径随 HOME=tmp → tmp 数据面（禁碰生产）
    monkeypatch.setattr(vein_api, "store", fresh)
    monkeypatch.setattr(vein_api, "cache", CacheManager(max_size_mb=32, default_ttl=360))

    # pipeline 重步骤换桩（sense/immune 本体不进测试面；knowledge 由 skip 标志跳过）
    async def _fake_sense(data, mime_type, filename, pipeline_type):
        return {"step": "sense-text", "status": "ok", "text": "hello world"}

    async def _fake_immune(text):
        return {
            "step": "immune",
            "status": "ok",
            "is_safe": True,
            "risk_level": "low",
            "findings_count": 0,
            "redacted_available": False,
        }

    monkeypatch.setattr(pipeline_api, "_step_sense_extract", _fake_sense)
    monkeypatch.setattr(pipeline_api, "_step_immune_scan", _fake_immune)

    app = FastAPI()
    app.include_router(vein_api.router, prefix="/api/vein")
    app.include_router(pipeline_api.router, prefix="/api/pipeline")
    client = TestClient(app)

    user_tenant_a = _seed_user(users_db, str(uuid.uuid4()), "alice-a", "tenant-a")
    user_tenant_b = _seed_user(users_db, str(uuid.uuid4()), "bob-b", "tenant-b")
    user_default = _seed_user(users_db, str(uuid.uuid4()), "carol-default", "default")

    yield {
        "client": client,
        "store": fresh,
        "cache": vein_api.cache,
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


# ── store 层闸（delete/rollback 归属闸同款语义） ────────────


class TestStoreGates:
    def test_get_meta_gate(self, env):
        fid = _upload(env["client"], _token(env["user_tenant_a"]), "a.txt", b"alpha")
        assert env["store"].get_meta(fid, tenant_id="tenant-a") is not None
        assert env["store"].get_meta(fid, tenant_id="tenant-b") is None
        assert env["store"].get_meta("nope", tenant_id="tenant-a") is None
        # None=系统视图不闸（既有内部调用面字节不变）
        assert env["store"].get_meta(fid, tenant_id=None) is not None

    def test_retrieve_and_stream_gate(self, env):
        fid = _upload(env["client"], _token(env["user_tenant_a"]), "a.txt", b"alpha")
        data = env["store"].retrieve(fid, tenant_id="tenant-a")
        assert data is not None and data[0] == b"alpha"
        assert env["store"].retrieve(fid, tenant_id="tenant-b") is None
        blob = env["store"].stream_retrieve(fid, tenant_id="tenant-a")
        assert blob[0] is not None
        assert env["store"].stream_retrieve(fid, tenant_id="tenant-b") == (None, None)
        assert env["store"].retrieve(fid) is not None  # 系统视图不闸

    def test_version_gates(self, env):
        c = env["client"]
        fid = _upload(c, _token(env["user_tenant_a"]), "a.txt", b"v1")
        r = c.put(
            f"/api/vein/files/{fid}/content",
            files={"file": ("a.txt", b"v2", "text/plain")},
            headers=_token(env["user_tenant_a"]),
        )
        assert r.status_code == 200
        assert env["store"].get_version_history(fid, tenant_id="tenant-a")
        assert env["store"].get_version_history(fid, tenant_id="tenant-b") == []
        assert env["store"].get_version(fid, 1, tenant_id="tenant-a") is not None
        assert env["store"].get_version(fid, 1, tenant_id="tenant-b") is None
        # 行缺失也回空（闸在文件行层，versions 表无行键）
        assert env["store"].get_version_history("nope", tenant_id="tenant-a") == []
        assert env["store"].get_version_history(fid) is not None  # 系统视图


# ── API by-ID 读面闸 ───────────────────────────────────────


class TestApiReadGates:
    def test_meta_cross_404_same_contract_as_missing(self, env):
        c = env["client"]
        fid = _upload(c, _token(env["user_tenant_a"]), "a.txt", b"alpha")
        cross = c.get(f"/api/vein/files/{fid}", headers=_token(env["user_tenant_b"]))
        missing = c.get("/api/vein/files/nope", headers=_token(env["user_tenant_b"]))
        assert cross.status_code == 404
        assert missing.status_code == 404
        assert cross.json()["detail"] == missing.json()["detail"]  # 不差分泄露

    def test_meta_own_and_anonymous_contract(self, env):
        c = env["client"]
        fid_a = _upload(c, _token(env["user_tenant_a"]), "a.txt", b"alpha")
        fid_d = _upload(c, None, "d.txt", b"delta")
        r = c.get(f"/api/vein/files/{fid_a}", headers=_token(env["user_tenant_a"]))
        assert r.status_code == 200
        assert r.json()["tenant_id"] == "tenant-a"  # 加性键延续
        assert c.get(f"/api/vein/files/{fid_d}").status_code == 200  # 匿名 default 契约
        assert c.get(f"/api/vein/files/{fid_a}").status_code == 404  # 匿名不见租户行

    def test_download_cross_404(self, env):
        c = env["client"]
        fid = _upload(c, _token(env["user_tenant_a"]), "a.txt", b"alpha-secret")
        r = c.get(f"/api/vein/files/{fid}/download", headers=_token(env["user_tenant_b"]))
        assert r.status_code == 404
        assert b"alpha-secret" not in r.content  # 内容零泄露
        assert c.get(f"/api/vein/files/{fid}/download").status_code == 404  # 匿名同闸

    def test_download_cache_channel_gated(self, env):
        """缓存键全局——修前缓存命中面绕过一切行归属直接回内容（泄露面本体）。"""
        c = env["client"]
        fid = _upload(c, _token(env["user_tenant_a"]), "a.txt", b"alpha-secret")
        env["cache"].put(f"file:{fid}", b"alpha-secret")  # 模拟已预热缓存
        r = c.get(f"/api/vein/files/{fid}/download", headers=_token(env["user_tenant_b"]))
        assert r.status_code == 404  # 闸先于缓存命中
        r = c.get(f"/api/vein/files/{fid}/download", headers=_token(env["user_tenant_a"]))
        assert r.status_code == 200
        assert r.content == b"alpha-secret"

    def test_download_own_200(self, env):
        c = env["client"]
        fid = _upload(c, _token(env["user_tenant_a"]), "a.txt", b"alpha")
        r = c.get(f"/api/vein/files/{fid}/download", headers=_token(env["user_tenant_a"]))
        assert r.status_code == 200
        assert r.content == b"alpha"

    def test_versions_endpoints_cross_404(self, env):
        c = env["client"]
        ha, hb = _token(env["user_tenant_a"]), _token(env["user_tenant_b"])
        fid = _upload(c, ha, "a.txt", b"v1")
        r = c.put(
            f"/api/vein/files/{fid}/content",
            files={"file": ("a.txt", b"v2", "text/plain")},
            headers=ha,
        )
        assert r.status_code == 200
        assert c.get(f"/api/vein/files/{fid}/versions", headers=hb).status_code == 404
        assert c.get(f"/api/vein/files/{fid}/versions/1", headers=hb).status_code == 404
        own = c.get(f"/api/vein/files/{fid}/versions", headers=ha)
        assert own.status_code == 200
        assert own.json()["versions"]  # 自租户照常可见
        assert c.get(f"/api/vein/files/{fid}/versions/1", headers=ha).status_code == 200

    def test_promote_cross_404_zero_side_effect(self, env, monkeypatch):
        import src.database.postgres as pg

        rec = _DbRecorder()
        monkeypatch.setattr(pg, "db_pool", rec)
        c = env["client"]
        fid = _upload(c, _token(env["user_tenant_a"]), "a.txt", b"alpha-secret")
        r = c.post(
            f"/api/vein/files/{fid}/promote",
            json={"user_id": "bob"},
            headers=_token(env["user_tenant_b"]),
        )
        assert r.status_code == 404
        assert rec.calls == []  # 零副作用硬证（knowledge/tags 一行不写）
        r = c.post(
            f"/api/vein/files/{fid}/promote",
            json={"user_id": "alice"},
            headers=_token(env["user_tenant_a"]),
        )
        assert r.status_code == 200 and r.json()["promoted"] is True
        assert rec.calls  # 自租户照常入库（闸不误伤）

    def test_auto_process_cross_404_zero_side_effect(self, env, monkeypatch):
        import src.database.postgres as pg

        rec = _DbRecorder()
        monkeypatch.setattr(pg, "db_pool", rec)
        c = env["client"]
        fid = _upload(c, _token(env["user_tenant_a"]), "a.png", b"png-bytes")
        # 行 mime 改成图片让 auto-process 走 OCR 路由（修前会真读他人内容）
        env["store"]._db.execute("UPDATE files SET mime_type='image/png' WHERE file_id=?", (fid,))
        env["store"]._db.commit()
        r = c.post(
            f"/api/vein/files/{fid}/auto-process",
            json={"user_id": "bob"},
            headers=_token(env["user_tenant_b"]),
        )
        assert r.status_code == 404
        assert rec.calls == []  # 不进知识入库零副作用

    def test_batch_only_own_tenant(self, env):
        c = env["client"]
        fa = _upload(c, _token(env["user_tenant_a"]), "alpha.txt", b"a")
        _upload(c, _token(env["user_tenant_b"]), "beta.txt", b"b")
        r = c.post(
            "/api/vein/auto-process/batch",
            json={"mime_filter": ["text/"], "limit": 50},
            headers=_token(env["user_tenant_a"]),
        )
        assert r.status_code == 200
        body = r.json()
        got = {x["file_id"] for x in body["results"]}
        assert got == {fa}  # 修前含 fb=批量跨租户读
        assert body["total_files"] == 1  # 修前=2（全租户枚举）

    def test_pipeline_run_cross_404(self, env):
        c = env["client"]
        fid = _upload(c, _token(env["user_tenant_a"]), "a.txt", b"alpha")
        payload = {"file_id": fid, "skip_immune": True, "skip_knowledge": True}
        r = c.post("/api/pipeline/run", json=payload, headers=_token(env["user_tenant_b"]))
        assert r.status_code == 404
        r = c.post("/api/pipeline/run", json=payload, headers=_token(env["user_tenant_a"]))
        assert r.status_code == 200
        assert r.json()["file_id"] == fid  # 自租户照常跑

    def test_missing_row_contract_unchanged(self, env):
        c = env["client"]
        h = _token(env["user_tenant_a"])
        assert c.get("/api/vein/files/nope", headers=h).json()["detail"] == "File not found"
        assert (
            c.get("/api/vein/files/nope/download", headers=h).json()["detail"] == "File not found"
        )
        assert (
            c.get("/api/vein/files/nope/versions", headers=h).json()["detail"] == "File not found"
        )
        assert (
            c.get("/api/vein/files/nope/versions/1", headers=h).json()["detail"]
            == "Version not found"
        )
        r = c.post("/api/vein/files/nope/promote", json={}, headers=h)
        assert r.status_code == 404 and r.json()["detail"] == "File not found"

    def test_write_face_contracts_untouched(self, env):
        """既有写闸契约回归钉（⑦-④ 劫持 403 / delete·rollback 404 零变化）。"""
        c = env["client"]
        ha, hb = _token(env["user_tenant_a"]), _token(env["user_tenant_b"])
        fid = _upload(c, ha, "a.txt", b"v1")
        r = c.put(
            f"/api/vein/files/{fid}/content",
            files={"file": ("a.txt", b"v2", "text/plain")},
            headers=hb,
        )
        assert r.status_code == 403  # 劫持闸
        assert c.delete(f"/api/vein/files/{fid}", headers=hb).status_code == 404
        assert c.post(f"/api/vein/files/{fid}/rollback/1", headers=hb).status_code == 404

    def test_system_view_unchanged(self, env):
        """tenant_id=None=系统视图字节不变（内部调用面契约）。"""
        c = env["client"]
        fid = _upload(c, _token(env["user_tenant_a"]), "a.txt", b"alpha")
        assert env["store"].get_meta(fid) is not None
        assert env["store"].retrieve(fid) is not None
        assert env["store"].stream_retrieve(fid)[0] is not None
