"""Tests for Soma Discovery API — system scanning and adapter management."""


class TestDiscoveryHealth:
    def test_discovery_health(self, client):
        resp = client.get("/api/soma/discovery/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestScan:
    def test_scan(self, client):
        resp = client.get("/api/soma/discovery/scan")
        assert resp.status_code == 200
        data = resp.json()
        # Scan should return some structure
        assert isinstance(data, dict)


class TestProcesses:
    def test_list_processes(self, client):
        resp = client.get("/api/soma/discovery/processes")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


class TestCLITools:
    def test_list_cli_tools(self, client):
        resp = client.get("/api/soma/discovery/cli-tools")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


class TestServices:
    def test_list_services(self, client):
        resp = client.get("/api/soma/discovery/services")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


class TestAdapters:
    def test_list_adapters(self, client):
        resp = client.get("/api/soma/discovery/adapters")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_adapter_contract_capabilities_configured(self, client):
        """前端 connectors 页契约（026b6794 起声明）：capabilities: string[] + configured: bool。
        任一缺失即 adapter.capabilities.length 整页崩（C3-⑨ 实测 live 崩溃）。"""
        resp = client.get("/api/soma/discovery/adapters")
        assert resp.status_code == 200
        adapters = resp.json()
        assert adapters, "应有内置适配器注册"
        for a in adapters:
            assert isinstance(a.get("capabilities"), list), f"{a.get('name')} 缺 capabilities"
            assert a["capabilities"], f"{a.get('name')} capabilities 为空"
            assert "scan" in a["capabilities"], f"{a.get('name')} 应声明 scan"
            assert isinstance(a.get("configured"), bool), f"{a.get('name')} 缺 configured"

    def test_adapter_capabilities_match_execute_dispatch(self):
        """真源对账：声明的 execute 能力必须真被 dispatch 支持（禁报不存在的能力）。"""
        import asyncio

        from src.api import soma_discovery as sd

        assert sd._adapter_registry, "应有内置适配器注册"
        for name, adapter in sd._adapter_registry.items():
            caps = list(adapter.to_info().capabilities)
            assert caps == list(adapter.capabilities)
            for cap in caps:
                if cap == "scan":
                    continue
                result = asyncio.run(adapter.execute(cap, {}))
                assert isinstance(result, (dict, list)), f"{name}.{cap} 应返回 dict/list"
                assert "Unknown action" not in str(result), f"{name} 声明了不存在的能力 {cap}"

    def test_adapter_configured_tracks_real_state(self):
        """configured 必须跟随真实配置态（rest 配 base_url 前后 False->True）。"""
        import asyncio

        from src.api import soma_discovery as sd

        rest = sd.get_adapter("rest")
        assert isinstance(rest, sd.RestAdapter)
        assert rest.to_info().configured is False
        try:
            result = asyncio.run(rest.execute("configure", {"base_url": "http://127.0.0.1:9"}))
            assert result.get("status") == "configured"
            assert rest.to_info().configured is True
        finally:
            rest._config.clear()  # 单例复位，防污染其他用例
