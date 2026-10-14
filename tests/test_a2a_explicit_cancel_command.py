"""⑥ a2a 显式 /cancel 指令——消息以 /cancel 开头=取消承载本消息的任务
（pending-decisions ⑥ 建议默认 B 先行执行，异议回「⑥改A」即 revert）。

修前缺口（F4b-㉔ 锁死语义的显式指令面）：`/cancel` 显式命令在任何路由位都不被
识别——pending 回答「/cancel」被当查询参数 verbatim 回填（搜索「/cancel」
fabricate 查询结果）、含触发词的「/cancel 搜索森林」还硬跑查询；㉔ 闭集取消短语
只在 §6.3 pending 回答分支生效且全等匹配认不出 `/cancel`（归一形带 "/" 前缀）。

收口（⑥ 选项 B：显式指令，无歧义、向后兼容）：
- _explicit_cancel_command 纯函数：命令 token 全等（宁窄勿滥）——去前导空白后
  以 `/cancel` 开头（大小写不敏感）且紧随字符非 ASCII 字母/数字（token 边界：
  `/canceled`、`/cancel123` 不是本命令）；starts-with 语义锁死（「如何使用
  /cancel 命令」不在行首=不是指令）。
- 消费点=_process_message 路由入口（skill 路由/普通路由/pending 回答三路同判
  之前）→ TaskCancelled → run_processing 归口 cancel_by_user（F4b-㉔ 唯一
  产生口）：取消确认入史恰一次 + state=canceled 终态。

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


_explicit_cancel_command = getattr(
    tm_mod, "_explicit_cancel_command", _missing("_explicit_cancel_command")
)


def _msg(text="hello-xcancel", **extra):
    m = {"role": "ROLE_USER", "parts": [{"text": text}], "messageId": f"xc-{text}"}
    m.update(extra)
    return m


def _rpc(client, method, params, rid="xcancel-cmd"):
    resp = client.post(
        "/a2a", json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
    )
    assert resp.status_code == 200
    return resp.json()


def _send(client, text, rid="xc-new", **msg_extra):
    data = _rpc(
        client,
        "SendMessage",
        {"message": _msg(text, **msg_extra), "metadata": dict(STUB)},
        rid=rid,
    )
    assert "result" in data, data
    return data["result"]["task"]


def _continue(client, task_id, text, rid="xc-cont"):
    return _rpc(
        client,
        "SendMessage",
        {"message": _msg(text, taskId=task_id), "metadata": dict(STUB)},
        rid=rid,
    )


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


def _record_handlers(monkeypatch, *method_names):
    """把 handler 换成记录桩：真路由决策打到真 dispatch，记录 (方法, 调用文本)。"""
    calls: list = []

    for name in method_names:

        def _make(n):
            async def _fake(self, text, task):
                calls.append((n, text))
                return f"MARKER-{n}"

            return _fake

        monkeypatch.setattr(TaskManager, name, _make(name))
    return calls


class TestExplicitCancelCommandPure:
    """命令 token 全等纯函数（宁窄勿滥）。"""

    def test_command_forms(self):
        for text in ("/cancel", "/cancel 不用了", "/Cancel", "  /cancel", "/cancel，谢谢"):
            assert _explicit_cancel_command(text), text

    def test_case_insensitive(self):
        assert _explicit_cancel_command("/CANCEL now")
        assert _explicit_cancel_command("/Cancel")

    def test_token_boundary_not_command(self):
        # 紧随 ASCII 字母/数字=别的 token 不是本命令
        for text in ("/canceled", "/cancel123", "/cancelx"):
            assert not _explicit_cancel_command(text), text

    def test_not_at_start_not_command(self):
        # starts-with 语义锁死：containment 会误杀这些真查询
        for text in ("如何使用 /cancel 命令", "请 /cancel", "帮我/cancel", "搜索 /cancel"):
            assert not _explicit_cancel_command(text), text

    def test_other_cancel_shapes_not_command(self):
        # 闭集取消短语归 ㉔（pending 分支）；中文/裸词/空串不是显式指令
        for text in ("取消", "cancel", "不用了", "/取消", "", "森林防火"):
            assert not _explicit_cancel_command(text), text


class TestExplicitCancelBehavior:
    """显式指令 → 承载任务 canceled（修前 fabricate 查询/走 LLM chat）。"""

    def test_new_task_command_cancels(self, app_client, clean_tasks, monkeypatch):
        calls = _record_handlers(monkeypatch, "_handle_search", "_handle_chat", "_handle_knowledge")
        task = _send(app_client, "/cancel")
        assert task["status"]["state"] == "TASK_STATE_CANCELED"
        assert calls == []  # 硬反证：零 handler 调用=没有 fabricated 处理
        roles = [m["role"] for m in task["history"]]
        assert roles == ["ROLE_USER", "ROLE_AGENT"]  # 消息入史不丢不重
        ack = task["history"][-1]
        assert "取消" in ack["parts"][0]["text"]
        assert task["status"]["message"]["parts"][0]["text"] == ack["parts"][0]["text"]

    def test_pending_answer_command_cancels_not_param(self, app_client, clean_tasks, monkeypatch):
        # 修前核心缺口：pending 回答「/cancel」被当查询参数 verbatim 回填
        calls = _record_handlers(monkeypatch, "_handle_search")
        task = _send(app_client, "搜索")
        assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        cont = _continue(app_client, task["id"], "/cancel")["result"]["task"]
        assert cont["status"]["state"] == "TASK_STATE_CANCELED"
        assert calls == []  # 硬反证：修前 _handle_search(["/cancel"])
        assert _PENDING_KEY not in cont["metadata"]  # pending 随收口即清

    def test_command_with_reason_cancels(self, app_client, clean_tasks, monkeypatch):
        calls = _record_handlers(monkeypatch, "_handle_search")
        task = _send(app_client, "搜索")
        cont = _continue(app_client, task["id"], "/cancel 不用了")["result"]["task"]
        assert cont["status"]["state"] == "TASK_STATE_CANCELED"
        assert calls == []

    def test_command_precedence_over_skill_trigger(self, app_client, clean_tasks, monkeypatch):
        # 修前：「/cancel 搜索森林防火」命中搜索触发词→硬跑查询 fabricate 结果
        calls = _record_handlers(monkeypatch, "_handle_search", "_handle_chat")
        task = _send(app_client, "/cancel 搜索森林防火")
        assert task["status"]["state"] == "TASK_STATE_CANCELED"
        assert calls == []

    @pytest.mark.parametrize("text", ["/cancel", "/Cancel 算了", "  /cancel", "/CANCEL"])
    def test_command_forms_cancels(self, app_client, clean_tasks, monkeypatch, text):
        calls = _record_handlers(monkeypatch, "_handle_search", "_handle_chat")
        task = _send(app_client, text)
        assert task["status"]["state"] == "TASK_STATE_CANCELED"
        assert calls == []

    def test_canceled_task_rejects_further(self, app_client, clean_tasks, monkeypatch):
        _record_handlers(monkeypatch, "_handle_search")
        task = _send(app_client, "搜索")
        _continue(app_client, task["id"], "/cancel")
        data = _continue(app_client, task["id"], "森林防火", rid="xc-after")
        assert "error" in data  # canceled=终态，§3.1.1/§3.1.2 拒收续接
        assert data["error"]["code"] == -32004


class TestZeroChangeFaces:
    """不变面：非指令文本路径零变化。"""

    def test_pending_answer_query_unchanged(self, app_client, clean_tasks, monkeypatch):
        # 「如何使用 /cancel 命令」不在行首=真查询，照旧 verbatim 当参数
        calls = _record_handlers(monkeypatch, "_handle_search")
        task = _send(app_client, "搜索")
        cont = _continue(app_client, task["id"], "如何使用 /cancel 命令")["result"]["task"]
        assert cont["status"]["state"] == "TASK_STATE_COMPLETED"
        assert calls == [("_handle_search", "如何使用 /cancel 命令")]

    def test_token_boundary_text_routes_unchanged(self, app_client, clean_tasks, monkeypatch):
        # /canceled 不是本命令：pending 回答照旧当参数（不变面）
        calls = _record_handlers(monkeypatch, "_handle_search")
        task = _send(app_client, "搜索")
        cont = _continue(app_client, task["id"], "/canceled")["result"]["task"]
        assert cont["status"]["state"] == "TASK_STATE_COMPLETED"
        assert calls == [("_handle_search", "/canceled")]

    def test_no_pending_normal_message_unchanged(self, app_client, clean_tasks, monkeypatch):
        # ㉔ 锁死语义延续：无 pending 的「不用了」/普通消息照常 completed
        calls = _record_handlers(monkeypatch, "_handle_search")
        task = _send(app_client, "不用了")
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert calls == []

    def test_closed_set_still_pending_only(self, app_client, clean_tasks, monkeypatch):
        # ㉔ 契约锁死：闭集取消短语只在 pending 回答位生效，无 pending 照常 completed
        calls = _record_handlers(monkeypatch, "_handle_search")
        task = _send(app_client, "取消")
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert calls == []
