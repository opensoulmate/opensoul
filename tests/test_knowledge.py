"""Tests for OpenKnowledge — knowledge base management."""


class TestKnowledgeHealth:
    def test_health(self, client):
        resp = client.get("/api/knowledge/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenKnowledge"


class TestKnowledgeStats:
    def test_stats(self, client):
        resp = client.get("/api/knowledge/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenKnowledge"
        assert "total_entries" in data
        assert "recent_24h" in data
        assert "top_users" in data


class TestKnowledgeCRUD:
    def test_list_knowledge_missing_user_id_contract(self, client):
        resp = client.get("/api/knowledge/")
        # 契约随更新（2026-10-20 F3b-⑮ 复核发现本锁未随 F3b-⑭ 身份轴收口更新——
        # F3b-⑭ 全量「0 failed」被 live 服务时序掩护：跑测时 :8090 仍载旧码，收口后
        # 未复跑本锁）：缺参=未声明→默认桶 200（F3b-⑭ 既有行为变更），4xx 契约退场。
        # 修前契约（缺参 4xx）由 F3b-⑭ 控制组/test_knowledge_identity_axis 锁证。
        assert resp.status_code == 200

    def test_list_knowledge_with_user_id(self, client):
        import uuid

        resp = client.get(
            "/api/knowledge/",
            params={"user_id": str(uuid.uuid4())},
        )
        assert resp.status_code in (200, 401, 403)

    def test_create_knowledge_requires_body(self, client):
        resp = client.post("/api/knowledge/", json={})
        assert resp.status_code in (400, 401, 403, 422)

    def test_get_nonexistent_knowledge(self, client):
        resp = client.get("/api/knowledge/00000000-0000-0000-0000-000000000000")
        assert resp.status_code in (404, 401, 403, 422)

    def test_delete_nonexistent_knowledge(self, client):
        resp = client.delete("/api/knowledge/00000000-0000-0000-0000-000000000000")
        assert resp.status_code in (404, 401, 403, 422)

    def test_upload_requires_file(self, client):
        resp = client.post("/api/knowledge/upload")
        assert resp.status_code in (400, 401, 403, 422)
