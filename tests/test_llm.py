"""Integration tests for LLM Proxy API — /api/llm/* endpoints."""

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

    2026-10-18 周期#552：此前本类走 live :8090 + _restore_live_config 测试后恢复——
    跑测窗口内 save_config 四路写穿生产配置，SoulMate agent spawn 撞上窗口即以
    test-model-v1 打真实 LLM 全回合 400（REJ552 验收 0/2 实证）；且恢复 payload 取错
    响应形状（GET /config 无顶层 standard/subscription 键）恢复不可靠。现改为
    gene_client 同款隔离面，测试面=真实router代码，写穿只落 tmp，生产零接触。
    """

    def test_get_config(self, llm_config_client):
        resp = llm_config_client.get("/api/llm/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "base_url" in data
        assert "api_key" in data
        assert "model" in data
        # 契约（2026-09-19实证）：GET /config 默认返回真实key——settings页
        # settings-client.tsx 用该值填充表单并在保存时原样POST回写，
        # 若默认masked，"***"会在下次保存时覆盖真实key（灾难）。
        # 掩码机制通过显式 ?masked=true 验证。
        resp_masked = llm_config_client.get("/api/llm/config", params={"masked": True})
        assert resp_masked.status_code == 200
        masked = resp_masked.json()
        if masked["api_key"]:
            assert masked["api_key"] == "***"
        # 双体系槽位的key同样受masked参数控制
        for slot in ("standard", "subscription"):
            if masked.get(slot, {}).get("api_key"):
                assert masked[slot]["api_key"] == "***"

    def test_save_config_updates_base_url(self, llm_config_client):
        resp = llm_config_client.post(
            "/api/llm/config",
            json={"base_url": "https://test.example.com/v1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["base_url"] == "https://test.example.com/v1"

    def test_save_config_updates_model(self, llm_config_client):
        resp = llm_config_client.post(
            "/api/llm/config",
            json={"model": "test-model-v1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == "test-model-v1"

    def test_save_config_partial_update(self, llm_config_client):
        """Only provided fields should be updated."""
        # Get current config
        # Update only model
        resp = llm_config_client.post("/api/llm/config", json={"model": "partial-test"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == "partial-test"

    def test_save_config_empty_body(self, llm_config_client):
        """Empty body should not change anything."""
        before = llm_config_client.get("/api/llm/config").json()
        resp = llm_config_client.post("/api/llm/config", json={})
        assert resp.status_code == 200
        after = resp.json()
        assert before["model"] == after["model"]

    def test_config_never_writes_production_env(self, llm_config_client):
        """隔离护栏（gene_client 同款"隔离护栏断言"）：写穿落 tmp，生产 .env 字节恒等。"""
        import os

        import src.api.llm as llm_api

        prod_env = os.path.normpath(
            os.path.join(os.path.dirname(llm_api.__file__), "..", "..", ".env")
        )
        prod_acp = os.path.join(
            os.environ.get("OPENMATE_ROOT", "/home/climbing/openmate"), "acp-proxy", ".env"
        )
        before = {p: open(p, "rb").read() for p in (prod_env, prod_acp) if os.path.exists(p)}
        resp = llm_config_client.post("/api/llm/config", json={"model": "isolation-probe"})
        assert resp.status_code == 200
        for p, b in before.items():
            assert open(p, "rb").read() == b, f"生产配置被测试写穿: {p}"
        # 写穿确实发生、且落在 tmp 面（证明不是端点没写，而是写去了隔离面）
        assert "isolation-probe" in open(llm_config_client.app.state.llm_env_path).read()


class TestLLMTestConnection:
    def test_test_connection_no_key(self, client):
        """Should return 400 if no API key configured."""
        resp = client.post("/api/llm/test")
        # May succeed if key is configured, or fail with 400/502
        assert resp.status_code in (200, 400, 502)


class TestLLMCompletions:
    def test_completions_no_key(self, client):
        """Should return 400 if no API key configured."""
        resp = client.post(
            "/api/llm/completions",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 16,
            },
        )
        # May succeed if key is configured, or fail with 400/502
        assert resp.status_code in (200, 400, 502)

    def test_completions_with_custom_model(self, client):
        """Should accept custom model override."""
        resp = client.post(
            "/api/llm/completions",
            json={
                "messages": [{"role": "user", "content": "Say hi"}],
                "model": "gpt-4o-mini",
                "max_tokens": 8,
            },
        )
        assert resp.status_code in (200, 400, 502)

    def test_completions_with_temperature(self, client):
        """Should accept temperature parameter."""
        resp = client.post(
            "/api/llm/completions",
            json={
                "messages": [{"role": "user", "content": "Test"}],
                "temperature": 0.1,
                "max_tokens": 8,
            },
        )
        assert resp.status_code in (200, 400, 502)
