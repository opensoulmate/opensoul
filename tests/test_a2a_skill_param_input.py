"""F4b-㉑ a2a 技能参数缺失触发件——查询型技能参数缺失 → input-required。

修前缺口（F4b-⑳ 留档"技能参数缺失"触发件 / F4b-⑱ 诚实边界④预留的 handler 层
产生面）：技能路由（_process_message 按 AgentSkill.tags 触发词匹配）命中后直接把
**整条消息文本**当查询参数丢给查询型 handler（_handle_knowledge/_handle_search）——
消息只剩触发词（「搜索」/「知识库」，或触发词+寒暄「帮我搜索一下」）时参数缺失，
修前拿触发词硬跑查询 fabricate 结果（搜「搜索」回"未找到匹配结果"）。

收口：
- _skill_residual 纯函数：文本去掉触发词（大小写不敏感首现）+寒暄填充词后
  无实质内容=参数缺失 → TaskInputRequired 问用户要参数（input-required，§6.3
  续接可达），禁 fabricated 查询。查询型=_SKILL_QUERY_PARAM（knowledge→查询内容、
  search→搜索关键词）；chat（自由对话）/graph（无参概览）不触发（宁窄勿滥）。
- §6.3 续接回填：问的是技能参数时用户回答=参数（task.metadata _pending_skill_id
  路由记忆，加性契约），直接回填同一技能执行——回答通常不含触发词，按普通路由
  会掉进 LLM chat 到不了提问的技能=参数永远送不达。pending 消费一次即清；
  回答仍无实质内容=再问（不丢不重，F4b-⑱ 同语义）。

真栈非 mock：in-process TestClient（src.main app 全路由）+ handler 记录器
（monkeypatch 类方法=真路由决策打到真 dispatch，只把查询执行换成记录桩）。
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.a2a.task_manager import (
    TaskManager,
    _skill_residual,
    _strip_fillers,
    task_manager,
)
from src.main import app

STUB = {"llm_mode": "stub"}
_PENDING_KEY = "_pending_skill_id"


def _msg(text="hello skill-param", **extra):
    m = {"role": "ROLE_USER", "parts": [{"text": text}], "messageId": f"sp-{text}"}
    m.update(extra)
    return m


def _rpc(client, method, params, rid="skill-param"):
    resp = client.post(
        "/a2a", json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
    )
    assert resp.status_code == 200
    return resp.json()


def _send(client, text, rid="sp-new", **msg_extra):
    data = _rpc(
        client,
        "SendMessage",
        {"message": _msg(text, **msg_extra), "metadata": dict(STUB)},
        rid=rid,
    )
    assert "result" in data, data
    return data["result"]["task"]


def _continue(client, task_id, text, rid="sp-cont"):
    data = _rpc(
        client,
        "SendMessage",
        {"message": _msg(text, taskId=task_id), "metadata": dict(STUB)},
        rid=rid,
    )
    assert "result" in data, data
    return data["result"]["task"]


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


class TestSkillResidual:
    """残余参数纯函数：触发词/寒暄之外无实质内容=参数缺失。"""

    def test_trigger_only_is_empty(self):
        assert _skill_residual("搜索", "搜索") == ""
        assert _skill_residual("知识库", "知识库") == ""
        assert _skill_residual("搜索！", "搜索") == ""  # 标点剥净
        assert _skill_residual(" 搜索 。", "搜索") == ""

    def test_filler_only_is_empty(self):
        assert _skill_residual("帮我搜索一下", "搜索") == ""
        assert _skill_residual("帮我查一下知识库", "知识库") == ""
        assert _skill_residual("请搜索，谢谢", "搜索") == ""

    def test_query_content_kept(self):
        assert _skill_residual("搜索森林防火", "搜索") == "森林防火"
        assert _skill_residual("搜索知识库中的AI相关内容", "知识库") != ""
        assert "AI" in _skill_residual("搜索知识库中的AI相关内容", "知识库")

    def test_longest_filler_first_no_residue(self):
        # 「查询」不许被「查」拆出「询」残渣（最长优先）
        assert _strip_fillers("查询") == ""
        assert _strip_fillers("搜索看看") == ""

    def test_tag_case_insensitive(self):
        assert _skill_residual("SEARCH", "search") == ""
        assert _skill_residual("Search 森林防火", "search") == "森林防火"


class TestSkillParamInputRequired:
    """产生面：查询型技能参数缺失 → input-required（修前拿触发词 fabricate 查询）。"""

    def test_bare_search_trigger_asks_param(self, app_client, clean_tasks, monkeypatch):
        calls = _record_handler(monkeypatch, "_handle_search", "SEARCH-MARKER")
        task = _send(app_client, "搜索")
        assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        question = task["history"][-1]["parts"][0]["text"]
        assert "搜索关键词" in question  # 问的是具体参数
        assert "统一搜索" in question  # 卡面 skill 名
        assert calls == []  # 硬反证：handler 零调用=没有 fabricated 查询
        assert task["metadata"][_PENDING_KEY] == "search"

    def test_bare_knowledge_trigger_asks_param(self, app_client, clean_tasks, monkeypatch):
        calls = _record_handler(monkeypatch, "_handle_knowledge", "KB-MARKER")
        task = _send(app_client, "知识库")
        assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        question = task["history"][-1]["parts"][0]["text"]
        assert "查询内容" in question
        assert "知识库管理" in question
        assert calls == []
        assert task["metadata"][_PENDING_KEY] == "knowledge"

    def test_question_recorded_exactly_once(self, app_client, clean_tasks, monkeypatch):
        _record_handler(monkeypatch, "_handle_search", "SEARCH-MARKER")
        task = _send(app_client, "帮我搜索一下")
        assert [m["role"] for m in task["history"]] == ["ROLE_USER", "ROLE_AGENT"]
        assert len(task["history"]) == 2  # 问句恰一次（F4b-⑧）
        assert (
            task["history"][-1]["parts"][0]["text"] == task["status"]["message"]["parts"][0]["text"]
        )

    def test_filler_only_trigger_asks_param(self, app_client, clean_tasks, monkeypatch):
        """触发词+寒暄（无实质查询内容）同样=参数缺失。"""
        calls = _record_handler(monkeypatch, "_handle_search", "SEARCH-MARKER")
        task = _send(app_client, "帮我搜索一下")
        assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        assert calls == []


class TestAnswerRoutesBack:
    """§6.3 续接回填：回答=参数送回提问的技能（修前回答掉进 LLM chat=参数送不达）。"""

    def test_answer_routes_to_pending_skill(self, app_client, clean_tasks, monkeypatch):
        calls = _record_handler(monkeypatch, "_handle_search", "SEARCH-MARKER")
        task = _send(app_client, "搜索")
        cont = _continue(app_client, task["id"], "森林防火")
        assert cont["id"] == task["id"]
        assert cont["status"]["state"] == "TASK_STATE_COMPLETED"
        assert calls == ["森林防火"]  # 参数真送达提问的技能
        assert cont["history"][-1]["parts"][0]["text"] == "SEARCH-MARKER"
        assert _PENDING_KEY not in cont["metadata"]  # pending 消费一次即清

    def test_filler_answer_reasks(self, app_client, clean_tasks, monkeypatch):
        calls = _record_handler(monkeypatch, "_handle_search", "SEARCH-MARKER")
        task = _send(app_client, "搜索")
        again = _continue(app_client, task["id"], "帮我一下")
        assert again["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        assert "搜索关键词" in again["history"][-1]["parts"][0]["text"]
        roles = [m["role"] for m in again["history"]]
        assert roles == ["ROLE_USER", "ROLE_AGENT", "ROLE_USER", "ROLE_AGENT"]  # 不丢不重
        assert calls == []
        assert again["metadata"][_PENDING_KEY] == "search"

    def test_pending_consumed_once(self, app_client, clean_tasks, monkeypatch):
        calls = _record_handler(monkeypatch, "_handle_search", "SEARCH-MARKER")
        task = _send(app_client, "搜索")
        _continue(app_client, task["id"], "森林防火")
        assert calls == ["森林防火"]
        # 完成后的新消息回普通路由：裸触发词再问，不盲送 pending 技能
        task2 = _send(app_client, "搜索", rid="sp-new2")
        assert task2["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        assert calls == ["森林防火"]  # 第二轮零额外 handler 调用


class TestZeroChangeFaces:
    """不变面：有参数/非查询型技能/无技能匹配——路径零变化（新旧行为同）。"""

    def test_query_with_arg_no_ask(self, app_client, clean_tasks, monkeypatch):
        calls = _record_handler(monkeypatch, "_handle_search", "SEARCH-MARKER")
        task = _send(app_client, "搜索关于机器学习的知识")
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert calls == ["搜索关于机器学习的知识"]  # 整条文本照旧当查询串
        assert _PENDING_KEY not in task["metadata"]

    def test_graph_trigger_only_no_ask(self, app_client, clean_tasks, monkeypatch):
        """graph=无参概览：裸触发词照旧执行，不触发参数询问。"""
        calls = _record_handler(monkeypatch, "_handle_graph", "GRAPH-MARKER")
        task = _send(app_client, "图谱")
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert calls == ["图谱"]
        assert task["history"][-1]["parts"][0]["text"] == "GRAPH-MARKER"

    def test_chat_trigger_only_no_ask(self, app_client, clean_tasks):
        """chat=自由对话：裸触发词照旧进对话（stub 确定性应答），不触发参数询问。"""
        task = _send(app_client, "对话")
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert "stub-llm" in task["history"][-1]["parts"][0]["text"]
        assert _PENDING_KEY not in task["metadata"]

    def test_no_skill_match_fallback_unchanged(self, app_client, clean_tasks):
        task = _send(app_client, "hello param-x")
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert "stub-llm" in task["history"][-1]["parts"][0]["text"]


class TestNoChunkBeforeAsk:
    """发问在任何 artifact 分块之前：问句前零 artifactUpdate、artifacts 真源空。"""

    @pytest.mark.asyncio
    async def test_ask_produces_zero_artifact_chunks(self, clean_tasks, monkeypatch):
        calls = _record_handler(monkeypatch, "_handle_search", "SEARCH-MARKER")
        from src.a2a.models import Message

        message = Message(role="user", parts=[{"text": "搜索"}])
        task = await task_manager.create_task(message, metadata=dict(STUB))
        q = task_manager.subscribe_stream(task.id, first_frame_ends=False)
        await task_manager.run_processing(task, message)

        assert task.status.state == "input-required"
        assert task.artifacts == []  # 真源零块
        assert calls == []
        kinds = []
        frames = []
        while True:
            payload, ends = q.get_nowait()
            frames.append((payload, ends))
            if ends:
                break
        for payload, _ in frames[:-1]:
            kinds.append(next(iter(payload)))
        assert kinds == [
            "statusUpdate",
            "statusUpdate",
        ]  # working→input-required，零 artifactUpdate
        final = frames[-1][0]["task"]
        assert final["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"  # wire 形 v1.0 枚举
        assert final.get("artifacts") in ([], None)
