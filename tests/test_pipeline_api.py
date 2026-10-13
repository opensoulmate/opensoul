"""Integration tests for Pipeline API — file upload and smart processing pipeline."""

import os
import sqlite3
import uuid
from pathlib import Path


class TestPipelineHealth:
    def test_pipeline_history(self, pipeline_client):
        resp = pipeline_client.get("/api/pipeline/history")
        assert resp.status_code == 200
        data = resp.json()
        assert "pipelines" in data
        assert "total" in data
        assert isinstance(data["pipelines"], list)

    def test_pipeline_history_limit(self, pipeline_client):
        resp = pipeline_client.get("/api/pipeline/history?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["pipelines"]) <= 5


class TestPipelineUpload:
    def test_upload_text_file(self, pipeline_client):
        """Upload a simple text file through the pipeline."""
        files = {
            "file": (
                "test.txt",
                b"Hello world, this is a test document for pipeline.",
                "text/plain",
            )
        }
        data = {"pipeline": "text", "skip_immune": "true", "skip_knowledge": "true"}
        resp = pipeline_client.post("/api/pipeline/upload", files=files, data=data)
        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] in ("completed", "partial")
        assert "pipeline_id" in result
        assert "steps" in result
        assert len(result["steps"]) >= 1

    def test_upload_auto_detect(self, pipeline_client):
        """Auto-detect pipeline type from file extension."""
        files = {
            "file": ("readme.md", b"# Test markdown content for auto-detection", "text/markdown")
        }
        data = {"pipeline": "auto", "skip_immune": "true", "skip_knowledge": "true"}
        resp = pipeline_client.post("/api/pipeline/upload", files=files, data=data)
        assert resp.status_code == 200
        result = resp.json()
        assert result["pipeline_type"] == "text"

    def test_upload_image_auto_detect(self, pipeline_client):
        """Auto-detect should select OCR for image files."""
        # Minimal 1x1 PNG
        png_data = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
            b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
            b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        files = {"file": ("test.png", png_data, "image/png")}
        data = {"pipeline": "auto", "skip_immune": "true", "skip_knowledge": "true"}
        resp = pipeline_client.post("/api/pipeline/upload", files=files, data=data)
        assert resp.status_code == 200
        result = resp.json()
        assert result["pipeline_type"] == "ocr"

    def test_upload_with_tags(self, pipeline_client):
        """Tags should be included in pipeline metadata."""
        files = {"file": ("tagged.txt", b"Content with tags", "text/plain")}
        data = {
            "pipeline": "text",
            "tags": "test,integration",
            "skip_immune": "true",
            "skip_knowledge": "true",
        }
        resp = pipeline_client.post("/api/pipeline/upload", files=files, data=data)
        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] in ("completed", "partial")


class TestPipelineRun:
    def test_run_nonexistent_file(self, pipeline_client):
        """Running pipeline on a non-existent file should fail."""
        resp = pipeline_client.post(
            "/api/pipeline/run",
            json={
                "file_id": "nonexistent-file-id",
                "pipeline": "text",
                "skip_immune": True,
                "skip_knowledge": True,
            },
        )
        assert resp.status_code in (404, 500)


class TestPipelineDetection:
    def test_detect_pipeline_types(self):
        """Test the pipeline auto-detection function."""
        from src.api.pipeline import _detect_pipeline

        assert _detect_pipeline("image/png", "test.png") == "ocr"
        assert _detect_pipeline("audio/wav", "test.wav") == "asr"
        assert _detect_pipeline("video/mp4", "test.mp4") == "video"
        assert _detect_pipeline("text/plain", "test.txt") == "text"
        assert _detect_pipeline("application/json", "data.json") == "text"
        assert _detect_pipeline("application/pdf", "doc.pdf") == "text"
        # Fallback by extension
        assert _detect_pipeline("application/octet-stream", "photo.jpg") == "ocr"
        assert _detect_pipeline("application/octet-stream", "song.mp3") == "asr"
        assert _detect_pipeline("application/octet-stream", "clip.mp4") == "video"
        assert _detect_pipeline("application/octet-stream", "readme.md") == "text"
        # Unknown fallback
        assert _detect_pipeline("application/octet-stream", "unknown.xyz") == "text"


# ── 测试隔离护栏（TL-② 拆件②：live 写方收编）──────────────────

PROD_TIMELINE_DB = str(Path(__file__).resolve().parents[1] / "data" / "timeline.db")
PROD_VEIN_DB = str(Path(__file__).resolve().parents[1] / "data" / "vein" / "vein_meta.db")
PROD_OPENSOUL_DB = str(Path(__file__).resolve().parents[1] / "data" / "opensoul.db")


def _ro_count(db_path, sql, params=()):
    """只读数库中匹配行数（file:...?mode=ro URI，绝不写）。"""
    if not Path(db_path).exists():
        return 0
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return con.execute(sql, params).fetchone()[0]
    finally:
        con.close()


class TestTl2PipelineGuard:
    """TL-② 拆件② 隔离护栏：事件/vein 文件面/知识入库面全落 tmp，生产库零测试行。"""

    def test_api_events_land_in_tmp_timeline_not_production(self, pipeline_client):
        marker = f"tl2pipe-{uuid.uuid4().hex}"
        resp = pipeline_client.post(
            "/api/pipeline/upload",
            files={"file": (f"{marker}.txt", b"guard sentinel content for timeline", "text/plain")},
            data={"pipeline": "text", "skip_immune": "true", "skip_knowledge": "true"},
        )
        assert resp.status_code == 200

        tmp_db = pipeline_client.app.state.pipeline_client_timeline_db
        assert tmp_db == os.environ["TIMELINE_DB"]  # 隔离链真源一致（禁只信构造参数）
        assert tmp_db != PROD_TIMELINE_DB
        hits = _ro_count(
            tmp_db, "SELECT COUNT(*) FROM events WHERE summary LIKE ?", (f"%{marker}%",)
        )
        assert hits == 1  # pipeline_completed 哨兵真落 tmp（进程内真路径真消费）
        prod_hits = _ro_count(
            PROD_TIMELINE_DB, "SELECT COUNT(*) FROM events WHERE summary LIKE ?", (f"%{marker}%",)
        )
        assert prod_hits == 0  # 生产库零测试行（修前恒落生产）

    def test_vein_files_land_in_tmp_not_production(self, pipeline_client):
        marker = f"tl2vein-{uuid.uuid4().hex}"
        resp = pipeline_client.post(
            "/api/pipeline/upload",
            files={"file": (f"{marker}.txt", b"guard sentinel content for vein", "text/plain")},
            data={"pipeline": "text", "skip_immune": "true", "skip_knowledge": "true"},
        )
        assert resp.status_code == 200
        result = resp.json()
        vein_step = next(s for s in result["steps"] if s["step"] == "vein")
        assert vein_step["status"] == "ok"

        tmp_root = Path(pipeline_client.app.state.pipeline_client_vein_root)
        assert tmp_root != Path.home() / "opensoul" / "data" / "vein" / "files"
        blob_hits = [p for p in tmp_root.rglob("*") if p.name == vein_step["content_hash"]]
        assert blob_hits  # 文件 blob 真落 tmp root（content-addressable 路径）
        tmp_meta = pipeline_client.app.state.pipeline_client_vein_db
        assert (
            _ro_count(tmp_meta, "SELECT COUNT(*) FROM files WHERE name LIKE ?", (f"%{marker}%",))
            == 1
        )
        prod_hits = _ro_count(
            PROD_VEIN_DB, "SELECT COUNT(*) FROM files WHERE name LIKE ?", (f"%{marker}%",)
        )
        assert prod_hits == 0  # 生产 vein 零测试行（修前落生产 vein_meta.db+files）

    def test_knowledge_rows_land_in_tmp_db_not_production(self, pipeline_client):
        marker = f"tl2know-{uuid.uuid4().hex}"
        user = f"tl2user-{uuid.uuid4().hex}"
        resp = pipeline_client.post(
            "/api/pipeline/upload",
            files={
                "file": (
                    f"{marker}.txt",
                    b"guard sentinel content for knowledge import",
                    "text/plain",
                )
            },
            data={"pipeline": "text", "user_id": user, "skip_immune": "true"},
        )
        assert resp.status_code == 200
        result = resp.json()
        know_step = next(s for s in result["steps"] if s["step"] == "knowledge")
        assert know_step["status"] == "ok"  # 真实知识入库路径真跑（tmp 真 schema）

        tmp_db = pipeline_client.app.state.pipeline_client_opensoul_db
        assert (
            _ro_count(tmp_db, "SELECT COUNT(*) FROM knowledge WHERE title LIKE ?", (f"%{marker}%",))
            == 1
        )
        assert _ro_count(tmp_db, "SELECT COUNT(*) FROM users WHERE username = ?", (user,)) == 1
        assert (
            _ro_count(
                PROD_OPENSOUL_DB,
                "SELECT COUNT(*) FROM knowledge WHERE title LIKE ?",
                (f"%{marker}%",),
            )
            == 0
        )
        assert (
            _ro_count(PROD_OPENSOUL_DB, "SELECT COUNT(*) FROM users WHERE username = ?", (user,))
            == 0
        )  # 生产 opensoul.db 零测试行（修前 live 上传写生产知识/用户行）
