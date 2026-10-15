"""DEC-⑦-㉕ vein chunked 上传会话面行租户键加轴测试。

修前（⑦-④/⑦-⑩ 留档「chunked 上传会话无租户轴（内存会话）（⑦-④ 边界延续）」
+HEAD 源码/live 实证）：UploadSession 无 tenant_id、create 写点零身份轴、
init/chunk/complete/status/list/cancel 六口零鉴权零归属闸——任意调用方可见
全部会话文件名（list 全量）、可向他人会话投毒分片（chunk）、可完成他人会话
把他人分片内容落成自己的文件（complete=内容窃取）、可取消/删除他人会话。

收口=DEC-⑦ 家族同款（⑦-㉔ task_row_tenant 先例）：
① upload_session_row_tenant 行租户键单一判法（自带键缺失/空白→'default'
   兜底，dict 行/对象/原始值三形同收，写读同源禁双源漂移 F4b-㉗ 纪律）
② UploadSession.tenant_id 行键 + create 写点归属（bind_identity 派生、
   匿名 'default' 契约不变、无自报轴）+ to_dict 加性键 tenant_id
③ by-ID 归属闸（_session_visible 单一判法）覆盖 get_session/upload_chunk/
   assemble/cleanup 四面：跨租户与 not-found 同返回同 detail 不差分泄露
   （③c-1）、闸前置禁状态差分（complete 先归属后 is_complete ⑦-㉔）、
   行零破坏（不删他人分片目录 ③b-3）
④ list_sessions 恒按行租户键过滤（None=系统视图全量=health 计数字节不变）
⑤ API 6 端点 bind_identity 派生流入（坏 token 401 先于读/写零副作用）。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB/文件库/缓存/上传临时面），绝不碰
生产数据面，不打 live :8090。
"""

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

NOT_FOUND_DETAIL = "Upload session not found"


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

    user_a = _seed_user(users_db, str(uuid.uuid4()), "alice-a", "tenant-a")
    user_b = _seed_user(users_db, str(uuid.uuid4()), "bob-b", "tenant-b")
    user_default = _seed_user(users_db, str(uuid.uuid4()), "carol-default", "default")

    yield {
        "client": client,
        "uploader": vein_api.uploader,
        "uploads_dir": Path(tmp_path / "uploads"),
        "user_a": user_a,
        "user_b": user_b,
        "user_default": user_default,
        "users_db": users_db,
        "h_a": _token(user_a),
        "h_b": _token(user_b),
        "h_default": _token(user_default),
    }


def _init(c, headers, name="doc.txt", total_size=4, chunk_size=2, extra=None):
    body = {
        "filename": name,
        "total_size": total_size,
        "chunk_size": chunk_size,
    }
    if extra:
        body.update(extra)
    return c.post("/api/vein/upload/chunked/init", json=body, headers=headers)


def _chunk(c, upload_id, idx, data, headers):
    return c.post(
        f"/api/vein/upload/chunked/{upload_id}/chunk/{idx}",
        files={"file": (f"chunk{idx}.bin", data, "application/octet-stream")},
        headers=headers,
    )


def _complete(c, upload_id, headers):
    return c.post(f"/api/vein/upload/chunked/{upload_id}/complete", headers=headers)


def _status(c, upload_id, headers):
    return c.get(f"/api/vein/upload/chunked/{upload_id}", headers=headers)


def _cancel(c, upload_id, headers):
    return c.delete(f"/api/vein/upload/chunked/{upload_id}", headers=headers)


def _fill(c, upload_id, headers):
    """把会话填到 complete-able（2/2 分片）。"""
    assert _chunk(c, upload_id, 0, b"ab", headers).status_code == 200
    assert _chunk(c, upload_id, 1, b"cd", headers).status_code == 200


# ── ① 行租户键单一判法 ──────────────────────────────────────────


class TestJudgmentSingleSource:
    def test_row_tenant_three_shapes(self):
        from src.vein.chunked import UploadSession, upload_session_row_tenant

        sess = UploadSession(
            upload_id="u", filename="f", total_size=1, chunk_size=1, total_chunks=1
        )
        assert upload_session_row_tenant(sess) == "default"
        sess2 = UploadSession(
            upload_id="u",
            filename="f",
            total_size=1,
            chunk_size=1,
            total_chunks=1,
            tenant_id="tenant-a",
        )
        assert upload_session_row_tenant(sess2) == "tenant-a"
        assert upload_session_row_tenant({"tenant_id": "tenant-a"}) == "tenant-a"
        assert upload_session_row_tenant({"filename": "x"}) == "default"
        assert upload_session_row_tenant(None) == "default"
        assert upload_session_row_tenant("") == "default"
        assert upload_session_row_tenant("   ") == "default"
        assert upload_session_row_tenant("tenant-a") == "tenant-a"

    def test_session_visible_gate_semantics(self):
        from src.vein.chunked import UploadSession, _session_visible

        sess = UploadSession(
            upload_id="u",
            filename="f",
            total_size=1,
            chunk_size=1,
            total_chunks=1,
            tenant_id="tenant-a",
        )
        assert _session_visible(sess, None) is True  # None=系统视图
        assert _session_visible(sess, "tenant-a") is True
        assert _session_visible(sess, "tenant-b") is False
        assert _session_visible(sess, "  tenant-a  ") is True  # 归一比对

    def test_single_source_mask_flips_gate_and_filter(self, env, monkeypatch):
        """monkeypatch 掩码翻转：闸/过滤/落行全走同一判法=写读同源硬证。"""
        import src.vein.chunked as chunked_mod

        c = env["client"]
        monkeypatch.setattr(chunked_mod, "upload_session_row_tenant", lambda raw: "tenant-masked")
        r = _init(c, env["h_a"])
        assert r.status_code == 200
        uid = r.json()["upload_id"]
        assert r.json()["tenant_id"] == "tenant-masked"  # 落行走单一判法

        # 闸也走同一判法：tenant-a 看不见（行键被掩码）、tenant-masked 可见
        assert _status(c, uid, env["h_a"]).status_code == 404
        masked_uid = _seed_user(env["users_db"], str(uuid.uuid4()), "masked-u", "tenant-masked")
        h_masked = _token(masked_uid)
        assert _status(c, uid, h_masked).status_code == 200
        listed = c.get("/api/vein/upload/chunked", headers=h_masked).json()["sessions"]
        assert [s["upload_id"] for s in listed] == [uid]


# ── ② 写点归属 ─────────────────────────────────────────────────


class TestWritePoint:
    def test_create_lands_derived_tenant(self, env):
        c = env["client"]
        r = _init(c, env["h_a"])
        assert r.status_code == 200
        data = r.json()
        assert data["tenant_id"] == "tenant-a"
        assert env["uploader"].get_session(data["upload_id"]).tenant_id == "tenant-a"

    def test_anonymous_default_contract(self, env):
        c = env["client"]
        r = _init(c, None)
        assert r.status_code == 200
        data = r.json()
        assert data["tenant_id"] == "default"
        for key in (
            "upload_id",
            "filename",
            "total_size",
            "chunk_size",
            "total_chunks",
            "received_chunks",
            "progress",
            "is_complete",
            "elapsed_seconds",
            "mime_type",
            "tags",
        ):
            assert key in data  # 加性键：既有键逐键不变

    def test_self_report_ignored(self, env):
        """请求体自报 tenant_id 不进落行（无自报轴）。"""
        c = env["client"]
        r = _init(c, env["h_a"], extra={"tenant_id": "tenant-hack"})
        assert r.status_code == 200
        assert r.json()["tenant_id"] == "tenant-a"

    def test_bad_token_401_zero_session(self, env):
        c = env["client"]
        r = _init(c, BAD_TOKEN)
        assert r.status_code == 401
        assert env["uploader"].list_sessions() == []  # 系统视图零会话=零副作用


# ── ③ by-ID 归属闸 ─────────────────────────────────────────────


class TestByIdGate:
    def test_own_status_additive_key(self, env):
        c = env["client"]
        uid = _init(c, env["h_a"]).json()["upload_id"]
        r = _status(c, uid, env["h_a"])
        assert r.status_code == 200
        assert r.json()["tenant_id"] == "tenant-a"

    def test_cross_status_same_contract_as_missing(self, env):
        """跨租户与 not-found 同 404 同 detail 逐字（禁差分泄露行存在性）。"""
        c = env["client"]
        uid = _init(c, env["h_a"]).json()["upload_id"]
        cross = _status(c, uid, env["h_b"])
        missing = _status(c, "no-such-session", env["h_b"])
        assert cross.status_code == 404
        assert missing.status_code == 404
        assert cross.json()["detail"] == missing.json()["detail"] == NOT_FOUND_DETAIL

    def test_cross_chunk_zero_bytes_written(self, env):
        """跨租户投毒分片=404+分片字节零落盘+行状态零改写（毒化防护反证）。"""
        c = env["client"]
        uid = _init(c, env["h_a"]).json()["upload_id"]
        r = _chunk(c, uid, 0, b"XX", env["h_b"])
        assert r.status_code == 404
        assert r.json()["detail"] == NOT_FOUND_DETAIL
        sess = env["uploader"].get_session(uid, tenant_id="tenant-a")
        assert sess.received_chunks == set()
        chunk_dir = env["uploads_dir"] / uid
        assert not list(chunk_dir.glob("chunk_*"))  # 零字节落盘

    def test_cross_complete_zero_file_row(self, env):
        """跨租户完成他人会话=404+零文件落库（内容窃取面 hard 反证）；
        他人会话仍可被本人完成=行零破坏。"""
        c = env["client"]
        uid = _init(c, env["h_a"], name="secret.txt").json()["upload_id"]
        _fill(c, uid, env["h_a"])

        cross = _complete(c, uid, env["h_b"])
        assert cross.status_code == 404
        assert cross.json()["detail"] == NOT_FOUND_DETAIL
        assert c.get("/api/vein/files", headers=env["h_b"]).json()["files"] == []  # B 零落库
        assert c.get("/api/vein/files", headers=env["h_a"]).json()["files"] == []  # 亦未被偷落

        own = _complete(c, uid, env["h_a"])  # 会话仍在、本人完成
        assert own.status_code == 200
        rows_a = c.get("/api/vein/files", headers=env["h_a"]).json()["files"]
        assert len(rows_a) == 1
        assert rows_a[0]["name"] == "secret.txt"
        assert rows_a[0]["tenant_id"] == "tenant-a"

    def test_cross_cancel_row_survives(self, env):
        """跨租户取消=404+会话/分片目录零破坏；本人取消=200 真清。"""
        c = env["client"]
        uid = _init(c, env["h_a"]).json()["upload_id"]
        _chunk(c, uid, 0, b"ab", env["h_a"])

        cross = _cancel(c, uid, env["h_b"])
        assert cross.status_code == 404
        assert cross.json()["detail"] == NOT_FOUND_DETAIL
        assert _status(c, uid, env["h_a"]).status_code == 200  # 行存活
        assert (env["uploads_dir"] / uid).is_dir()  # 分片目录存活

        own = _cancel(c, uid, env["h_a"])
        assert own.status_code == 200
        assert _status(c, uid, env["h_a"]).status_code == 404
        assert not (env["uploads_dir"] / uid).exists()

    def test_cross_upload_chunk_engine_level_rejects(self, env):
        """引擎层 upload_chunk/assemble/cleanup 闸（API 之外消费方无法绕闸）。"""
        eng = env["uploader"]
        sess = eng.create_session("f.bin", total_size=4, chunk_size=2, tenant_id="tenant-a")
        assert eng.upload_chunk(sess.upload_id, 0, b"ab", tenant_id="tenant-b") is None
        assert eng.assemble(sess.upload_id, tenant_id="tenant-b") is None
        assert eng.cleanup(sess.upload_id, tenant_id="tenant-b") is False
        assert eng.get_session(sess.upload_id, tenant_id="tenant-a") is not None

    def test_bad_token_battery_401_zero_side_effect(self, env):
        """六口坏 token 全 401 零副作用（身份证明先于一切 F3b-⑤）。"""
        c = env["client"]
        uid = _init(c, env["h_a"]).json()["upload_id"]
        calls = [
            c.post(
                "/api/vein/upload/chunked/init",
                json={"filename": "x", "total_size": 2, "chunk_size": 2},
                headers=BAD_TOKEN,
            ),
            _chunk(c, uid, 0, b"ab", BAD_TOKEN),
            _complete(c, uid, BAD_TOKEN),
            _status(c, uid, BAD_TOKEN),
            c.get("/api/vein/upload/chunked", headers=BAD_TOKEN),
            _cancel(c, uid, BAD_TOKEN),
        ]
        assert [r.status_code for r in calls] == [401] * 6
        # 零副作用：会话未被投毒/取消/完成，系统视图仍恰 1 行
        sess = env["uploader"].get_session(uid, tenant_id="tenant-a")
        assert sess is not None and sess.received_chunks == set()
        assert len(env["uploader"].list_sessions()) == 1


# ── ④ 读面恒过滤 ───────────────────────────────────────────────


class TestListFilter:
    def test_three_face_disjoint(self, env):
        c = env["client"]
        a1 = _init(c, env["h_a"], name="a1.txt").json()["upload_id"]
        a2 = _init(c, env["h_a"], name="a2.txt").json()["upload_id"]
        b1 = _init(c, env["h_b"], name="b1.txt").json()["upload_id"]
        d1 = _init(c, None, name="d1.txt").json()["upload_id"]

        ids_a = {
            s["upload_id"]
            for s in c.get("/api/vein/upload/chunked", headers=env["h_a"]).json()["sessions"]
        }
        ids_b = {
            s["upload_id"]
            for s in c.get("/api/vein/upload/chunked", headers=env["h_b"]).json()["sessions"]
        }
        ids_d = {
            s["upload_id"]
            for s in c.get("/api/vein/upload/chunked", headers=None).json()["sessions"]
        }
        assert ids_a == {a1, a2}
        assert ids_b == {b1}
        assert ids_d == {d1}
        # 加性键=行归属可观测
        rows = c.get("/api/vein/upload/chunked", headers=env["h_a"]).json()["sessions"]
        assert {r["tenant_id"] for r in rows} == {"tenant-a"}

    def test_list_no_cross_filename_leak(self, env):
        """文件名不跨租户泄露（修前 list 全量=任意调用方见全部文件名）。"""
        c = env["client"]
        _init(c, env["h_a"], name="only-a-secret-name.txt")
        rows = c.get("/api/vein/upload/chunked", headers=env["h_b"]).json()["sessions"]
        assert rows == []


# ── ⑤ 系统视图/不变面 ──────────────────────────────────────────


class TestSystemViewAndContract:
    def test_none_system_view_byte_unchanged(self, env):
        """None=系统视图（内部清理面/health 计数）字节不变。"""
        eng = env["uploader"]
        c = env["client"]
        _init(c, env["h_a"])
        _init(c, env["h_b"])
        assert len(eng.list_sessions()) == 2  # 全量
        sess = eng.create_session("f", total_size=2, chunk_size=2, tenant_id="tenant-a")
        assert eng.get_session(sess.upload_id) is not None  # 系统视图跨租户可读

    def test_health_stats_global_count_unchanged(self, env):
        """/api/vein/stats uploads 计数=系统视图全量（health 面字节不变）。"""
        c = env["client"]
        _init(c, env["h_a"])
        _init(c, env["h_b"])
        _init(c, None)
        stats = c.get("/api/vein/stats", headers=None).json()
        assert stats["uploads"]["active_sessions"] == 3

    def test_own_full_flow(self, env):
        """own 全流程：init→chunk×2→complete 落库行归属=派生租户+会话清面。"""
        c = env["client"]
        uid = _init(c, env["h_a"], name="flow.txt").json()["upload_id"]
        _fill(c, uid, env["h_a"])
        r = _complete(c, uid, env["h_a"])
        assert r.status_code == 200
        rows = c.get("/api/vein/files", headers=env["h_a"]).json()["files"]
        assert len(rows) == 1 and rows[0]["tenant_id"] == "tenant-a"
        assert _status(c, uid, env["h_a"]).status_code == 404  # complete 后会话清面
        assert not (env["uploads_dir"] / uid).exists()
