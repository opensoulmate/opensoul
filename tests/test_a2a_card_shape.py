"""F4b-⑪ a2a 卡面形状 v1.0 对齐 —— supportedInterfaces 一等 + well-known 注册路径。

修前缺陷（F4b-⑩ 留档候选"卡面形状（supportedInterfaces、well-known agent-card.json）"）：
①卡面是 0.x 形状而 protocolVersion 声明 1.0.0：v1.0 卡面（a2aproject/A2A
  docs/whats-new-v1.md BREAKING）把 url/preferredTransport/additionalInterfaces/
  protocolVersion 收进 supportedInterfaces[]（每接口自带 url/protocolBinding/
  protocolVersion，Major.Minor 形如 "1.0"），顶层 url/protocolVersion 已从
  AgentCard 移除——继续吐出=卡片不过 v1.0 schema（官方 TCK CARD-EXT-001 同型违规）。
②well-known 注册路径漂移：v1.0 标准位是 /.well-known/agent-card.json（0.3 起推荐、
  v1.0 注册位），修前只发布 /.well-known/agent.json（0.x 老位）——按注册路径发现
  的 v1.0 客户端拿不到卡。

收口（根因修，声明从实现派生——F4b-⑤/envelope 同哲学）：AgentCard.published()
卡面唯一发布形状（0.x 顶层字段只进不出=accepted on input, never emitted，参考实现
同款；旧形自定义卡 fold 进 supportedInterfaces，接口 url=绑定真实所在 <base>/a2a，
spec MUST accurately declare）+双 well-known 路径同口同形（单真源禁两套卡）。
"""

import pytest
from fastapi.testclient import TestClient

from src.a2a.models import DEFAULT_AGENT_CARD, AgentCard, AgentInterface
from src.a2a.task_manager import task_manager
from src.main import app


@pytest.fixture()
def app_client():
    """In-process TestClient（不经 live 服务）。"""
    with TestClient(app) as c:
        yield c


class TestPublishedCardV10Shape:
    """published() 发布形状：v1.0 卡面 schema 逐键收口。"""

    def test_legacy_top_level_fields_never_emitted(self):
        """0.x 顶层 url/protocolVersion 只进不出（修前照吐=不过 v1.0 schema）。"""
        d = DEFAULT_AGENT_CARD.published()
        assert "url" not in d
        assert "protocolVersion" not in d

    def test_supported_interfaces_declares_real_rpc_endpoint(self):
        """接口行=绑定真实所在：本实现 JSON-RPC 端点恒在 <base>/a2a（api.py POST /a2a）。"""
        d = DEFAULT_AGENT_CARD.published()
        assert d["supportedInterfaces"] == [
            {
                "url": "http://localhost:8090/a2a",
                "protocolBinding": "JSONRPC",
                "protocolVersion": "1.0",
            }
        ]

    def test_published_key_set_is_v10_card_shape(self):
        """发布键集恰为 v1.0 卡面（无 0.x 顶层键、supportedInterfaces 一等）。"""
        d = DEFAULT_AGENT_CARD.published()
        assert set(d.keys()) == {
            "name",
            "description",
            "version",
            "capabilities",
            "skills",
            "defaultInputModes",
            "defaultOutputModes",
            "supportedInterfaces",
        }

    def test_model_still_parses_legacy_input_fields(self):
        """0.x 卡只进不出：模型仍收 url（输入兼容，参考实现同款），但不进发布形状。"""
        card = AgentCard(name="n", description="d", url="http://x:8090")
        assert card.model_dump()["url"] == "http://x:8090"
        pub = card.published()
        assert "url" not in pub and "protocolVersion" not in pub
        assert pub["supportedInterfaces"] == [
            {"url": "http://x:8090/a2a", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        ]


class TestFoldLegacyCustomCard:
    """旧形自定义卡（set_agent_card 只带 url）fold 语义逐条。"""

    def test_no_double_suffix_when_url_already_endpoint(self):
        card = AgentCard(name="n", description="d", url="http://x:8090/a2a")
        assert card.published()["supportedInterfaces"][0]["url"] == "http://x:8090/a2a"

    def test_trailing_slash_normalized(self):
        card = AgentCard(name="n", description="d", url="http://x:8090/")
        assert card.published()["supportedInterfaces"][0]["url"] == "http://x:8090/a2a"

    def test_explicit_interfaces_win_over_url_fold(self):
        """显式声明优先：禁 fold 覆盖真实声明。"""
        card = AgentCard(
            name="n",
            description="d",
            url="http://old:8090",
            supportedInterfaces=[AgentInterface(url="http://new:9000/rpc")],
        )
        assert card.published()["supportedInterfaces"] == [
            {"url": "http://new:9000/rpc", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        ]

    def test_empty_url_fabricates_no_interface(self):
        """url 为空不编造接口行（如实留空，禁 fabricate URL）。"""
        card = AgentCard(name="n", description="d")
        assert card.published()["supportedInterfaces"] == []


class TestWellKnownPaths:
    """双 well-known 路径同口同形（v1.0 注册路径为主口，旧路径=别名）。"""

    def test_v10_registered_path_exists(self, app_client):
        """v1.0 注册位 /.well-known/agent-card.json 真发布（修前 404=按注册路径发现拿不到卡）。"""
        resp = app_client.get("/.well-known/agent-card.json")
        assert resp.status_code == 200
        d = resp.json()
        assert d["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
        assert "url" not in d and "protocolVersion" not in d

    def test_both_paths_same_single_source(self, app_client):
        """两路径逐字节同形（published() 单真源，禁两套卡漂移）。"""
        new = app_client.get("/.well-known/agent-card.json").json()
        old = app_client.get("/.well-known/agent.json").json()
        assert new == old

    def test_capabilities_face_zero_regression(self, app_client):
        """F4b-⑤⑥ 能力声明面零回归：键集恰 {streaming,pushNotifications}、streaming 派生 False。"""
        caps = app_client.get("/.well-known/agent-card.json").json()["capabilities"]
        assert set(caps.keys()) == {"streaming", "pushNotifications"}
        assert caps["streaming"] is False

    def test_set_agent_card_custom_flows_through_fold(self, app_client):
        """自定义卡真流经发布口（含 fold）——发布形状不只对 DEFAULT 成立。"""
        old_card = task_manager.get_agent_card()
        try:
            task_manager.set_agent_card(
                AgentCard(name="custom-agent", description="d", url="http://custom:9090")
            )
            d = app_client.get("/.well-known/agent-card.json").json()
            assert d["name"] == "custom-agent"
            assert d["supportedInterfaces"][0]["url"] == "http://custom:9090/a2a"
            assert "url" not in d
        finally:
            task_manager.set_agent_card(old_card)
