"""H1-②b-⑫ KeyManager 有效性验证面——POST /api/gland/keys/{provider}/validate（全钥）
+ POST /api/gland/keys/{provider}/{index}/validate（单钥）。

隔离纪律（TL-②/H1-②b-④ 先例）：进程内 FastAPI 挂真实 gland router + monkeypatch 替换
gateway.key_manager 为全新 KeyManager 实例（内存面无落盘，teardown 自动还原）+
gateway.providers 置换还原，绝不打 live :8090、绝不碰生产 env 密钥；_ensure_bootstrapped
置 no-op。验证面真打 HTTP：真 socket 桩（http.server）收 /models 请求并回放状态码，
Authorization 头回读=真钥真送达（非 mock httpx）。
"""

import http.server
import socket
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.gland as gland_api
from src.gland.key_manager import KeyManager

# ── 真 socket 桩（/models 状态码可控 + 请求回读） ─────────────────


class _StubServer(http.server.ThreadingHTTPServer):
    """带请求记录/状态码开关的桩服务器（typed：消 Pyright 动态属性噪音）。"""

    def __init__(self, addr, handler):
        super().__init__(addr, handler)
        self.requests = []
        self.status_code = 200


class _StubHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        srv = getattr(self, "server")
        getattr(srv, "requests").append((self.path, self.headers.get("Authorization")))
        code = getattr(srv, "status_code", 200)
        body = b'{"data": []}'
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静音
        pass


@pytest.fixture()
def stub():
    srv = _StubServer(("127.0.0.1", 0), _StubHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def stub_url(stub):
    host, port = stub.server_address
    return f"http://{host}:{port}"


@pytest.fixture()
def dead_url():
    """connection refused 的死端口（异常路径用）。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


# ── 隔离 fixtures ───────────────────────────────────────────────


@pytest.fixture()
def km():
    fresh = KeyManager()
    original = gland_api.gateway.key_manager
    gland_api.gateway.key_manager = fresh
    yield fresh
    gland_api.gateway.key_manager = original


@pytest.fixture()
def providers():
    """gateway.providers 置换 + 还原（模块单例跨测试文件共享，绝不留测试 provider）。"""
    original = gland_api.gateway.providers
    gland_api.gateway.providers = {}
    yield gland_api.gateway
    gland_api.gateway.providers = original


@pytest.fixture()
def client(km, providers, monkeypatch):
    monkeypatch.setattr(gland_api, "_ensure_bootstrapped", lambda: None)
    app = FastAPI()
    app.include_router(gland_api.router, prefix="/api/gland")
    with TestClient(app) as c:
        yield c


# ── KeyManager 原语 ─────────────────────────────────────────────


class TestValidatePrimitive:
    def test_validate_all_pings_every_key(self, km, stub_url, stub):
        for i in range(3):
            km.add_key("prov-a", f"key-{i:02d}-111111")
        assert await_all(km.validate_all("prov-a", stub_url)) == [True, True, True]
        # 每钥都真打了 /models（Authorization 头逐钥送达）
        assert [h for _, h in stub.requests] == [
            "Bearer key-00-111111",
            "Bearer key-01-111111",
            "Bearer key-02-111111",
        ]
        rows = km.status()["prov-a"]
        assert all(r["last_checked"] > 0 for r in rows)
        assert all(r["is_valid"] for r in rows)

    def test_negative_index_miss_no_misaddress(self, km, stub_url, stub):
        """接线前定形收口：负 index 不得静默误验末钥（修前 slots[-1] 真验）。"""
        km.add_key("prov-b", "key-one-222222")
        km.add_key("prov-b", "key-two-333333")
        assert await_all(km.validate_key("prov-b", stub_url, -1)) is False
        # 末钥哨兵一字不动：没被误验
        last = km.status()["prov-b"][1]
        assert last["last_checked"] == 0.0
        assert last["fail_count"] == 0
        assert stub.requests == []

    @pytest.mark.parametrize("index", [2, 99])
    def test_out_of_range_false(self, km, stub_url, index):
        km.add_key("prov-c", "key-four-444444")
        assert await_all(km.validate_key("prov-c", stub_url, index)) is False

    def test_unknown_provider_false(self, km, stub_url):
        assert await_all(km.validate_key("prov-never", stub_url, 0)) is False

    def test_http_error_marks_invalid(self, km, stub_url, stub):
        stub.status_code = 401
        km.add_key("prov-d", "key-bad-555555")
        assert await_all(km.validate_key("prov-d", stub_url, 0)) is False
        row = km.status()["prov-d"][0]
        assert row["is_valid"] is False
        assert row["fail_count"] == 1

    def test_connection_failure_three_strikes(self, km, dead_url):
        km.add_key("prov-e", "key-dead-666666")
        assert await_all(km.validate_key("prov-e", dead_url, 0)) is False
        row = km.status()["prov-e"][0]
        assert row["fail_count"] == 1
        assert row["is_valid"] is True  # 1 次失败不摘牌（3 次门槛既有语义）
        await_all(km.validate_key("prov-e", dead_url, 0))
        await_all(km.validate_key("prov-e", dead_url, 0))
        row = km.status()["prov-e"][0]
        assert row["fail_count"] == 3
        assert row["is_valid"] is False  # 3 次后真摘牌


# ── API 全链 ────────────────────────────────────────────────────


class TestValidateFace:
    def test_single_validate_ok_shape(self, client, km, providers, stub_url, stub):
        km.add_key("prov-api", "api-key-aaaa-7777")
        providers.add_provider(name="prov-api", base_url=stub_url)
        resp = client.post("/api/gland/keys/prov-api/0/validate")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {
            "provider",
            "index",
            "ok",
            "masked_key",
            "is_valid",
            "fail_count",
            "last_checked",
        }
        assert data["ok"] is True
        assert data["is_valid"] is True
        assert data["index"] == 0
        assert data["masked_key"] == "api-...7777"
        # 真钥真送达（Authorization 头回读）
        assert stub.requests == [("/models", "Bearer api-key-aaaa-7777")]

    @pytest.mark.parametrize(
        "path",
        [
            "/api/gland/keys/prov-none/0/validate",
            "/api/gland/keys/prov-api/5/validate",
            "/api/gland/keys/prov-api/-1/validate",
        ],
    )
    def test_miss_404_zero_side_effect(self, client, km, providers, stub_url, stub, path):
        km.add_key("prov-api", "api-key-aaaa-7777")
        providers.add_provider(name="prov-api", base_url=stub_url)
        resp = client.post(path)
        assert resp.status_code == 404
        assert "Key not found" in resp.json()["detail"]
        # 404 零副作用：槽位一字不动 + 零网络触达
        row = km.status()["prov-api"][0]
        assert row["last_checked"] == 0.0
        assert row["fail_count"] == 0
        assert stub.requests == []

    def test_single_unregistered_provider_404(self, client, km, stub_url):
        """keys 在场但 provider 未注册=无 base_url 真源，如实 404 禁猜 URL 硬验。"""
        km.add_key("prov-orphan", "orphan-key-8888")
        resp = client.post("/api/gland/keys/prov-orphan/0/validate")
        assert resp.status_code == 404
        assert "base_url" in resp.json()["detail"]
        assert km.status()["prov-orphan"][0]["last_checked"] == 0.0

    def test_validate_all_shape(self, client, km, providers, stub_url):
        for i in range(3):
            km.add_key("prov-all", f"key-{i:02d}-999999")
        providers.add_provider(name="prov-all", base_url=stub_url)
        resp = client.post("/api/gland/keys/prov-all/validate")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"provider", "total", "valid", "results"}
        assert data["total"] == 3
        assert data["valid"] == 3
        assert [r["index"] for r in data["results"]] == [0, 1, 2]  # 槽位序=GET /keys 序
        for r in data["results"]:
            assert set(r.keys()) == {
                "index",
                "ok",
                "masked_key",
                "is_valid",
                "fail_count",
                "last_checked",
            }
            assert r["ok"] is True

    def test_validate_all_zero_keys_honest(self, client, km, providers, stub_url):
        providers.add_provider(name="prov-empty", base_url=stub_url)
        resp = client.post("/api/gland/keys/prov-empty/validate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["valid"] == 0
        assert data["results"] == []

    def test_validate_all_honest_failure(self, client, km, providers, stub_url, stub):
        stub.status_code = 401
        km.add_key("prov-bad", "bad-key-000001")
        km.add_key("prov-bad", "bad-key-000002")
        providers.add_provider(name="prov-bad", base_url=stub_url)
        data = client.post("/api/gland/keys/prov-bad/validate").json()
        assert data["total"] == 2
        assert data["valid"] == 0  # 不谎报 ok
        assert [r["ok"] for r in data["results"]] == [False, False]
        assert [r["is_valid"] for r in data["results"]] == [False, False]

    def test_validate_all_unregistered_404(self, client, km):
        km.add_key("prov-orphan", "orphan-key-8888")
        resp = client.post("/api/gland/keys/prov-orphan/validate")
        assert resp.status_code == 404
        assert "base_url" in resp.json()["detail"]

    def test_endpoints_delegate_not_reimplement(self, client, km, providers, stub_url, monkeypatch):
        """端点真委托 validate_key/validate_all（禁 API 层重实现第二套验证）。"""
        km.add_key("prov-spy", "spy-key-123456")
        providers.add_provider(name="prov-spy", base_url=stub_url)
        calls = []

        async def spy_one(provider, base_url, index=0):
            calls.append(("one", provider, base_url, index))
            return True

        async def spy_all(provider, base_url):
            calls.append(("all", provider, base_url))
            return [True]

        monkeypatch.setattr(km, "validate_key", spy_one)
        monkeypatch.setattr(km, "validate_all", spy_all)
        client.post("/api/gland/keys/prov-spy/0/validate")
        client.post("/api/gland/keys/prov-spy/validate")
        assert calls == [
            ("one", "prov-spy", stub_url, 0),
            ("all", "prov-spy", stub_url),
        ]

    def test_existing_faces_unchanged(self, client, km, providers, stub_url):
        """既有面零变化：GET /keys 行形状 + POST /keys 契约 + DELETE 移除面。"""
        km.add_key("prov-old", "old-key-424242")
        providers.add_provider(name="prov-old", base_url=stub_url)
        rows = client.get("/api/gland/keys").json()["prov-old"]
        assert set(rows[0].keys()) == {"masked_key", "is_valid", "fail_count", "last_checked"}
        added = client.post(
            "/api/gland/keys", json={"provider": "prov-old", "api_key": "second-key-42"}
        ).json()
        assert added == {"status": "ok", "masked": "seco...y-42"}
        removed = client.delete("/api/gland/keys/prov-old/0").json()
        assert removed == {"status": "ok", "provider": "prov-old", "index": 0, "remaining": 1}


# ── async 帮手（原语测试同步上下文直驱） ────────────────────────


def await_all(coro):
    import asyncio

    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)
