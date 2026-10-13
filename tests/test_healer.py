"""Integration tests for Healer API (/api/healer) — self-diagnosis and repair."""

import os
import sqlite3
from pathlib import Path

import pytest

PROD_TIMELINE_DB = str(Path(__file__).resolve().parents[1] / "data" / "timeline.db")


class TestHealerHealth:
    def test_health(self, healer_client):
        resp = healer_client.get("/api/healer/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestHealerStats:
    def test_stats(self, healer_client):
        resp = healer_client.get("/api/healer/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


class TestHealerOrgans:
    def test_organs(self, healer_client):
        resp = healer_client.get("/api/healer/organs")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))


class TestHealerDiagnose:
    def test_diagnose_health(self, healer_client):
        resp = healer_client.post("/api/healer/diagnose/health")
        assert resp.status_code in (200, 400, 404)

    def test_diagnose_nonexistent_organ(self, healer_client):
        resp = healer_client.post("/api/healer/diagnose/nonexistent_organ_xyz")
        assert resp.status_code in (200, 400, 404)

    def test_diagnose_all(self, healer_client):
        resp = healer_client.post("/api/healer/diagnose-all")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (dict, list))


class TestHealerHeal:
    def test_heal_health(self, healer_client):
        resp = healer_client.post("/api/healer/heal/health")
        assert resp.status_code in (200, 400, 404)

    def test_heal_nonexistent_organ(self, healer_client):
        resp = healer_client.post("/api/healer/heal/nonexistent_organ_xyz")
        assert resp.status_code in (200, 400, 404)

    def test_heal_all(self, healer_client):
        resp = healer_client.post("/api/healer/heal-all")
        assert resp.status_code == 200


class TestHealerCycle:
    def test_cycle(self, healer_client):
        resp = healer_client.post("/api/healer/cycle")
        assert resp.status_code in (200, 400)


class TestHealerHistory:
    def test_history(self, healer_client):
        resp = healer_client.get("/api/healer/history")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_history_with_limit(self, healer_client):
        resp = healer_client.get("/api/healer/history?limit=5")
        assert resp.status_code == 200


class TestTl2HealerGuard:
    """TL-② 拆件⑥ 隔离护栏：healer 事件面落 tmp、探针/修复/通知/审计零生产
    接触，生产 timeline.db 零测试行。"""

    @staticmethod
    def _count(db, sql):
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return con.execute(sql).fetchone()[0]
        finally:
            con.close()

    def test_diagnosis_events_land_in_tmp_not_production(self, healer_client):
        prod_before = self._count(
            PROD_TIMELINE_DB, "SELECT COUNT(*) FROM events WHERE event_type='diagnosis_all'"
        )
        resp = healer_client.post("/api/healer/diagnose-all")
        assert resp.status_code == 200

        tmp_tl = healer_client.app.state.healer_client_timeline_db
        assert tmp_tl == os.environ["TIMELINE_DB"]  # 隔离链真源一致
        assert tmp_tl != PROD_TIMELINE_DB
        assert (
            self._count(tmp_tl, "SELECT COUNT(*) FROM events WHERE event_type='diagnosis_all'") == 1
        )  # diagnosis_all 哨兵真落 tmp（进程内真路径真消费）
        assert (
            self._count(
                PROD_TIMELINE_DB,
                "SELECT COUNT(*) FROM events WHERE event_type='diagnosis_all'",
            )
            == prod_before
        )  # 生产库零测试行（修前恒落生产）

    def test_zero_production_contact_and_singleton(self, healer_client):
        import src.api.healer as healer_api

        tmp_healer = healer_client.app.state.healer_client_healer
        # 真源一致：handler 用的模块单例=fixture 实例（禁只信构造参数）
        assert healer_api.healer is tmp_healer
        # 零生产接触结构证：探针/修复/notify/audit 全打死端口，不打生产 :8090
        assert tmp_healer._base == "http://127.0.0.1:1"
        resp = healer_client.post("/api/healer/heal-all")
        assert resp.status_code == 200
        # 历史/统计面真落 fixture 内存实例（修前留生产服务进程内存单例）
        resp = healer_client.get("/api/healer/history")
        assert resp.status_code == 200
        assert resp.json()["total"] > 0
