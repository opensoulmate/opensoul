"""F4b-⑥ a2a 任务历史/能力声明面 v1.0 对齐 —— stateTransitionHistory 复核收口 + historyLength 真执行。

修前缺陷（两处，均是 F4b-⑤ 留档"stateTransitionHistory 另拍可复核"的复核产出）：
①capabilities.stateTransitionHistory=true = 对外谎报 + 废弃协议字段：A2A v1.0
  §4.4.3 的 AgentCapabilities 只有 streaming/pushNotifications/extensions/
  extendedAgentCard——stateTransitionHistory 是 0.x 字段，因"协议自身无任何 API
  可取回状态迁移历史"被 a2aproject/A2A#1228 在 v1.0 移除；且本实现从未暴露状态
  迁移历史（Task.history 是消息历史非状态迁移历史），发布 true 让客户端以为可取
  状态迁移历史。
②tasks/get（及 tasks/send 响应）忽略 historyLength：v1.0 §3.1.3 "server MUST NOT
  return more messages than the provided value"+§3.2.4 语义（0=不返回且 history
  字段 SHOULD 被省略、>0=至多最近 N 条）未执行 = 协议违规（修前恒全量超发）。

收口思路（根因修）：①按"协议必须用最新标准版、拒绝废弃协议"移除 0.x 字段（非
bool 翻转——字段本身就不该出现在 v1.0 卡面）②historyLength 按 §3.2.4 语义真执行
（_apply_history_limit 纯函数，只改返回 dump 不裁剪任务本体真源）。
"""

import pytest
from fastapi.testclient import TestClient

from src.a2a import api as a2a_api
from src.a2a.models import DEFAULT_AGENT_CARD, AgentCapabilities
from src.main import app


@pytest.fixture()
def app_client():
    """In-process TestClient（不经 live 服务）。"""
    with TestClient(app) as c:
        yield c


def _send(client, text, *, task_id=None, extra_params=None):
    params = {
        "metadata": {"llm_mode": "stub"},
        "message": {"role": "user", "parts": [{"type": "text", "text": text}]},
    }
    if task_id:
        params["id"] = task_id
    if extra_params:
        params.update(extra_params)
    resp = client.post(
        "/a2a",
        json={"jsonrpc": "2.0", "id": "hist-send", "method": "tasks/send", "params": params},
    )
    return resp.json()


def _get(client, task_id, extra_params=None):
    params = {"id": task_id}
    if extra_params:
        params.update(extra_params)
    resp = client.post(
        "/a2a",
        json={"jsonrpc": "2.0", "id": "hist-get", "method": "tasks/get", "params": params},
    )
    return resp.json()


class TestStateTransitionHistoryDeclarationRemoved:
    """①0.x 废弃字段收口：v1.0 卡面禁出现 stateTransitionHistory。"""

    def test_card_has_no_state_transition_history(self, app_client):
        """发布卡面 capabilities 逐键 = v1.0 实现面，无 stateTransitionHistory（修前 True=谎报）。"""
        caps = app_client.get("/.well-known/agent.json").json()["capabilities"]
        assert "stateTransitionHistory" not in caps
        assert set(caps.keys()) == {"streaming", "pushNotifications", "extendedAgentCard"}

    def test_model_drops_deprecated_field(self):
        """AgentCapabilities 模型本身不再携带 0.x 废弃字段（禁复活）。"""
        assert "stateTransitionHistory" not in AgentCapabilities.model_fields
        assert set(AgentCapabilities.model_fields.keys()) == {
            "streaming",
            "pushNotifications",
            "extendedAgentCard",
        }

    def test_default_agent_card_roundtrip_honest(self):
        """DEFAULT_AGENT_CARD 序列化同样零废弃字段（发布口/默认卡同真源）。"""
        caps = DEFAULT_AGENT_CARD.model_dump()["capabilities"]
        assert "stateTransitionHistory" not in caps
        assert set(caps.keys()) == {"streaming", "pushNotifications", "extendedAgentCard"}

    def test_deprecated_kwarg_not_accepted_as_claim(self):
        """调用方带 stateTransitionHistory=True 也不能变出声明（字段被忽略，不进序列化）。"""
        caps = AgentCapabilities(**{"streaming": True, "stateTransitionHistory": True})
        dumped = caps.model_dump()
        assert "stateTransitionHistory" not in dumped
        assert dumped["streaming"] is True


class TestHistoryLimitHelper:
    """②_apply_history_limit 纯函数：§3.2.4 语义逐条（0=省略/大于0=最近N/未提供=不设限）。"""

    def _dump(self, n=3):
        return {"id": "t", "history": [f"m{i}" for i in range(n)]}

    def test_unset_no_limit(self):
        d = self._dump()
        assert a2a_api._apply_history_limit(dict(d), None)["history"] == ["m0", "m1", "m2"]

    def test_zero_omits_history_field(self):
        out = a2a_api._apply_history_limit(self._dump(), 0)
        assert "history" not in out

    def test_positive_takes_most_recent_n_in_order(self):
        out = a2a_api._apply_history_limit(self._dump(), 2)
        assert out["history"] == ["m1", "m2"]

    def test_limit_larger_than_history_returns_all(self):
        out = a2a_api._apply_history_limit(self._dump(2), 10)
        assert out["history"] == ["m0", "m1"]

    @pytest.mark.parametrize("bad", [-1, "2", 2.0, True, False])
    def test_invalid_values_rejected_not_silently_ignored(self, bad):
        """非法 historyLength 如实拒绝（禁静默当未提供超发全量）。"""
        with pytest.raises(ValueError, match="historyLength"):
            a2a_api._apply_history_limit(self._dump(), bad)

    def test_no_mutation_of_input_dump_history_source(self):
        """裁剪只作用于返回 dump：传入 dump 的 history 不被就地截断。"""
        d = self._dump()
        a2a_api._apply_history_limit(d, 1)
        assert d["history"] == ["m0", "m1", "m2"]


class TestHistoryLengthOnApi:
    """tasks/get 与 tasks/send 真 API 面：historyLength 真执行（修前恒全量=协议违规）。"""

    def test_tasks_get_unset_returns_full_history(self, app_client):
        r = _send(app_client, "hello one")
        task_id = r["result"]["id"]
        full = _get(app_client, task_id)["result"]["history"]
        assert len(full) >= 2  # 至少用户+agent 各一条
        assert [m["messageId"] for m in full] == [m["messageId"] for m in r["result"]["history"]]

    def test_tasks_get_zero_omits_history(self, app_client):
        task_id = _send(app_client, "hello zero")["result"]["id"]
        result = _get(app_client, task_id, {"historyLength": 0})["result"]
        assert "history" not in result
        assert result["id"] == task_id  # 其余字段照常

    def test_tasks_get_last_n_matches_full_suffix(self, app_client):
        task_id = _send(app_client, "hello n")["result"]["id"]
        full = _get(app_client, task_id)["result"]["history"]
        limited = _get(app_client, task_id, {"historyLength": 1})["result"]["history"]
        assert len(limited) == 1
        assert limited[0]["messageId"] == full[-1]["messageId"]

    def test_limit_does_not_mutate_stored_task(self, app_client):
        """限长只裁返回面：随后不带参数 get 仍全量（task_manager 真源未被污染）。"""
        task_id = _send(app_client, "hello store")["result"]["id"]
        full_len = len(_get(app_client, task_id)["result"]["history"])
        assert "history" not in _get(app_client, task_id, {"historyLength": 0})["result"]
        assert len(_get(app_client, task_id)["result"]["history"]) == full_len

    def test_tasks_send_configuration_history_length(self, app_client):
        """v1.0 SendMessageConfiguration.historyLength 真生效。"""
        r = _send(app_client, "hello cfg", extra_params={"configuration": {"historyLength": 1}})
        assert len(r["result"]["history"]) == 1

    def test_tasks_send_legacy_top_level_history_length(self, app_client):
        """旧草案顶层 historyLength 兜底同样生效。"""
        r = _send(app_client, "hello legacy", extra_params={"historyLength": 1})
        assert len(r["result"]["history"]) == 1

    def test_invalid_history_length_error_visible(self, app_client):
        """非法值走 error 可见（禁静默忽略后照常全量返回）。"""
        task_id = _send(app_client, "hello bad")["result"]["id"]
        data = _get(app_client, task_id, {"historyLength": -1})
        assert "error" in data
        assert "historyLength" in data["error"]["message"]
        assert "result" not in data
