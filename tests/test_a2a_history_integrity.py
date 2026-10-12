"""F4b-⑧ a2a 任务消息历史完整性 —— 新任务首条用户消息 history 双 append 收口。

修前缺陷（F4b-⑦ 留档候选）：_send_message_impl 新任务路径
create_task(message)（history=[message]，A2A §3.1.2 任务历史以用户消息开头）
+ process_task(task.id, message)（task.history.append(message)）——
同一首条消息入史两次。live 实证：新任务 hist=3（2 user + 1 agent），
两条 user 同 messageId = 消息历史重复记录；多轮上下文
（_handle_chat task.history[-10:]）也随之把首条消息喂两遍。

收口：create_task 入史首条消息（唯一入史点），run_processing 只产 agent
回复不重复入史 —— 每条消息恰入史一次（含失败路径）。
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.a2a.models import Message, TaskStatus
from src.a2a.task_manager import task_manager
from src.main import app

STUB = {"llm_mode": "stub"}


def _reopen(task_id):
    """§6.3 多轮正典：终态任务不再接受消息（F4b-⑯d 终态拒收）——续接前置回
    input-required（客户端继续 input-required 任务=规范多轮路径）。"""
    task_manager.tasks[task_id].status = TaskStatus(state="input-required")


def _msg(text="hello history", **extra):
    m = {"role": "user", "parts": [{"type": "text", "text": text}]}
    m.update(extra)
    return m


def _rpc(client, method, params, rid="hist"):
    resp = client.post(
        "/a2a",
        json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params},
    )
    assert resp.status_code == 200
    return resp.json()


@pytest.fixture()
def app_client():
    with TestClient(app) as c:
        yield c


def _ids(history):
    return [m["messageId"] for m in history]


class TestNewTaskHistoryOnce:
    """新任务首条用户消息恰入史一次（修前双 append=本类全部必炸）。"""

    def test_send_message_new_task_history_exactly_two(self, app_client):
        data = _rpc(app_client, "SendMessage", {"message": _msg(), "metadata": STUB})
        history = data["result"]["task"]["history"]
        assert [m["role"] for m in history] == ["ROLE_USER", "ROLE_AGENT"], (
            f"新任务历史应恰 [user, agent]，实得 {[m['role'] for m in history]}"
        )
        assert len(_ids(history)) == len(set(_ids(history))), "messageId 不得重复入史"

    def test_first_message_recorded_once_not_twice(self, app_client):
        """修前硬反证：首条 user 消息在 history 出现 2 次（双 append）。"""
        data = _rpc(
            app_client,
            "SendMessage",
            {"message": _msg("once only"), "metadata": STUB},
        )
        history = data["result"]["task"]["history"]
        texts = [p.get("text", "") for m in history if m["role"] == "ROLE_USER" for p in m["parts"]]
        assert texts == ["once only"], f"首条用户消息应恰入史一次，实得 {texts}"

    def test_legacy_tasks_send_same_integrity(self, app_client):
        """旧线名裸 Task 契约下同样恰入史一次（两代共用执行体）。"""
        data = _rpc(app_client, "tasks/send", {"message": _msg("legacy once"), "metadata": STUB})
        history = data["result"]["history"]
        assert [m["role"] for m in history] == ["ROLE_USER", "ROLE_AGENT"]
        assert len(_ids(history)) == len(set(_ids(history)))

    def test_agent_reply_recorded_once(self, app_client):
        data = _rpc(app_client, "SendMessage", {"message": _msg(), "metadata": STUB})
        history = data["result"]["task"]["history"]
        assert sum(1 for m in history if m["role"] == "ROLE_AGENT") == 1


class TestContinuationNoDuplicates:
    """续接每条消息恰入史一次，多轮历史无重复。"""

    def test_two_rounds_exactly_four_messages(self, app_client):
        first = _rpc(app_client, "SendMessage", {"message": _msg("round-1"), "metadata": STUB})
        task_id = first["result"]["task"]["id"]
        _reopen(task_id)  # 终态拒收（F4b-⑯d）：续接走 §6.3 input-required 路径
        second = _rpc(
            app_client,
            "SendMessage",
            {"message": _msg("round-2", taskId=task_id), "metadata": STUB},
        )
        history = second["result"]["task"]["history"]
        assert [m["role"] for m in history] == [
            "ROLE_USER",
            "ROLE_AGENT",
            "ROLE_USER",
            "ROLE_AGENT",
        ], f"两轮应恰 4 条 [u,a,u,a]，实得 {[m['role'] for m in history]}"
        assert len(_ids(history)) == len(set(_ids(history))), "多轮历史不得有重复 messageId"

    def test_three_rounds_texts_unique_in_order(self, app_client):
        first = _rpc(app_client, "SendMessage", {"message": _msg("t-1"), "metadata": STUB})
        task_id = first["result"]["task"]["id"]
        for i in (2, 3):
            _reopen(task_id)  # 终态拒收（F4b-⑯d）：续接走 §6.3 input-required 路径
            _rpc(
                app_client,
                "SendMessage",
                {"message": _msg(f"t-{i}", taskId=task_id), "metadata": STUB},
            )
        got = _rpc(app_client, "GetTask", {"id": task_id})
        history = got["result"]["history"]
        user_texts = [
            p.get("text", "") for m in history if m["role"] == "ROLE_USER" for p in m["parts"]
        ]
        assert user_texts == ["t-1", "t-2", "t-3"], f"用户消息按序恰各一次，实得 {user_texts}"

    def test_true_source_matches_response(self, app_client):
        """tasks/get 真源与 send 响应一致——修前真源也被双 append 污染。"""
        first = _rpc(app_client, "SendMessage", {"message": _msg(), "metadata": STUB})
        task_id = first["result"]["task"]["id"]
        sent_len = len(first["result"]["task"]["history"])
        got = _rpc(app_client, "GetTask", {"id": task_id})
        assert len(got["result"]["history"]) == sent_len == 2


class TestTaskManagerSemantics:
    """TaskManager 层语义：入史责任单一（create=首条 / process_task=续接）。"""

    def test_create_task_records_initial_message(self):
        """create_task 契约保留：任务历史以用户消息开头（A2A §3.1.2）。"""
        msg = Message(**_msg("initial"))
        task = asyncio.run(task_manager.create_task(msg, metadata=STUB))
        assert [m.role for m in task.history] == ["user"]
        assert task.history[0].messageId == msg.messageId

    def test_run_processing_does_not_record_message(self):
        """run_processing 不入史：message 入史责任在调用方（禁双 append）。"""
        msg = Message(**_msg("no-record"))
        task = asyncio.run(task_manager.create_task(msg, metadata=STUB))
        task = asyncio.run(task_manager.run_processing(task, msg))
        ids = [m.messageId for m in task.history]
        assert ids.count(msg.messageId) == 1, "run_processing 重复入史=双 append 复发"
        assert [m.role for m in task.history] == ["user", "agent"]

    def test_process_task_records_continuation_once(self):
        msg1 = Message(**_msg("first"))
        task = asyncio.run(task_manager.create_task(msg1, metadata=STUB))
        task = asyncio.run(task_manager.run_processing(task, msg1))  # 首轮处理（真实流程）
        msg2 = Message(**_msg("second"))
        task = asyncio.run(task_manager.process_task(task.id, msg2, metadata=STUB))
        ids = [m.messageId for m in task.history]
        assert ids.count(msg1.messageId) == 1
        assert ids.count(msg2.messageId) == 1
        assert [m.role for m in task.history] == ["user", "agent", "user", "agent"]

    def test_failure_path_no_duplicate_user_message(self, monkeypatch):
        """失败路径同样恰入史一次（错误进 status.message 不进 history）。"""

        async def _boom(text, task):
            raise RuntimeError("boom")

        monkeypatch.setattr(task_manager, "_process_message", _boom)
        msg = Message(**_msg("fail-once"))
        task = asyncio.run(task_manager.create_task(msg, metadata={}))
        task = asyncio.run(task_manager.run_processing(task, msg))
        ids = [m.messageId for m in task.history]
        assert ids.count(msg.messageId) == 1, "失败路径不得重复入史"
        assert task.status.state == "failed"
        assert [m.role for m in task.history] == ["user"]
