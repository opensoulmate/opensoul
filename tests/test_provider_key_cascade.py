"""GLAND-① remove_provider 级联清钥——整 provider 移除含钥（孤钥清理语义 DEC-㉑ 取 B）。

修前语义缺口：DELETE /api/gland/providers/{name} 只 pop 配置不动 KeyManager._slots
——移除注册后其钥留驻内存（GET /keys 可见面、同名再注册即复用旧钥），与
H1-②b-④ 单钥面 docstring「整 provider 移除走 DELETE /providers/{name}，语义分界」
漂移=孤钥。收口（DEC-㉑ 取 B）：provider 注册与其凭据同生共死，移除注册即撤销其钥。

隔离纪律（test_key_remove_face 先例）：进程内 FastAPI 挂真实 gland router +
monkeypatch 替换 gateway.key_manager 为全新 KeyManager 实例（内存面无落盘，teardown
自动还原），绝不打 live :8090、绝不碰生产 env 密钥；_ensure_bootstrapped 置 no-op
（既有 bootstrap 装配与本拍级联面正交，且会注入 env 密钥使计数不确定）。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.gland as gland_api
from src.gland.key_manager import KeyManager
from src.gland.router import ModelRouter


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
    """进程内 app 挂真实 gland router（与 main.py 同 prefix）。"""
    monkeypatch.setattr(gland_api, "_ensure_bootstrapped", lambda: None)
    app = FastAPI()
    app.include_router(gland_api.router, prefix="/api/gland")
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def gw():
    """全新 ModelRouter（独立 providers/KeyManager，不碰模块单例）。"""
    return ModelRouter()


def _slot_keys(km: KeyManager, provider: str) -> list[str]:
    """测试内省：逐槽解密钥值（KeySlot.key 活原语直接表达，test_key_remove_face 同款）。"""
    return [s.key for s in km._slots.get(provider, []) if s.is_valid]


# ── KeyManager 级联清钥原语 ─────────────────────────────────────


class TestRemoveProviderKeysPrimitive:
    def test_removes_all_keys_of_provider_only(self, km):
        km.add_key("prov-a", "key-one-111111")
        km.add_key("prov-a", "key-two-222222")
        km.add_key("prov-b", "key-three-333333")
        assert km.remove_provider_keys("prov-a") == 2
        assert _slot_keys(km, "prov-a") == []
        assert km.next_key("prov-a") is None
        # 只动本 provider：其余 provider 一行不动
        assert _slot_keys(km, "prov-b") == ["key-three-333333"]

    def test_zero_keys_returns_zero_not_error(self, km):
        km.add_key("prov-c", "key-four-444444")
        assert km.remove_provider_keys("prov-empty") == 0
        assert _slot_keys(km, "prov-c") == ["key-four-444444"]

    def test_unknown_provider_zero_side_effect(self, km):
        km.add_key("prov-d", "key-five-555555")
        assert km.remove_provider_keys("prov-never") == 0
        assert _slot_keys(km, "prov-d") == ["key-five-555555"]

    def test_rotation_index_reset_with_keys(self, km):
        km.add_key("prov-e", "key-six-666666")
        km.add_key("prov-e", "key-seven-777777")
        km.next_key("prov-e")  # 推进轮转指针
        assert "prov-e" in km._indices
        assert km.remove_provider_keys("prov-e") == 2
        # 轮转指针随钥一并清（防陈旧指针偏移后续 rotation）
        assert "prov-e" not in km._indices


# ── ModelRouter.remove_provider 级联 ────────────────────────────


class TestGatewayCascade:
    def test_remove_provider_cascades_keys_and_returns_count(self, gw):
        gw.add_provider("prov-x", "https://x.example/v1")
        gw.key_manager.add_key("prov-x", "key-x-11111111")
        gw.key_manager.add_key("prov-x", "key-x-22222222")
        gw.add_provider("prov-y", "https://y.example/v1")
        gw.key_manager.add_key("prov-y", "key-y-33333333")

        assert gw.remove_provider("prov-x") == 2
        assert "prov-x" not in gw.providers
        assert gw.key_manager.status().get("prov-x", []) == []
        # 其余 provider 注册与钥一行不动
        assert "prov-y" in gw.providers
        assert _slot_keys(gw.key_manager, "prov-y") == ["key-y-33333333"]

    def test_provider_without_keys_returns_zero_still_removed(self, gw):
        """0 钥=成功移除（falsy 陷阱回归锁死：0 不得被当未命中）。"""
        gw.add_provider("prov-z", "https://z.example/v1")
        res = gw.remove_provider("prov-z")
        assert res == 0
        # 修前形态是 bool False（False == 0 会假绿）——新契约=int，显式排除 bool
        assert res is not False
        assert isinstance(res, int) and not isinstance(res, bool)
        assert "prov-z" not in gw.providers

    def test_unknown_provider_returns_none(self, gw):
        assert gw.remove_provider("ghost-none") is None

    def test_unknown_provider_keys_untouched(self, gw):
        """未命中零副作用（不变面，新旧皆过）：绝不顺带清同名钥（env bootstrap 钥可合法存在于未注册 provider）。"""
        gw.key_manager.add_key("ghost", "key-g-44444444")
        gw.remove_provider("ghost")
        assert _slot_keys(gw.key_manager, "ghost") == ["key-g-44444444"]

    def test_return_contract_found_int_missing_none(self, gw):
        gw.add_provider("prov-w", "https://w.example/v1")
        found = gw.remove_provider("prov-w")
        missing = gw.remove_provider("prov-w")
        assert isinstance(found, int) and not isinstance(found, bool)
        assert missing is None


# ── API 全链（DELETE /api/gland/providers/{name}）────────────────


class TestProviderDeleteAPI:
    def test_delete_cascades_keys_response_contract(self, client):
        resp = client.post(
            "/api/gland/providers",
            json={
                "name": "e2e-prov",
                "base_url": "https://e2e.example/v1",
                "api_key": "sk-e2e-11111111",
            },
        )
        assert resp.status_code == 200
        assert client.get("/api/gland/keys").json().get("e2e-prov")

        resp = client.delete("/api/gland/providers/e2e-prov")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "provider": "e2e-prov", "keys_removed": 1}
        # 钥真随删（孤钥归零）+ 注册真删
        assert "e2e-prov" not in client.get("/api/gland/keys").json()
        names = [p["name"] for p in client.get("/api/gland/providers").json()["providers"]]
        assert "e2e-prov" not in names

    def test_delete_unknown_404_zero_side_effect(self, client):
        resp = client.post(
            "/api/gland/keys",
            json={"provider": "ghost-api", "api_key": "sk-ghost-22222222"},
        )
        assert resp.status_code == 200
        resp = client.delete("/api/gland/providers/ghost-api")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Provider 'ghost-api' not found"
        # 未命中绝不变相清钥
        assert client.get("/api/gland/keys").json().get("ghost-api")

    def test_delete_provider_without_keys_returns_zero(self, client):
        resp = client.post(
            "/api/gland/providers",
            json={"name": "e2e-nokey", "base_url": "https://nokey.example/v1"},
        )
        assert resp.status_code == 200
        resp = client.delete("/api/gland/providers/e2e-nokey")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "provider": "e2e-nokey", "keys_removed": 0}

    def test_other_provider_keys_survive_and_single_key_face_intact(self, client):
        client.post(
            "/api/gland/providers",
            json={
                "name": "e2e-keep",
                "base_url": "https://keep.example/v1",
                "api_key": "sk-keep-33333333",
            },
        )
        client.post(
            "/api/gland/providers",
            json={
                "name": "e2e-drop",
                "base_url": "https://drop.example/v1",
                "api_key": "sk-drop-44444444",
            },
        )
        resp = client.delete("/api/gland/providers/e2e-drop")
        assert resp.status_code == 200 and resp.json()["keys_removed"] == 1
        keys = client.get("/api/gland/keys").json()
        assert "e2e-drop" not in keys
        assert len(keys.get("e2e-keep", [])) == 1
        # 单钥移除面零回归（H1-②b-④ 语义分界另一侧照旧）
        resp = client.delete("/api/gland/keys/e2e-keep/0")
        assert resp.status_code == 200 and resp.json()["remaining"] == 0
