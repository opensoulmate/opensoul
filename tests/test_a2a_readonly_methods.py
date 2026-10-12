"""F4b-⑫ a2a 只读方法面 v1.0 对齐 —— ListTasks + GetExtendedAgentCard。

修前缺口（F4b-⑪ 留档候选"未实现方法面 / extendedAgentCard 派生随方法面落地"）：
v1.0 §5.3 方法映射表里的 ListTasks 与 GetExtendedAgentCard 全是 Method not found，
而卡面 protocolVersion 声明 1.0.0 —— 客户端丢了任务 ID 就再也找不回来（无列表），
扩展卡能力位也缺失（AgentCapabilities 连 extendedAgentCard 字段都没有）。

收口（根因修，声明从实现派生——F4b-⑤/envelope 同哲学）：
①ListTasks（§3.1.4）：过滤（contextId/status/statusTimestampAfter）+ keyset 游标
  分页（spec 明文禁 offset）+ status.timestamp 降序（spec MUST）+ includeArtifacts
  缺省整键省略 artifacts（spec 明文：不得空数组/null）+ historyLength 逐任务收口。
②GetExtendedAgentCard（§3.1.11）+ capabilities.extendedAgentCard（§4.4.3）：能力位
  按"扩展卡真配置"派生（真能给才宣称），§3.3.4 两型错误分开（未支持 -32004 /
  宣称但未配置 -32007）。
"""

import pytest
from fastapi.testclient import TestClient

from src.a2a import api as a2a_api
from src.a2a.models import (
    DEFAULT_AGENT_CARD,
    AgentCard,
    Artifact,
    Message,
    Task,
    TaskStatus,
)
from src.a2a.task_manager import task_manager
from src.main import app


@pytest.fixture()
def app_client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def clean_tasks():
    """全局 TaskManager 隔离：任务面/扩展卡清空，事后逐字还原（禁污染既有用例）。

    扩展卡属性走 getattr/setattr（控制组=旧代码无该属性，fixture 须同样能跑，
    让失败落在行为断言上而非 fixture 崩溃）。
    """
    saved_tasks = dict(task_manager.tasks)
    saved_card = getattr(task_manager, "extended_agent_card", None)
    task_manager.tasks.clear()
    task_manager.extended_agent_card = None
    try:
        yield task_manager
    finally:
        task_manager.tasks.clear()
        task_manager.tasks.update(saved_tasks)
        task_manager.extended_agent_card = saved_card


def make_task(
    state="completed",
    ts="2026-10-12T10:00:00Z",
    context_id="ctx-1",
    text="hello",
    task_id=None,
):
    msg = Message(role="user", parts=[{"type": "text", "text": text}])
    task = Task(
        id=task_id or "",
        contextId=context_id,
        status=TaskStatus(state=state, timestamp=ts),
        history=[msg],
    )
    task_manager.tasks[task.id] = task
    return task


def rpc(client, method, params=None, rid="ro-1"):
    resp = client.post(
        "/a2a",
        json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}},
    )
    assert resp.status_code == 200
    return resp.json()


class TestMethodFace:
    """方法面注册：v1.0 正典名一等，未实现方法如实 -32601 禁冒充。"""

    def test_v10_readonly_names_registered(self):
        assert "ListTasks" in a2a_api.RPC_HANDLERS
        assert "GetExtendedAgentCard" in a2a_api.RPC_HANDLERS

    def test_03_era_alias_not_claimed(self):
        """本实现从未发布过 tasks/list / agent/getAuthenticatedExtendedCard 代名，
        不收（F4b-⑦ 纪律：两代并收=v1.0 正典+本实现历史旧线名）。"""
        assert "tasks/list" not in a2a_api.RPC_HANDLERS
        assert "agent/getAuthenticatedExtendedCard" not in a2a_api.RPC_HANDLERS

    def test_unimplemented_methods_stay_honest(self, app_client):
        """流式两方法仍未实现=禁注册空壳；错误面随 F4b-⑮ §3.3.4 能力门收口
        （能力位 false 时 -32004 而非 -32601）；推送配置四方法已实现（F4b-⑮）
        不在此列。"""
        for method in ("SendStreamingMessage", "SubscribeToTask"):
            assert method not in a2a_api.RPC_HANDLERS
            body = rpc(client=app_client, method=method, params={"id": "x"})
            assert body["error"]["code"] == -32004  # §3.3.4 UnsupportedOperation
        body = rpc(client=app_client, method="tasks/list", params={})
        assert body["error"]["code"] == -32601  # 真未知方法不受能力门影响


class TestListTasksShape:
    """ListTasksResponse 形状逐键（§3.1.4 Outputs 表）。"""

    def test_empty_store_response_key_set(self, app_client, clean_tasks):
        body = rpc(app_client, "ListTasks")
        result = body["result"]
        assert set(result.keys()) == {"tasks", "nextPageToken", "pageSize", "totalSize"}
        assert result["tasks"] == []
        # nextPageToken 恒在场（spec MUST always be present）；末页=空串非 null
        assert result["nextPageToken"] == ""
        assert result["pageSize"] == 50  # spec 缺省
        assert result["totalSize"] == 0

    def test_sorted_by_status_timestamp_desc(self, app_client, clean_tasks):
        oldest = make_task(ts="2026-10-12T10:00:00Z", task_id="t-oldest")
        newest = make_task(ts="2026-10-12T12:00:00Z", task_id="t-newest")
        mid = make_task(ts="2026-10-12T11:00:00Z", task_id="t-mid")
        ids = [t["id"] for t in rpc(app_client, "ListTasks")["result"]["tasks"]]
        assert ids == [newest.id, mid.id, oldest.id]

    def test_page_size_echo_is_effective_limit_not_row_count(self, app_client, clean_tasks):
        """pageSize=本响应用的页大小（生效上限），非实际条数（last page 只剩 1 条仍回 2）。"""
        for i in range(3):
            make_task(ts=f"2026-10-12T10:0{i}:00Z", task_id=f"t-{i}")
        result = rpc(app_client, "ListTasks", {"pageSize": 2})["result"]
        assert result["pageSize"] == 2
        second = rpc(app_client, "ListTasks", {"pageSize": 2, "pageToken": result["nextPageToken"]})
        assert len(second["result"]["tasks"]) == 1
        assert second["result"]["pageSize"] == 2


class TestListTasksFilters:
    """过滤面：contextId / status（双形）/ statusTimestampAfter（含边界）。"""

    def test_context_id_filter(self, app_client, clean_tasks):
        make_task(context_id="ctx-a", task_id="t-a")
        make_task(context_id="ctx-b", task_id="t-b")
        ids = [
            t["id"] for t in rpc(app_client, "ListTasks", {"contextId": "ctx-a"})["result"]["tasks"]
        ]
        assert ids == ["t-a"]

    def test_status_filter_accepts_both_enum_and_lowercase_forms(self, app_client, clean_tasks):
        """v1.0 JSON 形 TASK_STATE_* 与 0.x 小写形同收（序列化面迁移另拍，见诚实边界）。"""
        make_task(state="completed", task_id="t-done")
        make_task(state="working", task_id="t-doing")
        for value in ("completed", "TASK_STATE_COMPLETED"):
            ids = [
                t["id"] for t in rpc(app_client, "ListTasks", {"status": value})["result"]["tasks"]
            ]
            assert ids == ["t-done"], value
        ids = [
            t["id"]
            for t in rpc(app_client, "ListTasks", {"status": "TASK_STATE_WORKING"})["result"][
                "tasks"
            ]
        ]
        assert ids == ["t-doing"]

    def test_status_timestamp_after_inclusive(self, app_client, clean_tasks):
        """spec：大于等于该值才返回（边界相等必须命中）。"""
        make_task(ts="2026-10-12T10:00:00Z", task_id="t-early")
        make_task(ts="2026-10-12T11:00:00Z", task_id="t-exact")
        make_task(ts="2026-10-12T12:00:00Z", task_id="t-late")
        ids = [
            t["id"]
            for t in rpc(app_client, "ListTasks", {"statusTimestampAfter": "2026-10-12T11:00:00Z"})[
                "result"
            ]["tasks"]
        ]
        assert ids == ["t-late", "t-exact"]

    def test_invalid_status_rejected_not_silent_empty(self, app_client, clean_tasks):
        body = rpc(app_client, "ListTasks", {"status": "TASK_STATE_NOPE"})
        assert body["error"]["code"] == -32602
        assert "status" in body["error"]["message"]

    def test_invalid_status_timestamp_rejected(self, app_client, clean_tasks):
        body = rpc(app_client, "ListTasks", {"statusTimestampAfter": "not-a-time"})
        assert body["error"]["code"] == -32602
        assert "statusTimestampAfter" in body["error"]["message"]

    def test_non_string_status_rejected(self, app_client, clean_tasks):
        assert rpc(app_client, "ListTasks", {"status": 7})["error"]["code"] == -32602


class TestListTasksPagination:
    """keyset 游标分页（spec 明文禁 offset）。"""

    def test_cursor_pages_no_overlap_no_missing(self, app_client, clean_tasks):
        for i in range(3):
            make_task(ts=f"2026-10-12T10:0{i}:00Z", task_id=f"t-{i}")
        first = rpc(app_client, "ListTasks", {"pageSize": 2})["result"]
        assert [t["id"] for t in first["tasks"]] == ["t-2", "t-1"]
        assert first["nextPageToken"] != ""
        assert first["totalSize"] == 3  # totalSize=分页前总数
        second = rpc(app_client, "ListTasks", {"pageSize": 2, "pageToken": first["nextPageToken"]})[
            "result"
        ]
        assert [t["id"] for t in second["tasks"]] == ["t-0"]
        assert second["nextPageToken"] == ""  # 末页必须空串
        assert second["totalSize"] == 3
        got = [t["id"] for t in first["tasks"] + second["tasks"]]
        assert len(got) == len(set(got)) == 3

    def test_keyset_stable_under_concurrent_insert(self, app_client, clean_tasks):
        """keyset 稳定性：翻页途中新建的任务不挪动已翻过的项（offset 式会跳/重）。"""
        older = make_task(ts="2026-10-12T10:00:00Z", task_id="t-old")
        newer = make_task(ts="2026-10-12T11:00:00Z", task_id="t-new")
        first = rpc(app_client, "ListTasks", {"pageSize": 1})["result"]
        assert [t["id"] for t in first["tasks"]] == [newer.id]
        make_task(ts="2026-10-12T12:00:00Z", task_id="t-inserted")
        second = rpc(app_client, "ListTasks", {"pageSize": 1, "pageToken": first["nextPageToken"]})[
            "result"
        ]
        assert [t["id"] for t in second["tasks"]] == [older.id]

    def test_page_size_bounds_and_invalid(self, app_client, clean_tasks):
        make_task(task_id="t-1")
        assert rpc(app_client, "ListTasks", {"pageSize": 1})["result"]["pageSize"] == 1
        assert rpc(app_client, "ListTasks", {"pageSize": 100})["result"]["pageSize"] == 100
        for bad in (0, -1, 101, True, "2", 2.5):
            assert rpc(app_client, "ListTasks", {"pageSize": bad})["error"]["code"] == -32602, bad

    def test_invalid_page_token_rejected_not_silent_first_page(self, app_client, clean_tasks):
        """坏游标如实 -32602 禁静默回第一页（禁静默当未提供）。"""
        make_task(ts="2026-10-12T10:00:00Z", task_id="t-1")
        body = rpc(app_client, "ListTasks", {"pageToken": "not-a-cursor"})
        assert body["error"]["code"] == -32602
        assert "pageToken" in body["error"]["message"]

    def test_empty_page_token_is_first_page(self, app_client, clean_tasks):
        """空串=协议自身的"无下页"哨兵值，回显即第一页（与缺省同义，非坏游标）。"""
        make_task(ts="2026-10-12T10:00:00Z", task_id="t-1")
        result = rpc(app_client, "ListTasks", {"pageToken": ""})["result"]
        assert [t["id"] for t in result["tasks"]] == ["t-1"]


class TestListTasksWireShaping:
    """includeArtifacts / historyLength 的返回面收口。"""

    def test_artifacts_key_omitted_entirely_by_default(self, app_client, clean_tasks):
        """spec：缺省必须整键省略——不得以空数组或 null 出现（修前 model_dump 恒带）。"""
        task = make_task(task_id="t-art")
        task.artifacts.append(Artifact(parts=[{"type": "text", "text": "report"}]))
        dumped = rpc(app_client, "ListTasks")["result"]["tasks"][0]
        assert "artifacts" not in dumped

    def test_include_artifacts_true_returns_real_content(self, app_client, clean_tasks):
        task = make_task(task_id="t-art")
        task.artifacts.append(Artifact(parts=[{"type": "text", "text": "report"}]))
        dumped = rpc(app_client, "ListTasks", {"includeArtifacts": True})["result"]["tasks"][0]
        assert dumped["artifacts"][0]["parts"][0]["text"] == "report"

    def test_invalid_include_artifacts_rejected(self, app_client, clean_tasks):
        assert rpc(app_client, "ListTasks", {"includeArtifacts": "yes"})["error"]["code"] == -32602

    def test_history_length_zero_omits_history_key(self, app_client, clean_tasks):
        make_task(task_id="t-1")
        dumped = rpc(app_client, "ListTasks", {"historyLength": 0})["result"]["tasks"][0]
        assert "history" not in dumped

    def test_history_length_truncates_each_task(self, app_client, clean_tasks):
        task = make_task(task_id="t-1")
        task.history.append(Message(role="agent", parts=[{"type": "text", "text": "r1"}]))
        task.history.append(Message(role="user", parts=[{"type": "text", "text": "u2"}]))
        dumped = rpc(app_client, "ListTasks", {"historyLength": 2})["result"]["tasks"][0]
        assert len(dumped["history"]) == 2
        assert dumped["history"][-1]["parts"][0]["text"] == "u2"

    def test_history_length_unlimited_by_default(self, app_client, clean_tasks):
        task = make_task(task_id="t-1")
        task.history.append(Message(role="agent", parts=[{"type": "text", "text": "r1"}]))
        dumped = rpc(app_client, "ListTasks")["result"]["tasks"][0]
        assert len(dumped["history"]) == 2


class TestGetExtendedAgentCard:
    """§3.1.11 + §3.3.4 能力校验两型 MUST 逐条。"""

    def test_default_capability_false_and_call_refused(self, app_client, clean_tasks):
        caps = app_client.get("/.well-known/agent-card.json").json()["capabilities"]
        assert caps["extendedAgentCard"] is False
        body = rpc(app_client, "GetExtendedAgentCard")
        # §3.3.4：能力位 false → UnsupportedOperationError(-32004)
        assert body["error"]["code"] == -32004

    def test_configured_card_served_and_declaration_follows(self, app_client, clean_tasks):
        """配置扩展卡后能力位自动 True（声明从实现派生）+ 卡真返回。"""
        task_manager.set_extended_agent_card(
            AgentCard(
                name="OpenSoul Extended",
                description="认证后可见的扩展卡",
                url="http://localhost:8090",
            )
        )
        caps = app_client.get("/.well-known/agent-card.json").json()["capabilities"]
        assert caps["extendedAgentCard"] is True
        result = rpc(app_client, "GetExtendedAgentCard")["result"]
        assert result["name"] == "OpenSoul Extended"

    def test_extended_card_uses_published_v10_shape(self, app_client, clean_tasks):
        """扩展卡同走 published() 唯一发布形状（禁两套卡）：0.x 顶层键只进不出。"""
        task_manager.set_extended_agent_card(
            AgentCard(name="ext", description="d", url="http://localhost:8090")
        )
        result = rpc(app_client, "GetExtendedAgentCard")["result"]
        assert "url" not in result and "protocolVersion" not in result
        assert result["supportedInterfaces"][0]["url"] == "http://localhost:8090/a2a"
        assert set(result.keys()) == set(DEFAULT_AGENT_CARD.published().keys())

    def test_declared_but_unconfigured_returns_not_configured(
        self, app_client, clean_tasks, monkeypatch
    ):
        """§3.3.4 第二型 MUST：宣称支持但未配置 → -32007（配置在发卡后被清的竞态）。"""
        monkeypatch.setattr(task_manager, "has_extended_agent_card", lambda: True)
        assert task_manager.get_extended_agent_card() is None
        body = rpc(app_client, "GetExtendedAgentCard")
        assert body["error"]["code"] == -32007

    def test_clearing_card_flips_declaration_back(self, app_client, clean_tasks):
        task_manager.set_extended_agent_card(AgentCard(name="ext", description="d"))
        assert (
            app_client.get("/.well-known/agent-card.json").json()["capabilities"][
                "extendedAgentCard"
            ]
            is True
        )
        task_manager.set_extended_agent_card(None)
        assert (
            app_client.get("/.well-known/agent-card.json").json()["capabilities"][
                "extendedAgentCard"
            ]
            is False
        )


class TestZeroRegression:
    """既有方法面零回归。"""

    def test_send_message_and_get_task_contract_unchanged(self, app_client, clean_tasks):
        created = rpc(
            app_client,
            "SendMessage",
            {
                "message": {"role": "user", "parts": [{"type": "text", "text": "hi"}]},
                "metadata": {"llm_mode": "stub"},
            },
        )["result"]
        task_id = created["task"]["id"]
        got = rpc(app_client, "GetTask", {"id": task_id})["result"]
        assert got["id"] == task_id
        assert got["status"]["state"] == "TASK_STATE_COMPLETED"
        listed = rpc(app_client, "ListTasks")["result"]
        assert [t["id"] for t in listed["tasks"]] == [task_id]

    def test_streaming_and_push_capabilities_still_false(self, app_client, clean_tasks):
        caps = app_client.get("/.well-known/agent-card.json").json()["capabilities"]
        assert caps["streaming"] is False
        # 契约随 F4b-⑮ 更新：推送配置四方法已真注册，pushNotifications 派生 True
        # （真能做才宣称）；守恒不变量=发布值恒等于派生值（禁硬编码 bool）。
        assert caps["pushNotifications"] is a2a_api.push_notifications_supported()
        assert a2a_api.streaming_supported() is False
