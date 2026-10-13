"""Integration tests for OpenLink (突触) — external system connectors."""

import os
import sqlite3
import uuid
from pathlib import Path

PROD_TIMELINE_DB = str(Path(__file__).resolve().parents[1] / "data" / "timeline.db")


class TestLinkHealth:
    def test_health(self, link_client):
        resp = link_client.get("/api/link/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenLink"


class TestLinkConnectors:
    def test_create_list_delete_connector(self, link_client):
        # Create
        resp = link_client.post(
            "/api/link/connectors",
            json={
                "name": "test_connector",
                "type": "webhook_in",
                "endpoint": "https://example.com/hook",
                "description": "Integration test",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        cid = data["connector_id"]

        # List
        resp = link_client.get("/api/link/connectors")
        assert resp.status_code == 200

        # Get specific
        resp = link_client.get(f"/api/link/connectors/{cid}")
        assert resp.status_code == 200

        # Update
        resp = link_client.patch(
            f"/api/link/connectors/{cid}",
            json={
                "description": "Updated by test",
            },
        )
        assert resp.status_code == 200

        # Delete
        resp = link_client.delete(f"/api/link/connectors/{cid}")
        assert resp.status_code == 200


class TestLinkEvents:
    def test_list_events(self, link_client):
        resp = link_client.get("/api/link/events")
        assert resp.status_code == 200


class TestTl2LinkGuard:
    """TL-② 拆件⑥ 隔离护栏：link 连接器/事件面与事件面全落 tmp 实例，
    生产 timeline.db 零测试行。"""

    @staticmethod
    def _count(db, sql, args=()):
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return con.execute(sql, args).fetchone()[0]
        finally:
            con.close()

    def test_connector_event_lands_in_tmp_not_production(self, link_client):
        marker = "tl2-link-guard-" + uuid.uuid4().hex
        resp = link_client.post(
            "/api/link/connectors",
            json={
                "name": marker,
                "type": "webhook_in",
                "endpoint": "https://example.com/hook",
            },
        )
        assert resp.status_code == 200

        tmp_tl = link_client.app.state.link_client_timeline_db
        assert tmp_tl == os.environ["TIMELINE_DB"]  # 隔离链真源一致
        assert tmp_tl != PROD_TIMELINE_DB
        assert (
            self._count(
                tmp_tl,
                "SELECT COUNT(*) FROM events WHERE summary LIKE ?",
                (f"%{marker}%",),
            )
            == 1
        )  # connector_created 哨兵真落 tmp（summary 带 name=哨兵可注入）
        assert (
            self._count(
                PROD_TIMELINE_DB,
                "SELECT COUNT(*) FROM events WHERE summary LIKE ?",
                (f"%{marker}%",),
            )
            == 0
        )  # 生产库零测试行（修前恒落生产）

    def test_connector_state_in_fixture_instance_not_production(self, link_client):
        import src.api.link as link_api

        state = link_client.app.state
        # 真源一致：handler 用的模块单例=fixture tmp 实例（禁只信构造参数）
        assert link_api.manager is state.link_client_manager
        marker = "tl2-link-state-" + uuid.uuid4().hex
        resp = link_client.post(
            "/api/link/connectors",
            json={"name": marker, "type": "webhook_in", "endpoint": ""},
        )
        assert resp.status_code == 200
        cid = resp.json()["connector_id"]
        # 连接器真落 fixture 实例（修前留生产服务进程内存单例永不自清）
        assert state.link_client_manager.get_connector(cid) is not None
        resp = link_client.delete(f"/api/link/connectors/{cid}")
        assert resp.status_code == 200
        assert state.link_client_manager.get_connector(cid) is None
