"""GLAND-③ PATCH /api/gland/providers/{name} 就地改面（不触钥，DEC-㉓）。

修前语义缺口（GLAND-② 诚实边界③留档件）：GLAND-② 注册面 create 语义收口后，
provider 配置没有任何就地改通道——改 priority/models 只能 DELETE（GLAND-①
级联清钥）+ POST（重录钥）= 改配置必丢钥；「保钥就地改配置」面整体缺席。

收口（DEC-㉓ 自决取 A）：PATCH 就地改（不触钥）——只改给出的字段（整值替换
非合并）：base_url/models/priority/enabled，keys 一律不动；凭据端点绑定原则
（DEC-㉒「旧钥不应打向新 base_url」）：持钥 provider 改 base_url=409 原子拒
零写入（换端点=先移钥或 DELETE+POST），无钥照常改；base_url 归一后与现值相同
=非改道放行；未注册 404 零副作用；空载荷 400；未知字段（name 等）422
（extra="forbid" 禁静默忽略）。

隔离纪律（test_provider_key_cascade/test_provider_reregister_face 先例）：
进程内 FastAPI 挂真实 gland router + monkeypatch 替换 gateway.key_manager 为
全新 KeyManager + 快照还原 gateway.providers（内存面无落盘，teardown 自动还原），
绝不打 live :8090、绝不碰生产 provider/env 密钥；_ensure_bootstrapped 置 no-op。
"""

import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.gland as gland_api
from src.gland.key_manager import KeyManager
from src.gland.router import ModelRouter, ProviderRetargetError


@pytest.fixture()
def km():
    """全新 KeyManager 替换模块单例（teardown 自动还原原实例）。"""
    fresh = KeyManager()
    original = gland_api.gateway.key_manager
    gland_api.gateway.key_manager = fresh
    yield fresh
    gland_api.gateway.key_manager = original


@pytest.fixture()
def client(km, monkeypatch):
    """进程内 app 挂真实 gland router（与 main.py 同 prefix）+providers 快照还原。"""
    monkeypatch.setattr(gland_api, "_ensure_bootstrapped", lambda: None)
    saved = dict(gland_api.gateway.providers)
    gland_api.gateway.providers.clear()
    app = FastAPI()
    app.include_router(gland_api.router, prefix="/api/gland")
    with TestClient(app) as c:
        yield c
    gland_api.gateway.providers.clear()
    gland_api.gateway.providers.update(saved)


@pytest.fixture()
def gw():
    """全新 ModelRouter（独立 providers/KeyManager，不碰模块单例）。"""
    return ModelRouter()


def _km_of() -> KeyManager:
    """测试内省：当前 gateway 挂载的 KeyManager（fixture 已替换为全新实例）。"""
    return gland_api.gateway.key_manager


def _slot_keys(km: KeyManager, provider: str) -> list[str]:
    """测试内省：逐槽解密钥值（KeySlot.key 活原语直接表达，test_key_remove_face 同款）。"""
    return [s.key for s in km._slots.get(provider, []) if s.is_valid]


def _create(client, name="prov-patch", base_url="https://a.example/v1", api_key=None, **kw):
    payload = {"name": name, "base_url": base_url}
    if api_key is not None:
        payload["api_key"] = api_key
    payload.update(kw)
    return client.post("/api/gland/providers", json=payload)


# ── HTTP 就地改面 ───────────────────────────────────────────────


class TestHttpPatchFace:
    def test_patch_priority_in_place_keys_untouched(self, client):
        assert _create(client, api_key="key-patch-11111111").status_code == 200
        before = _slot_keys(_km_of(), "prov-patch")
        r = client.patch("/api/gland/providers/prov-patch", json={"priority": 7})
        assert r.status_code == 200
        assert r.json() == {
            "status": "ok",
            "provider": "prov-patch",
            "updated": ["priority"],
            "keys_untouched": 1,
        }
        rows = {p["name"]: p for p in client.get("/api/gland/providers").json()["providers"]}
        assert rows["prov-patch"]["priority"] == 7
        assert rows["prov-patch"]["base_url"] == "https://a.example/v1"
        # 不触钥硬反证：槽位一字不动
        assert _slot_keys(_km_of(), "prov-patch") == before

    def test_patch_models_replace_semantics(self, client):
        assert _create(client, models={"chat": "m1", "embedding": "e1"}).status_code == 200
        r = client.patch("/api/gland/providers/prov-patch", json={"models": {"chat": "m2"}})
        assert r.status_code == 200
        assert r.json()["updated"] == ["models"]
        rows = {p["name"]: p for p in client.get("/api/gland/providers").json()["providers"]}
        # 整值替换非合并：{"chat":"m2"} 整表替换 {"chat":"m1","embedding":"e1"}
        assert rows["prov-patch"]["models"] == {"chat": "m2"}

    def test_patch_enabled_toggle_routing_field(self, client):
        assert _create(client).status_code == 200
        r1 = client.patch("/api/gland/providers/prov-patch", json={"enabled": False})
        assert r1.status_code == 200
        rows = {p["name"]: p for p in client.get("/api/gland/providers").json()["providers"]}
        assert rows["prov-patch"]["enabled"] is False
        r2 = client.patch("/api/gland/providers/prov-patch", json={"enabled": True})
        assert r2.status_code == 200
        rows = {p["name"]: p for p in client.get("/api/gland/providers").json()["providers"]}
        assert rows["prov-patch"]["enabled"] is True

    def test_patch_multiple_fields_applied(self, client):
        assert _create(client).status_code == 200
        r = client.patch(
            "/api/gland/providers/prov-patch",
            json={"priority": 3, "models": {"chat": "mm"}, "enabled": False},
        )
        assert r.status_code == 200
        assert r.json()["updated"] == ["models", "priority", "enabled"]
        rows = {p["name"]: p for p in client.get("/api/gland/providers").json()["providers"]}
        assert rows["prov-patch"]["priority"] == 3
        assert rows["prov-patch"]["models"] == {"chat": "mm"}
        assert rows["prov-patch"]["enabled"] is False

    def test_patch_base_url_without_keys_allowed(self, client):
        assert _create(client, base_url="https://a.example/v1").status_code == 200
        r = client.patch(
            "/api/gland/providers/prov-patch",
            json={"base_url": "https://b.example/v1/"},
        )
        assert r.status_code == 200
        rows = {p["name"]: p for p in client.get("/api/gland/providers").json()["providers"]}
        # rstrip("/") 归一同 add_provider
        assert rows["prov-patch"]["base_url"] == "https://b.example/v1"

    def test_patch_base_url_with_keys_409_atomic_zero_side_effect(self, client):
        assert _create(client, api_key="key-patch-11111111").status_code == 200
        before = _slot_keys(_km_of(), "prov-patch")
        r = client.patch(
            "/api/gland/providers/prov-patch",
            json={"base_url": "https://evil.example/v1", "priority": 9},
        )
        assert r.status_code == 409
        detail = str(r.json()["detail"])
        assert "base_url" in detail and "key" in detail
        rows = {p["name"]: p for p in client.get("/api/gland/providers").json()["providers"]}
        # 原子拒硬反证：同载荷的 priority 也一字不写（先校验后落写）
        assert rows["prov-patch"]["base_url"] == "https://a.example/v1"
        assert rows["prov-patch"]["priority"] == 0
        assert _slot_keys(_km_of(), "prov-patch") == before

    def test_patch_base_url_same_value_allowed_with_keys(self, client):
        assert _create(client, api_key="key-patch-11111111").status_code == 200
        before = _slot_keys(_km_of(), "prov-patch")
        # 归一后与现值相同 = 非改道，不误伤
        r = client.patch(
            "/api/gland/providers/prov-patch",
            json={"base_url": "https://a.example/v1/"},
        )
        assert r.status_code == 200
        assert _slot_keys(_km_of(), "prov-patch") == before

    def test_patch_retarget_after_key_removal_allowed(self, client):
        """换端点流程：移钥后改道照常放行（凭据端点绑定的正路径）。"""
        assert _create(client, api_key="key-patch-11111111").status_code == 200
        assert client.delete("/api/gland/keys/prov-patch/0").status_code == 200
        r = client.patch(
            "/api/gland/providers/prov-patch", json={"base_url": "https://b.example/v1"}
        )
        assert r.status_code == 200
        rows = {p["name"]: p for p in client.get("/api/gland/providers").json()["providers"]}
        assert rows["prov-patch"]["base_url"] == "https://b.example/v1"

    def test_patch_unknown_provider_404_zero_side_effect(self, client):
        assert _create(client, api_key="key-patch-11111111").status_code == 200
        before = _slot_keys(_km_of(), "prov-patch")
        r = client.patch("/api/gland/providers/no-such", json={"priority": 5})
        assert r.status_code == 404
        # 404 必须来自 provider 缺行（带真因 detail），非「路由不存在」通用 404
        assert "not found" in str(r.json()["detail"])
        rows = {p["name"]: p for p in client.get("/api/gland/providers").json()["providers"]}
        assert rows["prov-patch"]["priority"] == 0
        assert _slot_keys(_km_of(), "prov-patch") == before

    def test_patch_empty_body_400(self, client):
        assert _create(client).status_code == 200
        r = client.patch("/api/gland/providers/prov-patch", json={})
        assert r.status_code == 400
        assert "No updatable fields" in str(r.json()["detail"])

    def test_patch_extra_field_422_name_immutable(self, client):
        assert _create(client).status_code == 200
        before = _slot_keys(_km_of(), "prov-patch")
        # name=身份不可改；未知字段如实 422 禁静默忽略
        r = client.patch("/api/gland/providers/prov-patch", json={"name": "renamed"})
        assert r.status_code == 422
        rows = {p["name"]: p for p in client.get("/api/gland/providers").json()["providers"]}
        assert "prov-patch" in rows and "renamed" not in rows
        assert _slot_keys(_km_of(), "prov-patch") == before

    def test_patch_explicit_null_field_400_not_silently_ignored(self, client):
        assert _create(client).status_code == 200
        # 显式 null ≠ 未提供：如实 400 禁静默当缺省（F4b-㉙ 同纪律）
        r = client.patch("/api/gland/providers/prov-patch", json={"priority": None})
        assert r.status_code == 400
        rows = {p["name"]: p for p in client.get("/api/gland/providers").json()["providers"]}
        assert rows["prov-patch"]["priority"] == 0

    def test_patch_keys_untouched_count_multiple(self, client):
        assert _create(client, api_key="key-patch-11111111").status_code == 200
        assert (
            client.post(
                "/api/gland/keys",
                json={"provider": "prov-patch", "api_key": "key-patch-22222222"},
            ).status_code
            == 200
        )
        before = _slot_keys(_km_of(), "prov-patch")
        r = client.patch("/api/gland/providers/prov-patch", json={"priority": 2})
        assert r.status_code == 200
        assert r.json()["keys_untouched"] == 2
        assert _slot_keys(_km_of(), "prov-patch") == before


# ── 引擎原语 ─────────────────────────────────────────────────────


class TestEngineUpdateProvider:
    def test_miss_returns_none_zero_side_effect(self, gw):
        gw.add_provider("p", "https://a.example/v1")
        assert gw.update_provider("ghost", {"priority": 9}) is None
        assert gw.providers["p"].priority == 0

    def test_unknown_field_raises_zero_side_effect(self, gw):
        gw.add_provider("p", "https://a.example/v1", priority=1)
        with pytest.raises(ValueError, match="Unknown provider field"):
            gw.update_provider("p", {"api_key": "k", "priority": 9})
        assert gw.providers["p"].priority == 1

    @pytest.mark.parametrize(
        "changes",
        [
            {"base_url": ""},
            {"base_url": "   "},
            {"base_url": 5},
            {"models": ["chat"]},
            {"models": {"chat": 5}},
            {"priority": True},
            {"priority": "3"},
            {"enabled": 1},
        ],
    )
    def test_type_validation_matrix(self, gw, changes):
        gw.add_provider("p", "https://a.example/v1", priority=1)
        with pytest.raises(ValueError):
            gw.update_provider("p", changes)
        # 原子：非法载荷零写入
        assert gw.providers["p"].base_url == "https://a.example/v1"
        assert gw.providers["p"].priority == 1

    def test_retarget_error_is_value_error(self):
        # 契约：既有宽泛 ValueError 捕获面可覆盖（A2AError 先例）
        assert issubclass(ProviderRetargetError, ValueError)

    def test_unprovided_fields_untouched_including_failure_counters(self, gw):
        gw.add_provider("p", "https://a.example/v1", models={"chat": "m1"}, priority=1)
        gw.providers["p"]._consecutive_failures = 2
        cfg = gw.update_provider("p", {"priority": 5})
        assert cfg is gw.providers["p"]
        assert cfg.priority == 5
        assert cfg.models == {"chat": "m1"}
        assert cfg._consecutive_failures == 2

    def test_models_empty_dict_clears(self, gw):
        gw.add_provider("p", "https://a.example/v1", models={"chat": "m1"})
        cfg = gw.update_provider("p", {"models": {}})
        assert cfg.models == {}


# ── 静态接线锁死 ─────────────────────────────────────────────────


class TestStaticWiring:
    def test_patch_route_wired_to_engine_update(self):
        src = inspect.getsource(gland_api)
        assert '@router.patch("/providers/{name}")' in src
        assert "gateway.update_provider(" in src
        assert "ProviderRetargetError" in src
        engine_src = inspect.getsource(ModelRouter)
        assert "def update_provider" in engine_src
