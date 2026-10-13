"""Integration tests for OpenMarrow (骨髓) — backup, restore, export/import."""

import os
import sqlite3
import uuid
from pathlib import Path

import pytest

PROD_TIMELINE_DB = str(Path(__file__).resolve().parents[1] / "data" / "timeline.db")
PROD_BACKUP_DIR = Path(os.path.expanduser("~/.opensoul/backups"))
PROD_EXPORT_DIR = Path(os.path.expanduser("~/.opensoul/exports"))


@pytest.fixture()
def backup_src(tmp_path):
    """备份测试源目录：固定2个小文件的专用目录。
    不用tempfile.gettempdir()——整目录tar /tmp（本机724MB）会让backup请求
    超过客户端30s预算（httpx.ReadTimeout），大小相关=天然flaky。"""
    d = tmp_path / "marrow_src"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_text("alpha", encoding="utf-8")
    (d / "sub" / "b.txt").write_text("beta", encoding="utf-8")
    return str(d)


class TestMarrowHealth:
    def test_health(self, marrow_client):
        resp = marrow_client.get("/api/marrow/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenMarrow"
        assert "backup" in data


class TestMarrowBackup:
    def test_list_backups(self, marrow_client):
        resp = marrow_client.get("/api/marrow/backups")
        assert resp.status_code == 200

    def test_create_and_delete_backup(self, marrow_client, backup_src):
        resp = marrow_client.post(
            "/api/marrow/backup",
            json={
                "name": "test_backup_integration",
                "description": "Created by integration test",
                "source_dirs": [backup_src],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "backup_id" in data
        backup_id = data["backup_id"]

        # Get specific backup
        resp = marrow_client.get(f"/api/marrow/backups/{backup_id}")
        assert resp.status_code == 200

        # Delete
        resp = marrow_client.delete(f"/api/marrow/backups/{backup_id}")
        assert resp.status_code == 200


class TestMarrowExport:
    def test_list_exports(self, marrow_client):
        resp = marrow_client.get("/api/marrow/exports")
        assert resp.status_code == 200

    def test_export_data(self, marrow_client):
        resp = marrow_client.post(
            "/api/marrow/export",
            json={
                "format": "json",
                "data": [{"type": "knowledge", "name": "test"}],
            },
        )
        assert resp.status_code == 200


class TestMarrowSchedules:
    def test_list_schedules(self, marrow_client):
        resp = marrow_client.get("/api/marrow/schedules")
        assert resp.status_code == 200
        assert "schedules" in resp.json()

    def test_create_and_delete_schedule(self, marrow_client, backup_src):
        resp = marrow_client.post(
            "/api/marrow/schedules",
            json={
                "name": "test_schedule",
                "source_dirs": [backup_src],
                "interval": "daily",
                "description": "Integration test schedule",
                "tags": ["test"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "schedule_id" in data
        assert data["name"] == "test_schedule"
        assert data["cron_expr"] == "daily"
        schedule_id = data["schedule_id"]

        # List schedules — should include our new one
        resp = marrow_client.get("/api/marrow/schedules")
        assert resp.status_code == 200
        ids = [s["schedule_id"] for s in resp.json()["schedules"]]
        assert schedule_id in ids

        # Toggle off
        resp = marrow_client.put(
            f"/api/marrow/schedules/{schedule_id}/toggle", json={"enabled": False}
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        # Toggle on
        resp = marrow_client.put(
            f"/api/marrow/schedules/{schedule_id}/toggle", json={"enabled": True}
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

        # Delete
        resp = marrow_client.delete(f"/api/marrow/schedules/{schedule_id}")
        assert resp.status_code == 200

    def test_create_schedule_invalid_interval(self, marrow_client, tmp_path):
        resp = marrow_client.post(
            "/api/marrow/schedules",
            json={
                "name": "bad_schedule",
                "source_dirs": [str(tmp_path)],
                "interval": "invalid",
            },
        )
        assert resp.status_code == 400

    def test_health_includes_scheduler(self, marrow_client):
        resp = marrow_client.get("/api/marrow/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "scheduler" in data
        assert "running" in data["scheduler"]


class TestTl2MarrowGuard:
    """TL-② 拆件⑥ 隔离护栏：marrow 备份/调度/导出面与事件面全落 tmp，生产
    timeline.db 与 ~/.opensoul/backups、~/.opensoul/exports 零测试行。"""

    @staticmethod
    def _count(db, sql, args=()):
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return con.execute(sql, args).fetchone()[0]
        finally:
            con.close()

    @staticmethod
    def _prod_marker_hits(marker):
        """生产备份/导出面哨兵扫描：文件名全量匹配，内容只读小文本文件
        （tar.gz 归档不读内容——大二进制读全文会挂死）。"""
        hits = 0
        for d in (PROD_BACKUP_DIR, PROD_EXPORT_DIR):
            if not d.exists():
                continue
            for p in d.rglob("*"):
                if not p.is_file():
                    continue
                if marker in p.name:
                    hits += 1
                    continue
                if p.suffix not in (".json", ".jsonl", ".txt"):
                    continue
                try:
                    if p.stat().st_size < 1024 * 1024 and marker in p.read_text(
                        encoding="utf-8", errors="ignore"
                    ):
                        hits += 1
                except OSError:
                    pass
        return hits

    def test_backup_face_lands_in_tmp_not_production(self, marrow_client, tmp_path):
        import src.api.marrow as marrow_api

        marker = "tl2-marrow-guard-" + uuid.uuid4().hex
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "a.txt").write_text("alpha", encoding="utf-8")
        resp = marrow_client.post(
            "/api/marrow/backup",
            json={"name": marker, "source_dirs": [str(src_dir)]},
        )
        assert resp.status_code == 200

        # 真源一致：handler 用的模块单例=fixture tmp 实例（禁只信构造参数）
        assert marrow_api.backup_manager is marrow_client.app.state.marrow_client_backup_manager
        backup_dir = Path(marrow_client.app.state.marrow_client_backup_dir)
        assert str(backup_dir) != str(PROD_BACKUP_DIR)
        assert any(p.name.endswith(".tar.gz") for p in backup_dir.iterdir())  # 备份 tar 真落 tmp
        assert marker in (backup_dir / "manifests.json").read_text(
            encoding="utf-8"
        )  # name 真落 tmp manifest

        tmp_tl = marrow_client.app.state.marrow_client_timeline_db
        assert tmp_tl == os.environ["TIMELINE_DB"]  # 隔离链真源一致
        assert tmp_tl != PROD_TIMELINE_DB
        assert (
            self._count(
                tmp_tl,
                "SELECT COUNT(*) FROM events WHERE summary LIKE ?",
                (f"%{marker}%",),
            )
            == 1
        )  # backup_created 哨兵真落 tmp（summary 带 name=哨兵可注入）
        assert (
            self._count(
                PROD_TIMELINE_DB,
                "SELECT COUNT(*) FROM events WHERE summary LIKE ?",
                (f"%{marker}%",),
            )
            == 0
        )  # 生产库零测试行（修前恒落生产）
        assert self._prod_marker_hits(marker) == 0  # 生产备份/导出面零测试行（修前恒写生产）

    def test_schedule_face_lands_in_tmp_not_production(self, marrow_client, tmp_path):
        import src.api.marrow as marrow_api

        marker = "tl2-marrow-sch-" + uuid.uuid4().hex
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        resp = marrow_client.post(
            "/api/marrow/schedules",
            json={"name": marker, "source_dirs": [str(src_dir)], "interval": "daily"},
        )
        assert resp.status_code == 200

        assert marrow_api.scheduler is marrow_client.app.state.marrow_client_scheduler
        backup_dir = Path(marrow_client.app.state.marrow_client_backup_dir)
        assert marker in (backup_dir / "schedules.json").read_text(encoding="utf-8")
        assert self._prod_marker_hits(marker) == 0  # 生产 schedules.json 零测试行
        tmp_tl = marrow_client.app.state.marrow_client_timeline_db
        assert (
            self._count(
                tmp_tl,
                "SELECT COUNT(*) FROM events WHERE summary LIKE ?",
                (f"%{marker}%",),
            )
            == 1
        )

    def test_export_face_lands_in_tmp_not_production(self, marrow_client):
        marker = "tl2-marrow-exp-" + uuid.uuid4().hex
        prod_before = (
            len([p for p in PROD_EXPORT_DIR.iterdir() if p.is_file()])
            if PROD_EXPORT_DIR.exists()
            else 0
        )
        resp = marrow_client.post(
            "/api/marrow/export",
            json={"format": "json", "data": [{"type": "knowledge", "name": marker}]},
        )
        assert resp.status_code == 200

        export_dir = Path(marrow_client.app.state.marrow_client_export_dir)
        assert str(export_dir) != str(PROD_EXPORT_DIR)
        files = [p for p in export_dir.iterdir() if p.is_file()]
        assert files  # 导出真落 tmp
        assert any(marker in p.read_text(encoding="utf-8") for p in files)
        prod_after = (
            len([p for p in PROD_EXPORT_DIR.iterdir() if p.is_file()])
            if PROD_EXPORT_DIR.exists()
            else 0
        )
        assert prod_after == prod_before  # 生产导出面零测试文件（修前恒写生产）
