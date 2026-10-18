"""Integration tests for LLM Proxy API — configuration and completions endpoints."""

import pytest


class TestLLMHealth:
    def test_health(self, client):
        resp = client.get("/api/llm/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "LLMProxy"


class TestLLMConfig:
    """config 写穿端点测试——隔离面 llm_config_client（conftest）。

    2026-10-18 周期#552：此前本类走 live :8090 + _restore_live_config 测试后恢复，
    跑测窗口内 save_config 四路写穿生产配置（REJ552 验收 0/2 "Unsupported model
    test-model" 实证，详见 conftest.llm_config_client 与 test_llm.py 同类注释）；
    现改为 gene_client 同款隔离面，写穿只落 tmp，生产零接触。
    """

    def test_get_config(self, llm_config_client):
        resp = llm_config_client.get("/api/llm/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "base_url" in data
        assert "api_key" in data
        assert "model" in data
        # 契约（2026-09-19实证）：默认返回真实key供settings页表单回写
        # （settings-client.tsx:384/:545 填充+原样POST回写，masked默认值会覆盖真实key）；
        # 掩码机制用显式 ?masked=true 验证
        resp_masked = llm_config_client.get("/api/llm/config", params={"masked": True})
        assert resp_masked.status_code == 200
        masked = resp_masked.json()
        if masked["api_key"]:
            assert masked["api_key"] == "***"

    def test_update_config(self, llm_config_client):
        resp = llm_config_client.post(
            "/api/llm/config",
            json={"model": "test-model"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == "test-model"

    def test_update_config_partial(self, llm_config_client):
        """Updating one field should not clear others."""
        # First set a model
        llm_config_client.post("/api/llm/config", json={"model": "partial-test"})
        # Then update only base_url
        resp = llm_config_client.post(
            "/api/llm/config",
            json={"base_url": "http://test.example.com"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["base_url"] == "http://test.example.com"
        # model should still be set (from override or settings)
        assert "model" in data


class TestLLMCompletions:
    def test_completions_no_api_key(self, client):
        """Should return 400 if no API key configured."""
        # Clear any overrides first
        from src.api.llm import _llm_overrides

        saved_key = _llm_overrides.pop("api_key", None)
        saved_base = _llm_overrides.pop("base_url", None)
        saved_model = _llm_overrides.pop("model", None)
        try:
            from src.config import settings

            if not settings.llm_api_key:
                resp = client.post(
                    "/api/llm/completions",
                    json={"messages": [{"role": "user", "content": "hello"}]},
                )
                assert resp.status_code == 400
                assert "API key" in resp.json()["detail"]
        finally:
            if saved_key:
                _llm_overrides["api_key"] = saved_key
            if saved_base:
                _llm_overrides["base_url"] = saved_base
            if saved_model:
                _llm_overrides["model"] = saved_model

    def test_test_endpoint_no_api_key(self, client):
        """Test connection endpoint should return 400 if no key."""
        from src.api.llm import _llm_overrides
        from src.config import settings

        saved_key = _llm_overrides.pop("api_key", None)
        try:
            if not settings.llm_api_key:
                resp = client.post("/api/llm/test")
                assert resp.status_code == 400
        finally:
            if saved_key:
                _llm_overrides["api_key"] = saved_key
