"""F4b-⑩ a2a JSON-RPC 2.0 包络语义（id/成员）收口 —— 修前错误响应 id=0（伪造成 0，
规范要求 Null）+ id=None/result=None 被 exclude_none 吞掉（id 键缺席、成功响应可
既无 result 也无 error）+ 显式 id:null / 数字 id 被拒或悄悄改写 + Notification（id
缺席）被当 Invalid Request 回错误响应（§5 MUST NOT reply to a Notification）。

收口面（JSON-RPC 2.0 §5）：
- id 无法检测（Parse error/Invalid Request）→ 响应 id MUST be Null（键在场值 null）
- id 能检测（String/Number/Null）→ 保真回显（值不变形：bool 不是 Number 拒之）
- 成员互斥：error 在场无 result；成功 result 恒在场（即使 None）
- id 缺席=Notification → 不响应（204 空体）
"""

import pytest
from fastapi.testclient import TestClient

from src.a2a import api as a2a_api
from src.a2a.models import ErrorCode, JSONRPCResponse
from src.main import app

STUB = {"llm_mode": "stub"}


def _msg(text="hello envelope", **extra):
    m = {"role": "user", "parts": [{"type": "text", "text": text}]}
    m.update(extra)
    return m


@pytest.fixture()
def app_client():
    """In-process TestClient（不经 live 服务）。"""
    with TestClient(app) as c:
        yield c


class TestUndetectableIdIsNull:
    """§5：id 无从检测（Parse error/Invalid Request）→ 响应 id MUST be Null。"""

    def test_parse_error_id_null_key_present(self, app_client):
        resp = app_client.post(
            "/a2a", content="not-json-at-all", headers={"Content-Type": "application/json"}
        )
        data = resp.json()
        assert data["error"]["code"] == ErrorCode.PARSE_ERROR
        # id 键必须在场且为 null（修前 id=0 伪造成 0；exclude_none 时代还会整键缺席）
        assert "id" in data
        assert data["id"] is None
        assert "result" not in data

    def test_invalid_request_id_null_even_when_id_detectable(self, app_client):
        # spec 字面：Invalid Request 错误响应 id MUST be Null（即使原请求带了 id）
        resp = app_client.post("/a2a", json={"jsonrpc": "2.0", "id": "p8", "params": {}})
        data = resp.json()
        assert data["error"]["code"] == ErrorCode.INVALID_REQUEST
        assert "id" in data
        assert data["id"] is None
        assert "result" not in data

    def test_bad_id_type_id_null(self, app_client):
        resp = app_client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": {"nested": 1}, "method": "GetTask", "params": {}},
        )
        data = resp.json()
        assert data["error"]["code"] == ErrorCode.INVALID_REQUEST
        assert data["id"] is None

    def test_bool_id_rejected_not_coerced(self, app_client):
        # bool 在 Python 是 int 子类：修前 True 被 lax 联合悄悄改写成 1（id 值被篡改）
        # 后请求被正常处理；JSON true/false 不是 Number（§5），现如实 Invalid Request。
        resp = app_client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": True, "method": "GetTask", "params": {"id": "x"}},
        )
        data = resp.json()
        assert data["error"]["code"] == ErrorCode.INVALID_REQUEST
        assert data["id"] is None

    def test_list_body_is_invalid_request(self, app_client):
        # batch 数组不支持（诚实边界）：不是 Request 对象→-32600，id 无从检测→Null
        resp = app_client.post(
            "/a2a", json=[{"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {}}]
        )
        data = resp.json()
        assert data["error"]["code"] == ErrorCode.INVALID_REQUEST
        assert data["id"] is None


class TestIdEchoFidelity:
    """§5：id 能检测→保真回显（MUST be the same value，不变形不改写）。"""

    @pytest.mark.parametrize("rid", ["echo-str", 7, 1.5])
    def test_error_response_echoes_id_verbatim(self, app_client, rid):
        resp = app_client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": rid, "method": "GetTask", "params": {"id": "no-such"}},
        )
        data = resp.json()
        assert data["error"]["code"] == ErrorCode.TASK_NOT_FOUND
        assert data["id"] == rid
        assert type(data["id"]) is type(rid)

    def test_success_response_echoes_id(self, app_client):
        resp = app_client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": 42,
                "method": "SendMessage",
                "params": {"message": _msg(), "metadata": STUB},
            },
        )
        data = resp.json()
        assert "error" not in data
        assert data["id"] == 42

    def test_explicit_null_id_processed_and_echoed_null(self, app_client):
        # 显式 id:null 合法（§5 MUST contain a String, Number, or NULL）——
        # 修前 JSONRPCRequest.id: str|int 拒收→被当 Invalid Request；现正常处理且回显 null。
        resp = app_client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": None,
                "method": "SendMessage",
                "params": {"message": _msg(), "metadata": STUB},
            },
        )
        data = resp.json()
        assert "error" not in data
        assert "id" in data
        assert data["id"] is None
        assert "result" in data and "task" in data["result"]


class TestMemberExclusivity:
    """§5 成员语义：error/result 互斥；成功 result 恒在场（REQUIRED）。"""

    def test_success_has_result_no_error(self, app_client):
        resp = app_client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "s1",
                "method": "SendMessage",
                "params": {"message": _msg(), "metadata": STUB},
            },
        )
        data = resp.json()
        assert set(data.keys()) == {"jsonrpc", "id", "result"}

    def test_error_has_error_no_result(self, app_client):
        resp = app_client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": "s2", "method": "GetTask", "params": {"id": "no-such"}},
        )
        data = resp.json()
        assert set(data.keys()) == {"jsonrpc", "id", "error"}
        assert set(data["error"].keys()) == {"code", "message"}

    def test_envelope_result_null_still_present(self):
        # 成功响应 result=None 也是 "result": null（REQUIRED on success）——
        # 修前 exclude_none 把它吞掉=既无 result 也无 error 的非法包络
        env = JSONRPCResponse(id="x", result=None).envelope()
        assert env == {"jsonrpc": "2.0", "id": "x", "result": None}

    def test_envelope_error_drops_result(self):
        env = JSONRPCResponse(id=None, error={"code": -32603, "message": "m"}).envelope()
        assert env == {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": "m"}}
        assert "result" not in env


class TestNotification:
    """§5：Notification（Request 对象不带 id 成员）→ MUST NOT reply，但照常执行。"""

    def test_notification_executed_but_not_replied(self, app_client, monkeypatch):
        # fire-and-forget 语义：方法真执行（spec：无兴趣的是 Response 不是执行），
        # 成功也不回（修前当 Invalid Request 回 -32600）
        calls = []

        async def recorder(params):
            calls.append(params)
            return {"ok": True}

        monkeypatch.setitem(a2a_api.RPC_HANDLERS, "SendMessage", recorder)
        resp = app_client.post(
            "/a2a", json={"jsonrpc": "2.0", "method": "SendMessage", "params": {"p": 1}}
        )
        assert resp.status_code == 204
        assert resp.content == b""
        assert calls == [{"p": 1}]  # 真执行了，只是结果被丢弃

    def test_notification_failure_also_silent(self, app_client, monkeypatch):
        # 失败也不回（同一条 MUST NOT reply）——静默是规范要求非瞒报

        async def boom(params):
            raise RuntimeError("notification-boom")

        monkeypatch.setitem(a2a_api.RPC_HANDLERS, "SendMessage", boom)
        resp = app_client.post(
            "/a2a", json={"jsonrpc": "2.0", "method": "SendMessage", "params": {}}
        )
        assert resp.status_code == 204
        assert resp.content == b""

    def test_unknown_method_notification_also_silent(self, app_client):
        # 未实现方法若是 Notification 同样禁回（Method not found 也只对有 id 的请求回）
        resp = app_client.post("/a2a", json={"jsonrpc": "2.0", "method": "NoSuch", "params": {}})
        assert resp.status_code == 204
        assert resp.content == b""

    def test_same_request_with_id_gets_reply(self, app_client):
        # 对照：同一请求带 id → 正常回错误响应（Notification 判别骑在 id 成员缺席，非方法面）
        resp = app_client.post(
            "/a2a", json={"jsonrpc": "2.0", "id": "n1", "method": "NoSuch", "params": {}}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"]["code"] == ErrorCode.METHOD_NOT_FOUND
        assert data["id"] == "n1"

    def test_invalid_request_shape_without_id_still_replies(self, app_client):
        # 非 Request 对象（缺 method）不是 Notification——如实回 -32600 且 id null
        resp = app_client.post("/a2a", json={"jsonrpc": "2.0", "params": {}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"]["code"] == ErrorCode.INVALID_REQUEST
        assert data["id"] is None
