"""GLAND-② add_provider 同名覆盖注册面清钥语义——注册面=create 语义收口（DEC-㉒ 自决取 A）。

修前语义缺口（pending-decisions ㉑ 诚实边界③留档件）：POST /api/gland/providers 对
同名 provider 直接覆盖注册但仍保留旧钥打向新 base_url——「同名再注册即复用旧钥」
（DEC-㉑ 命名的凭据滞留残留面）：意外撞名（手误/同名不同端点）时旧凭据被静默用于
新上游端点=凭据泄露；且 create 面静默覆盖既有身份=谎报「新建」。

收口（DEC-㉒ 自决取 A）：注册面=create 语义——同名注册=身份冲突如实 409 零副作用
（既有注册与钥一字不动）；替换=先 DELETE /api/gland/providers/{name}（GLAND-①
级联清钥=旧凭据随旧注册撤销）再 POST 新注册。引擎层 ModelRouter.add_provider
保持 upsert（内部备胎幂等重注册语义真依赖：register_local_backup
「已注册→幂等返回不重建ProviderConfig」/variant 备胎注册），本闸只收外部注册面
——边界由 test_engine_upsert_boundary 锁死（改语义须先改契约）。

隔离纪律（test_provider_key_cascade 先例）：进程内 FastAPI 挂真实 gland router +
monkeypatch 替换 gateway.key_manager 为全新 KeyManager + 快照还原 gateway.providers
（内存面无落盘，teardown 自动还原），绝不打 live :8090、绝不碰生产 provider/env 密钥；
_ensure_bootstrapped 置 no-op（既有 bootstrap 装配与本拍注册面正交，且会注入
env 密钥/注册 openai 使冲突判定不确定）。
"""

import inspect

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


# ── HTTP 注册面：create 语义 ────────────────────────────────────


class TestHttpRegistrationFace:
    def test_same_name_conflict_409_zero_side_effect(self, client):
        r1 = client.post(
            "/api/gland/providers",
            json={
                "name": "prov-rereg",
                "base_url": "https://a.example/v1",
                "api_key": "key-old-11111111",
                "priority": 1,
            },
        )
        assert r1.status_code == 200
        r2 = client.post(
            "/api/gland/providers",
            json={
                "name": "prov-rereg",
                "base_url": "https://b.example/v1",
                "api_key": "key-new-22222222",
                "priority": 7,
            },
        )
        assert r2.status_code == 409
        # 零副作用硬反证：既有注册一字不动（base_url/priority 不被覆盖）
        row = [
            p
            for p in client.get("/api/gland/providers").json()["providers"]
            if p["name"] == "prov-rereg"
        ]
        assert len(row) == 1
        assert row[0]["base_url"] == "https://a.example/v1"
        assert row[0]["priority"] == 1
        # 零副作用硬反证：旧钥原样留驻（绝不被 409 路径顺带清），新钥绝不落槽
        assert _slot_keys(_km_of(), "prov-rereg") == ["key-old-11111111"]

    def test_conflict_detail_names_delete_path(self, client):
        client.post(
            "/api/gland/providers",
            json={"name": "prov-detail", "base_url": "https://a.example/v1"},
        )
        r = client.post(
            "/api/gland/providers",
            json={"name": "prov-detail", "base_url": "https://b.example/v1"},
        )
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert "prov-detail" in detail
        assert "DELETE" in detail

    def test_new_name_contract_unchanged(self, client):
        r = client.post(
            "/api/gland/providers",
            json={
                "name": "prov-fresh",
                "base_url": "https://f.example/v1",
                "api_key": "key-fresh-33333333",
            },
        )
        assert r.status_code == 200
        # 既有响应契约逐键不变（加性键都不加：本拍零契约变更）
        assert r.json() == {"status": "ok", "provider": "prov-fresh"}
        assert _slot_keys(_km_of(), "prov-fresh") == ["key-fresh-33333333"]

    def test_replace_flow_delete_then_post(self, client):
        client.post(
            "/api/gland/providers",
            json={
                "name": "prov-replace",
                "base_url": "https://a.example/v1",
                "api_key": "key-old-44444444",
            },
        )
        rd = client.delete("/api/gland/providers/prov-replace")
        assert rd.status_code == 200
        assert rd.json()["keys_removed"] == 1  # GLAND-① 级联清钥=旧凭据随旧注册撤销
        assert _slot_keys(_km_of(), "prov-replace") == []
        r2 = client.post(
            "/api/gland/providers",
            json={
                "name": "prov-replace",
                "base_url": "https://b.example/v1",
                "api_key": "key-new-55555555",
            },
        )
        assert r2.status_code == 200
        row = [
            p
            for p in client.get("/api/gland/providers").json()["providers"]
            if p["name"] == "prov-replace"
        ]
        assert row[0]["base_url"] == "https://b.example/v1"
        # 替换后唯一钥=新钥，旧钥绝不可达（「同名再注册即复用旧钥」构造上不可达）
        assert _slot_keys(_km_of(), "prov-replace") == ["key-new-55555555"]
        assert "key-old-44444444" not in _slot_keys(_km_of(), "prov-replace")


# ── 引擎层 upsert 边界锁死（内部备胎幂等重注册语义不动） ────────


class TestEngineUpsertBoundary:
    def test_engine_upsert_overwrites_registration_preserves_keys(self, gw):
        gw.add_provider("prov-up", "https://a.example/v1", priority=1)
        gw.key_manager.add_key("prov-up", "key-up-66666666")
        gw.add_provider("prov-up", "https://b.example/v1", priority=2)
        # 引擎面保持 upsert：注册被覆盖、钥留驻（register_variant_backup 注册块
        # 依赖此语义；若要改语义须先改本契约——HTTP 面与引擎面刻意两语义）
        assert gw.providers["prov-up"].base_url == "https://b.example/v1"
        assert gw.providers["prov-up"].priority == 2
        assert [s.key for s in gw.key_manager._slots.get("prov-up", [])] == ["key-up-66666666"]

    def test_engine_upsert_not_gated_by_conflict_check(self, gw):
        gw.add_provider("prov-gate", "https://a.example/v1")
        assert gw.has_provider("prov-gate") is True
        # has_provider 是判定口非闸门：引擎面照常 upsert 不被拒
        gw.add_provider("prov-gate", "https://b.example/v1")
        assert gw.providers["prov-gate"].base_url == "https://b.example/v1"

    def test_has_provider_predicate(self, gw):
        assert gw.has_provider("prov-none") is False
        gw.add_provider("prov-has", "https://a.example/v1")
        assert gw.has_provider("prov-has") is True
        gw.remove_provider("prov-has")
        assert gw.has_provider("prov-has") is False


# ── 静态接线普查 ────────────────────────────────────────────────


class TestStaticWiring:
    def test_conflict_check_wired_before_registration_write(self):
        src = inspect.getsource(gland_api.add_provider)
        assert "has_provider" in src
        assert "409" in src
        # 判定先于落写（零副作用语义的结构性保证）
        assert src.index("has_provider") < src.index("gateway.add_provider")

    def test_engine_face_keeps_upsert_single_registration_write(self):
        src = inspect.getsource(ModelRouter.add_provider)
        # 引擎面唯一写点保持覆盖语义（无冲突拒/无清钥——边界锁死）
        assert "self.providers[name] = cfg" in src
        assert "remove_provider_keys" not in src
