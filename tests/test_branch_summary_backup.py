"""branch_summary变体备胎入链 + register_variant_backup单一真源测试 — 01:35轮遗留#4销账。

纯离线（httpx.MockTransport，test_fallback_chain/test_memory_model同款模式）。覆盖：
1. register_variant_backup（src/api/llm.py注册块单一真源）：priority=5注册语义
   / 返回provider名 / 无备胎 / 同端点 / 备胎model空回退fallback_model /
   fail-safe异常→None
2. branch_summary._call_llm_router：变体备胎真实入链（此前漏注册=遗留#4缺陷形态）
   / router_factory测试seam / **primary 402→变体备胎成活**（live失败形态全回归：
   修复前同输入=AllProvidersFailedError→generate_branch_summary降级extractive-
   fallback）/ 负控制：无备胎时402链全灭确实抛错
3. memory_model收敛到单一真源后行为不变由tests/test_memory_model.py
   TestVariantBackupWiring既有6用例回归覆盖（本文件跑回归不重复造轮）
"""

import json
import sys

import httpx
import pytest

sys.path.insert(0, "/home/climbing/opensoul")

import src.api.llm as llm_api  # noqa: E402
import src.config as src_config  # noqa: E402
import src.trajectory.branch_summary as branch_summary  # noqa: E402
from src.gland.router import AllProvidersFailedError, ModelRouter  # noqa: E402

# 双体系overrides（test_memory_model._OVERRIDES_ACTIVE_STD同构）：激活standard
# （llm.example），备胎=subscription（sub.example，tp-订阅key）。
_OVERRIDES = {
    "active_variant": "standard",
    "base_url": "https://llm.example/v1",
    "api_key": "sk-std",
    "model": "m-v",
    "standard_base_url": "https://llm.example/v1",
    "standard_api_key": "sk-std",
    "standard_model": "m-v",
    "subscription_base_url": "https://sub.example/v1",
    "subscription_api_key": "tp-sub",
    "subscription_model": "",  # 空→回退fallback_model（占位符自愈形态）
}


def _pin_settings(monkeypatch):
    monkeypatch.setattr(src_config.settings, "llm_base_url", "https://llm.example/v1")
    monkeypatch.setattr(src_config.settings, "llm_model", "m-v")
    monkeypatch.setattr(src_config.settings, "llm_api_key", "sk-std")
    # ollama是localhost provider，cost模式会把它排到最前（test_memory_model同款钉死）
    monkeypatch.setattr("src.gland.route_policy.get_mode", lambda: "balance")


@pytest.fixture(autouse=True)
def _pin_local_probe(monkeypatch):
    """本地兜底探活确定性钉死：默认可达（同改动前无条件注册语义，ollama链尾语义
    由既有用例继续锁死）；不可达跳过分支由test_local_backup_probe.py +
    test_unreachable_local_backup_not_registered专门覆盖。"""
    monkeypatch.setattr("src.api.llm._tcp_reachable", lambda *a, **k: True)
    llm_api._LOCAL_PROBE_CACHE.clear()


class _RecorderRouter:
    """add_provider/key_manager最小记录器（register_variant_backup契约面）。"""

    def __init__(self):
        self.providers = {}
        self.keys = {}
        self.key_manager = self

    def add_provider(self, name, base_url, models, priority, persist_row=True):
        # persist_row：RV-①b-⑩ ModelRouter.add_provider 契约随更新（备胎注册恒
        # persist_row=False，记录器照单全收不落盘语义本就无从体现）。
        self.providers[name] = {
            "base_url": base_url,
            "models": models,
            "priority": priority,
        }

    def add_key(self, provider, key):
        self.keys.setdefault(provider, []).append(key)

    def next_key(self, provider):
        ks = self.keys.get(provider) or []
        return ks[0] if ks else None


class TestRegisterVariantBackup:
    def test_registers_priority5_with_key(self, monkeypatch):
        monkeypatch.setattr("src.api.llm._llm_overrides", dict(_OVERRIDES))
        r = _RecorderRouter()
        name = llm_api.register_variant_backup(r, "fallback-m")
        assert name == "variant-subscription"
        assert r.providers[name]["priority"] == 5
        assert r.providers[name]["base_url"] == "https://sub.example/v1"
        assert r.providers[name]["models"] == {"chat": "fallback-m"}  # 空model回退
        assert r.next_key(name) == "tp-sub"

    def test_declared_model_wins_over_fallback(self, monkeypatch):
        ov = dict(_OVERRIDES)
        ov["subscription_model"] = "partial-test"
        monkeypatch.setattr("src.api.llm._llm_overrides", ov)
        r = _RecorderRouter()
        name = llm_api.register_variant_backup(r, "fallback-m")
        assert r.providers[name]["models"] == {"chat": "partial-test"}

    def test_none_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr(
            "src.api.llm._llm_overrides",
            {"active_variant": "standard", "base_url": "https://llm.example/v1"},
        )
        r = _RecorderRouter()
        assert llm_api.register_variant_backup(r, "m") is None
        assert r.providers == {}

    def test_none_when_same_endpoint(self, monkeypatch):
        ov = dict(_OVERRIDES)
        ov["subscription_base_url"] = "https://llm.example/v1"  # 同端点=非备胎
        monkeypatch.setattr("src.api.llm._llm_overrides", ov)
        r = _RecorderRouter()
        assert llm_api.register_variant_backup(r, "m") is None
        assert r.providers == {}

    def test_no_key_means_no_backup(self, monkeypatch):
        """alternate_variant_config契约：url+key双齐才是备胎（key空→非备胎）。"""
        ov = dict(_OVERRIDES)
        ov["subscription_api_key"] = ""
        monkeypatch.setattr("src.api.llm._llm_overrides", ov)
        r = _RecorderRouter()
        assert llm_api.register_variant_backup(r, "m") is None
        assert r.providers == {}

    def test_fail_safe_on_exception(self, monkeypatch):
        def boom():
            raise RuntimeError("config parse blew up")

        monkeypatch.setattr("src.api.llm.alternate_variant_config", boom)
        r = _RecorderRouter()
        assert llm_api.register_variant_backup(r, "m") is None  # 绝不反噬调用方
        assert r.providers == {}


def _mk_factory(handler, captured):
    """router_factory seam：真ModelRouter + MockTransport，注册逻辑照常执行。"""

    def factory():
        router = ModelRouter()
        router._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5)
        captured["router"] = router
        return router

    return factory


class TestBranchRouterWiring:
    @pytest.mark.asyncio
    async def test_variant_backup_registered_in_branch_router(self, monkeypatch):
        """遗留#4缺陷形态锁死：branch摘要router此前无任何variant-*备胎。"""
        monkeypatch.setattr("src.api.llm._llm_overrides", dict(_OVERRIDES))
        _pin_settings(monkeypatch)
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}}], "usage": {}},
            )

        out = await branch_summary._call_llm_router(
            "sys", "usr", router_factory=_mk_factory(handler, captured)
        )
        assert out == "ok"
        providers = captured["router"].providers  # ProviderConfig对象属性面
        assert set(providers) == {"openai", "variant-subscription", "ollama"}
        assert providers["openai"].priority == 0
        assert providers["variant-subscription"].priority == 5
        assert providers["ollama"].priority == 10
        assert providers["variant-subscription"].models == {"chat": "m-v"}

    @pytest.mark.asyncio
    async def test_unreachable_local_backup_not_registered(self, monkeypatch):
        """幻影备胎豁免（01:35遗留#2/08:48遗留#3销账）：ollama不可达→不入branch摘要
        链。修复前无条件注册=链尾死ollama，全链失败白打ConnectError噪音。"""
        monkeypatch.setattr("src.api.llm._llm_overrides", dict(_OVERRIDES))
        monkeypatch.setattr("src.api.llm._tcp_reachable", lambda *a, **k: False)
        llm_api._LOCAL_PROBE_CACHE.clear()
        _pin_settings(monkeypatch)
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}}], "usage": {}},
            )

        out = await branch_summary._call_llm_router(
            "sys", "usr", router_factory=_mk_factory(handler, captured)
        )
        assert out == "ok"
        providers = captured["router"].providers
        assert set(providers) == {"openai", "variant-subscription"}  # 无幻影ollama
        assert "ollama" not in providers

    @pytest.mark.asyncio
    async def test_primary_402_falls_to_variant_backup(self, monkeypatch):
        """live失败形态全回归：primary 402（额度耗尽）→ 变体备胎成活。

        修复前（备胎未入链）同输入=AllProvidersFailedError（只剩死ollama）→
        generate_branch_summary降级extractive-fallback=branch摘要质量饿死；
        修复后经变体备胎拿到LLM摘要，且tp- key按协议走api-key头。
        """
        monkeypatch.setattr("src.api.llm._llm_overrides", dict(_OVERRIDES))
        _pin_settings(monkeypatch)
        seen: list[tuple] = []
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(
                (
                    request.url.host,
                    request.headers.get("api-key"),
                    request.headers.get("authorization"),
                )
            )
            if request.url.host == "llm.example":
                return httpx.Response(402, json={"error": "quota"})
            if request.url.host == "sub.example":
                return httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": "branch-out"}}],
                        "usage": {},
                    },
                )
            return httpx.Response(500, json={"error": "unexpected host"})

        out = await branch_summary._call_llm_router(
            "sys", "usr", router_factory=_mk_factory(handler, captured)
        )
        assert out == "branch-out"
        # primary先打（Bearer标准key）→ 402端点级放弃 → 变体备胎（tp-走api-key头）
        assert seen[0] == ("llm.example", None, "Bearer sk-std")
        assert ("sub.example", "tp-sub", None) in seen
        assert all(host != "llm.example" or auth == "Bearer sk-std" for host, _, auth in seen)

    @pytest.mark.asyncio
    async def test_placeholder_backup_model_self_heals_live_shape(self, monkeypatch):
        """live失败形态原样全回归（2026-09-25 live实录输入逐字重放）：

        subscription_model=partial-test占位符 + chat无显式model（role="summarize"
        路径）→ 备胎首发partial-test 400 → chain_fallback主模型接住。chain_fallback
        修复前同输入=AllProvidersFailedError（6链attempt全灭实录）→
        generate_branch_summary降级extractive-fallback。
        """
        ov = dict(_OVERRIDES)
        ov["subscription_model"] = "partial-test"
        monkeypatch.setattr("src.api.llm._llm_overrides", ov)
        _pin_settings(monkeypatch)
        seen: list[tuple] = []
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            model = json.loads(request.content)["model"]
            seen.append((request.url.host, model))
            if request.url.host == "llm.example":
                return httpx.Response(402, json={"error": "quota"})
            if request.url.host == "sub.example":
                if model == "partial-test":
                    return httpx.Response(400, json={"error": f"Unsupported model {model}"})
                return httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": "branch-out"}}],
                        "usage": {},
                    },
                )
            return httpx.Response(500, json={"error": "unexpected host"})

        out = await branch_summary._call_llm_router(
            "sys", "usr", router_factory=_mk_factory(handler, captured)
        )
        assert out == "branch-out"
        assert ("sub.example", "partial-test") in seen  # 占位model先被试并拒绝
        assert ("sub.example", "m-v") in seen  # chain_fallback主模型接住

    @pytest.mark.asyncio
    async def test_no_backup_primary_402_fails_loudly(self, monkeypatch):
        """负控制：无变体备胎时primary 402=链全灭显式抛错（不是静默空摘要）。"""
        monkeypatch.setattr(
            "src.api.llm._llm_overrides",
            {"active_variant": "standard", "base_url": "https://llm.example/v1"},
        )
        _pin_settings(monkeypatch)
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "llm.example":
                return httpx.Response(402, json={"error": "quota"})
            return httpx.Response(500, json={"error": "unexpected host"})

        with pytest.raises(AllProvidersFailedError):
            await branch_summary._call_llm_router(
                "sys", "usr", router_factory=_mk_factory(handler, captured)
            )
        assert not any(n.startswith("variant-") for n in captured["router"].providers)

    @pytest.mark.asyncio
    async def test_summary_degrades_visibly_without_backup(self, monkeypatch):
        """负控制·端到端语义：无备胎402→generate_branch_summary降级extractive-
        fallback+error可见（失败必须可见），证明修复前的用户可见缺陷形态。"""
        monkeypatch.setattr(
            "src.api.llm._llm_overrides",
            {"active_variant": "standard", "base_url": "https://llm.example/v1"},
        )
        _pin_settings(monkeypatch)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(402, json={"error": "quota"})

        real_call = branch_summary._call_llm_router  # 先捕获真身（防自递归）
        router_holder = {}

        async def fake_router(system_prompt, user_prompt, router_factory=None):
            return await real_call(
                system_prompt,
                user_prompt,
                router_factory=_mk_factory(handler, router_holder),
            )

        monkeypatch.setattr(branch_summary, "_call_llm_router", fake_router)
        entries = [
            {"role": "user", "content": "帮我修复登录bug", "timestamp": 1.0},
            {"role": "assistant", "content": "已定位到auth模块", "timestamp": 2.0},
        ]
        result = branch_summary.generate_branch_summary(
            entries, summarizer=branch_summary.llm_branch_summarizer
        )
        assert result["summarizer"] == "extractive-fallback"
        assert result.get("error")  # 失败可见

    @pytest.mark.asyncio
    async def test_variant_backup_makes_summary_llm_success(self, monkeypatch):
        """对照上一用例：同输入+备胎入链→summarizer_used="llm"（LLM摘要成活）。"""
        monkeypatch.setattr("src.api.llm._llm_overrides", dict(_OVERRIDES))
        _pin_settings(monkeypatch)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "llm.example":
                return httpx.Response(402, json={"error": "quota"})
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    "## Goal\n修复登录bug\n\n## Progress\n### Done\n"
                                    "- [x] read_file\n\n## Next Steps\n1. 继续\n"
                                )
                            }
                        }
                    ],
                    "usage": {},
                },
            )

        real_call = branch_summary._call_llm_router  # 先捕获真身（防自递归）
        router_holder = {}

        async def fake_router(system_prompt, user_prompt, router_factory=None):
            return await real_call(
                system_prompt,
                user_prompt,
                router_factory=_mk_factory(handler, router_holder),
            )

        monkeypatch.setattr(branch_summary, "_call_llm_router", fake_router)
        entries = [
            {"role": "user", "content": "帮我修复登录bug", "timestamp": 1.0},
            {"role": "assistant", "content": "已定位到auth模块", "timestamp": 2.0},
        ]
        result = branch_summary.generate_branch_summary(
            entries, summarizer=branch_summary.llm_branch_summarizer
        )
        assert result["summarizer"] == "llm"
        assert "## Goal" in result["summary"]


class TestConvergedWiringUnchanged:
    """memory_model收敛到register_variant_backup单一真源后语义不变（防收敛回归）。"""

    def test_memory_model_backup_via_single_source(self, monkeypatch):
        from src.hippo import memory_model

        monkeypatch.setattr("src.api.llm._llm_overrides", dict(_OVERRIDES))
        r = memory_model._build_router(
            memory_model.ResolvedMemoryModel(
                model_id="m-v",
                base_url="https://llm.example/v1",
                api_key="sk-std",
                source="session",
                fallback=None,
                sampling={},
            )
        )
        assert "variant-subscription" in r.providers
        vp = r.providers["variant-subscription"]
        assert vp.priority == 5
        assert vp.models == {"chat": "m-v"}
        assert r.key_manager.next_key("variant-subscription") == "tp-sub"
