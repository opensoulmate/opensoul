"""G1-① creativity.cross_domain_idea 死方法收口——补端点真消费。

修前（G0 审计⑤）：cross_domain_idea 全仓零调用零端点（wired-but-not-functional
死方法，D1/F4b-① 同型）——「跨领域联想」能力只存在于方法定义里，任何调用方
都到不了。收口：POST /api/brain/creativity/cross-domain 唯一真消费面（声明从
实现派生：方法真被调用才可达）。

数据面全 tmp：LLM 上游=桩（src.cortex.creativity.httpx 整体替换，不打真 LLM）、
不打 live :8090。
"""

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.brain as brain_api
import src.cortex.creativity as creativity_mod
from src.cortex.creativity import CreativityEngine

_URL = "/api/brain/creativity/cross-domain"


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(brain_api.router, prefix="/api/brain")
    return TestClient(app)


# ---------------------------------------------------------------- 端点真消费


def test_route_registered(client):
    """路由真注册（非 404）——方法可达面存在（openapi 声明面+真 POST 双证）。"""
    spec_paths = client.get("/openapi.json").json()["paths"]
    assert "/api/brain/creativity/cross-domain" in spec_paths
    assert "post" in spec_paths["/api/brain/creativity/cross-domain"]


def test_endpoint_truly_calls_method(client, monkeypatch):
    """端点真调用 cross_domain_idea（spy 直证）：domain/problem 逐字送达+结果透传。"""
    calls = []
    sentinel = {
        "ideas": [{"domain": "建筑", "approach": "拱结构", "analogy": "…"}],
        "best_idea": "拱结构",
    }

    async def _spy(self, domain, problem):
        calls.append((domain, problem))
        return dict(sentinel)

    monkeypatch.setattr(CreativityEngine, "cross_domain_idea", _spy)
    resp = client.post(_URL, params={"domain": "生物", "problem": "如何降低延迟"})
    assert resp.status_code == 200
    assert calls == [("生物", "如何降低延迟")], "端点必须真调用方法且入参逐字送达"
    body = resp.json()
    assert body["status"] == "ok"
    assert body["ideas"] == sentinel["ideas"]
    assert body["best_idea"] == "拱结构"


def test_success_response_keys_exact(client, monkeypatch):
    """成功响应键集恰 {status, ideas, best_idea}（契约面）。"""

    async def _spy(self, domain, problem):
        return {"ideas": [], "best_idea": "X"}

    monkeypatch.setattr(CreativityEngine, "cross_domain_idea", _spy)
    body = client.post(_URL, params={"domain": "d", "problem": "p"}).json()
    assert set(body.keys()) == {"status", "ideas", "best_idea"}


def test_error_not_lies_ok(client, monkeypatch):
    """方法层如实带回 error → 端点 status=error 禁谎报 ok（C2）。"""

    async def _spy(self, domain, problem):
        return {"ideas": [], "error": "boom"}

    monkeypatch.setattr(CreativityEngine, "cross_domain_idea", _spy)
    body = client.post(_URL, params={"domain": "d", "problem": "p"}).json()
    assert body == {"status": "error", "error": "boom", "ideas": []}


def test_missing_params_422(client):
    """domain/problem 必填：缺失=422 如实拒（禁空参硬跑 LLM fabricate）。"""
    resp = client.post(_URL)
    assert resp.status_code == 422
    resp2 = client.post(_URL, params={"domain": "d"})
    assert resp2.status_code == 422


def test_static_wiring_in_endpoint_body():
    """静态接线：端点函数体真引用 cross_domain_idea（防写了≠接线了）。"""
    src = inspect.getsource(brain_api.creativity_cross_domain)
    assert "cross_domain_idea" in src


# ------------------------------------------------------- 方法本体（桩 LLM 上游）


class _FakeResp:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _fake_httpx(resp=None, exc=None):
    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            if exc is not None:
                raise exc
            return resp

    return SimpleNamespace(AsyncClient=lambda: _FakeClient())


def test_method_parses_llm_json(monkeypatch):
    """方法本体真解析 LLM JSON（含 ``` 围栏剥壳）——功能面非空壳。"""
    raw = '{"ideas": [{"domain": "医学", "approach": "A", "analogy": "n"}], "best_idea": "A"}'
    monkeypatch.setattr(
        creativity_mod, "httpx", _fake_httpx(resp=_FakeResp(f"```json\n{raw}\n```"))
    )
    out = asyncio.run(CreativityEngine(api_key="k").cross_domain_idea("软件", "缓存失效"))
    assert out == {"ideas": [{"domain": "医学", "approach": "A", "analogy": "n"}], "best_idea": "A"}


def test_method_llm_failure_error_dict(monkeypatch):
    """方法本体故障如实带 error（禁静默空结果）。"""
    monkeypatch.setattr(creativity_mod, "httpx", _fake_httpx(exc=RuntimeError("conn refused")))
    out = asyncio.run(CreativityEngine(api_key="k").cross_domain_idea("软件", "缓存失效"))
    assert out["ideas"] == []
    assert "conn refused" in out["error"]
