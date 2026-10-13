"""Integration tests for OpenVein (血管) — file storage, cache, chunked upload."""

import os
import sqlite3
import uuid
from pathlib import Path


class TestVeinHealth:
    def test_health(self, vein_client):
        resp = vein_client.get("/api/vein/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenVein"
        assert "store" in data
        assert "cache" in data


class TestVeinFileStore:
    def test_upload_and_retrieve(self, vein_client):
        content = b"Hello OpenVein test content"
        resp = vein_client.post(
            "/api/vein/upload",
            files={"file": ("test.txt", content, "text/plain")},
            data={"tags": "test,integration"},
        )
        assert resp.status_code == 200
        data = resp.json()
        file_id = data["file_id"]
        assert data["name"] == "test.txt"
        assert data["size"] == len(content)
        assert "test" in data["tags"]

        # Retrieve metadata
        resp = vein_client.get(f"/api/vein/files/{file_id}")
        assert resp.status_code == 200
        assert resp.json()["file_id"] == file_id

        # Download
        resp = vein_client.get(f"/api/vein/files/{file_id}/download")
        assert resp.status_code == 200
        assert resp.content == content

        # Cleanup
        vein_client.delete(f"/api/vein/files/{file_id}")

    def test_list_files(self, vein_client):
        resp = vein_client.get("/api/vein/files")
        assert resp.status_code == 200
        data = resp.json()
        assert "files" in data
        assert "count" in data

    def test_dedup(self, vein_client):
        content = b"dedup test content"
        r1 = vein_client.post(
            "/api/vein/upload",
            files={"file": ("dup1.txt", content, "text/plain")},
        )
        r2 = vein_client.post(
            "/api/vein/upload",
            files={"file": ("dup2.txt", content, "text/plain")},
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
        # Same content hash
        assert r1.json()["content_hash"] == r2.json()["content_hash"]
        # Different file IDs
        assert r1.json()["file_id"] != r2.json()["file_id"]

        # Cleanup
        vein_client.delete(f"/api/vein/files/{r1.json()['file_id']}")
        vein_client.delete(f"/api/vein/files/{r2.json()['file_id']}")

    def test_delete_nonexistent(self, vein_client):
        resp = vein_client.delete("/api/vein/files/nonexistent_id")
        assert resp.status_code == 404


class TestVeinCache:
    def test_cache_stats(self, vein_client):
        resp = vein_client.get("/api/vein/cache/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert "max_size_bytes" in data

    def test_cache_put_get_delete(self, vein_client):
        import base64

        payload = {"key": "test_key_123", "data": base64.b64encode(b"cached data").decode()}
        resp = vein_client.put("/api/vein/cache", json=payload)
        assert resp.status_code == 200

        resp = vein_client.get("/api/vein/cache/test_key_123")
        assert resp.status_code == 200

        resp = vein_client.delete("/api/vein/cache/test_key_123")
        assert resp.status_code == 200


class TestVeinChunkedUpload:
    def test_init_upload_session(self, vein_client):
        resp = vein_client.post(
            "/api/vein/upload/chunked/init",
            json={
                "filename": "big.bin",
                "total_size": 1024,
                "chunk_size": 512,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "upload_id" in data
        assert data["filename"] == "big.bin"

        # Cleanup
        vein_client.delete(f"/api/vein/upload/chunked/{data['upload_id']}")

    def test_list_uploads(self, vein_client):
        resp = vein_client.get("/api/vein/upload/chunked")
        assert resp.status_code == 200
        assert "sessions" in resp.json()


class TestVeinStats:
    def test_stats(self, vein_client):
        resp = vein_client.get("/api/vein/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "store" in data
        assert "cache" in data
        assert "uploads" in data


# ── 测试隔离护栏（TL-② 拆件④：live 写方收编）──────────────────

PROD_TIMELINE_DB = str(Path(__file__).resolve().parents[1] / "data" / "timeline.db")
PROD_VEIN_DIR = Path(os.path.expanduser("~/opensoul/data/vein"))


def _count_prod_summary(marker: str) -> int:
    """只读数生产 timeline.db 中含哨兵的行数（file:...?mode=ro URI，绝不写）。"""
    if not Path(PROD_TIMELINE_DB).exists():
        return 0
    con = sqlite3.connect(f"file:{PROD_TIMELINE_DB}?mode=ro", uri=True)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM events WHERE summary LIKE ?", (f"%{marker}%",)
        ).fetchone()[0]
    finally:
        con.close()


class TestTl2VeinGuard:
    """TL-② 拆件④ 隔离护栏：vein 事件面/文件面/分片面全落 tmp，生产零测试行。"""

    def test_api_events_land_in_tmp_timeline_not_production(self, vein_client):
        marker = f"tl2-vein-guard-{uuid.uuid4().hex}.txt"
        resp = vein_client.post(
            "/api/vein/upload",
            files={"file": (marker, b"tl2 vein guard payload", "text/plain")},
        )
        assert resp.status_code == 200

        tmp_db = vein_client.app.state.vein_client_timeline_db
        assert tmp_db == os.environ["TIMELINE_DB"]  # 隔离链真源一致（禁只信构造参数）
        assert tmp_db != PROD_TIMELINE_DB
        con = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
        try:
            hits = con.execute(
                "SELECT COUNT(*) FROM events WHERE summary LIKE ?", (f"%{marker}%",)
            ).fetchone()[0]
        finally:
            con.close()
        assert hits == 1  # file_uploaded 哨兵真落 tmp（进程内真路径真消费）
        assert _count_prod_summary(marker) == 0  # 生产库零测试行（修前恒落生产）

    def test_file_face_lands_in_tmp_not_production(self, vein_client):
        import src.api.vein as vein_api

        marker = f"tl2-vein-file-{uuid.uuid4().hex}.txt"
        resp = vein_client.post(
            "/api/vein/upload",
            files={"file": (marker, b"tl2 vein file face", "text/plain")},
        )
        assert resp.status_code == 200
        file_id = resp.json()["file_id"]

        # 真源一致：handler 写的模块单例=fixture tmp 实例（禁只信构造参数）
        assert vein_api.store is vein_client.app.state.vein_client_store
        root = Path(vein_client.app.state.vein_client_root)
        meta_db = vein_client.app.state.vein_client_meta_db
        assert str(root) != str(PROD_VEIN_DIR / "files")

        # blob 与 meta 真落 tmp
        con = sqlite3.connect(f"file:{meta_db}?mode=ro", uri=True)
        try:
            tmp_rows = con.execute(
                "SELECT COUNT(*) FROM files WHERE file_id=?", (file_id,)
            ).fetchone()[0]
        finally:
            con.close()
        assert tmp_rows == 1
        assert any(root.rglob("*"))  # blob 真落 tmp root

        # 生产 meta 库零测试行（只读探针）
        prod_meta = PROD_VEIN_DIR / "vein_meta.db"
        if prod_meta.exists():
            con = sqlite3.connect(f"file:{prod_meta}?mode=ro", uri=True)
            try:
                prod_rows = con.execute(
                    "SELECT COUNT(*) FROM files WHERE file_id=?", (file_id,)
                ).fetchone()[0]
            finally:
                con.close()
            assert prod_rows == 0

    def test_chunked_temp_face_lands_in_tmp_not_production(self, vein_client):
        import src.api.vein as vein_api

        assert vein_api.uploader is vein_client.app.state.vein_client_uploader
        uploads_dir = Path(vein_client.app.state.vein_client_uploads)
        assert str(uploads_dir) != str(PROD_VEIN_DIR / "uploads")

        resp = vein_client.post(
            "/api/vein/upload/chunked/init",
            json={"filename": "tl2-vein-chunk.bin", "total_size": 4, "chunk_size": 2},
        )
        assert resp.status_code == 200
        upload_id = resp.json()["upload_id"]
        assert (uploads_dir / upload_id).is_dir()  # 分片临时面真落 tmp

        prod_uploads = PROD_VEIN_DIR / "uploads"
        if prod_uploads.is_dir():
            assert upload_id not in {p.name for p in prod_uploads.iterdir()}
