"""Integration tests for OpenMirror (镜像) — sandbox testing.

TL-② 拆件①收编：client(live :8090) → mirror_client（进程内真实 router + tmp
数据面）——修前每轮全量把 test_sandbox/var_test 2 条 sandbox_created 写生产
timeline.db，且 /cleanup 对 live 生产沙箱执行过期清理（测试副作用碰生产）。
"""


class TestMirrorHealth:
    def test_health(self, mirror_client):
        resp = mirror_client.get("/api/mirror/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenMirror"


class TestMirrorSandbox:
    def test_create_list_delete_sandbox(self, mirror_client):
        # Create
        resp = mirror_client.post(
            "/api/mirror/sandboxes",
            json={
                "name": "test_sandbox",
                "description": "Integration test sandbox",
                "config": {"isolated": True},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        sid = data["sandbox_id"]

        # List
        resp = mirror_client.get("/api/mirror/sandboxes")
        assert resp.status_code == 200

        # Get specific
        resp = mirror_client.get(f"/api/mirror/sandboxes/{sid}")
        assert resp.status_code == 200

        # Delete
        resp = mirror_client.delete(f"/api/mirror/sandboxes/{sid}")
        assert resp.status_code == 200

    def test_sandbox_variables(self, mirror_client):
        # Create sandbox
        resp = mirror_client.post(
            "/api/mirror/sandboxes",
            json={
                "name": "var_test",
                "config": {},
            },
        )
        assert resp.status_code == 200
        sid = resp.json()["sandbox_id"]

        # Set variable
        resp = mirror_client.post(
            f"/api/mirror/sandboxes/{sid}/variables",
            json={
                "key": "test_var",
                "value": "hello",
            },
        )
        assert resp.status_code == 200

        # Get variables
        resp = mirror_client.get(f"/api/mirror/sandboxes/{sid}/variables")
        assert resp.status_code == 200

        # Cleanup
        mirror_client.delete(f"/api/mirror/sandboxes/{sid}")


class TestMirrorCleanup:
    def test_cleanup(self, mirror_client):
        resp = mirror_client.post("/api/mirror/cleanup")
        assert resp.status_code == 200
