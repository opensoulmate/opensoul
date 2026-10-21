"""RV-①b-⑥-1 vein ChunkedUploader 上传会话行持久化——_sessions 写穿/懒回载/
重启会话不丢（跨重启续传成真）/真实路径接线。

修前（RV-① A 组定性）：ChunkedUploader._sessions 全在进程内存——上传会话行
（received/complete 状态）随 service 重启即丢，而分片字节本就在盘=孤儿分片目录。
本文件锁死：
①UploadSessionPersistence 行语义（状态快照表 upsert 当前态/tags·received_chunks
  json 列/删行幂等/坏行跳过可见/回载不建库）
②create/upload_chunk/assemble 三变更点随写落盘（写穿）+cleanup 删行
③重启回载逐键一致+只补缺不覆盖+跨重启续传真跑通（分片字节+会话行双真源）
④persist=None 纯内存既有语义零变化（零 IO 硬证+to_dict 形状逐键）
⑤写/回载失败可见不阻断（记账旁路纪律）+懒回载重试+TTL 清扫删行联动
⑥生产真实路径（src/api/vein uploader 单例）真接线（AST 锁）。
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import sqlite3
from pathlib import Path


def _persist(tmp_path):
    from src.vein.session_persist import UploadSessionPersistence

    return UploadSessionPersistence(str(tmp_path / "vein_upload_sessions.db"))


def _uploader(tmp_path, with_persist: bool = True):
    from src.vein.chunked import ChunkedUploader

    tmp_dir = str(tmp_path / "uploads")
    if with_persist:
        return ChunkedUploader(temp_dir=tmp_dir, persist=_persist(tmp_path))
    return ChunkedUploader(temp_dir=tmp_dir)


def _rows(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT upload_id, filename, total_size, chunk_size, total_chunks,"
            " received_chunks, content_hash, mime_type, tags, created_at,"
            " completed_at, tenant_id FROM vein_upload_sessions ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()


# ── ① 行语义（状态快照表）──────────────────────────────────────


def test_upsert_row_verbatim_and_json_columns(tmp_path):
    """逐列落盘+tags/received_chunks 整体 json 列（dict/list 列禁裸存）+NULL 往返。"""
    p = _persist(tmp_path)
    p.upsert(
        upload_id="u1",
        filename="a.bin",
        total_size=10,
        chunk_size=5,
        total_chunks=2,
        received_chunks=json.dumps([0, 1]),
        content_hash=None,
        mime_type="application/octet-stream",
        tags=json.dumps(["t1", "中文标签"], ensure_ascii=False),
        created_at=100.0,
        completed_at=None,
        tenant_id="tenant-a",
    )
    rows = _rows(tmp_path / "vein_upload_sessions.db")
    assert len(rows) == 1
    r = rows[0]
    assert r[0] == "u1" and r[1] == "a.bin" and r[2] == 10 and r[3] == 5 and r[4] == 2
    assert json.loads(r[5]) == [0, 1]
    assert r[6] is None  # content_hash NULL 往返
    assert json.loads(r[8]) == ["t1", "中文标签"]
    assert r[9] == 100.0 and r[10] is None and r[11] == "tenant-a"
    loaded = p.load_all()
    assert loaded[0]["received_chunks"] == {0, 1}
    assert loaded[0]["tags"] == ["t1", "中文标签"]
    assert loaded[0]["content_hash"] is None
    assert loaded[0]["completed_at"] is None


def test_upsert_snapshot_overwrites_current_state(tmp_path):
    """状态快照表语义：同 upload_id upsert 覆盖=一上传会话一行当前态（a2a_tasks 同族）。"""
    p = _persist(tmp_path)
    for i, (recv, done) in enumerate([("[]", None), ("[0]", None), ("[0, 1]", 200.0)]):
        p.upsert(
            upload_id="u1",
            filename="a",
            total_size=2,
            chunk_size=1,
            total_chunks=2,
            received_chunks=recv,
            content_hash=None,
            mime_type="m",
            tags="[]",
            created_at=100.0,
            completed_at=done,
            tenant_id="default",
        )
        assert p.count() == 1
    rows = _rows(tmp_path / "vein_upload_sessions.db")
    assert len(rows) == 1 and json.loads(rows[0][5]) == [0, 1] and rows[0][10] == 200.0


def test_delete_removes_row_idempotent(tmp_path):
    """删行=cleanup 写穿口；缺行零操作（幂等不抛）。"""
    p = _persist(tmp_path)
    p.upsert(
        upload_id="u1",
        filename="a",
        total_size=1,
        chunk_size=1,
        total_chunks=1,
        received_chunks="[]",
        content_hash=None,
        mime_type="m",
        tags="[]",
        created_at=1.0,
        completed_at=None,
        tenant_id="default",
    )
    p.delete("u1")
    assert p.count() == 0
    p.delete("u1")  # 幂等
    assert p.count() == 0


def test_load_all_order_and_column_authority(tmp_path):
    """回载序=created_at 升序稳定序+列值为权威（payload 漂移面：本表无 payload，列即真源）。"""
    p = _persist(tmp_path)
    for uid, ts in [("u2", 200.0), ("u1", 100.0)]:
        p.upsert(
            upload_id=uid,
            filename=uid,
            total_size=1,
            chunk_size=1,
            total_chunks=1,
            received_chunks="[]",
            content_hash="h" + uid,
            mime_type="m",
            tags="[]",
            created_at=ts,
            completed_at=ts + 1,
            tenant_id="default",
        )
    loaded = p.load_all()
    assert [d["upload_id"] for d in loaded] == ["u1", "u2"]
    assert loaded[0]["content_hash"] == "hu1" and loaded[0]["completed_at"] == 101.0


def test_load_all_skips_bad_row_visible(tmp_path, caplog):
    """坏行（json 不可解/字段漂移）跳过该行+logger.error 可见禁静默丢。"""
    caplog.set_level(logging.ERROR)
    p = _persist(tmp_path)
    p.upsert(
        upload_id="good",
        filename="a",
        total_size=1,
        chunk_size=1,
        total_chunks=1,
        received_chunks="[]",
        content_hash=None,
        mime_type="m",
        tags="[]",
        created_at=1.0,
        completed_at=None,
        tenant_id="default",
    )
    conn = sqlite3.connect(str(tmp_path / "vein_upload_sessions.db"))
    conn.execute(
        "INSERT INTO vein_upload_sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("bad", "b", 1, 1, 1, "{not json", None, "m", "[]", 2.0, None, "default"),
    )
    conn.commit()
    conn.close()
    loaded = p.load_all()
    assert [d["upload_id"] for d in loaded] == ["good"]
    assert any("不可解跳过" in r.message for r in caplog.records)


def test_load_all_readonly_no_db_creation(tmp_path):
    """回载只读不建库（RV-①b-②④ 纪律）：库缺失=空起步，禁 import 期建空生产库。"""
    p = _persist(tmp_path)
    assert p.load_all() == []
    assert not (tmp_path / "vein_upload_sessions.db").exists()
    assert p.count() == 0
    assert not (tmp_path / "vein_upload_sessions.db").exists()


# ── ② 写穿（变更点随写随落盘）─────────────────────────────────


def test_create_session_writes_row(tmp_path):
    up = _uploader(tmp_path)
    s = up.create_session(
        "f.bin", total_size=10, chunk_size=4, mime_type="m", tags=["t"], tenant_id="tenant-a"
    )
    rows = _rows(tmp_path / "vein_upload_sessions.db")
    assert len(rows) == 1
    r = rows[0]
    assert r[0] == s.upload_id and r[1] == "f.bin" and r[4] == 3  # total_chunks=ceil(10/4)
    assert json.loads(r[5]) == [] and json.loads(r[8]) == ["t"]
    assert r[11] == "tenant-a"


def test_upload_chunk_writes_received_and_complete(tmp_path):
    """收分片写穿：received_chunks 随收随落盘、末片 completed_at 真落。"""
    up = _uploader(tmp_path)
    s = up.create_session("f.bin", total_size=6, chunk_size=3)
    up.upload_chunk(s.upload_id, 0, b"abc")
    rows = _rows(tmp_path / "vein_upload_sessions.db")
    assert json.loads(rows[0][5]) == [0] and rows[0][10] is None
    up.upload_chunk(s.upload_id, 1, b"def")
    rows = _rows(tmp_path / "vein_upload_sessions.db")
    assert json.loads(rows[0][5]) == [0, 1] and rows[0][10] is not None


def test_assemble_writes_content_hash(tmp_path):
    up = _uploader(tmp_path)
    s = up.create_session("f.bin", total_size=3, chunk_size=3)
    up.upload_chunk(s.upload_id, 0, b"abc")
    result = up.assemble(s.upload_id)
    assert result is not None
    data, sess = result
    assert data == b"abc"
    rows = _rows(tmp_path / "vein_upload_sessions.db")
    assert rows[0][6] == hashlib.sha256(b"abc").hexdigest()
    assert sess.content_hash == rows[0][6]


def test_cleanup_drops_row_and_dir(tmp_path):
    up = _uploader(tmp_path)
    s = up.create_session("f.bin", total_size=3, chunk_size=3)
    up.upload_chunk(s.upload_id, 0, b"abc")
    chunk_dir = Path(up._temp) / s.upload_id
    assert chunk_dir.exists()
    assert up.cleanup(s.upload_id) is True
    assert p_count(tmp_path) == 0
    assert not chunk_dir.exists()


def p_count(tmp_path):
    return _persist(tmp_path).count()


def test_ttl_cleanup_drops_row_too(tmp_path):
    """TTL 清扫联动（㉕ 既有清理面）：陈旧会话清扫=行随会话走删行+分片目录删。"""
    p = _persist(tmp_path)
    up = _uploader(tmp_path)
    import time as _t

    stale_id = "stale0000001"
    (Path(up._temp) / stale_id).mkdir(parents=True)
    (Path(up._temp) / stale_id / "chunk_000000").write_bytes(b"x")
    p.upsert(
        upload_id=stale_id,
        filename="old",
        total_size=1,
        chunk_size=1,
        total_chunks=1,
        received_chunks="[]",
        content_hash=None,
        mime_type="m",
        tags="[]",
        created_at=_t.time() - 7200,
        completed_at=None,
        tenant_id="default",
    )
    up.create_session("new.bin", total_size=1, chunk_size=1)  # 触发 _cleanup_old_sessions
    ids = [r[0] for r in _rows(tmp_path / "vein_upload_sessions.db")]
    assert ids == [r for r in ids if r != stale_id]
    assert stale_id not in up._sessions
    assert not (Path(up._temp) / stale_id).exists()  # 孤儿分片目录随行清扫


# ── ③ 重启回载（跨重启续传成真）───────────────────────────────


def test_restart_reload_parity_and_resumable(tmp_path):
    """双实例（同 persist 路径+同 temp_dir）=重启语义：逐键 parity+续传真跑通。"""
    from src.vein.chunked import ChunkedUploader

    persist_path = str(tmp_path / "vein_upload_sessions.db")
    tmp_dir = str(tmp_path / "uploads")
    a = ChunkedUploader(temp_dir=tmp_dir, persist=_persist(tmp_path))
    s = a.create_session("f.bin", total_size=6, chunk_size=3, tags=["t"], tenant_id="tenant-a")
    a.upload_chunk(s.upload_id, 0, b"abc")

    # 「重启」：全新实例（内存空），同一真源
    from src.vein.session_persist import UploadSessionPersistence

    b = ChunkedUploader(temp_dir=tmp_dir, persist=UploadSessionPersistence(persist_path))
    got = b.get_session(s.upload_id, tenant_id="tenant-a")
    assert got is not None
    assert got.received_chunks == {0}
    assert got.filename == "f.bin" and got.total_chunks == 2
    assert got.tags == ["t"] and got.tenant_id == "tenant-a"
    assert [d["upload_id"] for d in b.list_sessions(tenant_id="tenant-a")] == [s.upload_id]

    # 续传：补第二片→组装全文一致
    b.upload_chunk(s.upload_id, 1, b"def")
    result = b.assemble(s.upload_id, tenant_id="tenant-a")
    assert result is not None
    data, sess = result
    assert data == b"abcdef"
    assert sess.content_hash == hashlib.sha256(b"abcdef").hexdigest()


def test_reload_only_add_missing_no_clobber(tmp_path):
    """只补缺不覆盖：内存新态优先，禁回载洗掉进程内已推进的 received。"""
    p = _persist(tmp_path)
    p.upsert(
        upload_id="u1",
        filename="f",
        total_size=6,
        chunk_size=3,
        total_chunks=2,
        received_chunks=json.dumps([0]),
        content_hash=None,
        mime_type="m",
        tags="[]",
        created_at=1.0,
        completed_at=None,
        tenant_id="default",
    )
    up = _uploader(tmp_path)
    from src.vein.chunked import UploadSession

    newer = UploadSession(
        upload_id="u1",
        filename="f",
        total_size=6,
        chunk_size=3,
        total_chunks=2,
        received_chunks={0, 1},
        tenant_id="default",
    )
    up._sessions["u1"] = newer
    up._ensure_loaded()
    got = up.get_session("u1")
    assert got is not None
    assert got.received_chunks == {0, 1}


# ── ④ 纯内存零变化 ───────────────────────────────────────────


def test_pure_memory_zero_io_and_shape(tmp_path):
    """persist=None 纯内存既有语义零变化：零库文件+to_dict 形状逐键不变。"""
    up = _uploader(tmp_path, with_persist=False)
    s = up.create_session("f.bin", total_size=3, chunk_size=3)
    up.upload_chunk(s.upload_id, 0, b"abc")
    d = s.to_dict()
    assert set(d.keys()) == {
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
        "tenant_id",
    }
    assert up.cleanup(s.upload_id) is True
    assert not (tmp_path / "vein_upload_sessions.db").exists()
    assert list(tmp_path.glob("**/*.db")) == []


# ── ⑤ 故障可见不阻断（记账旁路纪律）───────────────────────────


def test_write_failure_visible_not_blocking(tmp_path, caplog):
    """写穿失败=上传本体照常（会话已建/分片已收）+logger.error 可见（非谎报 durable）。"""
    caplog.set_level(logging.ERROR)

    class Broken:
        def upsert(self, **kw):
            raise RuntimeError("disk full")

        def delete(self, uid):
            raise RuntimeError("disk full")

        def load_all(self):
            return []

    from src.vein.chunked import ChunkedUploader

    up = ChunkedUploader(temp_dir=str(tmp_path / "uploads"), persist=Broken())
    s = up.create_session("f.bin", total_size=3, chunk_size=3)
    assert s is not None and s.upload_id in up._sessions
    assert up.cleanup(s.upload_id) is True  # 删行失败也可见不阻断
    msgs = [r.message for r in caplog.records]
    assert any("写穿失败" in m for m in msgs)
    assert any("删行失败" in m for m in msgs)


def test_reload_failure_retries_next_touch(tmp_path, caplog):
    """回载失败不置 _loaded=下次触碰重试（RV-①b-②④ 语义）。"""
    caplog.set_level(logging.ERROR)
    calls = {"n": 0}

    class Flaky:
        def upsert(self, **kw):
            pass

        def delete(self, uid):
            pass

        def load_all(self):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("db locked")
            return [
                {
                    "upload_id": "u9",
                    "filename": "f",
                    "total_size": 3,
                    "chunk_size": 3,
                    "total_chunks": 1,
                    "received_chunks": {0},
                    "content_hash": None,
                    "mime_type": "m",
                    "tags": [],
                    "created_at": 1.0,
                    "completed_at": None,
                    "tenant_id": "default",
                }
            ]

    from src.vein.chunked import ChunkedUploader

    up = ChunkedUploader(temp_dir=str(tmp_path / "uploads"), persist=Flaky())
    up._ensure_loaded()
    assert calls["n"] == 1 and up._loaded is False
    assert up._sessions.get("u9") is None
    up._ensure_loaded()  # 下次触碰=重试真回载
    assert calls["n"] == 2
    assert up.get_session("u9") is not None
    assert up._loaded is True
    assert any("回载失败" in r.message for r in caplog.records)


# ── ⑥ 真实路径接线（AST 锁）─────────────────────────────────────


def test_singleton_wired_with_persist_keyword():
    """生产单例构造带 persist= 关键字（AST 锁，防接线漂移）+真实例在场。"""
    import src.api.vein as vein_api

    src = Path(vein_api.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    wired = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "uploader" for t in node.targets
        ):
            call = node.value
            assert isinstance(call, ast.Call)
            kws = {k.arg for k in call.keywords}
            assert "persist" in kws
            wired = True
    assert wired
    assert vein_api.uploader._persist is not None


def test_change_points_wired_static():
    """变更点/读点全接线（AST 静态锁）：写穿/懒回载在真实方法体内+写穿调用点不散落。"""
    import src.vein.chunked as chunked_mod

    src = Path(chunked_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    bodies = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
            "create_session",
            "upload_chunk",
            "get_session",
            "list_sessions",
            "assemble",
            "cleanup",
            "_ensure_loaded",
            "_sync_session",
            "_drop_session",
        }:
            bodies[node.name] = ast.unparse(node)

    def calls(body: str, name: str) -> bool:
        return f"self.{name}(" in body

    # 懒回载：6 个公开口全接（含变更点自身读内存前置）
    for m in (
        "create_session",
        "upload_chunk",
        "get_session",
        "list_sessions",
        "assemble",
        "cleanup",
    ):
        assert calls(bodies[m], "_ensure_loaded"), m
    # 写穿：3 变更点（建会话/收分片/组装落 hash）
    for m in ("create_session", "upload_chunk", "assemble"):
        assert calls(bodies[m], "_sync_session"), m
    # 删行：cleanup 唯一删行口
    assert calls(bodies["cleanup"], "_drop_session")
    # 写穿单一点纪律：_sync_session/_drop_session 调用点恰好=变更点数（禁散落写点漂移）
    assert src.count("self._sync_session(") == 3
    assert src.count("self._drop_session(") == 1
