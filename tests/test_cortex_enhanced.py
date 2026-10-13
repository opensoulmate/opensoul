"""Integration tests for Cortex Enhanced API (/api/cortex) — GraphRAG, recommendations, quality."""

import os
import sqlite3
import uuid
from pathlib import Path

import pytest

PROD_TIMELINE_DB = str(Path(__file__).resolve().parents[1] / "data" / "timeline.db")
PROD_OSOUL_DB = str(Path(__file__).resolve().parents[1] / "data" / "opensoul.db")


class TestCortexEnhancedHealth:
    def test_enhanced_health(self, cortex_client):
        resp = cortex_client.get("/api/cortex/enhanced/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestCortexGraphRAG:
    def test_graphrag_build_missing(self, cortex_client):
        resp = cortex_client.post("/api/cortex/graphrag/build", json={})
        assert resp.status_code in (200, 400, 422)

    def test_graphrag_query_missing(self, cortex_client):
        resp = cortex_client.post("/api/cortex/graphrag/query", json={})
        assert resp.status_code in (200, 400, 422)

    def test_graphrag_query_with_text(self, cortex_client):
        resp = cortex_client.post(
            "/api/cortex/graphrag/query",
            json={"query": "test query"},
        )
        assert resp.status_code in (200, 400, 422)

    def test_graphrag_extract_missing(self, cortex_client):
        resp = cortex_client.post("/api/cortex/graphrag/extract", json={})
        assert resp.status_code in (200, 400, 422)


class TestCortexRecommend:
    def test_trending(self, cortex_client):
        resp = cortex_client.get("/api/cortex/recommend/trending")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_recent(self, cortex_client):
        resp = cortex_client.get("/api/cortex/recommend/recent")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_recommend_nonexistent(self, cortex_client):
        resp = cortex_client.get("/api/cortex/recommend/nonexistent-knowledge-id-99999")
        assert resp.status_code in (200, 404)


class TestCortexQuality:
    def test_quality_report(self, cortex_client):
        resp = cortex_client.get("/api/cortex/quality/report")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_quality_batch(self, cortex_client):
        resp = cortex_client.get("/api/cortex/quality/batch")
        assert resp.status_code in (200, 400)

    def test_quality_score_nonexistent(self, cortex_client):
        resp = cortex_client.get("/api/cortex/quality/score/nonexistent-knowledge-id-99999")
        assert resp.status_code in (200, 404)


class TestTl2CortexGuard:
    """TL-② 拆件⑥ 隔离护栏：cortex 知识图谱面/事件面全落 tmp，生产 timeline.db
    与生产 opensoul.db 图谱面零测试行。"""

    @staticmethod
    def _count(db, sql):
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return con.execute(sql).fetchone()[0]
        finally:
            con.close()

    def test_graphrag_build_lands_in_tmp_not_production(self, cortex_client):
        import src.api.cortex_enhanced as cortex_api

        prod_events_before = self._count(
            PROD_TIMELINE_DB,
            "SELECT COUNT(*) FROM events WHERE event_type='graphrag_build'",
        )
        prod_entities_before = self._count(PROD_OSOUL_DB, "SELECT COUNT(*) FROM entities")

        resp = cortex_client.post("/api/cortex/graphrag/build")
        assert resp.status_code == 200
        assert resp.json()["entities_new"] > 0  # 种子知识真被抽取

        # 真源一致：handler 用的 db_pool=fixture tmp pool（禁只信构造参数）
        assert cortex_api.db_pool is cortex_client.app.state.cortex_client_pool
        tmp_db = cortex_client.app.state.cortex_client_opensoul_db
        assert tmp_db != PROD_OSOUL_DB
        assert self._count(tmp_db, "SELECT COUNT(*) FROM entities") > 0  # 真抽取真落 tmp

        tmp_tl = cortex_client.app.state.cortex_client_timeline_db
        assert tmp_tl == os.environ["TIMELINE_DB"]  # 隔离链真源一致
        assert tmp_tl != PROD_TIMELINE_DB
        assert (
            self._count(tmp_tl, "SELECT COUNT(*) FROM events WHERE event_type='graphrag_build'")
            == 1
        )  # graphrag_build 哨兵真落 tmp（进程内真路径真消费）
        assert (
            self._count(
                PROD_TIMELINE_DB,
                "SELECT COUNT(*) FROM events WHERE event_type='graphrag_build'",
            )
            == prod_events_before
        )  # 生产库零测试行（修前恒落生产）
        assert (
            self._count(PROD_OSOUL_DB, "SELECT COUNT(*) FROM entities") == prod_entities_before
        )  # 生产图谱面零测试写入（修前 build 恒写生产）

    def test_module_singleton_tmp_backed(self, cortex_client):
        import src.api.cortex_enhanced as cortex_api

        state = cortex_client.app.state
        assert cortex_api.db_pool is state.cortex_client_pool
        assert state.cortex_client_timeline_db == os.environ["TIMELINE_DB"]
        # 推荐面真走 tmp 知识面（种子知识真可见）
        resp = cortex_client.get("/api/cortex/recommend/recent")
        assert resp.status_code == 200
        assert len(resp.json()["entries"]) == 2
