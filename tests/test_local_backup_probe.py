"""register_local_backup探活入链 + 三处调用点接线测试 — 01:35遗留#2 / 08:48遗留#3销账。

litellm health_check「探活结果喂路由决策」最小切片的测试面：
1. _tcp_reachable：真实socket双向（监听端口=可达 / 关闭端口=不可达 / 坏URL=
   fail-safe False）+ 默认端口解析（https→443 / http→80 / 显式端口）
2. _probe_cached：TTL缓存（同URL TTL内只探一次 / TTL过期重探 / 按URL分键）
3. register_local_backup：可达才入链（priority=10 / models透传 / 自定义name）/
   不可达不入链（幻影备胎豁免——修复前无条件注册）/ probe=False强制注册 /
   幂等（重复注册不清failure计数）/ add_provider异常·探活异常 fail-safe→None
4. 调用点接线（防死代码，三处手搓块收敛单一真源后的真实运行时路径）：
   memory_model._build_router / api/gland._ensure_bootstrapped（含ollama后启动
   补口）/ branch_summary._call_llm_router（源码级防死接线）
"""

import inspect
import socket
import sys

import pytest

sys.path.insert(0, "/home/climbing/opensoul")

import src.api.llm as llm_api  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    """探活缓存跨测试隔离（monkeypatch _tcp_reachable后旧缓存会吞掉新桩）。"""
    llm_api._LOCAL_PROBE_CACHE.clear()
    yield
    llm_api._LOCAL_PROBE_CACHE.clear()


class _RecorderRouter:
    """add_provider最小记录器（register_local_backup契约面）。"""

    def __init__(self, boom: bool = False):
        self.providers = {}
        self.add_calls = 0
        self._boom = boom

    def add_provider(self, name, base_url, models=None, priority=0, persist_row=True):
        # persist_row：RV-①b-⑩ ModelRouter.add_provider 契约随更新。
        self.add_calls += 1
        if self._boom:
            raise RuntimeError("add_provider blew up")
        self.providers[name] = {
            "base_url": base_url,
            "models": models,
            "priority": priority,
        }


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestTcpReachable:
    def test_listening_port_is_reachable(self):
        srv = socket.socket()
        try:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            assert llm_api._tcp_reachable(f"http://127.0.0.1:{port}/v1") is True
        finally:
            srv.close()

    def test_closed_port_is_unreachable(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.close()  # 释放端口→connect被拒（幻影备胎形态）
        assert llm_api._tcp_reachable(f"http://127.0.0.1:{port}/v1") is False

    def test_bad_url_fail_safe_false(self):
        assert llm_api._tcp_reachable("http://[bad") is False
        assert llm_api._tcp_reachable("") is False

    def test_default_ports_and_scheme_parsing(self, monkeypatch):
        captured = {}

        def fake_create(addr, timeout=None):
            captured["addr"] = addr
            captured["timeout"] = timeout
            return _FakeConn()

        monkeypatch.setattr(llm_api.socket, "create_connection", fake_create)
        assert llm_api._tcp_reachable("https://llm.example/v1") is True
        assert captured["addr"] == ("llm.example", 443)
        assert llm_api._tcp_reachable("http://localhost:11434/v1") is True
        assert captured["addr"] == ("localhost", 11434)
        assert llm_api._tcp_reachable("llm.example/v1") is True  # 无scheme→http
        assert captured["addr"] == ("llm.example", 80)
        assert captured["timeout"] == 0.35  # 默认探活超时


class TestProbeCache:
    def test_cached_within_ttl(self, monkeypatch):
        calls = []

        def fake(url, timeout=0.35):
            calls.append(url)
            return True

        monkeypatch.setattr(llm_api, "_tcp_reachable", fake)
        assert llm_api._probe_cached("http://a:1/v1") == (True, True)  # 新探
        assert llm_api._probe_cached("http://a:1/v1") == (True, False)  # 命中缓存
        assert len(calls) == 1  # TTL内只探一次（fresh-per-call router防逐调用探活）

    def test_expired_reprobes(self, monkeypatch):
        calls = []
        monkeypatch.setattr(llm_api, "LOCAL_PROBE_TTL", 0.0)  # 立即过期
        monkeypatch.setattr(
            llm_api, "_tcp_reachable", lambda url, timeout=0.35: calls.append(url) or True
        )
        assert llm_api._probe_cached("http://a:1/v1") == (True, True)
        assert llm_api._probe_cached("http://a:1/v1") == (True, True)  # 过期=重新新探
        assert len(calls) == 2

    def test_per_url_keying(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            llm_api, "_tcp_reachable", lambda url, timeout=0.35: calls.append(url) or True
        )
        llm_api._probe_cached("http://a:1/v1")
        llm_api._probe_cached("http://b:2/v1")
        assert len(calls) == 2

    def test_negative_result_is_cached_too(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            llm_api, "_tcp_reachable", lambda url, timeout=0.35: calls.append(url) or False
        )
        assert llm_api._probe_cached("http://down:1/v1") == (False, True)
        assert llm_api._probe_cached("http://down:1/v1") == (False, False)
        assert len(calls) == 1  # 不可达同样缓存（防逐调用重探down端口）


class TestRegisterLocalBackup:
    def test_reachable_registers_priority10(self, monkeypatch):
        monkeypatch.setattr(llm_api, "_tcp_reachable", lambda *a, **k: True)
        r = _RecorderRouter()
        name = llm_api.register_local_backup(
            r, "http://127.0.0.1:11434/v1", {"chat": "deepseek-r1:latest"}
        )
        assert name == "ollama"
        assert set(r.providers) == {"ollama"}
        assert r.providers["ollama"]["priority"] == 10
        assert r.providers["ollama"]["models"] == {"chat": "deepseek-r1:latest"}
        assert r.providers["ollama"]["base_url"] == "http://127.0.0.1:11434/v1"

    def test_unreachable_not_registered(self, monkeypatch):
        """幻影备胎豁免核心：不可达→不入链（修复前无条件注册=链尾死ollama）。"""
        monkeypatch.setattr(llm_api, "_tcp_reachable", lambda *a, **k: False)
        r = _RecorderRouter()
        assert llm_api.register_local_backup(r, "http://127.0.0.1:1/v1", {"chat": "m"}) is None
        assert r.providers == {}
        assert r.add_calls == 0

    def test_probe_false_forces_registration(self, monkeypatch):
        monkeypatch.setattr(llm_api, "_tcp_reachable", lambda *a, **k: False)
        r = _RecorderRouter()
        name = llm_api.register_local_backup(r, "http://x:9/v1", {"chat": "m"}, probe=False)
        assert name == "ollama"
        assert "ollama" in r.providers

    def test_idempotent_no_double_register(self, monkeypatch):
        monkeypatch.setattr(llm_api, "_tcp_reachable", lambda *a, **k: True)
        r = _RecorderRouter()
        assert llm_api.register_local_backup(r, "http://x:9/v1", {"chat": "m"}) == "ollama"
        assert llm_api.register_local_backup(r, "http://x:9/v1", {"chat": "m"}) == "ollama"
        assert r.add_calls == 1  # 幂等：重复注册不重建ProviderConfig（不清failure计数）

    def test_custom_name_and_models_passthrough(self, monkeypatch):
        monkeypatch.setattr(llm_api, "_tcp_reachable", lambda *a, **k: True)
        r = _RecorderRouter()
        name = llm_api.register_local_backup(
            r,
            "http://lm:1234/v1",
            {"chat": "qwen", "embedding": "bge"},
            name="lmstudio",
        )
        assert name == "lmstudio"
        assert r.providers["lmstudio"]["models"] == {"chat": "qwen", "embedding": "bge"}

    def test_fail_safe_on_add_provider_exception(self, monkeypatch):
        monkeypatch.setattr(llm_api, "_tcp_reachable", lambda *a, **k: True)
        r = _RecorderRouter(boom=True)
        assert (
            llm_api.register_local_backup(r, "http://x:9/v1", {"chat": "m"}) is None
        )  # 绝不反噬调用方

    def test_fail_safe_on_probe_exception(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("probe blew up")

        monkeypatch.setattr(llm_api, "_tcp_reachable", boom)
        r = _RecorderRouter()
        assert llm_api.register_local_backup(r, "http://x:9/v1", {"chat": "m"}) is None
        assert r.providers == {}

    def test_skip_is_visible_warning(self, monkeypatch, caplog):
        """可观测性契约（mem0 §1.1处理必须可见禁止静默）：探活豁免必须以WARNING
        落日志——root无handler时lastResort只放行WARNING+，info会被吞=静默豁免。"""
        import logging

        monkeypatch.setattr(llm_api, "_tcp_reachable", lambda *a, **k: False)
        r = _RecorderRouter()
        with caplog.at_level(logging.WARNING, logger="src.api.llm"):
            llm_api.register_local_backup(r, "http://127.0.0.1:1/v1", {"chat": "m"})
        assert "skipped from fallback chain" in caplog.text
        assert any(rec.levelno >= logging.WARNING for rec in caplog.records)

    def test_skip_log_deduped_within_ttl(self, monkeypatch, caplog):
        """日志去重契约：缓存命中（非新探）不再重复记WARNING——gland请求路径每次
        调_register_local_backup，逐请求刷屏=可观测性反面（live实测4条/2秒修正）。"""
        import logging

        monkeypatch.setattr(llm_api, "_tcp_reachable", lambda *a, **k: False)
        r = _RecorderRouter()
        with caplog.at_level(logging.WARNING, logger="src.api.llm"):
            llm_api.register_local_backup(r, "http://127.0.0.1:1/v1", {"chat": "m"})
            llm_api.register_local_backup(r, "http://127.0.0.1:1/v1", {"chat": "m"})
            llm_api.register_local_backup(r, "http://127.0.0.1:1/v1", {"chat": "m"})
        lines = [rec for rec in caplog.records if "skipped from fallback chain" in rec.message]
        assert len(lines) == 1  # 每TTL窗口恰好一次（可见但不刷屏）


class TestCallSiteWiring:
    """三处调用点真实运行时路径接线（写了≠接线了：防死代码）。"""

    def test_memory_model_probe_on(self, monkeypatch):
        from src.hippo import memory_model

        monkeypatch.setattr(llm_api, "_tcp_reachable", lambda *a, **k: True)
        r = memory_model._build_router(
            memory_model.ResolvedMemoryModel(
                model_id="m",
                base_url="https://mem.example/v1",
                api_key="k",
                source="session",
                fallback=None,
                sampling={},
            )
        )
        assert "ollama" in r.providers
        assert r.providers["ollama"].priority == 10
        assert r.providers["ollama"].models == {"chat": "deepseek-r1:latest"}

    def test_memory_model_probe_off_skips(self, monkeypatch):
        from src.hippo import memory_model

        monkeypatch.setattr(llm_api, "_tcp_reachable", lambda *a, **k: False)
        r = memory_model._build_router(
            memory_model.ResolvedMemoryModel(
                model_id="m",
                base_url="https://mem.example/v1",
                api_key="k",
                source="session",
                fallback=None,
                sampling={},
            )
        )
        assert "ollama" not in r.providers

    def test_gland_bootstrap_registers_with_embedding_model(self, monkeypatch):
        import src.api.gland as gland
        from src.gland.router import ModelRouter

        monkeypatch.setattr(gland, "gateway", ModelRouter())
        monkeypatch.setattr(llm_api, "_tcp_reachable", lambda *a, **k: True)
        gland._ensure_bootstrapped()
        g = gland.gateway
        assert "ollama" in g.providers
        assert g.providers["ollama"].priority == 10
        assert g.providers["ollama"].models == {
            "chat": "deepseek-r1:latest",
            "embedding": "nomic-embed-text",
        }

    def test_gland_bootstrap_skips_unreachable(self, monkeypatch):
        import src.api.gland as gland
        from src.gland.router import ModelRouter

        monkeypatch.setattr(gland, "gateway", ModelRouter())
        monkeypatch.setattr(llm_api, "_tcp_reachable", lambda *a, **k: False)
        gland._ensure_bootstrapped()
        assert "ollama" not in gland.gateway.providers

    def test_gland_late_start_heal(self, monkeypatch):
        """补口语义：bootstrap时ollama不可达→缺席；ollama后启动→下次请求路径补注册
        （gateway单例不因启动时序永久丢备胎）。"""
        import src.api.gland as gland
        from src.gland.router import ModelRouter

        monkeypatch.setattr(gland, "gateway", ModelRouter())
        monkeypatch.setattr(llm_api, "_tcp_reachable", lambda *a, **k: False)
        gland._ensure_bootstrapped()
        assert "ollama" not in gland.gateway.providers
        # ollama起来了：探活翻True + 探活TTL过期（缓存清空模拟30s后重探）
        monkeypatch.setattr(llm_api, "_tcp_reachable", lambda *a, **k: True)
        llm_api._LOCAL_PROBE_CACHE.clear()  # TTL过期重探的确定性模拟
        gland._ensure_bootstrapped()  # providers非空→early-return路径带补口
        assert "ollama" in gland.gateway.providers

    def test_branch_router_source_uses_single_source(self):
        from src.trajectory import branch_summary

        src_text = inspect.getsource(branch_summary._call_llm_router)
        assert "register_local_backup" in src_text  # 真接线（非死代码残留手搓块）
        assert 'name="ollama"' not in src_text  # 手搓注册块已收敛

    def test_three_callsites_all_converged(self):
        import src.api.gland as gland
        from src.hippo import memory_model
        from src.trajectory import branch_summary

        assert "register_local_backup" in inspect.getsource(gland._register_local_backup)
        assert "register_local_backup" in inspect.getsource(memory_model._build_router)
        assert "register_local_backup" in inspect.getsource(branch_summary._call_llm_router)
        assert callable(llm_api.register_local_backup)
