"""Integration tests for OpenCortex (皮层) — advanced reasoning, task planning,
GraphRAG, recommendations, and quality scoring."""


class TestCortexHealth:
    def test_health(self, cortex_client):
        resp = cortex_client.get("/api/cortex/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestCortexEnhancedHealth:
    def test_enhanced_health(self, cortex_client):
        resp = cortex_client.get("/api/cortex/enhanced/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenCortex-Enhanced"
        assert "graphrag" in data["features"]
        assert "recommendation" in data["features"]
        assert "quality" in data["features"]
        assert data["features"]["graphrag"]["available"] is True
        assert data["features"]["recommendation"]["available"] is True
        assert data["features"]["quality"]["available"] is True


class TestGraphRAGExtract:
    def test_extract_entities_and_relations(self, cortex_client):
        resp = cortex_client.post(
            "/api/cortex/graphrag/extract",
            params={
                "text": "OpenAI发布了GPT-4模型，Google随后推出了Gemini。两家公司都在人工智能领域竞争。"
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "entities" in data
        assert "relations" in data
        assert "entity_count" in data
        assert "relation_count" in data
        assert isinstance(data["entities"], list)
        assert isinstance(data["relations"], list)

    def test_extract_empty_text(self, cortex_client):
        resp = cortex_client.post(
            "/api/cortex/graphrag/extract",
            params={"text": ""},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["entity_count"] == 0
        assert data["relation_count"] == 0


class TestGraphRAGBuild:
    def test_build_graph(self, cortex_client):
        resp = cortex_client.post("/api/cortex/graphrag/build?user_id=default")
        assert resp.status_code == 200
        data = resp.json()
        assert "entities_new" in data
        assert "relations_new" in data
        assert "knowledge_scanned" in data


class TestGraphRAGQuery:
    def test_query_nonexistent_entity(self, cortex_client):
        resp = cortex_client.post(
            "/api/cortex/graphrag/query?user_id=default",
            json={"entity_name": "nonexistent_entity_xyz", "depth": 2},
        )
        # Should return 404 for non-existent entity
        assert resp.status_code in (200, 404)


class TestRecommendations:
    def test_trending(self, cortex_client):
        resp = cortex_client.get("/api/cortex/recommend/trending?user_id=default&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert isinstance(data["entries"], list)

    def test_recent(self, cortex_client):
        resp = cortex_client.get("/api/cortex/recommend/recent?user_id=default&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert isinstance(data["entries"], list)

    def test_related_nonexistent(self, cortex_client):
        resp = cortex_client.get("/api/cortex/recommend/nonexistent_id?user_id=default")
        assert resp.status_code == 200
        data = resp.json()
        assert "recommendations" in data


class TestQualityScoring:
    def test_quality_report(self, cortex_client):
        resp = cortex_client.get("/api/cortex/quality/report?user_id=default")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "avg_score" in data

    def test_quality_batch(self, cortex_client):
        resp = cortex_client.get("/api/cortex/quality/batch?user_id=default&limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert "scores" in data
        assert "count" in data
        assert isinstance(data["scores"], list)

    def test_quality_score_nonexistent(self, cortex_client):
        resp = cortex_client.get("/api/cortex/quality/score/nonexistent_id?user_id=default")
        # Should return 404 for non-existent entry
        assert resp.status_code in (200, 404)
