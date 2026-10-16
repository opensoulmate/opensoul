"""G1-③a 创造力回流任务执行门禁面：generate_alternatives 真消费。

G0 定性⑤「生成新方案」只出 API 不进任何决策面（speculative API-only）收口：
deny/confirm_required 阻塞时刻=「生成新方案」的真实需求时刻，CreativityEngine
.generate_alternatives 真消费进人机裁决面（deny_reason→任务结果+failed 事件、
confirm_prompt→任务结果+问句回群聊+看板「确认提示」块）。

测的是 src/api/ai_groups.py::_execute_task_impl 真实门禁路径 + _creative_
alternatives_for 真实取数口 + src/cortex/creativity.py 真实方法本体（LLM 上游=
桩 src.cortex.creativity.httpx 整体替换，不打真 LLM）。只桩 agent 子进程调用与
brain.think 决策（可控 execute_mode），数据面换 tmp（ai_groups.db）隔离。
"""

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

import src.api.ai_groups as ai_groups
import src.cortex.creativity as creativity_mod
from src.cortex.creativity import alternatives_payload, render_alternatives

_ALT_JSON = [
    {
        "approach": "灰度切换",
        "description": "先在小流量灰度验证，再全量切换，出问题可秒级回退。",
        "pros": ["风险低"],
        "cons": ["周期长"],
        "novelty": 2,
        "feasibility": 5,
    },
    {
        "approach": "影子写入",
        "description": "新旧链路双写对账，一致后再切读流量。",
        "pros": ["零丢数据"],
        "cons": ["成本高"],
        "novelty": 4,
        "feasibility": 3,
    },
    {
        "approach": "快照回滚",
        "description": "操作前打快照，异常即回滚到快照点。",
        "pros": ["可逆"],
        "cons": ["快照开销"],
        "novelty": 1,
        "feasibility": 4,
    },
]


# ---------------------------------------------------------------- 纯函数面


def test_payload_classifies_sentinel_error():
    """故障哨兵项（approach="错误"/"无LLM配置"）如实归入 error 键，禁当真方案。"""
    payload = alternatives_payload([{"approach": "错误", "description": "conn refused"}])
    assert payload == {"count": 0, "items": [], "error": "conn refused"}
    payload2 = alternatives_payload([{"approach": "无LLM配置", "description": "需要配置LLM API"}])
    assert payload2["error"] == "需要配置LLM API"
    assert payload2["items"] == []


def test_payload_keeps_real_items_verbatim():
    """真项原样保留（pros/cons/novelty/feasibility 上游没发不硬造）。"""
    payload = alternatives_payload(_ALT_JSON)
    assert payload["count"] == 3
    assert payload["items"] == _ALT_JSON
    assert "error" not in payload


def test_payload_skips_junk_without_fabrication():
    """非 dict 条目/缺 approach 条目如实跳过；非 list 产出如实报形态非法。"""
    payload = alternatives_payload([None, "x", {"description": "无名"}, {"approach": "  "}])
    assert payload == {"count": 0, "items": []}
    bad = alternatives_payload({"not": "a list"})
    assert bad["count"] == 0 and "形态非法" in bad["error"]


def test_render_empty_zero_weave():
    """空产出=空串零织入（无痕可撤）。"""
    assert render_alternatives([]) == ""
    assert render_alternatives(alternatives_payload([{"approach": "错误"}])["items"]) == ""


def test_render_top3_exact():
    """最多 3 条、序号/全角冒号/描述逐字（缺描述如实略）。"""
    four = _ALT_JSON + [{"approach": "第四案", "description": "不该出现"}]
    text = render_alternatives(four)
    assert text.startswith("\n\n💡 备选方案（创造力回流，供改写任务计划参考）：")
    assert "1. 灰度切换：先在小流量灰度验证，再全量切换，出问题可秒级回退。" in text
    assert "2. 影子写入：新旧链路双写对账，一致后再切读流量。" in text
    assert "3. 快照回滚：操作前打快照，异常即回滚到快照点。" in text
    assert "第四案" not in text
    only_name = render_alternatives([{"approach": "单名案"}])
    assert "1. 单名案" in only_name and "：" not in only_name.splitlines()[-1]


def test_sentinel_never_woven_hard_counterproof():
    """反证：哨兵故障项绝不织入文案（fabricate 硬断言）。"""
    payload = alternatives_payload([{"approach": "错误", "description": "boom"}])
    text = render_alternatives(payload["items"])
    assert text == ""
    assert "错误" not in text and "boom" not in text


# ---------------------------------------------------------------- 门禁全链


class _FakeResp:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _fake_httpx(monkeypatch, items=None, exc=None, capture=None):
    """替换 creativity_mod.httpx：真 CreativityEngine 方法本体走桩上游。"""

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            if capture is not None:
                capture.append(kwargs)
            if exc is not None:
                raise exc
            body = json.dumps(items if items is not None else _ALT_JSON, ensure_ascii=False)
            return _FakeResp(body)

    monkeypatch.setattr(creativity_mod, "httpx", SimpleNamespace(AsyncClient=lambda: _FakeClient()))


def _think_result(mode: str) -> dict:
    return {
        "intent": {
            "goal": "fix_bug",
            "target_files": [],
            "change_size": "small",
            "modify_scope": "single_file",
        },
        "risk": {
            "risks": [],
            "overall_level": {"deny": "critical", "confirm_required": "high"}.get(mode, "low"),
            "recommendation": "高风险操作，建议先备份再执行",
        },
        "decision": {
            "edit_mode": "patch",
            "execute_mode": mode,
            "confirm_prompt": "需要确认：目标含不可逆破坏性操作"
            if mode == "confirm_required"
            else ("风险过高：删除核心模块" if mode == "deny" else None),
        },
        "emotion": {"primary": "neutral", "confidence": 0.5, "sentiment": "neutral"},
        "memory_count": 0,
    }


@pytest.fixture()
def gate_env(tmp_path, monkeypatch):
    """真实门禁路径 + 可控 brain 决策 + 真创造力取数口/方法本体（桩 LLM 上游）。"""
    from src.api import brain as brain_api
    from src.hippo.memory_store import MemoryStore

    monkeypatch.setattr(ai_groups, "DB_PATH", tmp_path / "ai_groups.db")
    ai_groups.init_db()
    monkeypatch.setattr(brain_api, "store", MemoryStore())
    monkeypatch.setattr(ai_groups, "GROUP_MSG_PUSH", "http://127.0.0.1:9/disabled")
    # LLM 凭据钉住（缺省 env 可能无 key→方法走"无LLM配置"哨兵；测试必须确定性）
    monkeypatch.setattr(creativity_mod.settings, "llm_api_key", "test-key")

    calls = {"n": 0}

    async def fake_run_agent_proxy(agent_id, text, on_event=None, **kwargs):
        calls["n"] += 1
        return ("桩执行结果：任务完成", "stub-agent", True)

    monkeypatch.setattr(ai_groups, "run_agent_proxy", fake_run_agent_proxy)

    think_modes: dict = {}

    async def fake_think(req):
        mode = "auto"
        for key, m in think_modes.items():
            if key in (req.user_input or ""):
                mode = m
        return _think_result(mode)

    monkeypatch.setattr(brain_api, "think", fake_think)

    def make_task(goal, tid="t-g13a-1"):
        conn = ai_groups.get_db()
        try:
            gid = "g-g13a-test"
            now = datetime.now().isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO ai_groups (id,name,description,status,created_at) VALUES (?,?,?,?,?)",
                (gid, "g13a", "", "active", now),
            )
            conn.execute(
                "INSERT OR REPLACE INTO ai_group_tasks (id,group_id,goal,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (tid, gid, goal, "pending", now, now),
            )
            conn.commit()
        finally:
            conn.close()
        return gid, tid

    def task_row(tid):
        conn = ai_groups.get_db()
        try:
            return dict(conn.execute("SELECT * FROM ai_group_tasks WHERE id=?", (tid,)).fetchone())
        finally:
            conn.close()

    def task_events(tid):
        conn = ai_groups.get_db()
        try:
            rows = conn.execute(
                "SELECT event_type, content FROM task_execution_events WHERE task_id=? ORDER BY id",
                (tid,),
            ).fetchall()
            return [(r["event_type"], r["content"]) for r in rows]
        finally:
            conn.close()

    def chat_messages(tid):
        conn = ai_groups.get_db()
        try:
            rows = conn.execute(
                "SELECT agent_id, content FROM discussion_messages WHERE task_id=? ORDER BY id",
                (tid,),
            ).fetchall()
            return [(r["agent_id"], r["content"]) for r in rows]
        finally:
            conn.close()

    yield {
        "ai_groups": ai_groups,
        "think_modes": think_modes,
        "agent_calls": calls,
        "make_task": make_task,
        "task_row": task_row,
        "task_events": task_events,
        "chat_messages": chat_messages,
        "fake_httpx": lambda **kw: _fake_httpx(monkeypatch, **kw),
    }


class TestGateBackflow:
    def test_deny_gate_backflows_alternatives(self, gate_env):
        """deny 门禁真消费 generate_alternatives：备选方案织入 deny_reason（任务结果+
        failed 事件），problem 逐字送达、返回载荷结构化可观测。"""
        env = gate_env
        ag = env["ai_groups"]
        env["think_modes"]["清空生产库"] = "deny"
        capture: list = []
        env["fake_httpx"](capture=capture)
        gid, tid = env["make_task"]("清空生产库并重置环境")
        req = ag.ExecuteTaskRequest(agent_id="mimo", goal="清空生产库并重置环境")

        result = asyncio.run(ag._execute_task_impl(gid, tid, req))

        assert result["status"] == "denied"
        assert result["alternatives"]["count"] == 3
        row = env["task_row"](tid)
        assert "风险过高：删除核心模块" in row["result"], "原文案必须保留（织入非替换）"
        assert "💡 备选方案" in row["result"]
        assert "1. 灰度切换" in row["result"] and "3. 快照回滚" in row["result"]
        events = env["task_events"](tid)
        types = [t for t, _ in events]
        assert "creative-alternatives" in types
        ev = next(c for t, c in events if t == "creative-alternatives")
        assert "3条" in ev and "deny" in ev
        # problem 逐字送达创造力引擎（用户任务原文进生成 prompt）
        prompt = capture[0]["json"]["messages"][1]["content"]
        assert "清空生产库并重置环境" in prompt
        assert "风险过高：删除核心模块" in prompt, "约束条件（deny_reason）必须送达"

    def test_confirm_gate_backflows_to_group_chat(self, gate_env):
        """confirm_required 门禁：备选方案织入问句→任务结果+问句回群聊（人机裁决面
        真消费）+返回载荷。"""
        env = gate_env
        ag = env["ai_groups"]
        env["think_modes"]["格式化"] = "confirm_required"
        env["fake_httpx"]()
        gid, tid = env["make_task"]("格式化数据盘")
        req = ag.ExecuteTaskRequest(agent_id="mimo", goal="格式化数据盘")

        result = asyncio.run(ag._execute_task_impl(gid, tid, req))

        assert result["status"] == "confirm_required"
        assert result["alternatives"]["count"] == 3
        row = env["task_row"](tid)
        assert row["status"] == "awaiting_confirm"
        assert "需要确认：目标含不可逆破坏性操作" in row["result"]
        assert "💡 备选方案" in row["result"]
        chat = env["chat_messages"](tid)
        system_msgs = [c for s, c in chat if s == "system"]
        assert any("💡 备选方案" in c and "影子写入" in c for c in system_msgs), (
            f"问句回群聊必须带备选方案: {system_msgs}"
        )

    def test_auto_path_zero_generation(self, gate_env):
        """成本契约：非阻塞路径零生成（generate_alternatives 零调用）。"""
        env = gate_env
        ag = env["ai_groups"]
        capture: list = []
        env["fake_httpx"](capture=capture)
        gid, tid = env["make_task"]("修复src/api/echo.py里的超时bug")
        req = ag.ExecuteTaskRequest(agent_id="mimo", goal="修复src/api/echo.py里的超时bug")

        result = asyncio.run(ag._execute_task_impl(gid, tid, req))

        assert result["status"] == "scored"
        assert capture == [], "auto 路径禁触达创造力引擎"
        types = [t for t, _ in env["task_events"](tid)]
        assert "creative-alternatives" not in types

    def test_generation_failure_no_trace_error_visible(self, gate_env):
        """生成故障：门禁语义零依赖（deny 照常落 failed）+无痕可撤（原文案照旧）+
        creative-alternatives-error 可见禁静默。"""
        env = gate_env
        ag = env["ai_groups"]
        env["think_modes"]["清空生产库"] = "deny"
        env["fake_httpx"](exc=RuntimeError("conn refused"))
        gid, tid = env["make_task"]("清空生产库并重置环境")
        req = ag.ExecuteTaskRequest(agent_id="mimo", goal="清空生产库并重置环境")

        result = asyncio.run(ag._execute_task_impl(gid, tid, req))

        assert result["status"] == "denied", "创造力故障绝不改变门禁判定"
        row = env["task_row"](tid)
        assert row["status"] == "failed"
        assert row["result"] == "风险过高：删除核心模块", "故障必须无痕可撤（禁半截织入）"
        assert "💡 备选方案" not in row["result"]
        events = env["task_events"](tid)
        err = [c for t, c in events if t == "creative-alternatives-error"]
        assert err and "conn refused" in err[0]
        assert "creative-alternatives" not in [t for t, _ in events]

    def test_sentinel_error_visible_not_fabricated(self, gate_env):
        """方法故障哨兵（无LLM配置）：如实 error 事件+零织入（禁哨兵当真方案 fabricate）。"""
        env = gate_env
        ag = env["ai_groups"]
        env["think_modes"]["格式化"] = "confirm_required"
        env["fake_httpx"](items=[{"approach": "无LLM配置", "description": "需要配置LLM API"}])
        gid, tid = env["make_task"]("格式化数据盘")
        req = ag.ExecuteTaskRequest(agent_id="mimo", goal="格式化数据盘")

        result = asyncio.run(ag._execute_task_impl(gid, tid, req))

        assert result["status"] == "confirm_required"
        assert result["alternatives"]["count"] == 0
        assert result["alternatives"]["error"] == "需要配置LLM API"
        row = env["task_row"](tid)
        assert row["result"] == "需要确认：目标含不可逆破坏性操作", "哨兵零织入无痕可撤"
        assert "无LLM配置" not in row["result"]
        events = env["task_events"](tid)
        err = [c for t, c in events if t == "creative-alternatives-error"]
        assert err and "需要配置LLM API" in err[0]
