"""G1-③b-2 备选方案 UI 数据面——/api/brain/creativity 载荷加性键 payload 归一契约。

admin-ui 备选方案展示层（admin-ui/components/creative-alternatives.tsx）消费归一
载荷（count/items/(error)），哨兵分类只在后端 alternatives_payload 单一源（前端
零哨兵分类禁第二套判法）。本文件锁：①加性键 payload 真归一（哨兵如实归 error 键
禁当真方案）②alternatives 原始键契约字节不变（既有消费面零破坏）③静态接线。

数据面全 tmp：LLM 上游=桩（monkeypatch CreativityEngine.generate_alternatives，
不打真 LLM）、不打 live :8090。
"""

import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.brain as brain_api
from src.cortex.creativity import CreativityEngine

_URL = "/api/brain/creativity"


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(brain_api.router, prefix="/api/brain")
    return TestClient(app)


_REAL_ITEMS = [
    {
        "approach": "灰度切换",
        "description": "逐步放量",
        "pros": ["风险低"],
        "cons": ["周期长"],
        "novelty": 2,
        "feasibility": 5,
    },
    {"approach": "影子写入", "description": "双写对比"},
]


def _patch_raw(monkeypatch, raw):
    async def _spy(self, problem, constraints=None, count=3):
        return raw

    monkeypatch.setattr(CreativityEngine, "generate_alternatives", _spy)


# ---------------------------------------------------------------- 载荷归一


def test_payload_key_normalized_real_items(client, monkeypatch):
    """真项：payload 加性键 count/items 归一，条目原样保留（不硬造字段）。"""
    _patch_raw(monkeypatch, list(_REAL_ITEMS))
    body = client.post(_URL, params={"problem": "p"}).json()
    payload = body["payload"]
    assert payload["count"] == 2
    assert payload["items"] == _REAL_ITEMS
    assert "error" not in payload


def test_sentinel_to_error_not_fake_item(client, monkeypatch):
    """哨兵「错误」如实归 error 键，禁当真方案进 items（fabricate 反证）。"""
    _patch_raw(monkeypatch, [{"approach": "错误", "description": "boom"}])
    body = client.post(_URL, params={"problem": "p"}).json()
    payload = body["payload"]
    assert payload["count"] == 0
    assert payload["items"] == []
    assert payload["error"] == "boom"
    assert all(it.get("approach") != "错误" for it in payload["items"])


def test_sentinel_no_llm_config(client, monkeypatch):
    """哨兵「无LLM配置」同判：error=真因，不当方案。"""
    _patch_raw(monkeypatch, [{"approach": "无LLM配置", "description": "需要配置LLM API"}])
    payload = client.post(_URL, params={"problem": "p"}).json()["payload"]
    assert payload["count"] == 0
    assert payload["error"] == "需要配置LLM API"


def test_non_list_garbage(client, monkeypatch):
    """产出形态非法如实归 error（不 fabricate 空成功）。"""
    _patch_raw(monkeypatch, {"not": "a list"})
    payload = client.post(_URL, params={"problem": "p"}).json()["payload"]
    assert payload["count"] == 0
    assert str(payload["error"]).startswith("产出形态非法")


# ---------------------------------------------------------------- 契约不变面


def test_raw_alternatives_key_unchanged(client, monkeypatch):
    """alternatives 原始键契约字节不变：哨兵原样保留（既有消费面零破坏）。"""
    raw = [{"approach": "错误", "description": "boom"}]
    _patch_raw(monkeypatch, raw)
    body = client.post(_URL, params={"problem": "p"}).json()
    assert body["alternatives"] == raw, "原始键必须原样透传，归一只进 payload"


def test_response_keys_exact(client, monkeypatch):
    """响应键集恰 {alternatives, payload}（加性键不夹带私货）。"""
    _patch_raw(monkeypatch, list(_REAL_ITEMS))
    body = client.post(_URL, params={"problem": "p"}).json()
    assert set(body.keys()) == {"alternatives", "payload"}


# ---------------------------------------------------------------- 静态接线


def test_static_wiring_payload_single_source():
    """端点真消费 alternatives_payload（归一单一源禁前端第二套判法）。"""
    src = inspect.getsource(brain_api.creativity)
    assert "alternatives_payload" in src
    assert "payload" in src
