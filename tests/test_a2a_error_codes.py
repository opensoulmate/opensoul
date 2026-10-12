"""F4b-⑨ a2a JSON-RPC 错误码随 A2A v1.0 §5.4 映射 —— 修前全部错误兜底 -32603。

修前缺陷（F4b-⑧ 留档候选，live 修前实证 8/9 项错码）：handler 错误统一
raise ValueError→端点 except Exception 兜底 INTERNAL_ERROR(-32603)——
TaskNotFound（GetTask/tasks/get/SendMessage 续接）应 -32001、Method not found
应 -32601、缺 task id/非法 historyLength/params 校验失败应 -32602，全被吞成
-32603（客户端按规范码分流无从谈起）；且 §9 两种规范错被并成一种（Request
对象非法误报 -32700 JSONParseError）；cancel 不存在任务与已终态任务两型
（TaskNotFoundError / TaskNotCancelableError）合并成一个"cannot be canceled"。

收口（声明从实现派生，F4b-⑤ 同哲学）：raise 点带 §5.4 规范码（models.A2AError），
端点归口不猜码；-32700/-32600 按 §9 语义分流。
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.a2a import api as a2a_api
from src.a2a.models import A2AError, ErrorCode, Message
from src.a2a.task_manager import task_manager
from src.main import app

STUB = {"llm_mode": "stub"}


def _msg(text="hello errcode", **extra):
    m = {"role": "user", "parts": [{"type": "text", "text": text}]}
    m.update(extra)
    return m


def _rpc(client, method, params, rid="errcode"):
    resp = client.post(
        "/a2a", json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
    )
    assert resp.status_code == 200
    return resp.json()


def _code(data):
    assert "error" in data, f"必须是错误响应，实得 {data}"
    assert "result" not in data
    return data["error"]["code"]


@pytest.fixture()
def app_client():
    """In-process TestClient（不经 live 服务，monkeypatch 才能作用到同一进程模块）。"""
    with TestClient(app) as c:
        yield c


class TestSpecErrorTable:
    """ErrorCode 表逐值对照规范（声明恒等于规范，防私自造码/漏码）。"""

    def test_a2a_specific_codes_match_spec_5_4(self):
        # A2A v1.0 §5.4 Error Code Mappings —— A2A 专有码区间 -32001..-32099 全集
        spec = {
            "TASK_NOT_FOUND": -32001,
            "TASK_NOT_CANCELABLE": -32002,
            "PUSH_NOTIFICATION_NOT_SUPPORTED": -32003,
            "UNSUPPORTED_OPERATION": -32004,
            "CONTENT_TYPE_NOT_SUPPORTED": -32005,
            "INVALID_AGENT_RESPONSE": -32006,
            "EXTENDED_AGENT_CARD_NOT_CONFIGURED": -32007,
            "EXTENSION_SUPPORT_REQUIRED": -32008,
            "VERSION_NOT_SUPPORTED": -32009,
        }
        for name, value in spec.items():
            assert getattr(ErrorCode, name) == value, f"ErrorCode.{name} 与 §5.4 规范值漂移"

    def test_standard_jsonrpc_codes_match_spec_9(self):
        # §9 标准 JSON-RPC 码（JSONParseError/InvalidRequestError/…）
        spec = {
            "PARSE_ERROR": -32700,
            "INVALID_REQUEST": -32600,
            "METHOD_NOT_FOUND": -32601,
            "INVALID_PARAMS": -32602,
            "INTERNAL_ERROR": -32603,
        }
        for name, value in spec.items():
            assert getattr(ErrorCode, name) == value, f"ErrorCode.{name} 与 §9 规范值漂移"


class TestMethodNotFound:
    """§9 MethodNotFoundError = -32601（修前 -32603）。"""

    def test_unknown_method(self, app_client):
        data = _rpc(app_client, "no/such/method", {})
        assert _code(data) == ErrorCode.METHOD_NOT_FOUND
        assert "no/such/method" in data["error"]["message"]

    def test_03_era_name_still_method_not_found(self, app_client):
        """0.3 代名不在收口面（F4b-⑦ 边界），如实 -32601 而非冒充处理。"""
        data = _rpc(app_client, "message/send", {"message": _msg(), "metadata": STUB})
        assert _code(data) == ErrorCode.METHOD_NOT_FOUND

    def test_unimplemented_v10_method_is_method_not_found(self, app_client):
        """未实现的方法归 -32601（§9 文案 'does not exist or is not available'）。

        用例方法随契约演进更新：原用 GetExtendedAgentCard（F4b-⑫ 已实现）、
        SubscribeToTask（F4b-⑯ 真流式已实现）——"未实现→-32601"断言语义由真未知
        方法（非能力门名单）保持；方法面至此 §5.3 全实现，无"已声明未实现"残留。
        """
        data = _rpc(app_client, "no/such/method", {"id": "some-task"})
        assert _code(data) == ErrorCode.METHOD_NOT_FOUND


class TestTaskNotFound:
    """§5.4 TaskNotFoundError = -32001（修前 -32603）。"""

    def test_get_task_missing(self, app_client):
        data = _rpc(app_client, "GetTask", {"id": "no-such-task"})
        assert _code(data) == ErrorCode.TASK_NOT_FOUND
        assert "no-such-task" in data["error"]["message"]

    def test_tasks_get_missing_same_code_two_generations(self, app_client):
        """新旧两代方法同一语义同一码（GetTask 与 tasks/get 共 handler）。"""
        data = _rpc(app_client, "tasks/get", {"id": "no-such-task"})
        assert _code(data) == ErrorCode.TASK_NOT_FOUND

    def test_send_message_continuation_missing_task(self, app_client):
        """message.taskId 续接指向不存在任务：-32001，禁静默新建任务。"""
        data = _rpc(
            app_client,
            "SendMessage",
            {"message": _msg(taskId="no-such-task"), "metadata": STUB},
        )
        assert _code(data) == ErrorCode.TASK_NOT_FOUND

    def test_tasks_send_continuation_missing_task(self, app_client):
        """旧线名顶层 id 续接同样 -32001。"""
        data = _rpc(
            app_client,
            "tasks/send",
            {"id": "no-such-task", "message": _msg(), "metadata": STUB},
        )
        assert _code(data) == ErrorCode.TASK_NOT_FOUND


class TestTaskNotCancelable:
    """§5.4 TaskNotCancelableError = -32002；与 TaskNotFound 两型必须分开（修前合并）。"""

    def test_completed_task_cancel_is_not_cancelable(self, app_client):
        data = _rpc(app_client, "SendMessage", {"message": _msg(), "metadata": STUB})
        task_id = data["result"]["task"]["id"]
        assert data["result"]["task"]["status"]["state"] in (
            "TASK_STATE_COMPLETED",
            "TASK_STATE_FAILED",
        )
        cancel = _rpc(app_client, "CancelTask", {"id": task_id})
        assert _code(cancel) == ErrorCode.TASK_NOT_CANCELABLE

    def test_missing_task_is_task_not_found_not_not_cancelable(self, app_client):
        """修前 cancel 不存在任务回 'Task cannot be canceled'（两型合并）——现分开。"""
        data = _rpc(app_client, "CancelTask", {"id": "no-such-task"})
        assert _code(data) == ErrorCode.TASK_NOT_FOUND
        assert "Task not found" in data["error"]["message"]

    def test_cancelable_task_cancels_without_error(self, app_client):
        """回归：可取消任务照常取消（收口未破坏正常路径）。"""
        task = asyncio.run(task_manager.create_task(Message(**_msg()), metadata=STUB))
        data = _rpc(app_client, "CancelTask", {"id": task.id})
        assert "result" in data
        assert data["result"]["status"]["state"] == "TASK_STATE_CANCELED"


class TestInvalidParams:
    """§9 InvalidParamsError = -32602（修前 -32603）。"""

    def test_get_task_missing_id(self, app_client):
        data = _rpc(app_client, "GetTask", {})
        assert _code(data) == ErrorCode.INVALID_PARAMS

    def test_cancel_task_missing_id(self, app_client):
        data = _rpc(app_client, "CancelTask", {})
        assert _code(data) == ErrorCode.INVALID_PARAMS

    def test_send_message_missing_message(self, app_client):
        """params.message 缺失→Message 校验失败→-32602（修前 -32603）。"""
        data = _rpc(app_client, "SendMessage", {"metadata": STUB})
        assert _code(data) == ErrorCode.INVALID_PARAMS

    def test_message_missing_parts(self, app_client):
        data = _rpc(app_client, "SendMessage", {"message": {"role": "user"}, "metadata": STUB})
        assert _code(data) == ErrorCode.INVALID_PARAMS

    @pytest.mark.parametrize("bad", [-1, "2", 2.0, True])
    def test_invalid_history_length_code(self, app_client, bad):
        """非法 historyLength→API 面 -32602 且消息可见 historyLength（既有契约字面量锁死）。"""
        task = asyncio.run(task_manager.create_task(Message(**_msg()), metadata=STUB))
        data = _rpc(app_client, "GetTask", {"id": task.id, "historyLength": bad})
        assert _code(data) == ErrorCode.INVALID_PARAMS
        assert "historyLength" in data["error"]["message"]

    def test_a2a_error_is_value_error(self):
        """兼容契约：A2AError 继承 ValueError——既有 pytest.raises(ValueError) 锁死面不变。"""
        assert issubclass(A2AError, ValueError)
        with pytest.raises(ValueError, match="historyLength"):
            a2a_api._apply_history_limit({"history": []}, -1)


class TestParseVersusInvalidRequest:
    """§9 两种规范错分流（修前同走 -32700）：解析失败 vs Request 对象非法。"""

    def test_bad_json_is_parse_error(self, app_client):
        resp = app_client.post(
            "/a2a", content="not-json-at-all", headers={"Content-Type": "application/json"}
        )
        data = resp.json()
        assert _code(data) == ErrorCode.PARSE_ERROR

    def test_missing_method_is_invalid_request(self, app_client):
        resp = app_client.post("/a2a", json={"jsonrpc": "2.0", "id": "p8", "params": {}})
        data = resp.json()
        assert _code(data) == ErrorCode.INVALID_REQUEST

    def test_bad_id_type_is_invalid_request(self, app_client):
        resp = app_client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": {"nested": 1}, "method": "GetTask", "params": {}},
        )
        data = resp.json()
        assert _code(data) == ErrorCode.INVALID_REQUEST


class TestInternalFallback:
    """非协议异常仍如实 -32603（兜底保留，禁把未知故障伪装成协议错）。"""

    def test_runtime_error_stays_internal(self, app_client, monkeypatch):
        async def boom(params):
            raise RuntimeError("boom-internal")

        monkeypatch.setitem(a2a_api.RPC_HANDLERS, "GetTask", boom)
        data = _rpc(app_client, "GetTask", {"id": "x"})
        assert _code(data) == ErrorCode.INTERNAL_ERROR
        assert "boom-internal" in data["error"]["message"]


class TestEnvelope:
    """error 响应包络契约：id 回显、error 对象形状、HTTP 200。"""

    def test_error_echoes_request_id(self, app_client):
        data = _rpc(app_client, "GetTask", {"id": "no-such"}, rid="echo-id-42")
        assert data["id"] == "echo-id-42"
        assert data["jsonrpc"] == "2.0"

    def test_error_object_shape_no_data_key_when_absent(self, app_client):
        data = _rpc(app_client, "GetTask", {"id": "no-such"})
        assert set(data["error"].keys()) == {"code", "message"}
        assert isinstance(data["error"]["code"], int)
        assert data["error"]["message"]
