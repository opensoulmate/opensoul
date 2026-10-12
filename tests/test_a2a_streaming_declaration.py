"""F4b-⑤ a2a streaming 声明对齐 —— capabilities.streaming 必须等于真实 RPC 流式方法面。

修前缺陷（F4 审计⑨ 对外谎报）：GET /.well-known/agent.json 发布
capabilities.streaming=true，而 JSON-RPC 仅 tasks/send|get|cancel，无
message/stream|tasks/sendSubscribe —— 客户端信声明调 message/stream 会拿
"Method not found"，能力声明与实现不符。

收口思路（根因修，非堵漏式 bool 翻转）：agent card 的 streaming 位从真实 RPC
方法面**派生**（单一源防漂移）——streaming_supported() 是唯一真值，实现里没有
流式方法就不对外宣称流式；未来真把 message/stream 加进 RPC_HANDLERS，声明自动
变 True，不必记得改两处（否则正是本次修的漂移 bug 会复发）。
"""

import pytest
from fastapi.testclient import TestClient

from src.a2a import api as a2a_api
from src.main import app


@pytest.fixture()
def app_client():
    """In-process TestClient（不经 live 服务，monkeypatch 才能作用到同一进程模块）。"""
    with TestClient(app) as c:
        yield c


class TestStreamingDeclarationHonesty:
    """声明诚实性：发布值恒等于真实 RPC 流式方法面。"""

    def test_streaming_true_after_streaming_methods_registered(self, app_client):
        """契约随 F4b-⑯ 更新：SendStreamingMessage/SubscribeToTask 真流式已实现，
        streaming 随注册派生 True（修前无流式方法=如实 False，F4b-⑤ 声明诚实性
        语义不变：发布值仍禁高于实现）。"""
        caps = app_client.get("/.well-known/agent.json").json()["capabilities"]
        assert caps["streaming"] is True

    def test_streaming_supported_reflects_real_surface(self):
        """streaming_supported() 是声明单一真值：流式两方法真注册=如实 True。"""
        assert a2a_api.streaming_supported() is True

    def test_streaming_matches_rpc_surface_invariant(self, app_client):
        """发布值恒等于 streaming_supported()（禁漂移不变量）。"""
        caps = app_client.get("/.well-known/agent.json").json()["capabilities"]
        assert caps["streaming"] == a2a_api.streaming_supported()


class TestStreamingDerivationDriftProof:
    """派生防漂移：加/删流式方法，声明自动跟随（根治"改实现忘改声明"）。"""

    def test_streaming_auto_true_when_streaming_method_registered(self, app_client, monkeypatch):
        """加 message/stream 进 RPC_HANDLERS → 声明自动 True（派生非硬编码）。"""
        monkeypatch.setitem(a2a_api.RPC_HANDLERS, "message/stream", lambda params: None)
        assert a2a_api.streaming_supported() is True
        caps = app_client.get("/.well-known/agent.json").json()["capabilities"]
        assert caps["streaming"] is True

    def test_tasks_send_subscribe_also_counts_then_removal_false(self, monkeypatch):
        """tasks/sendSubscribe（旧草案流式方法）同样算流式；**全部**流式方法移除后
        回 False（真值随实现面走，禁硬编码）。契约随 F4b-⑯ 更新：真流式两方法已
        注册，单独删旧线名不再回 False——须删净全部流式方法才 False。"""
        monkeypatch.setitem(a2a_api.RPC_HANDLERS, "tasks/sendSubscribe", lambda params: None)
        assert a2a_api.streaming_supported() is True
        monkeypatch.delitem(a2a_api.RPC_HANDLERS, "tasks/sendSubscribe")
        assert a2a_api.streaming_supported() is True  # 真流式方法仍在=不虚报
        monkeypatch.delitem(a2a_api.RPC_HANDLERS, "SendStreamingMessage")
        monkeypatch.delitem(a2a_api.RPC_HANDLERS, "SubscribeToTask")
        assert a2a_api.streaming_supported() is False

    def test_default_agent_card_no_hardcoded_streaming_claim(self):
        """DEFAULT_AGENT_CARD 自身不硬编码 streaming=True（修前 AgentCapabilities
        默认 True=内置谎言）。默认=不虚报，真值只由 streaming_supported() 派生。"""
        from src.a2a.models import DEFAULT_AGENT_CARD

        assert DEFAULT_AGENT_CARD.capabilities.streaming is False


class TestNonStreamingRoutingRegression:
    """handle_rpc 改用模块级 RPC_HANDLERS 后，既有非流式方法路由零回归。"""

    def test_tasks_send_still_routes(self, app_client):
        resp = app_client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "reg-1",
                "method": "tasks/send",
                "params": {
                    "metadata": {"llm_mode": "stub"},
                    "message": {"role": "user", "parts": [{"type": "text", "text": "hi"}]},
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data
        assert data["result"]["status"]["state"] in ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED")

    def test_unknown_method_still_rejected(self, app_client):
        resp = app_client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": "reg-2", "method": "nope/nope", "params": {}},
        )
        assert resp.status_code == 200
        assert "error" in resp.json()
