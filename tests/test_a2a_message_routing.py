"""F4b-⑯d a2a 消息路由语义 v1.0 收口——终态任务拒收 + contextId 语义（§3.1.1/3.1.2/§3.4.2/§3.4.3）。

修前缺口（live 修前实证，非推断）：
① 终态任务照常续接（§3.1.1/§3.1.2 Errors 明文「Messages sent to Tasks that are
   in a terminal state (COMPLETED/FAILED/CANCELED/REJECTED) cannot accept further
   messages」→UnsupportedOperationError）——修前 SendMessage 带 message.taskId 续接
   已完成任务照常重跑，history 3→4 增长（live 实证）；a2a-python 参考实现
   (#215 "feat: raise error for tasks in terminal states") 同款语义。
② contextId/taskId 不匹配照常接受（§3.4.3「Agents MUST reject messages containing
   mismatching contextId and taskId」）——修前 totally-different-ctx 续接照常成功
   （live 实证）。
③ contextId 不采纳/不推断（§3.4.3 Context Continuity：无 taskId 带 contextId=在
   既有上下文开新任务、只给 taskId 时「Agents MUST infer contextId from the task」）
   ——修前建任务恒生成新 contextId、入史消息 contextId 恒空。

收口（task_manager.ensure_accepts_message 单一收口，SendMessage/SendStreamingMessage/
旧线名 tasks/send 共用路由缝）：存在性(-32001)→一致性(-32602)→推断→终态拒收(-32004)。
终态拒收后多轮正典=§6.3 input-required 续接 / §3.4.3 contextId 新任务续上下文。

真栈非 mock：in-process TestClient（src.main app 全路由）。
"""

import pytest
from fastapi.testclient import TestClient

from src.a2a.models import TaskStatus
from src.a2a.task_manager import task_manager
from src.main import app

STUB = {"llm_mode": "stub"}

TERMINAL_STATES = ("completed", "failed", "canceled", "rejected")


def _msg(text="hello routing", **extra):
    m = {"role": "ROLE_USER", "parts": [{"text": text}], "messageId": f"m-{text}"}
    m.update(extra)
    return m


def _rpc(client, method, params, rid="routing"):
    resp = client.post(
        "/a2a", json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
    )
    assert resp.status_code == 200
    return resp.json()


def _code(data):
    assert "error" in data, f"必须是错误响应，实得 {data}"
    assert "result" not in data
    return data["error"]["code"]


def _send(client, text="turn-1", rid="routing-new", **msg_extra):
    """SendMessage 新任务（stub 确定性应答），返回 result["task"]。"""
    data = _rpc(
        client,
        "SendMessage",
        {"message": _msg(text, **msg_extra), "metadata": dict(STUB)},
        rid=rid,
    )
    assert "result" in data, data
    return data["result"]["task"]


def _reopen(task_id, state="input-required"):
    """§6.3 多轮正典路径：任务须处于非终态才可续接——置回 input-required。"""
    task_manager.tasks[task_id].status = TaskStatus(state=state)


@pytest.fixture()
def app_client():
    """In-process TestClient（不经 live 服务）。"""
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def clean_tasks():
    """全局 TaskManager 隔离：任务/推送配置/投递留痕清空，事后逐字还原。"""
    saved_tasks = dict(task_manager.tasks)
    saved_cfgs = {k: dict(v) for k, v in task_manager.push_configs.items()}
    saved_log = list(task_manager.push_delivery_log)
    task_manager.tasks.clear()
    task_manager.push_configs.clear()
    task_manager.push_delivery_log.clear()
    try:
        yield task_manager
    finally:
        task_manager.tasks.clear()
        task_manager.tasks.update(saved_tasks)
        task_manager.push_configs.clear()
        task_manager.push_configs.update(saved_cfgs)
        task_manager.push_delivery_log.clear()
        task_manager.push_delivery_log.extend(saved_log)


@pytest.fixture(autouse=True)
def isolate_intrusion_state():
    """WAF 单例测试隔离（test_a2a_push_notifications 同款坑记档：密集突发毒化
    共享 IP 速率账本/自动封禁，逐测清账本）。"""
    from src.api.immune import intrusion

    intrusion._ip_suspicion.clear()
    intrusion._request_counts.clear()
    yield
    intrusion._ip_suspicion.clear()
    intrusion._request_counts.clear()


class TestTerminalTaskGate:
    """§3.1.1/§3.1.2 Errors：终态任务不再接受消息 → -32004（修前照常续接重跑）。"""

    @pytest.mark.parametrize("state", TERMINAL_STATES)
    def test_terminal_task_rejects_message(self, app_client, clean_tasks, state):
        task = _send(app_client, f"gate-{state}")
        if state != "completed":
            task_manager.tasks[task["id"]].status = TaskStatus(state=state)
        data = _rpc(
            app_client,
            "SendMessage",
            {
                "message": _msg("late turn", taskId=task["id"]),
                "metadata": dict(STUB),
            },
            rid="gate-1",
        )
        assert _code(data) == -32004
        assert "terminal state" in data["error"]["message"]
        # 拒收=零副作用：消息未入史（恰 [user, agent] 不变）
        assert [m.role for m in task_manager.tasks[task["id"]].history] == ["user", "agent"]

    def test_legacy_tasks_send_same_gate(self, app_client, clean_tasks):
        """旧线名 tasks/send 同一执行体同一门（修前照常续接）。"""
        task = _send(app_client, "gate-legacy")
        data = _rpc(
            app_client,
            "tasks/send",
            {"message": _msg("late", taskId=task["id"]), "metadata": dict(STUB)},
            rid="gate-2",
        )
        assert _code(data) == -32004

    def test_streaming_rejected_pre_stream(self, app_client, clean_tasks):
        """SendStreamingMessage 对终态任务：开流前如实 JSON-RPC 错误禁伪 SSE。"""
        task = _send(app_client, "gate-stream")
        resp = app_client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "gate-3",
                "method": "SendStreamingMessage",
                "params": {
                    "message": _msg("late", taskId=task["id"]),
                    "metadata": dict(STUB),
                },
            },
        )
        assert resp.status_code == 200
        assert not resp.headers["content-type"].startswith("text/event-stream")
        data = resp.json()
        assert _code(data) == -32004
        assert "terminal state" in data["error"]["message"]

    def test_nonterminal_continuation_still_accepted(self, app_client, clean_tasks):
        """正对照（§6.3 多轮正典）：input-required 续接照常，history 恰 [u,a,u,a]。"""
        task = _send(app_client, "cont-1")
        _reopen(task["id"])
        data = _rpc(
            app_client,
            "SendMessage",
            {
                "message": _msg("cont-2", taskId=task["id"]),
                "metadata": dict(STUB),
            },
            rid="gate-4",
        )
        assert "result" in data, data
        final = data["result"]["task"]
        assert final["id"] == task["id"]
        assert [m.role for m in task_manager.tasks[task["id"]].history] == [
            "user",
            "agent",
            "user",
            "agent",
        ]

    def test_read_paths_on_terminal_unaffected(self, app_client, clean_tasks):
        """终态只拒收消息，读面（GetTask/SubscribeToTask）照常（§3.1.3/§3.1.6）。"""
        task = _send(app_client, "read-1")
        got = _rpc(app_client, "GetTask", {"id": task["id"]}, rid="gate-5")
        assert got["result"]["status"]["state"] == "TASK_STATE_COMPLETED"
        resp = app_client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "gate-6",
                "method": "SubscribeToTask",
                "params": {"id": task["id"]},
            },
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")


class TestContextSemantics:
    """§3.4.2/§3.4.3 contextId 语义：一致性 MUST reject、推断 MUST infer、采纳。"""

    def test_mismatched_context_rejected(self, app_client, clean_tasks):
        """双给且不等 → -32602（修前静默接受越上下文续接，live 修前实证）。"""
        task = _send(app_client, "ctx-1")
        _reopen(task["id"])
        data = _rpc(
            app_client,
            "SendMessage",
            {
                "message": _msg(
                    "cross ctx",
                    taskId=task["id"],
                    contextId="totally-different-ctx",
                ),
                "metadata": dict(STUB),
            },
            rid="ctx-1",
        )
        assert _code(data) == -32602
        assert "contextId" in data["error"]["message"]
        # 拒收=零副作用：消息未入史
        assert [m.role for m in task_manager.tasks[task["id"]].history] == ["user", "agent"]

    def test_matching_context_accepted(self, app_client, clean_tasks):
        task = _send(app_client, "ctx-2")
        _reopen(task["id"])
        data = _rpc(
            app_client,
            "SendMessage",
            {
                "message": _msg("same ctx", taskId=task["id"], contextId=task["contextId"]),
                "metadata": dict(STUB),
            },
            rid="ctx-2",
        )
        assert "result" in data, data
        assert len(task_manager.tasks[task["id"]].history) == 4

    def test_context_inferred_on_continuation(self, app_client, clean_tasks):
        """只给 taskId → 入史消息 contextId=任务上下文（§3.4.3 MUST infer）。"""
        task = _send(app_client, "ctx-3")
        _reopen(task["id"])
        data = _rpc(
            app_client,
            "SendMessage",
            {"message": _msg("infer", taskId=task["id"]), "metadata": dict(STUB)},
            rid="ctx-3",
        )
        assert "result" in data, data
        hist = task_manager.tasks[task["id"]].history
        assert all(m.contextId == task["contextId"] for m in hist), [m.contextId for m in hist]

    def test_context_adopted_on_new_task(self, app_client, clean_tasks):
        """无 taskId 带 contextId=在既有上下文开新任务（§3.4.3）。"""
        task = _send(app_client, "ctx-4", contextId="ctx-fixed-42")
        assert task["contextId"] == "ctx-fixed-42"
        assert task_manager.tasks[task["id"]].history[0].contextId == "ctx-fixed-42"

    def test_generated_context_stamped_on_history(self, app_client, clean_tasks):
        """未带 contextId：生成新上下文并回填入史消息（同语义）。"""
        task = _send(app_client, "ctx-5")
        assert task["contextId"]
        assert task_manager.tasks[task["id"]].history[0].contextId == task["contextId"]

    def test_new_tasks_in_same_context_are_distinct(self, app_client, clean_tasks):
        """contextId 续上下文=同上下文新任务（任务 id 各自唯一）。"""
        t1 = _send(app_client, "ctx-6a", contextId="ctx-shared")
        t2 = _send(app_client, "ctx-6b", contextId="ctx-shared")
        assert t1["id"] != t2["id"]
        assert t1["contextId"] == t2["contextId"] == "ctx-shared"

    def test_not_found_with_context_still_32001(self, app_client, clean_tasks):
        """存在性先于一致性：指向不存在任务仍 -32001（既有契约零变化）。"""
        data = _rpc(
            app_client,
            "SendMessage",
            {
                "message": _msg("ghost", taskId="no-such-task", contextId="any-ctx"),
                "metadata": dict(STUB),
            },
            rid="ctx-7",
        )
        assert _code(data) == -32001
