"""Integration tests for OpenImmune (免疫) — security, rate limiting, audit."""

import os
import sqlite3
import uuid
from pathlib import Path

PROD_TIMELINE_DB = str(Path(__file__).resolve().parents[1] / "data" / "timeline.db")
PROD_PERM_DB = Path(os.path.expanduser("~/.hermes/opensoul/immune/permission.db"))


class TestImmuneHealth:
    def test_health(self, immune_client):
        resp = immune_client.get("/api/immune/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenImmune"
        assert "modules" in data
        assert "rate_limiter" in data["modules"]
        assert "moderator" in data["modules"]


class TestImmuneModeration:
    def test_moderate_clean_content(self, immune_client):
        resp = immune_client.post(
            "/api/immune/moderate",
            json={
                "text": "Hello, this is a normal message.",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "is_safe" in data
        assert data["is_safe"] is True

    def test_moderate_requires_text(self, immune_client):
        resp = immune_client.post("/api/immune/moderate", json={})
        assert resp.status_code == 422


class TestImmuneRateLimit:
    def test_rate_limit_check(self, immune_client):
        resp = immune_client.post(
            "/api/immune/rate-limit/check",
            json={
                "key": "test_user_integration",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "allowed" in data

    def test_rate_limit_stats(self, immune_client):
        resp = immune_client.get("/api/immune/rate-limit/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "tracked_keys" in data

    def test_rate_limit_config_update(self, immune_client):
        resp = immune_client.put(
            "/api/immune/rate-limit/config",
            json={
                "requests_per_minute": 60,
                "requests_per_hour": 1000,
                "burst_size": 20,
            },
        )
        assert resp.status_code == 200


class TestImmuneIPLists:
    def test_get_lists(self, immune_client):
        resp = immune_client.get("/api/immune/ip/lists")
        assert resp.status_code == 200
        data = resp.json()
        assert "blacklist" in data
        assert "whitelist" in data

    def test_blacklist_add_remove(self, immune_client):
        ip = "192.168.99.99"
        # Add
        resp = immune_client.post("/api/immune/ip/blacklist", json={"ip": ip, "reason": "test"})
        assert resp.status_code == 200
        # Check
        resp = immune_client.get(f"/api/immune/ip/check/{ip}")
        assert resp.status_code == 200
        assert resp.json()["allowed"] is False
        # Remove
        resp = immune_client.delete(f"/api/immune/ip/blacklist/{ip}")
        assert resp.status_code == 200


class TestImmuneAudit:
    def test_audit_log(self, immune_client):
        resp = immune_client.get("/api/immune/audit/log")
        assert resp.status_code == 200

    def test_audit_stats(self, immune_client):
        resp = immune_client.get("/api/immune/audit/stats")
        assert resp.status_code == 200


class TestTl2ImmuneGuard:
    """TL-② 拆件⑤ 隔离护栏：immune 事件面/权限持久面/内存单例面全落 tmp，
    生产 timeline.db 与生产 permission.db 零测试行。"""

    def test_event_face_lands_in_tmp_timeline_not_production(self, immune_client):
        marker = f"tl2-immune-guard-{uuid.uuid4().hex}"
        resp = immune_client.post(
            "/api/immune/ip/blacklist", json={"ip": "10.99.88.77", "reason": marker}
        )
        assert resp.status_code == 200

        tmp_db = immune_client.app.state.immune_client_timeline_db
        assert tmp_db == os.environ["TIMELINE_DB"]  # 隔离链真源一致（禁只信构造参数）
        assert tmp_db != PROD_TIMELINE_DB
        con = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
        try:
            hits = con.execute(
                "SELECT COUNT(*) FROM events WHERE summary LIKE ?", (f"%{marker}%",)
            ).fetchone()[0]
        finally:
            con.close()
        assert hits == 1  # ip_blacklisted 哨兵真落 tmp（进程内真路径真消费）
        con = sqlite3.connect(f"file:{PROD_TIMELINE_DB}?mode=ro", uri=True)
        try:
            prod_hits = con.execute(
                "SELECT COUNT(*) FROM events WHERE summary LIKE ?", (f"%{marker}%",)
            ).fetchone()[0]
        finally:
            con.close()
        assert prod_hits == 0  # 生产库零测试行（修前恒落生产）

    def test_permission_audit_face_lands_in_tmp_store_not_production(self, immune_client):
        import src.api.immune as immune_api

        marker = f"tl2-immune-perm-{uuid.uuid4().hex}"
        resp = immune_client.post(
            "/api/immune/permission/check",
            json={
                "tool_name": "read_file",
                "arguments": {"path": "src/main.py"},
                "session_id": marker,
            },
        )
        assert resp.status_code == 200

        # 真源一致：handler 用的模块单例=fixture tmp 实例（禁只信构造参数）
        assert (
            immune_api.permission_service
            is immune_client.app.state.immune_client_permission_service
        )
        tmp_db = immune_client.app.state.immune_client_permission_db
        assert tmp_db != str(PROD_PERM_DB)
        con = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
        try:
            tmp_rows = con.execute(
                "SELECT COUNT(*) FROM perm_decision_audit WHERE session_id=?", (marker,)
            ).fetchone()[0]
        finally:
            con.close()
        assert tmp_rows == 1  # 决策审计真落 tmp permission.db

        if PROD_PERM_DB.exists():
            con = sqlite3.connect(f"file:{PROD_PERM_DB}?mode=ro", uri=True)
            try:
                prod_rows = con.execute(
                    "SELECT COUNT(*) FROM perm_decision_audit WHERE session_id=?", (marker,)
                ).fetchone()[0]
            finally:
                con.close()
            assert prod_rows == 0  # 生产 permission.db 零测试行（修前恒写生产）

    def test_module_singletons_fresh_tmp_backed(self, immune_client):
        import src.api.immune as immune_api

        state = immune_client.app.state
        assert immune_api.rate_limiter is state.immune_client_rate_limiter
        assert immune_api.ip_control is state.immune_client_ip_control
        assert immune_api.permission_service is state.immune_client_permission_service
        assert immune_api.audit is state.immune_client_audit

        # 状态面真隔离：黑名单写 fixture 实例（生产服务进程单例零接触）
        ip = f"10.77.66.{uuid.uuid4().int % 200}"
        resp = immune_client.post("/api/immune/ip/blacklist", json={"ip": ip, "reason": "guard"})
        assert resp.status_code == 200
        assert state.immune_client_ip_control.is_allowed(ip)["allowed"] is False
