"""F4b-⑬ a2a Task/Message 序列化 enum 形状 v1.0 对齐 —— wire 面枚举值单一源。

修前漂移（F4b-⑫ 留档候选）：Task.status.state 与 Message.role 以 0.x 小写形
（"working"/"user"）直接 model_dump 进 wire，而卡面声明 protocolVersion 1.0.0 ——
v1.0（proto-first/ProtoJSON，spec §5.5/§6.1）枚举值应是 SCREAMING_SNAKE_CASE
（TASK_STATE_*/ROLE_*）。全域序列化面漂移。

收口（声明从实现派生 + 单点出口，F4b-⑤/envelope 同哲学）：内部表示保留归一小写形，
Task.wire() 单点派生 v1.0 形，全响应面唯一出口；输入双形兼容（"user"/"ROLE_USER" 同收）。
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.a2a.models import Message, Task, TaskStatus
from src.main import app

STUB = {"llm_mode": "stub"}


def _msg(text="hello enum", role="user", **extra):
    m = {"role": role, "parts": [{"type": "text", "text": text}]}
    m.update(extra)
    return m


def _rpc(client, method, params, rid="enum"):
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


class TestWireEnumShape:
    """Task.wire() 纯函数：内部归一小写形 → v1.0 SCREAMING_SNAKE。"""

    def test_state_maps_to_v10(self):
        t = Task(status=TaskStatus(state="working"))
        assert t.wire()["status"]["state"] == "TASK_STATE_WORKING"

    def test_state_full_set(self):
        cases = {
            "submitted": "TASK_STATE_SUBMITTED",
            "working": "TASK_STATE_WORKING",
            "input-required": "TASK_STATE_INPUT_REQUIRED",
            "completed": "TASK_STATE_COMPLETED",
            "failed": "TASK_STATE_FAILED",
            "canceled": "TASK_STATE_CANCELED",
            "auth-required": "TASK_STATE_AUTH_REQUIRED",
            "rejected": "TASK_STATE_REJECTED",
        }
        for internal, wire in cases.items():
            t = Task(status=TaskStatus(state=internal))
            assert t.wire()["status"]["state"] == wire, internal

    def test_role_maps_to_v10(self):
        m = Message(role="user", parts=[{"type": "text", "text": "x"}])
        t = Task(status=TaskStatus(state="submitted"), history=[m])
        assert t.wire()["history"][0]["role"] == "ROLE_USER"

    def test_wire_does_not_mutate_internal(self):
        # 内部保留归一小写形（只改返回 dump 不污染真源，_apply_history_limit 同纪律）
        m = Message(role="agent", parts=[{"type": "text", "text": "x"}])
        t = Task(status=TaskStatus(state="completed", message=m), history=[m])
        w = t.wire()
        assert w["status"]["state"] == "TASK_STATE_COMPLETED"
        assert t.status.state == "completed", "wire 不得就地改内存 state"
        assert t.history[0].role == "agent", "wire 不得就地改内存 role"

    def test_status_message_role_also_wire(self):
        m = Message(role="agent", parts=[{"type": "text", "text": "r"}])
        t = Task(status=TaskStatus(state="completed", message=m))
        assert t.wire()["status"]["message"]["role"] == "ROLE_AGENT"

    def test_idempotent_no_double_prefix(self):
        t = Task(status=TaskStatus(state="working"))
        d = t.wire()
        d["status"]["state"] = "TASK_STATE_WORKING"  # 已是 wire 形
        # _state_to_wire 幂等：不再叠 TASK_STATE_TASK_STATE_...
        from src.a2a.models import _role_to_wire, _state_to_wire

        assert _state_to_wire("TASK_STATE_WORKING") == "TASK_STATE_WORKING"
        assert _role_to_wire("ROLE_USER") == "ROLE_USER"

    def test_unknown_value_not_fabricated(self):
        # off-spec 值不 fabricate 前缀——原样透传如实暴露
        from src.a2a.models import _role_to_wire, _state_to_wire

        assert _state_to_wire("bogus") == "bogus"
        assert _role_to_wire("system") == "system"


class TestDualShapeInput:
    """Message.role 输入双形兼容：v1.0 形 ROLE_USER 归一到内部 'user'。"""

    def test_v10_role_input_normalized(self):
        m = Message(role="ROLE_USER", parts=[{"type": "text", "text": "x"}])
        assert m.role == "user", "ROLE_USER 输入应归一到内部 user（LLM 角色映射依赖）"

    def test_v10_agent_role_input_normalized(self):
        m = Message(role="ROLE_AGENT", parts=[{"type": "text", "text": "x"}])
        assert m.role == "agent"

    def test_legacy_role_input_unchanged(self):
        m = Message(role="user", parts=[{"type": "text", "text": "x"}])
        assert m.role == "user"


class TestAPIEnumWire:
    """API 全链：v1.0 方法与旧线名响应枚举值均 v1.0 形（单点出口）。"""

    def test_send_message_state_is_v10(self, app_client):
        data = _rpc(
            app_client,
            "SendMessage",
            {"message": _msg(), "metadata": STUB},
        )
        task = data["result"]["task"]
        assert task["status"]["state"] in ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED")
        roles = [m["role"] for m in task["history"]]
        assert roles == ["ROLE_USER", "ROLE_AGENT"]

    def test_legacy_tasks_send_also_v10(self, app_client):
        data = _rpc(app_client, "tasks/send", {"message": _msg("legacy"), "metadata": STUB})
        task = data["result"]
        assert task["status"]["state"] in ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED")
        assert [m["role"] for m in task["history"]] == ["ROLE_USER", "ROLE_AGENT"]

    def test_v10_role_input_roundtrip(self, app_client):
        # 客户端发 ROLE_USER（v1.0 形）→ 历史回 ROLE_USER（不叠前缀不丢映射）
        data = _rpc(
            app_client,
            "SendMessage",
            {"message": _msg("hi", role="ROLE_USER"), "metadata": STUB},
        )
        task = data["result"]["task"]
        assert task["history"][0]["role"] == "ROLE_USER"
