"""H1-②b-④ KeyManager remove_key 单钥移除面——DELETE /api/gland/keys/{provider}/{index}。

隔离纪律（TL-②/H1-②b-① 先例）：进程内 FastAPI 挂真实 gland router + monkeypatch 替换
gateway.key_manager 为全新 KeyManager 实例（内存面无落盘，teardown 自动还原），绝不打
live :8090、绝不碰生产 env 密钥；_ensure_bootstrapped 置 no-op（既有 bootstrap 装配与
本拍移除面正交，且会注入 env 密钥使列表位置不确定）。
"""

import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.gland as gland_api
from src.gland.key_manager import KeyManager


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


# ── KeyManager 原语 ─────────────────────────────────────────────


def _slot_keys(km: KeyManager, provider: str) -> list[str]:
    """测试内省：逐槽解密钥值（KeySlot.key 活原语直接表达）。

    DEC-⑳-②：get_keys（明文批量回吐便利包装）已删（H1-②「回吐面不应存在」
    贯彻到方法本体）；本 helper 只是断言读法，语义与被删包装逐字一致
    （is_valid 过滤 + 保序解密），断言意图零丢失。
    """
    return [s.key for s in km._slots.get(provider, []) if s.is_valid]


class TestRemoveKeyPrimitive:
    def test_remove_by_index_keeps_rest_in_order(self, km):
        km.add_key("prov-a", "key-one-111111")
        km.add_key("prov-a", "key-two-222222")
        km.add_key("prov-a", "key-three-333333")
        assert km.remove_key("prov-a", 1) is True
        assert _slot_keys(km, "prov-a") == ["key-one-111111", "key-three-333333"]

    def test_remove_last_key_leaves_provider_empty(self, km):
        km.add_key("prov-b", "only-key-444444")
        assert km.remove_key("prov-b", 0) is True
        assert _slot_keys(km, "prov-b") == []
        assert km.next_key("prov-b") is None

    @pytest.mark.parametrize("index", [1, 99, -1])
    def test_miss_returns_false(self, km, index):
        km.add_key("prov-c", "key-five-555555")
        assert km.remove_key("prov-c", index) is False
        # 未命中零副作用：钥仍在场
        assert _slot_keys(km, "prov-c") == ["key-five-555555"]

    def test_unknown_provider_returns_false(self, km):
        assert km.remove_key("prov-never", 0) is False

    def test_rotation_survives_removal(self, km):
        km.add_key("prov-d", "key-six-666666")
        km.add_key("prov-d", "key-seven-777777")
        km.next_key("prov-d")  # 推进轮转指针
        assert km.remove_key("prov-d", 0) is True
        # 轮转指针越界不炸，仍返回存活钥
        assert km.next_key("prov-d") == "key-seven-777777"


# ── API 全链 ────────────────────────────────────────────────────


class TestRemoveKeyFace:
    def test_delete_removes_single_key(self, client, km):
        km.add_key("prov-api", "api-key-aaaa")
        km.add_key("prov-api", "api-key-bbbb")
        resp = client.delete("/api/gland/keys/prov-api/0")
        assert resp.status_code == 200
        data = resp.json()
        # 响应键集恰 4 键
        assert set(data.keys()) == {"status", "provider", "index", "remaining"}
        assert data["status"] == "ok"
        assert data["provider"] == "prov-api"
        assert data["index"] == 0
        assert data["remaining"] == 1
        # 只删 index 0 那把，第二把仍在（按 GET /keys 位置序）
        listed = client.get("/api/gland/keys").json()["prov-api"]
        assert len(listed) == 1
        assert listed[0]["masked_key"] == "api-...bbbb"

    @pytest.mark.parametrize(
        "path",
        [
            "/api/gland/keys/prov-nope/0",  # 未知 provider
            "/api/gland/keys/prov-api/5",  # 越界
            "/api/gland/keys/prov-api/-1",  # 负 index
        ],
    )
    def test_miss_is_404_not_ok(self, client, km, path):
        km.add_key("prov-api", "api-key-cccc")
        resp = client.delete(path)
        assert resp.status_code == 404
        data = resp.json()
        assert "Key not found" in data["detail"]
        # 404 零副作用
        assert len(client.get("/api/gland/keys").json()["prov-api"]) == 1

    def test_delete_only_touches_target_slot(self, client, km):
        km.add_key("prov-e", "key-ee-888888")
        km.add_key("prov-f", "key-ff-999999")
        resp = client.delete("/api/gland/keys/prov-e/0")
        assert resp.status_code == 200
        assert resp.json()["remaining"] == 0
        all_keys = client.get("/api/gland/keys").json()
        # 他 provider 一行不动
        assert len(all_keys["prov-f"]) == 1


# ── 既有面零变化 ────────────────────────────────────────────────


class TestExistingFacesUnchanged:
    def test_get_keys_shape_unchanged(self, client, km):
        km.add_key("prov-g", "key-gg-121212")
        resp = client.get("/api/gland/keys")
        assert resp.status_code == 200
        entry = resp.json()["prov-g"][0]
        assert set(entry.keys()) == {"masked_key", "is_valid", "fail_count", "last_checked"}

    def test_post_keys_contract_unchanged(self, client, km):
        resp = client.post(
            "/api/gland/keys", json={"provider": "prov-h", "api_key": "key-hh-343434"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"status", "masked"}
        assert data["status"] == "ok"
        assert _slot_keys(km, "prov-h") == ["key-hh-343434"]


# ── 静态接线普查 ────────────────────────────────────────────────


class TestStaticWiring:
    def test_endpoint_calls_remove_key(self):
        src = inspect.getsource(gland_api)
        assert "gateway.key_manager.remove_key(" in src
        assert '@router.delete("/keys/{provider}/{index}")' in src
