"""F4b-㉔ a2a 取消语义——§6.3 技能参数追问回答=取消意图 → 任务 canceled。

修前缺口（F4b-㉑ 留档诚实边界）：pending 回答=参数 verbatim——「不用了」「算了」
这类取消回答被原样当查询参数丢给查询型 handler（搜索「不用了」fabricate 查询
结果），任务还标 completed。

收口：
- _is_cancel_intent 纯函数：闭集字面全等匹配（宁窄勿滥）——归一（去空白/首尾
  标点/句末语气词/小写）后全等命中才算取消；双形判定（直接归一形+剥寒暄填充
  词后归一形）。「如何取消订单」「停止按钮在哪」是真查询不是取消（containment
  会误杀，全等语义锁死）。
- §6.3 续接回答=取消意图 → TaskCancelled → run_processing 归口 cancel_by_user
  （唯一产生口）：取消确认以 agent 消息入史恰一次（F4b-⑧）+ state=canceled
  （终态：此后续接 -32004 拒收，§3.1.1/§3.1.2）；禁当查询参数、也不 completed
  （没干的活不标成功，F4b-⑲ fabricated completed 同型纪律）。

真栈非 mock：in-process TestClient（src.main app 全路由）+ handler 记录器
（monkeypatch 类方法=真路由决策打到真 dispatch，只把查询执行换成记录桩）。
"""

import pytest
from fastapi.testclient import TestClient

from src.a2a import task_manager as tm_mod
from src.a2a.task_manager import TaskManager, task_manager
from src.main import app

STUB = {"llm_mode": "stub"}
_PENDING_KEY = "_pending_skill_id"


# 容缺取符号（控制组旧代码无此符号时按 AttributeError 炸在行为断言上，而非
# 收集期 ImportError——控制组语义内建）。
def _missing(name: str):
    def _f(*args, **kwargs):
        raise AttributeError(f"old code has no {name}")

    return _f


_is_cancel_intent = getattr(tm_mod, "_is_cancel_intent", _missing("_is_cancel_intent"))
_cancel_norm = getattr(tm_mod, "_cancel_norm", _missing("_cancel_norm"))


def _msg(text="hello-cancel", **extra):
    m = {"role": "ROLE_USER", "parts": [{"text": text}], "messageId": f"cx-{text}"}
    m.update(extra)
    return m


def _rpc(client, method, params, rid="cancel-sem"):
    resp = client.post(
        "/a2a", json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
    )
    assert resp.status_code == 200
    return resp.json()


def _send(client, text, rid="cx-new", **msg_extra):
    data = _rpc(
        client,
        "SendMessage",
        {"message": _msg(text, **msg_extra), "metadata": dict(STUB)},
        rid=rid,
    )
    assert "result" in data, data
    return data["result"]["task"]


def _continue(client, task_id, text, rid="cx-cont"):
    data = _rpc(
        client,
        "SendMessage",
        {"message": _msg(text, taskId=task_id), "metadata": dict(STUB)},
        rid=rid,
    )
    return data


@pytest.fixture()
def app_client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def clean_tasks():
    """全局 TaskManager 隔离（含流订阅表），事后逐字还原。"""
    subs = getattr(task_manager, "_stream_subs", None)
    saved_tasks = dict(task_manager.tasks)
    saved_cfgs = {k: dict(v) for k, v in task_manager.push_configs.items()}
    saved_log = list(task_manager.push_delivery_log)
    saved_subs = {k: list(v) for k, v in subs.items()} if subs is not None else {}
    task_manager.tasks.clear()
    task_manager.push_configs.clear()
    task_manager.push_delivery_log.clear()
    if subs is not None:
        subs.clear()
    try:
        yield task_manager
    finally:
        task_manager.tasks.clear()
        task_manager.tasks.update(saved_tasks)
        task_manager.push_configs.clear()
        task_manager.push_configs.update(saved_cfgs)
        task_manager.push_delivery_log.clear()
        task_manager.push_delivery_log.extend(saved_log)
        if subs is not None:
            subs.clear()
            subs.update(saved_subs)


@pytest.fixture(autouse=True)
def isolate_intrusion_state():
    """WAF 单例测试隔离（test_a2a_push_notifications 坑记档：密集突发毒化共享 IP）。"""
    from src.api.immune import intrusion

    intrusion._ip_suspicion.clear()
    intrusion._request_counts.clear()
    yield
    intrusion._ip_suspicion.clear()
    intrusion._request_counts.clear()


def _record_handler(monkeypatch, method_name: str, marker: str):
    """把 handler 换成记录桩：真路由决策打到真 dispatch，记录 (调用文本) 列表。"""
    calls: list = []

    async def _fake(self, text, task):
        calls.append(text)
        return marker

    monkeypatch.setattr(TaskManager, method_name, _fake)
    return calls


class TestCancelIntent:
    """取消意图纯函数：闭集全等匹配（宁窄勿滥）。"""

    def test_plain_cancel_phrases(self):
        for text in ("不用了", "算了", "取消", "不要了", "不需要", "不必了", "停止"):
            assert _is_cancel_intent(text), text

    def test_punct_and_particle_forms(self):
        for text in ("不用了，谢谢", "取消吧", "算了啊", "取消！", " 不用了 。"):
            assert _is_cancel_intent(text), text

    def test_polite_stripped_forms(self):
        # 剥礼貌寒暄后归一形命中（帮我/一下/谢谢/请=礼貌填充词）
        for text in ("帮我取消一下", "不用了，谢谢", "请取消", "别搜了", "别查了"):
            assert _is_cancel_intent(text), text

    def test_case_insensitive_english(self):
        assert _is_cancel_intent("CANCEL")
        assert _is_cancel_intent("Stop!")

    def test_real_query_containing_cancel_word_is_not_cancel(self):
        # 全等语义锁死：containment 会误杀这些真查询
        for text in ("如何取消订单", "取消订单流程", "停止按钮在哪", "不用管我"):
            assert not _is_cancel_intent(text), text

    def test_substance_not_cancel(self):
        for text in ("森林防火", "怎么取消", "搜索取消", "帮我查一下取消", ""):
            assert not _is_cancel_intent(text), text

    def test_norm_shape(self):
        # 归一形：去空白/首尾标点/句末语气词/小写（集合存归一形）
        assert _cancel_norm("取消吧") == "取消"
        assert _cancel_norm(" 不 用 了 ") == "不用了"
        assert _cancel_norm("CANCEL") == "cancel"


class TestPendingAnswerCancel:
    """§6.3 续接回答=取消意图 → canceled（修前 verbatim 当查询参数）。"""

    def test_cancel_answer_cancels_task(self, app_client, clean_tasks, monkeypatch):
        calls = _record_handler(monkeypatch, "_handle_search", "SEARCH-MARKER")
        task = _send(app_client, "搜索")
        assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        data = _continue(app_client, task["id"], "不用了")
        assert "result" in data, data
        cont = data["result"]["task"]
        assert cont["status"]["state"] == "TASK_STATE_CANCELED"
        assert calls == []  # 硬反证：handler 零调用=没有 fabricated 查询（修前搜「不用了」）
        assert _PENDING_KEY not in cont["metadata"]  # pending 随任务收口即清

    def test_cancel_ack_recorded_exactly_once(self, app_client, clean_tasks, monkeypatch):
        _record_handler(monkeypatch, "_handle_search", "SEARCH-MARKER")
        task = _send(app_client, "搜索")
        cont = _continue(app_client, task["id"], "算了")["result"]["task"]
        roles = [m["role"] for m in cont["history"]]
        assert roles == ["ROLE_USER", "ROLE_AGENT", "ROLE_USER", "ROLE_AGENT"]  # 不丢不重
        ack = cont["history"][-1]
        assert "取消" in ack["parts"][0]["text"]  # 取消确认是真回复
        # status.message 指同一消息（F4b-⑧ 禁双入史）
        assert cont["status"]["message"]["parts"][0]["text"] == ack["parts"][0]["text"]
        assert len(cont["history"]) == 4

    @pytest.mark.parametrize(
        "answer", ["不用了", "算了", "取消吧", "帮我取消一下", "不要了", "stop"]
    )
    def test_cancel_variants(self, app_client, clean_tasks, monkeypatch, answer):
        calls = _record_handler(monkeypatch, "_handle_search", "SEARCH-MARKER")
        task = _send(app_client, "搜索")
        cont = _continue(app_client, task["id"], answer)["result"]["task"]
        assert cont["status"]["state"] == "TASK_STATE_CANCELED"
        assert calls == []

    def test_how_to_cancel_order_is_query(self, app_client, clean_tasks, monkeypatch):
        # 全等锁死的真断点反面：含「取消」的真查询照旧当参数（不是取消）
        calls = _record_handler(monkeypatch, "_handle_search", "SEARCH-MARKER")
        task = _send(app_client, "搜索")
        cont = _continue(app_client, task["id"], "如何取消订单")["result"]["task"]
        assert cont["status"]["state"] == "TASK_STATE_COMPLETED"
        assert calls == ["如何取消订单"]  # verbatim 照旧=查询参数（不变面）

    def test_canceled_task_rejects_further(self, app_client, clean_tasks, monkeypatch):
        _record_handler(monkeypatch, "_handle_search", "SEARCH-MARKER")
        task = _send(app_client, "搜索")
        _continue(app_client, task["id"], "不用了")
        data = _continue(app_client, task["id"], "森林防火", rid="cx-after")
        assert "error" in data  # canceled=终态，§3.1.1/§3.1.2 拒收续接
        assert data["error"]["code"] == -32004


class TestZeroChangeFaces:
    """不变面：正常参数回答/寒暄再问/无 pending 消息——路径零变化。"""

    def test_answer_routes_back_unchanged(self, app_client, clean_tasks, monkeypatch):
        calls = _record_handler(monkeypatch, "_handle_search", "SEARCH-MARKER")
        task = _send(app_client, "搜索")
        cont = _continue(app_client, task["id"], "森林防火")["result"]["task"]
        assert cont["status"]["state"] == "TASK_STATE_COMPLETED"
        assert calls == ["森林防火"]  # 参数真送达提问的技能（F4b-㉑ 契约零变化）

    def test_filler_answer_reasks_unchanged(self, app_client, clean_tasks, monkeypatch):
        calls = _record_handler(monkeypatch, "_handle_search", "SEARCH-MARKER")
        task = _send(app_client, "搜索")
        again = _continue(app_client, task["id"], "帮我一下")["result"]["task"]
        assert again["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        assert "搜索关键词" in again["history"][-1]["parts"][0]["text"]
        assert calls == []
        assert again["metadata"][_PENDING_KEY] == "search"

    def test_no_pending_message_not_cancelled(self, app_client, clean_tasks, monkeypatch):
        # 诚实边界锁死：取消语义只在 §6.3 pending 回答分支；无 pending 的「不用了」
        # 走普通路由（LLM chat）照常 completed，不越权改状态
        calls = _record_handler(monkeypatch, "_handle_search", "SEARCH-MARKER")
        task = _send(app_client, "不用了")
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert calls == []


class TestCancelByUserUnit:
    """cancel_by_user 唯一产生口单元：入史恰一次+终态+同对象。"""

    def test_cancel_by_user_shape(self):
        import asyncio

        from src.a2a.models import Message, Task

        tm = TaskManager()
        task = asyncio.run(tm.create_task(Message(role="user", parts=[{"text": "hi"}])))
        tm.cancel_by_user(task, "好的，已取消。")
        assert task.status.state == "canceled"
        assert task.history[-1].role == "agent"
        assert task.history[-1].parts[0]["text"] == "好的，已取消。"
        # status.message 与入史末条同一对象（禁双入史）
        assert task.status.message is task.history[-1]
        assert len(task.history) == 2  # [user, agent-ack]
