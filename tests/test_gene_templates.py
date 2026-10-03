"""Integration tests for Gene Templates API."""


class TestGeneTemplatesHealth:
    def test_health(self, gene_client):
        resp = gene_client.get("/api/gene/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestGeneTemplatesList:
    def test_list_templates(self, gene_client):
        resp = gene_client.get("/api/gene/adapter-templates")
        assert resp.status_code == 200
        data = resp.json()
        # Response may be a list or wrapped object
        assert data is not None

    def test_list_instances(self, gene_client):
        resp = gene_client.get("/api/gene/instances")
        assert resp.status_code == 200
        data = resp.json()
        assert data is not None
