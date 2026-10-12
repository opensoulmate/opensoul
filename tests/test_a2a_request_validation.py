"""F4b-㉙ a2a JSON-RPC 2.0 Request 对象校验面收口（§5 Request object）——
修前（F4b-⑩ 留档「jsonrpc 成员值不校验（"jsonrpc":"1.0" 仍被当合法），Request 校验面另拍」）：
①jsonrpc 成员缺失被静默补默认 "2.0"（fabricate 合法请求）、异值（"1.0"）/数字照收
②params 在场非结构化（标量/null）与 by-position 数组全部被并成 -32600 Invalid Request
——数组（JSON Array）按 §5 是合法 Request 形状（by-position），误判=Request 校验面漏检。

收口面（JSON-RPC 2.0 §5 Request object 成员逐项）：
- jsonrpc：MUST be a String exactly "2.0"（缺失/非字符串/异值 → -32600，id Null）
- method：MUST be a String（缺失/非字符串 → -32600，id Null）
- params：若在场 MUST be structured（Object by-name / Array by-position）；
  标量/null 在场 → -32600（Request 非法）；数组=合法 Request 但 A2A 方法参数
  恒 by-name 单对象（spec/a2a.proto）→ 方法层 -32602（与 -32600 分开禁混码）
- id：F4b-⑩ 已收（String/Number/Null 保真、bool 拒）——本拍零变化锁死
"""

import pytest
from fastapi.testclient import TestClient

from src.main import app


@pytest.fixture()
def app_client():
    """In-process TestClient（不经 live 服务）。"""
    with TestClient(app) as c:
        yield c


def _post(app_client, body):
    resp = app_client.post("/a2a", json=body)
    assert resp.status_code == 200
    return resp.json()


def _base(**over):
    body = {"jsonrpc": "2.0", "id": "rv-1", "method": "GetTask", "params": {"id": "no-such"}}
    body.update(over)
    return body


class TestJsonrpcMember:
    """§5：jsonrpc MUST be exactly "2.0"（字符串）。"""

    @pytest.mark.parametrize("bad", ["1.0", "2", "JSON-RPC 2.0", "", 2.0, 2, True, None])
    def test_wrong_jsonrpc_value_rejected(self, app_client, bad):
        data = _post(app_client, _base(jsonrpc=bad))
        assert data["error"]["code"] == -32600
        assert "jsonrpc" in data["error"]["message"]
        # §5：Invalid Request → id 无从检测，响应 id MUST be Null（带 id 请求也 Null）
        assert "id" in data and data["id"] is None

    def test_missing_jsonrpc_member_rejected(self, app_client):
        """修前：缺省静默补 "2.0" 当合法请求（fabricate）。"""
        body = {"id": "rv-2", "method": "GetTask", "params": {"id": "no-such"}}
        data = _post(app_client, body)
        assert data["error"]["code"] == -32600
        assert "jsonrpc" in data["error"]["message"]

    def test_valid_jsonrpc_dispatches_unchanged(self, app_client):
        """合法 "2.0" 路由零变化：GetTask 缺任务 → -32001（TaskNotFound），非 -32600。"""
        data = _post(app_client, _base(id="rv-3"))
        assert data["error"]["code"] == -32001
        assert data["id"] == "rv-3"

    def test_response_envelope_jsonrpc_is_2_0(self, app_client):
        data = _post(app_client, _base(jsonrpc="1.0", id="rv-4"))
        assert data["jsonrpc"] == "2.0"


class TestMethodMember:
    """§5：method MUST be a String。"""

    @pytest.mark.parametrize("bad", [123, 1.5, True, None, ["GetTask"]])
    def test_non_string_method_rejected(self, app_client, bad):
        data = _post(app_client, _base(method=bad))
        assert data["error"]["code"] == -32600
        assert "id" in data and data["id"] is None

    def test_missing_method_rejected(self, app_client):
        data = _post(app_client, {"jsonrpc": "2.0", "id": "rv-5", "params": {}})
        assert data["error"]["code"] == -32600


class TestParamsMember:
    """§5：params 若在场 MUST be structured（Array 或 Object）。"""

    @pytest.mark.parametrize("bad", ["x", 42, 1.5, True])
    def test_scalar_params_invalid_request(self, app_client, bad):
        data = _post(app_client, _base(params=bad))
        assert data["error"]["code"] == -32600
        assert "id" in data and data["id"] is None

    def test_explicit_null_params_invalid_request(self, app_client):
        """在场但 null=非结构化值（"MAY be omitted" ≠ "MAY be null"）→ Request 非法。"""
        data = _post(app_client, _base(params=None))
        assert data["error"]["code"] == -32600

    def test_array_params_is_valid_request_not_invalid_request(self, app_client):
        """by-position（Array）= §5 合法 Request 形状：不回 -32600（修前误判），
        A2A 方法参数恒 by-name → 方法层 -32602（混码收口）。"""
        data = _post(app_client, _base(params=[{"id": "x"}]))
        assert data["error"]["code"] != -32600
        assert data["error"]["code"] == -32602
        assert "by-name" in data["error"]["message"]
        assert data["id"] == "rv-1"  # 合法 Request → id 保真回显

    @pytest.mark.parametrize("method", ["GetTask", "tasks/get", "ListTasks", "NoSuch"])
    def test_array_params_rejected_for_every_dispatch_shape(self, app_client, method):
        """方法存在与否、两代方法名、只读方法——by-position 一律 -32602；
        未知方法仍 -32601（方法解析先于参数形态，Method not found 不被吞）。"""
        data = _post(app_client, _base(method=method, params=[1]))
        if method == "NoSuch":
            assert data["error"]["code"] == -32601
        else:
            assert data["error"]["code"] == -32602

    def test_object_params_dispatch_unchanged(self, app_client):
        data = _post(app_client, _base(id="rv-6"))
        assert data["error"]["code"] == -32001  # 真进 handler

    def test_params_omitted_dispatches_unchanged(self, app_client):
        """params 成员 MAY be omitted——缺省照常派发（ListTasks 无参可跑）。"""
        data = _post(app_client, {"jsonrpc": "2.0", "id": "rv-7", "method": "ListTasks"})
        assert "result" in data
        assert "error" not in data
        assert data["id"] == "rv-7"


class TestNotificationFace:
    """Notification（id 缺席）与 Request 校验面的交界（F4b-⑩ 语义延续）。"""

    def test_notification_with_bad_jsonrpc_replies_invalid_request(self, app_client):
        """jsonrpc 非法=不是合法 Request 对象→不是 Notification（Notification 定义=
        Request 对象去掉 id），照常回 -32600 id Null（修前当合法 Notification 204 执行）。"""
        resp = app_client.post("/a2a", json={"jsonrpc": "1.0", "method": "ListTasks", "params": {}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"]["code"] == -32600
        assert "id" in data and data["id"] is None

    def test_notification_valid_zero_change(self, app_client):
        """合法 Notification 照旧 204 静默（§5 MUST NOT reply）。"""
        resp = app_client.post("/a2a", json={"jsonrpc": "2.0", "method": "ListTasks", "params": {}})
        assert resp.status_code == 204


class TestEnvelopeUnchanged:
    """F4b-⑩ 包络语义零变化锁死。"""

    def test_invalid_request_error_envelope_shape(self, app_client):
        data = _post(app_client, _base(jsonrpc="1.0"))
        assert set(data.keys()) == {"jsonrpc", "id", "error"}
        assert set(data["error"].keys()) == {"code", "message"}
