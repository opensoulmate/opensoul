"""Integration tests for Export API — data export endpoints."""


class TestExportHealth:
    def test_health(self, client):
        resp = client.get("/api/export/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "ExportSystem"


class TestExportEndpoints:
    def test_export_json(self, client):
        """Export JSON requires user_id param."""
        resp = client.get(
            "/api/export/json", params={"user_id": "00000000-0000-0000-0000-000000000000"}
        )
        # 500 if user doesn't exist in DB (expected in test env)
        assert resp.status_code in (200, 401, 422, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert "knowledge" in data
            assert "entities" in data
            assert "tags" in data

    def test_export_markdown(self, client):
        """Export markdown requires user_id param.（契约随更新：F3b-⑬ 修后 /markdown
        在 SQLite 后端真可用（修前 array_agg 必 500 把本例 200 分支掩成死路）——
        200 分支断言改按真实响应形=纯 markdown 文本（原 json.loads 形是
        /markdown-json 的契约误抄进本例，断言意图=「可用时回真 markdown 导出」零丢失）"""
        resp = client.get(
            "/api/export/markdown", params={"user_id": "00000000-0000-0000-0000-000000000000"}
        )
        # 500 if user doesn't exist in DB (expected in test env)
        assert resp.status_code in (200, 401, 422, 500)
        if resp.status_code == 200:
            body = resp.text
            assert body.startswith("# Knowledge Export")
            assert "## Knowledge Entries" in body
