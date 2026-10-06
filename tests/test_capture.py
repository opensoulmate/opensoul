"""Integration tests for Capture API (/api/capture) — page/selection capture.

live（:8090）=只读冒烟；写用例走conftest.capture_client隔离面（capture复活轮：
schema自愈修复后live写用例会开始往生产captures表写测试行——写路径收编进隔离面）。
"""

import pytest


class TestCaptureHealth:
    def test_health(self, client):
        resp = client.get("/api/capture/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        # 修复后健康必须是真实计数（此前谎报ok+"table not yet initialized"）
        assert "total_captures" in data
        assert "error" not in data
        assert "note" not in data


class TestCaptureStats:
    def test_stats(self, client):
        resp = client.get("/api/capture/stats")
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, dict)
            # 修复后/stats返回真实计数（此前PG方言残留被except吞成全0）
            assert data["status"] == "ok"
            assert isinstance(data.get("total_captures"), int)
            assert isinstance(data.get("recent_24h"), int)
            assert "error" not in data


class TestCaptureList:
    def test_list(self, client):
        resp = client.get("/api/capture/list")
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, (list, dict))


class TestCapturePage:
    def test_page_missing_fields(self, client):
        resp = client.post("/api/capture/page", json={})
        assert resp.status_code in (200, 400, 422)

    def test_page_with_url(self, capture_client):
        # 隔离面（写用例不写生产库）；开局=生产旧版漂移schema，此钉同时
        # 证明 schema 自愈后写路径真实可用（修复前恒500 no such column）
        resp = capture_client.post(
            "/api/capture/page",
            json={"url": "https://example.com", "title": "Example", "content": "Example body"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "captured"


class TestCaptureSelection:
    def test_selection_missing(self, client):
        resp = client.post("/api/capture/selection", json={})
        assert resp.status_code in (200, 400, 422)


class TestCaptureGetDelete:
    def test_get_nonexistent(self, client):
        resp = client.get("/api/capture/99999")
        assert resp.status_code in (200, 404)

    def test_delete_nonexistent(self, client):
        resp = client.delete("/api/capture/99999")
        assert resp.status_code in (200, 404)

    def test_promote_nonexistent(self, client):
        resp = client.post("/api/capture/99999/promote", json={})
        assert resp.status_code in (200, 404, 422)
