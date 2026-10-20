"""Integration tests for OpenHippo (海马体) — memory lifecycle management."""


class TestHippoHealth:
    def test_health(self, client):
        resp = client.get("/api/hippo/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenHippo"
        assert "memory" in data
        assert "sessions" in data


class TestHippoMemories:
    def test_create_list_delete_memory(self, client):
        # First create a session
        sess_resp = client.post(
            "/api/hippo/sessions",
            json={
                "user_id": "test_user",
            },
        )
        assert sess_resp.status_code == 200
        session_id = sess_resp.json()["session_id"]

        # Create memory (requires session_id)
        resp = client.post(
            "/api/hippo/memories",
            json={
                "content": "Test memory for integration testing",
                "importance": 0.8,
                "tags": ["test", "integration"],
                "session_id": session_id,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        mid = data["memory_id"]

        # List
        resp = client.get("/api/hippo/memories")
        assert resp.status_code == 200

        # Get specific
        resp = client.get(f"/api/hippo/memories/{mid}")
        assert resp.status_code == 200

        # Update
        resp = client.patch(
            f"/api/hippo/memories/{mid}",
            json={
                "importance": 0.9,
            },
        )
        assert resp.status_code == 200

        # Delete
        resp = client.delete(f"/api/hippo/memories/{mid}")
        assert resp.status_code == 200

        # Cleanup session
        client.delete(f"/api/hippo/sessions/{session_id}", params={"user_id": "test_user"})


class TestHippoDecay:
    def test_decay_config(self, client):
        resp = client.get("/api/hippo/decay/config")
        assert resp.status_code == 200

    def test_decay_simulate(self, client):
        resp = client.post(
            "/api/hippo/decay/simulate",
            json={
                "hours": 24,
            },
        )
        assert resp.status_code == 200


class TestHippoSessions:
    def test_list_sessions(self, client):
        resp = client.get("/api/hippo/sessions")
        assert resp.status_code == 200
