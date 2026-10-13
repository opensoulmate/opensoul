"""Tests for OpenSoma Connector API — component registration, heartbeat, and data push."""

import os
import sqlite3
import uuid
from pathlib import Path


class TestSomaHealth:
    def test_soma_health(self, soma_client):
        resp = soma_client.get("/api/soma/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestComponentRegistration:
    def test_register_component(self, soma_client):
        resp = soma_client.post(
            "/api/soma/register",
            json={
                "component_id": "test-node-1",
                "name": "Test Node",
                "component_type": "collector",
                "version": "0.1.0",
                "capabilities": ["file", "clipboard"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["component_id"] == "test-node-1"
        assert data["name"] == "Test Node"
        assert data["status"] == "online"
        assert "secret_token" in data

    def test_register_updates_existing(self, soma_client):
        # Register first（每用例独立注册表——修前靠 test_register_component 跨测试残留，
        # 隔离后自包含：先注册再重注册才真走 update-existing 路径）
        soma_client.post(
            "/api/soma/register",
            json={
                "component_id": "test-node-1",
                "name": "Test Node",
                "component_type": "collector",
                "version": "0.1.0",
            },
        )
        # Register again with different version
        resp = soma_client.post(
            "/api/soma/register",
            json={
                "component_id": "test-node-1",
                "name": "Test Node Updated",
                "component_type": "collector",
                "version": "0.2.0",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test Node Updated"

    def test_list_components(self, soma_client):
        # 每用例独立注册表——修前靠 test_register_component 跨测试残留，隔离后自包含
        soma_client.post(
            "/api/soma/register",
            json={
                "component_id": "test-node-1",
                "name": "Test Node",
                "component_type": "collector",
                "version": "0.1.0",
            },
        )
        resp = soma_client.get("/api/soma/components")
        assert resp.status_code == 200
        data = resp.json()
        assert "components" in data
        ids = [c["component_id"] for c in data["components"]]
        assert "test-node-1" in ids

    def test_get_component(self, soma_client):
        # 每用例独立注册表——修前靠 test_register_component 跨测试残留，隔离后自包含
        soma_client.post(
            "/api/soma/register",
            json={
                "component_id": "test-node-1",
                "name": "Test Node",
                "component_type": "collector",
                "version": "0.1.0",
            },
        )
        resp = soma_client.get("/api/soma/components/test-node-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["component_id"] == "test-node-1"
        assert data["component_type"] == "collector"

    def test_get_nonexistent_component(self, soma_client):
        resp = soma_client.get("/api/soma/components/nonexistent-xyz-999")
        assert resp.status_code == 404


class TestHeartbeat:
    def test_heartbeat(self, soma_client):
        # First register to get token
        reg = soma_client.post(
            "/api/soma/register",
            json={
                "component_id": "test-hb-node",
                "name": "HB Test",
                "component_type": "test",
                "version": "0.0.1",
            },
        )
        token = reg.json().get("secret_token", "")

        resp = soma_client.post(
            "/api/soma/heartbeat",
            json={"component_id": "test-hb-node"},
            headers={"X-Component-Token": token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["component_id"] == "test-hb-node"
        assert data["status"] == "acknowledged"


class TestDataPush:
    def test_push_data(self, soma_client):
        # Register to get token
        reg = soma_client.post(
            "/api/soma/register",
            json={
                "component_id": "test-push-node",
                "name": "Push Test",
                "component_type": "test",
                "version": "0.0.1",
            },
        )
        token = reg.json().get("secret_token", "")

        resp = soma_client.post(
            "/api/soma/push",
            json={
                "data_type": "file_change",
                "payload": {"path": "/tmp/test.txt", "action": "created"},
            },
            headers={
                "X-Component-Id": "test-push-node",
                "X-Component-Token": token,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data_type"] == "file_change"

    def test_push_data_missing_header(self, soma_client):
        resp = soma_client.post(
            "/api/soma/push",
            json={
                "data_type": "test",
                "payload": {},
            },
        )
        assert resp.status_code == 400


class TestCapabilities:
    def test_capabilities(self, soma_client):
        resp = soma_client.get("/api/soma/capabilities")
        assert resp.status_code == 200


class TestStats:
    def test_stats(self, soma_client):
        resp = soma_client.get("/api/soma/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data or "total_components" in data


class TestComponentCleanup:
    def test_delete_component(self, soma_client):
        # Register a temp component
        soma_client.post(
            "/api/soma/register",
            json={
                "component_id": "test-delete-me",
                "name": "Delete Me",
                "component_type": "test",
                "version": "0.0.1",
            },
        )
        # Delete it
        resp = soma_client.delete("/api/soma/components/test-delete-me")
        assert resp.status_code == 200

        # Verify it's gone
        resp = soma_client.get("/api/soma/components/test-delete-me")
        assert resp.status_code == 404


# ── 测试隔离护栏（TL-② 拆件③：live 写方收编）──────────────────

PROD_TIMELINE_DB = str(Path(__file__).resolve().parents[1] / "data" / "timeline.db")


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


class TestTl2SomaGuard:
    """TL-② 拆件③ 隔离护栏：soma 事件面/组件注册面全落 tmp，生产库零测试行。"""

    def test_api_events_land_in_tmp_timeline_not_production(self, soma_client):
        marker = f"tl2-soma-guard-{uuid.uuid4().hex}"
        resp = soma_client.post(
            "/api/soma/register",
            json={
                "component_id": marker,
                "name": marker,
                "component_type": "test",
                "version": "0.0.1",
            },
        )
        assert resp.status_code == 200

        tmp_db = soma_client.app.state.soma_client_timeline_db
        assert tmp_db == os.environ["TIMELINE_DB"]  # 隔离链真源一致（禁只信构造参数）
        assert tmp_db != PROD_TIMELINE_DB
        con = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
        try:
            hits = con.execute(
                "SELECT COUNT(*) FROM events WHERE summary LIKE ?", (f"%{marker}%",)
            ).fetchone()[0]
        finally:
            con.close()
        assert hits == 1  # component_registered 哨兵真落 tmp（进程内真路径真消费）
        assert _count_prod_summary(marker) == 0  # 生产库零测试行（修前恒落生产）

    def test_registry_is_tmp_fresh_not_production(self, soma_client):
        import src.api.soma_connector as soma_api

        tmp_registry = soma_client.app.state.soma_client_registry
        assert soma_api.registry is tmp_registry  # 真源一致：handler 写的模块单例=fixture tmp 实例
        assert (
            tmp_registry.get("test-node-1") is None
        )  # 每用例 fresh（修前 live 污染残留 id 不在场）

        marker = f"tl2-soma-reg-{uuid.uuid4().hex}"
        resp = soma_client.post(
            "/api/soma/register",
            json={
                "component_id": marker,
                "name": marker,
                "component_type": "test",
                "version": "0.0.1",
            },
        )
        assert resp.status_code == 200
        assert tmp_registry.get(marker) is not None  # 注册状态真落 tmp 注册面
        assert tmp_registry.get(marker)["status"] == "online"

    def test_push_state_lands_in_tmp_registry(self, soma_client):
        marker = f"tl2-soma-push-{uuid.uuid4().hex}"
        reg = soma_client.post(
            "/api/soma/register",
            json={
                "component_id": marker,
                "name": marker,
                "component_type": "test",
                "version": "0.0.1",
            },
        )
        token = reg.json().get("secret_token", "")
        resp = soma_client.post(
            "/api/soma/push",
            json={"data_type": "file_change", "payload": {"path": "/tmp/guard.txt"}},
            headers={"X-Component-Id": marker, "X-Component-Token": token},
        )
        assert resp.status_code == 200

        import src.api.soma_connector as soma_api

        assert soma_api.registry is soma_client.app.state.soma_client_registry  # 真源一致
        comp = soma_api.registry.get(marker)
        assert comp is not None and comp.get("data_push_count") == 1  # push 计数真落 tmp 注册面
