"""G1-③b-1 创造力回流规划面：generate_alternatives 真消费进 TaskPlanner 规划决策。

G0 定性⑤「生成新方案」只出 API 不进任何决策面收口的规划面半边（门禁人机裁决
面=G1-③a 已落）：备选思路软建议块织入 TaskPlanner 规划 prompt（以目标要求为
准免责），闭「task_planner 零消费 alternatives」断点；/api/cortex/plan 返回载荷
加性键 alternatives（count/items/(error) 与 G1-③a 同形）。

测的是真实 TaskPlanner.plan 方法本体 + 真实 creative_alternatives_for 取数口/
CreativityEngine 方法本体 + 真实 /api/cortex/plan handler（LLM 上游=桩 httpx
整模块替换，不打真 LLM）。数据面零落库。
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

import src.api.ai_groups as ai_groups
import src.cortex.creativity as creativity_mod
import src.cortex.task_planner as planner_mod

# 控制组（git worktree 旧代码）容缺导入：新符号不存在时置 None，让失败暴露在
# 行为断言上而非收集期 ImportError（家族纪律）。
try:
    from src.cortex.creativity import (
        creative_alternatives_for,
        render_alternatives_for_prompt,
    )
except ImportError:  # pragma: no cover - 控制组旧代码面
    creative_alternatives_for = None
    render_alternatives_for_prompt = None

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

_PLAN_JSON = [
    {"description": "步骤一：分析需求", "dependencies": [], "priority": 1},
    {"description": "步骤二：实施改造", "dependencies": [0], "priority": 2},
]

_DISCLAIMER = "仅供参考，以目标要求为准，可全不采纳"


class _FakeResp:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _fake_httpx(monkeypatch, module, items=None, exc=None, capture=None, content=None):
    """替换 module.httpx：真方法本体走桩上游（capture 回读请求体）。"""

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
            if content is not None:
                body = content
            else:
                body = json.dumps(items if items is not None else _ALT_JSON, ensure_ascii=False)
            return _FakeResp(body)

    monkeypatch.setattr(module, "httpx", SimpleNamespace(AsyncClient=lambda: _FakeClient()))


def _pin_llm_key(monkeypatch):
    """LLM 凭据钉住（缺省 env 可能无 key→哨兵/ValueError，测试必须确定性）。"""
    monkeypatch.setattr(creativity_mod.settings, "llm_api_key", "test-key")


# ---------------------------------------------------------------- prompt 侧纯函数


def test_prompt_render_empty_zero_weave():
    """空产出=空串零织入（无痕可撤）；哨兵归一后 items 空同样零织入。"""
    assert render_alternatives_for_prompt([]) == ""
    payload = creativity_mod.alternatives_payload([{"approach": "错误", "description": "boom"}])
    assert render_alternatives_for_prompt(payload["items"]) == ""


def test_prompt_render_shape_and_disclaimer():
    """块形：可选思路抬头+免责逐字+序号/全角冒号/描述逐字（缺描述如实略）。"""
    text = render_alternatives_for_prompt(_ALT_JSON)
    assert text.startswith("可选思路（创造力回流参考；")
    assert _DISCLAIMER in text
    assert "1. 灰度切换：先在小流量灰度验证，再全量切换，出问题可秒级回退。" in text
    assert "2. 影子写入：新旧链路双写对账，一致后再切读流量。" in text
    only_name = render_alternatives_for_prompt([{"approach": "单名案"}])
    assert "1. 单名案" in only_name
    assert only_name.splitlines()[-1] == "1. 单名案"


def test_prompt_render_top3_cap():
    """最多 3 条（第 4 条不进 prompt）。"""
    four = _ALT_JSON + [{"approach": "第四案", "description": "不该出现"}]
    text = render_alternatives_for_prompt(four)
    assert "3. 快照回滚" in text
    assert "第四案" not in text


def test_sentinel_never_in_prompt_hard_counterproof():
    """反证：哨兵故障项绝不织入 prompt（fabricate 硬断言）。"""
    payload = creativity_mod.alternatives_payload([{"approach": "错误", "description": "boom"}])
    text = render_alternatives_for_prompt(payload["items"])
    assert text == ""
    assert "错误" not in text and "boom" not in text


# ---------------------------------------------------------------- 取数口单一源


def test_single_source_delegation():
    """ai_groups 门禁面取数口委托 creativity.creative_alternatives_for 单一源
    （禁两套装配漂移）：入参逐字透传（goal/constraints 归一/count=3）。"""
    calls = []

    async def recorder(problem, constraints=None, count=3):
        calls.append((problem, constraints, count))
        return {"count": 9, "items": []}, "TEXT"

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(creativity_mod, "creative_alternatives_for", recorder, raising=False)
        result = asyncio.run(ai_groups._creative_alternatives_for("目标甲", ["约束乙"]))
        assert result == ({"count": 9, "items": []}, "TEXT")
        assert calls == [("目标甲", ["约束乙"], 3)]
        asyncio.run(ai_groups._creative_alternatives_for("目标丙", []))
        assert calls[-1] == ("目标丙", None, 3)
    finally:
        monkeypatch.undo()


def test_take_point_uses_engine_and_payload():
    """真实取数口全链（桩 LLM 上游）：generate_alternatives 问题逐字送达、
    载荷归一+裁决文案成对返回。"""
    capture: list = []
    monkeypatch = pytest.MonkeyPatch()
    try:
        _pin_llm_key(monkeypatch)
        _fake_httpx(monkeypatch, creativity_mod, capture=capture)
        payload, text = asyncio.run(creative_alternatives_for("部署新服务", ["不能停机"], 3))
    finally:
        monkeypatch.undo()
    assert payload["count"] == 3
    assert payload["items"] == _ALT_JSON
    prompt = capture[0]["json"]["messages"][1]["content"]
    assert "部署新服务" in prompt
    assert "不能停机" in prompt
    assert "1. 灰度切换" in text


# ---------------------------------------------------------------- TaskPlanner 织入


def test_planner_weaves_advisory_into_prompt():
    """advisory 软建议块真织入规划 user prompt（goal 逐字、免责在场、
    SYSTEM_PROMPT 规划规则不动）。"""
    monkeypatch = pytest.MonkeyPatch()
    capture: list = []
    try:
        _pin_llm_key(monkeypatch)
        _fake_httpx(
            monkeypatch,
            planner_mod,
            content=json.dumps(_PLAN_JSON, ensure_ascii=False),
            capture=capture,
        )
        tasks = asyncio.run(
            planner_mod.TaskPlanner().plan(
                "建设监控系统", advisory="可选思路（创造力回流参考）：\n1. 灰度切换：先灰度"
            )
        )
    finally:
        monkeypatch.undo()
    assert [t.description for t in tasks] == ["步骤一：分析需求", "步骤二：实施改造"]
    msgs = capture[0]["json"]["messages"]
    assert msgs[0]["content"] == planner_mod.TaskPlanner.SYSTEM_PROMPT
    user = msgs[1]["content"]
    assert user.startswith("Goal: 建设监控系统")
    assert "可选思路（创造力回流参考）" in user


def test_planner_empty_advisory_zero_change():
    """零变化契约：advisory 空=请求与接线前逐字节一致（user 恰 'Goal: {goal}'）。"""
    monkeypatch = pytest.MonkeyPatch()
    capture: list = []
    try:
        _pin_llm_key(monkeypatch)
        _fake_httpx(
            monkeypatch,
            planner_mod,
            content=json.dumps(_PLAN_JSON, ensure_ascii=False),
            capture=capture,
        )
        asyncio.run(planner_mod.TaskPlanner().plan("原样目标"))
    finally:
        monkeypatch.undo()
    user = capture[0]["json"]["messages"][1]["content"]
    assert user == "Goal: 原样目标"


# ---------------------------------------------------------------- /plan 端点全链


def test_plan_endpoint_backflows_alternatives(cortex_client, monkeypatch):
    """/api/cortex/plan 全链：真取数口+真 TaskPlanner（双桩 LLM 上游）——备选
    思路真进规划 prompt+返回载荷加性键 alternatives+tasks 契约逐键不变。"""
    _pin_llm_key(monkeypatch)
    creative_cap: list = []
    planner_cap: list = []
    _fake_httpx(monkeypatch, creativity_mod, capture=creative_cap)
    _fake_httpx(
        monkeypatch,
        planner_mod,
        content=json.dumps(_PLAN_JSON, ensure_ascii=False),
        capture=planner_cap,
    )

    resp = cortex_client.post("/api/cortex/plan", json={"goal": "建设监控系统"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["goal"] == "建设监控系统"
    assert [t["description"] for t in data["tasks"]] == ["步骤一：分析需求", "步骤二：实施改造"]
    for t in data["tasks"]:
        assert set(t.keys()) == {"index", "description", "dependencies", "priority"}
    assert data["alternatives"]["count"] == 3
    assert data["alternatives"]["items"] == _ALT_JSON
    assert "error" not in data["alternatives"]
    # 创造力生成 prompt：goal 逐字送达
    assert "建设监控系统" in creative_cap[0]["json"]["messages"][1]["content"]
    # 规划 prompt：软建议块真织入（goal 保留在前）
    user = planner_cap[0]["json"]["messages"][1]["content"]
    assert user.startswith("Goal: 建设监控系统")
    assert "可选思路（创造力回流参考" in user
    assert "1. 灰度切换" in user
    assert _DISCLAIMER in user


def test_plan_endpoint_generation_failure_proceeds_no_weave(cortex_client, monkeypatch):
    """生成故障：规划照常跑（不阻断）+error 如实进载荷+prompt 零织入无痕可撤
    （硬反证：'可选思路' 不得出现在规划 prompt）。"""
    _pin_llm_key(monkeypatch)
    planner_cap: list = []
    _fake_httpx(monkeypatch, creativity_mod, exc=RuntimeError("LLM down"))
    _fake_httpx(
        monkeypatch,
        planner_mod,
        content=json.dumps(_PLAN_JSON, ensure_ascii=False),
        capture=planner_cap,
    )

    resp = cortex_client.post("/api/cortex/plan", json={"goal": "故障目标"})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tasks"]) == 2, "生成故障禁阻断规划"
    assert data["alternatives"]["count"] == 0
    assert "LLM down" in data["alternatives"]["error"]
    user = planner_cap[0]["json"]["messages"][1]["content"]
    assert user == "Goal: 故障目标", "故障零织入（无痕可撤）"
    assert "可选思路" not in user and "LLM down" not in user


def test_plan_endpoint_no_llm_config_error_visible(cortex_client, monkeypatch):
    """无 LLM 配置哨兵：如实 error 键+规划侧 400（LLM API key not configured
    既有契约）——不 fabricate 备选方案也不 fabricate 规划。"""
    monkeypatch.setattr(creativity_mod.settings, "llm_api_key", "")
    _fake_httpx(monkeypatch, creativity_mod)

    resp = cortex_client.post("/api/cortex/plan", json={"goal": "无凭据目标"})

    assert resp.status_code == 400
    assert "LLM API key not configured" in resp.json()["detail"]


# ---------------------------------------------------------------- 静态接线


def test_static_wiring():
    """接线普查：/plan handler 真调用取数口+真传 advisory；门禁面真委托单一源。"""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    cortex_src = (root / "src/api/cortex.py").read_text(encoding="utf-8")
    assert "creative_alternatives_for(req.goal)" in cortex_src
    assert "advisory=advisory_text" in cortex_src
    assert "render_alternatives_for_prompt(" in cortex_src
    planner_src = (root / "src/cortex/task_planner.py").read_text(encoding="utf-8")
    assert 'user_content = f"{user_content}\\n\\n{advisory}"' in planner_src
    ag_src = (root / "src/api/ai_groups.py").read_text(encoding="utf-8")
    assert "return await creative_alternatives_for(run_goal" in ag_src
