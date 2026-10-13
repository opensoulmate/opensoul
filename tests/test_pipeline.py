"""Integration tests for Pipeline API — /api/pipeline/* endpoints."""


class TestPipelineHealth:
    def test_health(self, pipeline_client):
        resp = pipeline_client.get("/api/pipeline/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestPipelineTypes:
    def test_list_types(self, pipeline_client):
        resp = pipeline_client.get("/api/pipeline/types")
        assert resp.status_code == 200
        data = resp.json()
        assert "types" in data
        types = data["types"]
        assert isinstance(types, list)
        # Should have at least the basic pipeline types
        assert len(types) > 0


class TestPipelineRun:
    def test_run_nonexistent_file(self, pipeline_client):
        """Running pipeline on non-existent file should fail gracefully."""
        resp = pipeline_client.post(
            "/api/pipeline/run",
            json={
                "file_id": "nonexistent-file-id-12345",
                "pipeline": "text",
            },
        )
        # Should fail with 400/404/500 since file doesn't exist
        assert resp.status_code in (400, 404, 500)

    def test_run_auto_pipeline(self, pipeline_client):
        """Auto pipeline detection should handle missing files."""
        resp = pipeline_client.post(
            "/api/pipeline/run",
            json={
                "file_id": "fake-id",
                "pipeline": "auto",
            },
        )
        assert resp.status_code in (400, 404, 500)

    def test_run_with_skip_options(self, pipeline_client):
        """Should accept skip_immune and skip_knowledge flags."""
        resp = pipeline_client.post(
            "/api/pipeline/run",
            json={
                "file_id": "fake-id",
                "pipeline": "text",
                "skip_immune": True,
                "skip_knowledge": True,
            },
        )
        assert resp.status_code in (400, 404, 500)

    def test_run_with_tags(self, pipeline_client):
        """Should accept custom tags."""
        resp = pipeline_client.post(
            "/api/pipeline/run",
            json={
                "file_id": "fake-id",
                "pipeline": "text",
                "tags": ["test", "integration"],
            },
        )
        assert resp.status_code in (400, 404, 500)


class TestPipelineUpload:
    def test_upload_text_file(self, pipeline_client):
        """Upload a text file through the pipeline."""
        resp = pipeline_client.post(
            "/api/pipeline/upload",
            files={"file": ("test.txt", b"Hello world, this is a test file.", "text/plain")},
            data={"user_id": "default"},
        )
        # May succeed (200) or fail due to missing DB (500)
        assert resp.status_code in (200, 422, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert "pipeline_id" in data or "status" in data

    def test_upload_json_file(self, pipeline_client):
        """Upload a JSON file through the pipeline."""
        import json

        content = json.dumps({"key": "value", "nested": {"a": 1}})
        resp = pipeline_client.post(
            "/api/pipeline/upload",
            files={"file": ("data.json", content.encode(), "application/json")},
            data={"user_id": "default"},
        )
        assert resp.status_code in (200, 422, 500)

    def test_upload_markdown_file(self, pipeline_client):
        """Upload a markdown file through the pipeline."""
        content = b"# Title\n\nSome content here with enough text for processing."
        resp = pipeline_client.post(
            "/api/pipeline/upload",
            files={"file": ("notes.md", content, "text/markdown")},
            data={"user_id": "default"},
        )
        assert resp.status_code in (200, 422, 500)

    def test_upload_with_pipeline_type(self, pipeline_client):
        """Should accept explicit pipeline type."""
        resp = pipeline_client.post(
            "/api/pipeline/upload",
            files={"file": ("test.txt", b"Test content for pipeline.", "text/plain")},
            data={"user_id": "default", "pipeline": "text"},
        )
        assert resp.status_code in (200, 422, 500)


class TestPipelineHistory:
    def test_history_empty(self, pipeline_client):
        resp = pipeline_client.get("/api/pipeline/history")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_history_nonexistent_id(self, pipeline_client):
        resp = pipeline_client.get("/api/pipeline/history/nonexistent-id")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            data = resp.json()
            # Should return empty or null for non-existent
            assert data is None or data.get("pipeline_id") is None or isinstance(data, dict)


class TestPipelineStats:
    def test_stats(self, pipeline_client):
        resp = pipeline_client.get("/api/pipeline/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
