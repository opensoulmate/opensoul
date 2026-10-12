"""F4b-⑦ a2a RPC 方法名 v1.0 对齐 —— protocolVersion 声明 1.0.0 就必须应答 v1.0 方法名。

修前缺陷（F4b-⑥ 留档候选）：agent card 声明 protocolVersion 1.0.0，而 JSON-RPC
方法面只有 0.2 草案旧线名 tasks/send|get|cancel —— v1.0 客户端按 spec §5.3 方法
映射表调 SendMessage/GetTask/CancelTask 全拿 Method not found = 方法面与协议版本
声明漂移。勘误（如实记）：F4b-⑥ 留档写"v1.0 规范名 message/send"，实为 0.3 代名；
v1.0 JSON-RPC 绑定（§9.1）是 PascalCase 方法名（SendMessage/GetTask/CancelTask）。

收口（Appendix A.2 重叠期纪律：服务端 MAY 接受旧形、新旧并收）：
- v1.0 正典名一等注册：SendMessage 返回 §9.4.1 SendMessageResponse 包裹体
  {"task": {...}}（oneof task|message，本实现恒 task 分支）；任务续接骑
  message.taskId（§6.3 多轮示例，v1.0 SendMessageRequest 无顶层 id）；
  GetTask/CancelTask 返回裸 Task（§3.1.3/§3.1.5）。
- 0.2/0.3 旧线名保留为别名，返回旧契约形状（裸 Task dump），既有客户端零破坏。
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.a2a import api as a2a_api
from src.a2a.models import Message
from src.a2a.task_manager import task_manager
from src.main import app

STUB = {"llm_mode": "stub"}


def _msg(text="hello v10", **extra):
    m = {"role": "user", "parts": [{"type": "text", "text": text}]}
    m.update(extra)
    return m


def _rpc(client, method, params, rid="v10"):
    resp = client.post(
        "/a2a",
        json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params},
    )
    assert resp.status_code == 200
    return resp.json()


@pytest.fixture()
def app_client():
    """In-process TestClient（不经 live 服务，monkeypatch 才能作用到同一进程模块）。"""
    with TestClient(app) as c:
        yield c


class TestV10MethodFace:
    """方法面单一源：v1.0 正典名与旧线名并收（Appendix A.2 重叠期）。"""

    def test_v10_names_registered(self):
        for name in ("SendMessage", "GetTask", "CancelTask"):
            assert name in a2a_api.RPC_HANDLERS, f"v1.0 方法名 {name} 未注册"

    def test_legacy_aliases_retained(self):
        """旧线名保留为重叠期别名（既有客户端/测试零破坏）。"""
        for name in ("tasks/send", "tasks/get", "tasks/cancel"):
            assert name in a2a_api.RPC_HANDLERS, f"旧线名 {name} 被删会破坏既有消费方"

    def test_send_message_versus_tasks_send_response_shape_is_distinct(self):
        """v1.0 SendMessage=包裹体、旧线名=裸 Task：两代形状各自正确，禁混用。"""
        assert a2a_api.RPC_HANDLERS["SendMessage"] is not a2a_api.RPC_HANDLERS["tasks/send"]
        assert a2a_api.RPC_HANDLERS["GetTask"] is a2a_api.RPC_HANDLERS["tasks/get"]
        assert a2a_api.RPC_HANDLERS["CancelTask"] is a2a_api.RPC_HANDLERS["tasks/cancel"]

    def test_03_era_name_not_claimed(self, app_client):
        """边界留档：0.3 中间代名（message/send）不在本拍收口面（v1.0 正典+旧线名
        兼容两代），如实 Method not found 而非静默当 v1.0 处理。若未来收 0.3 名，
        本例随契约更新。"""
        data = _rpc(app_client, "message/send", {"message": _msg(), "metadata": STUB})
        assert "error" in data


class TestV10SendMessageShape:
    """SendMessage 响应 = §9.4.1 SendMessageResponse（oneof task|message）。"""

    def test_result_is_send_message_response_wrapper(self, app_client):
        data = _rpc(app_client, "SendMessage", {"message": _msg(), "metadata": STUB})
        result = data["result"]
        assert set(result.keys()) == {"task"}, "SendMessage result 必须是 {task} 包裹体"
        task = result["task"]
        assert task["id"]
        assert task["status"]["state"] in ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED")
        assert "history" in task

    def test_wrapped_task_shape_matches_legacy_bare_dump(self, app_client):
        """同代执行体、两代包装：包裹内 Task dump 与旧线名裸 dump 键集逐键一致。"""
        v10 = _rpc(app_client, "SendMessage", {"message": _msg(), "metadata": STUB})
        legacy = _rpc(app_client, "tasks/send", {"message": _msg(), "metadata": STUB}, rid="legacy")
        wrapped = v10["result"]["task"]
        bare = legacy["result"]
        assert set(wrapped.keys()) == set(bare.keys())
        assert set(bare.keys()) != {"task"}, "旧线名必须保持裸 Task 契约（回归锁）"

    def test_history_length_via_configuration_under_v10_name(self, app_client):
        """F4b-⑥ historyLength 语义在 v1.0 名+包裹形状下同样真执行。"""
        data = _rpc(
            app_client,
            "SendMessage",
            {
                "message": _msg(),
                "metadata": STUB,
                "configuration": {"historyLength": 0},
            },
        )
        assert "history" not in data["result"]["task"]


class TestV10Continuation:
    """任务续接双源：v1.0 骑 message.taskId（§6.3）；旧线名顶层 id 照旧。"""

    def test_continuation_via_message_task_id(self, app_client):
        first = _rpc(app_client, "SendMessage", {"message": _msg(), "metadata": STUB})
        task_id = first["result"]["task"]["id"]
        n0 = len(first["result"]["task"].get("history", []))
        second = _rpc(
            app_client,
            "SendMessage",
            {"message": _msg("continue please", taskId=task_id), "metadata": STUB},
        )
        task = second["result"]["task"]
        assert task["id"] == task_id, "message.taskId 续接必须回同一任务而非新建"
        assert len(task.get("history", [])) > n0, "续接必须追加消息"
        assert task["history"][-1]["role"] == "ROLE_AGENT"

    def test_continuation_missing_task_fails_visible(self, app_client):
        """message.taskId 指向不存在任务=如实失败，禁静默新建任务。"""
        data = _rpc(
            app_client,
            "SendMessage",
            {"message": _msg(taskId="no-such-task-xyz"), "metadata": STUB},
        )
        assert "error" in data
        assert "result" not in data

    def test_legacy_top_level_id_still_continues(self, app_client):
        first = _rpc(app_client, "tasks/send", {"message": _msg(), "metadata": STUB})
        task_id = first["result"]["id"]
        second = _rpc(
            app_client,
            "tasks/send",
            {"id": task_id, "message": _msg("legacy continue"), "metadata": STUB},
        )
        assert second["result"]["id"] == task_id


class TestV10GetCancel:
    """GetTask/CancelTask：v1.0 名返回裸 Task（§3.1.3/§3.1.5）。"""

    def test_get_task_v10_name_bare_task(self, app_client):
        created = _rpc(app_client, "SendMessage", {"message": _msg(), "metadata": STUB})
        task_id = created["result"]["task"]["id"]
        data = _rpc(app_client, "GetTask", {"id": task_id})
        task = data["result"]
        assert task["id"] == task_id
        assert "status" in task
        assert "task" not in task, "GetTask 契约=裸 Task，不套 SendMessageResponse"

    def test_get_task_v10_history_length_zero(self, app_client):
        created = _rpc(app_client, "SendMessage", {"message": _msg(), "metadata": STUB})
        task_id = created["result"]["task"]["id"]
        data = _rpc(app_client, "GetTask", {"id": task_id, "historyLength": 0})
        assert "history" not in data["result"]

    def test_cancel_task_v10_name(self, app_client):
        task = asyncio.run(task_manager.create_task(Message(**_msg()), metadata=STUB))
        data = _rpc(app_client, "CancelTask", {"id": task.id})
        assert data["result"]["id"] == task.id
        assert data["result"]["status"]["state"] == "TASK_STATE_CANCELED"

    def test_v10_missing_id_errors_are_visible(self, app_client):
        for method in ("GetTask", "CancelTask"):
            data = _rpc(app_client, method, {}, rid=f"miss-{method}")
            assert "error" in data, f"{method} 缺 id 必须如实报错"


class TestStreamingDerivationV10Names:
    """流式派生名单覆盖 v1.0 方法名——防'按 v1.0 名实现流式却漏认'二次漂移。"""

    def test_v10_streaming_methods_count_as_streaming(self, app_client, monkeypatch):
        for name in ("SendStreamingMessage", "SubscribeToTask"):
            monkeypatch.setitem(a2a_api.RPC_HANDLERS, name, lambda params: None)
            assert a2a_api.streaming_supported() is True, f"{name} 未被流式派生名单认出"
            caps = app_client.get("/.well-known/agent.json").json()["capabilities"]
            assert caps["streaming"] is True
            monkeypatch.delitem(a2a_api.RPC_HANDLERS, name)
        assert a2a_api.streaming_supported() is False

    def test_streaming_true_on_real_surface(self, app_client):
        """真实面（真流式两方法已注册）如实 True——契约随 F4b-⑯ 更新：
        JSON-RPC-over-SSE 真流式落地（见 test_a2a_streaming_methods）。"""
        caps = app_client.get("/.well-known/agent.json").json()["capabilities"]
        assert caps["streaming"] is True
