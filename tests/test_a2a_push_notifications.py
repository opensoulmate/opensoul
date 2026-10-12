"""F4b-⑮ a2a 推送通知配置四方法 v1.0 + 真 webhook 投递 + pushNotifications 声明派生。

修前缺口（F4b-⑭ 留档"SubscribeToTask·推送配置四方法留待拍"的推送半）：
①§5.3 推送配置四方法（CreateTaskPushNotificationConfig/GetTaskPushNotificationConfig/
  ListTaskPushNotificationConfigs/DeleteTaskPushNotificationConfig）全 Method not found，
  而卡面 protocolVersion 声明 1.0.0。
②capabilities.pushNotifications 硬编码 False（虽不虚报但无派生源，加实现会忘改=F4b-⑤
  漂移 bug 复发面）。
③§3.3.4 Capability Validation 违规：能力位 false 时流式/推送操作 MUST 回
  -32004/-32003，修前未实现方法恒 -32601（live 规范违规）。

收口（根因修，声明从实现派生——F4b-⑤/envelope 同哲学）：
①四方法 v1.0 名一等注册（无 0.x 旧线名——本实现此前从未有过推送面）：配置 CRUD
  （§3.1.7-3.1.10，含 §3.1.10 幂等删除、§3.1.8 缺配置复用 -32001 规范码）+ 分页
  （MAY 但真支持：pageSize 上限 + 不透明游标，坏游标如实 -32602）。
②真 webhook 投递（§4.3.3）：任务状态迁移=真实更新事件——working→{"statusUpdate"}
  单键 StreamResponse、终态→{"task"} 全量 wire；Authorization: {scheme} {credentials}；
  每更新每配置投一次（at-least-once），失败留痕 push_delivery_log 禁静默。
  SendMessageConfiguration.taskPushNotificationConfig 随发送注册（§3.1.7 双源：
  configuration 内字段优先、旧草案顶层 pushNotificationConfig 兜底，禁静默丢）。
③pushNotifications 由 push_notifications_supported() 派生（四方法全注册才 True）；
  handle_rpc 能力门：能力 False 时流式操作 -32004、推送操作 -32003（§3.3.4 逐条 MUST）。
"""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from src.a2a import api as a2a_api
from src.a2a.models import DEFAULT_AGENT_CARD, AgentCapabilities, TaskPushNotificationConfig
from src.a2a.task_manager import task_manager
from src.main import app

PUSH_METHODS = (
    "CreateTaskPushNotificationConfig",
    "GetTaskPushNotificationConfig",
    "ListTaskPushNotificationConfigs",
    "DeleteTaskPushNotificationConfig",
)


@pytest.fixture()
def app_client():
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
    """WAF 单例（src.immune.intrusion）测试隔离——坑记档：该检测器按 IP 有速率
    账本（200 req/min→RATE_ANOMALY）与自动封禁（5 threats→auto-block 后该 IP 每
    请求 CRITICAL 403）。TestClient 的 ip="testclient" 不在 localhost 白名单，密集
    测试突发跨阈值后毒化共享 IP 状态、403 掉后续用例（本家族实测被毒化 4 例=探针
    自伤非代码问题；生产 localhost 白名单恒放行不受影响）。逐测清账本=测试隔离
    （clean_tasks 同款纪律），杜绝一个文件的请求量毒化整个测试会话。"""
    from src.api.immune import intrusion

    intrusion._ip_suspicion.clear()
    intrusion._request_counts.clear()
    yield
    intrusion._ip_suspicion.clear()
    intrusion._request_counts.clear()


def rpc(client, method, params=None, rid="push-1"):
    resp = client.post(
        "/a2a",
        json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}},
    )
    assert resp.status_code == 200
    return resp.json()


def make_stub(status=200):
    """真 HTTP webhook 桩（独立线程 + 真 socket），记录每次 POST 的头与体。"""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            received.append(
                {"path": self.path, "headers": dict(self.headers), "body": json.loads(body)}
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):  # 静音
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}/hook", received
    finally:
        server.shutdown()
        server.server_close()


def closed_port_url():
    """拿到一个真关闭的端口（连接必拒）——投递失败路径。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}/hook"


def send_stub_task(client, extra_params=None, rid="push-send-1"):
    params = {
        "message": {"role": "user", "parts": [{"type": "text", "text": "hi"}]},
        "metadata": {"llm_mode": "stub"},
    }
    params.update(extra_params or {})
    return rpc(client, "SendMessage", params, rid=rid)["result"]["task"]


class TestCapabilityDerivation:
    """pushNotifications 声明从实现派生（禁硬编码 bool，F4b-⑤ 同哲学）。"""

    def test_push_capability_true_on_current_surface(self, app_client, clean_tasks):
        caps = app_client.get("/.well-known/agent-card.json").json()["capabilities"]
        assert caps["pushNotifications"] is True
        assert a2a_api.push_notifications_supported() is True
        # 守恒不变量：发布值恒等于派生值
        assert caps["pushNotifications"] is a2a_api.push_notifications_supported()

    def test_default_model_and_card_do_not_overclaim(self):
        assert AgentCapabilities().pushNotifications is False
        assert DEFAULT_AGENT_CARD.capabilities.pushNotifications is False

    def test_derivation_follows_registration(self, app_client, monkeypatch, clean_tasks):
        """防漂移：摘掉一个方法→能力自动 False→四操作（含仍在册的）全 -32003。"""
        monkeypatch.delitem(a2a_api.RPC_HANDLERS, "ListTaskPushNotificationConfigs")
        assert a2a_api.push_notifications_supported() is False
        caps = app_client.get("/.well-known/agent-card.json").json()["capabilities"]
        assert caps["pushNotifications"] is False
        task = send_stub_task(client=app_client)
        for method, params in (
            ("CreateTaskPushNotificationConfig", {"taskId": task["id"], "url": "http://x/h"}),
            ("GetTaskPushNotificationConfig", {"taskId": task["id"], "id": "c"}),
            ("ListTaskPushNotificationConfigs", {"taskId": task["id"]}),
            ("DeleteTaskPushNotificationConfig", {"taskId": task["id"], "id": "c"}),
        ):
            env = rpc(app_client, method, params)
            assert env["error"]["code"] == -32003, method  # §3.3.4 PushNotificationNotSupported

    def test_streaming_gate_when_capability_absent(self, app_client, monkeypatch, clean_tasks):
        """§3.3.4：streaming=false 时流式操作回 -32004（修前 -32601=live 规范违规）。

        契约随 F4b-⑯ 更新：真流式两方法已注册（能力派生 True，真实面不再走此门）——
        拆掉全部流式方法模拟能力缺席，验证门本身仍在位（未来拆方法时自动恢复拦截）。"""
        monkeypatch.delitem(a2a_api.RPC_HANDLERS, "SendStreamingMessage")
        monkeypatch.delitem(a2a_api.RPC_HANDLERS, "SubscribeToTask")
        assert a2a_api.streaming_supported() is False
        for method in (
            "SendStreamingMessage",
            "SubscribeToTask",
            "message/stream",
            "tasks/sendSubscribe",
        ):
            env = rpc(app_client, method, {"id": "t"})
            assert env["error"]["code"] == -32004, method
        env = rpc(app_client, "NoSuchMethod")
        assert env["error"]["code"] == -32601  # 未知方法不受能力门影响

    def test_streaming_gate_opens_when_registered(self, app_client, monkeypatch, clean_tasks):
        """真注册流式方法→能力自动 True→门放行（派生防漂移，不是 bool 堵漏）。"""

        async def _dummy(params):
            return {"subscribed": True}

        monkeypatch.setitem(a2a_api.RPC_HANDLERS, "SubscribeToTask", _dummy)
        assert a2a_api.streaming_supported() is True
        assert rpc(app_client, "SubscribeToTask", {"id": "t"})["result"] == {"subscribed": True}
        # 能力 True 后未注册的流式方法归 -32601（能力门不再拦截）
        assert rpc(app_client, "message/stream", {})["error"]["code"] == -32601


class TestPushConfigCrud:
    """§3.1.7-3.1.10 四操作语义。"""

    def test_create_assigns_id_and_roundtrip(self, app_client, clean_tasks):
        task = send_stub_task(app_client)
        created = rpc(
            app_client,
            "CreateTaskPushNotificationConfig",
            {"taskId": task["id"], "url": "http://hook/h", "token": "t-1"},
        )["result"]
        assert created["id"]  # §3.1.7 "with assigned ID"
        assert created["taskId"] == task["id"]
        assert created["url"] == "http://hook/h"
        got = rpc(
            app_client, "GetTaskPushNotificationConfig", {"taskId": task["id"], "id": created["id"]}
        )
        assert got["result"] == created
        listed = rpc(app_client, "ListTaskPushNotificationConfigs", {"taskId": task["id"]})
        assert listed["result"]["configs"] == [created]
        assert listed["result"]["nextPageToken"] == ""
        # 删除后 get 如实 -32001（§3.1.8 规范码），二次删除幂等成功
        assert (
            rpc(
                app_client,
                "DeleteTaskPushNotificationConfig",
                {"taskId": task["id"], "id": created["id"]},
            )["result"]
            == {}
        )
        err = rpc(
            app_client, "GetTaskPushNotificationConfig", {"taskId": task["id"], "id": created["id"]}
        )
        assert err["error"]["code"] == -32001
        assert "config" in err["error"]["message"].lower()  # 如实说缺配置，禁谎报缺任务
        assert (
            rpc(
                app_client,
                "DeleteTaskPushNotificationConfig",
                {"taskId": task["id"], "id": created["id"]},
            )["result"]
            == {}
        )

    def test_client_supplied_id_preserved(self, app_client, clean_tasks):
        task = send_stub_task(app_client)
        created = rpc(
            app_client,
            "CreateTaskPushNotificationConfig",
            {"taskId": task["id"], "id": "my-config-1", "url": "http://hook/h"},
        )["result"]
        assert created["id"] == "my-config-1"

    def test_wire_shape_exact_camel_case(self, app_client, clean_tasks):
        task = send_stub_task(app_client)
        full = rpc(
            app_client,
            "CreateTaskPushNotificationConfig",
            {
                "taskId": task["id"],
                "url": "http://hook/h",
                "token": "tok",
                "authentication": {"scheme": "Bearer", "credentials": "c-1"},
            },
        )["result"]
        assert set(full.keys()) == {"id", "taskId", "url", "token", "authentication"}
        assert set(full["authentication"].keys()) == {"scheme", "credentials"}
        minimal = rpc(
            app_client,
            "CreateTaskPushNotificationConfig",
            {"taskId": task["id"], "url": "http://hook/h2"},
        )["result"]
        assert set(minimal.keys()) == {"id", "taskId", "url"}  # 未设置成员整键省略

    def test_create_validation_errors(self, app_client, clean_tasks):
        task = send_stub_task(app_client)
        # url=REQUIRED 缺失 → -32602（pydantic ValidationError 端点归口）
        env = rpc(app_client, "CreateTaskPushNotificationConfig", {"taskId": task["id"]})
        assert env["error"]["code"] == -32602
        # taskId 缺失/非串 → -32602
        env = rpc(app_client, "CreateTaskPushNotificationConfig", {"url": "http://h"})
        assert env["error"]["code"] == -32602
        # 任务不存在 → §3.1.7 TaskNotFoundError(-32001)
        env = rpc(
            app_client, "CreateTaskPushNotificationConfig", {"taskId": "no-such", "url": "http://h"}
        )
        assert env["error"]["code"] == -32001

    def test_list_pagination_and_validation(self, app_client, clean_tasks):
        task = send_stub_task(app_client)
        made = [
            rpc(
                app_client,
                "CreateTaskPushNotificationConfig",
                {"taskId": task["id"], "url": f"http://h/{i}"},
            )["result"]
            for i in range(5)
        ]
        p1 = rpc(
            app_client, "ListTaskPushNotificationConfigs", {"taskId": task["id"], "pageSize": 2}
        )["result"]
        assert len(p1["configs"]) == 2 and p1["nextPageToken"]
        p2 = rpc(
            app_client,
            "ListTaskPushNotificationConfigs",
            {"taskId": task["id"], "pageSize": 2, "pageToken": p1["nextPageToken"]},
        )["result"]
        p3 = rpc(
            app_client,
            "ListTaskPushNotificationConfigs",
            {"taskId": task["id"], "pageSize": 2, "pageToken": p2["nextPageToken"]},
        )["result"]
        assert p3["nextPageToken"] == ""
        seen = [c["id"] for c in p1["configs"] + p2["configs"] + p3["configs"]]
        assert seen == [c["id"] for c in made]  # 无重无漏保登记序
        # 非法 pageSize 如实 -32602 禁静默当缺省
        for bad in (-1, 0, True, "2"):
            env = rpc(
                app_client,
                "ListTaskPushNotificationConfigs",
                {"taskId": task["id"], "pageSize": bad},
            )
            assert env["error"]["code"] == -32602, bad
        # 坏游标禁静默回第一页
        env = rpc(
            app_client,
            "ListTaskPushNotificationConfigs",
            {"taskId": task["id"], "pageToken": "!!!bad"},
        )
        assert env["error"]["code"] == -32602
        # 空列表形状
        task2 = send_stub_task(app_client, rid="push-send-2")
        empty = rpc(app_client, "ListTaskPushNotificationConfigs", {"taskId": task2["id"]})[
            "result"
        ]
        assert empty == {"configs": [], "nextPageToken": ""}
        # 任务不存在 → §3.1.9 -32001
        env = rpc(app_client, "ListTaskPushNotificationConfigs", {"taskId": "no-such"})
        assert env["error"]["code"] == -32001

    def test_delete_missing_task_and_get_missing_fields(self, app_client, clean_tasks):
        env = rpc(app_client, "DeleteTaskPushNotificationConfig", {"taskId": "no-such", "id": "c"})
        assert env["error"]["code"] == -32001
        env = rpc(app_client, "GetTaskPushNotificationConfig", {"taskId": "t"})
        assert env["error"]["code"] == -32602


class TestDelivery:
    """§4.3.3 真 webhook 投递（真 socket 桩非 mock）。"""

    def test_inline_config_delivers_status_update_and_terminal_task(self, app_client, clean_tasks):
        for _server, url, received in make_stub():
            task = send_stub_task(
                app_client,
                {
                    "configuration": {
                        "taskPushNotificationConfig": {
                            "url": url,
                            "token": "t-1",
                            "authentication": {"scheme": "Bearer", "credentials": "tok-1"},
                        }
                    }
                },
            )
            assert task["status"]["state"] == "TASK_STATE_COMPLETED"
            assert len(received) == 2  # working + completed 各投一次
            working, terminal = received[0]["body"], received[1]["body"]
            # §4.3.3 恰一键 StreamResponse
            assert set(working.keys()) == {"statusUpdate"}
            assert set(terminal.keys()) == {"task"}
            su = working["statusUpdate"]
            assert su["taskId"] == task["id"]
            assert su["contextId"] == task["contextId"]
            assert su["status"]["state"] == "TASK_STATE_WORKING"  # v1.0 枚举形
            assert terminal["task"]["id"] == task["id"]
            assert terminal["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
            # §4.3.3 Authorization: {scheme} {credentials}
            for req in received:
                assert req["headers"].get("Authorization") == "Bearer tok-1"
            assert all(e["ok"] for e in task_manager.push_delivery_log)

    def test_no_config_zero_delivery(self, app_client, clean_tasks):
        for _server, _url, received in make_stub():
            send_stub_task(app_client)
            assert received == []
            assert task_manager.push_delivery_log == []

    def test_delivery_failure_never_breaks_task(self, app_client, clean_tasks):
        task = send_stub_task(
            app_client,
            {"configuration": {"taskPushNotificationConfig": {"url": closed_port_url()}}},
        )
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"  # 任务不受投递失败累
        assert len(task_manager.push_delivery_log) == 2
        assert all(
            e["ok"] is False and e.get("error") for e in task_manager.push_delivery_log
        )  # 失败留痕禁静默

    def test_legacy_top_level_push_config_dual_source(self, app_client, clean_tasks):
        """旧草案 TaskSendParams 顶层 pushNotificationConfig 兜底，禁静默丢。"""
        for _server, url, received in make_stub():
            send_stub_task(app_client, {"pushNotificationConfig": {"url": url}}, rid="push-send-3")
            assert len(received) == 2

    def test_configuration_wins_over_top_level(self, app_client, clean_tasks):
        for _server, url, received in make_stub():
            task = send_stub_task(
                app_client,
                {
                    "configuration": {"taskPushNotificationConfig": {"url": url}},
                    "pushNotificationConfig": {"url": "http://should-not-win/h"},
                },
            )
            assert [e["url"] for e in task_manager.push_delivery_log] == [url, url]
            assert len(task_manager.push_configs[task["id"]]) == 1

    def test_cancel_pushes_terminal_task(self, app_client, clean_tasks):
        for _server, url, received in make_stub():
            from src.a2a.models import Message, Task, TaskStatus

            task_obj = Task(
                status=TaskStatus(state="submitted"),
                history=[Message(role="user", parts=[{"type": "text", "text": "x"}])],
            )
            task_manager.tasks[task_obj.id] = task_obj
            task_manager.create_push_config(task_obj.id, TaskPushNotificationConfig(url=url))
            env = rpc(app_client, "CancelTask", {"id": task_obj.id})
            assert env["result"]["status"]["state"] == "TASK_STATE_CANCELED"
            assert len(received) == 1
            assert set(received[0]["body"].keys()) == {"task"}
            assert received[0]["body"]["task"]["status"]["state"] == "TASK_STATE_CANCELED"
