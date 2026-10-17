"""Goal 自驱循环驱动 — record_loop 首个生产消费面（H1-②b-⑪）。

H1-② 定性③（GoalRunner.record_loop 真缺口·轴级）：loop_count 仅在 record_loop
内增长，_evaluate_state 的 max_loops 停机判定（goal_runner.py Rule 3）生产不可达
——goal 推进全靠外部 API 上报（/goals/{id}/events、/goals/{id}/report），自驱循环
轴无任何驱动方=「自主循环」整轴休眠。

本模块给循环轴补驱动方：
- run_loop_iteration：一步=一次真 LLM 调用产出朝目标的下一步动作/进展（真产出
  真留痕），随后 record_loop 计次并触发 max_loops 停机判定——停机判定自此生产可达。
- run_active_goal_loops：批量推进全部 ACTIVE goal（系统视图）。
- proactive goal_loop 行为（proactive_behaviors.py）周期调度 + 手动端点
  POST /api/will/goals/{goal_id}/loop 共用 run_loop_iteration 同一代码路径
  （F2b-① 纪律，禁两套编排漂移）。

诚实边界：
- 循环步骤事件分类=NONE（classify_tool_outcome 单一源判法）：LLM 建议/进展是
  过程回合非工具执行证据，不得膨胀 success_count 供 goal_report 'complete' 过
  事件验证（禁 fabricated success）；LLM 真失败=FAILED 计入（连续 3 失败
  fail-stop 判定可达）。
- LLM 产出是建议性下一步/进展记录，本驱动不执行工具动作——工具执行证据仍从
  /goals/{id}/events 外部上报来（goal_report 自报协议语义零变化）。
- 无 LLM 配置/调用失败如实记 error 事件，禁 fabricate 进展。
- 驱动方系统视图（run_active_goal_loops 不带身份轴）；手动端点走 bind_identity
  派生租户经 record_event/record_loop 写闸门。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx

from src.config import settings

from .goal_runner import LOOP_STEP_TOOL, Goal, GoalState, get_goal_runner

logger = logging.getLogger(__name__)

# 一步 LLM 预算（proactive 心跳 tick 不可长阻塞；超时如实 error 禁静默）
STEP_TIMEOUT_S = 30.0
# 事件 detail 保留长度（record_event raw_data 侧已有 200 截断）
DETAIL_MAX = 200

# 步骤执行器接缝：goal → {"output": ...} 或 {"error": ...}（测试可注入确定性执行器）
StepExecutor = Callable[[Goal], dict]


def default_step_executor(goal: Goal) -> dict:
    """自驱循环默认步骤执行器：真 LLM 调用产出朝目标的下一步动作/进展。

    返回 {"output": str}（成功）或 {"error": str}（无配置/超时/上游失败——
    如实带真因，禁 fabricate）。
    """
    if not (settings.llm_api_key or "").strip():
        return {"error": "无LLM配置（settings.llm_api_key 为空）"}
    recent = "; ".join((e.detail or e.tool_name) for e in goal.events[-3:]) or "（暂无）"
    prompt = (
        "你是自主目标执行器。针对下面的目标，给出下一步应执行的一个具体动作，"
        "一句话以内，只输出动作文本。\n"
        f"目标：{goal.description}\n"
        f"最近进展：{recent}"
    )
    try:
        resp = httpx.post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={
                "model": settings.llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 100,
            },
            timeout=STEP_TIMEOUT_S,
        )
        if resp.status_code != 200:
            return {"error": f"LLM 上游 HTTP {resp.status_code}: {resp.text[:200]}"}
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            return {"error": "LLM 返回空/非文本 content"}
        return {"output": text.strip()}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def run_loop_iteration(
    goal_id: str,
    *,
    runner: Any = None,
    executor: StepExecutor | None = None,
    tenant_id: str | None = None,
) -> dict | None:
    """推进一个 goal 的一次自驱循环迭代（record_loop 首个生产消费点）。

    序=先记工作事件再计次（record_event 先于 record_loop）：第 max_loops 次迭代的
    工作事件必须留痕——若先计次，停机转换为终态后 record_event 会因 terminal 被丢。
    返回 None=goal 不存在；goal 非 ACTIVE=如实 skipped 不触达执行器（禁对暂停/
    阻塞/终态 goal 硬跑 LLM）。
    """
    runner = runner or get_goal_runner()
    goal = runner.get_goal(goal_id, tenant_id=tenant_id)
    if goal is None:
        return None
    if goal.state != GoalState.ACTIVE:
        return {
            "goal_id": goal_id,
            "performed": False,
            "skipped": f"not_active:{goal.state.value}",
            "loop_count": goal.loop_count,
            "state": goal.state.value,
        }
    exec_fn = executor or default_step_executor
    result = exec_fn(goal)
    if not isinstance(result, dict):
        result = {"error": f"executor 返回非 dict: {type(result).__name__}"}
    detail = str(result.get("output") or result.get("error") or "")[:DETAIL_MAX]
    event = runner.record_event(goal_id, LOOP_STEP_TOOL, result, detail=detail, tenant_id=tenant_id)
    goal_after = runner.record_loop(goal_id, tenant_id=tenant_id)
    final = goal_after or goal
    return {
        "goal_id": goal_id,
        "performed": True,
        "skipped": None,
        "loop_count": final.loop_count,
        "state": final.state.value,
        "event_outcome": event.outcome.value if event else None,
        "step": detail,
    }


def run_active_goal_loops(runner: Any = None, executor: StepExecutor | None = None) -> dict:
    """批量推进全部 ACTIVE goal（系统视图：驱动方=心跳/系统行为，无身份轴）。

    单 goal 失败绝不掀翻其余（G1-⑤ 旁路纪律），错误逐条进 errors 可见禁静默。
    """
    runner = runner or get_goal_runner()
    active = runner.list_goals(state=GoalState.ACTIVE)
    result: dict = {
        "active_goals": len(active),
        "performed": 0,
        "skipped": 0,
        "states": {},
        "errors": [],
    }
    for goal in active:
        try:
            r = run_loop_iteration(goal.goal_id, runner=runner, executor=executor)
        except Exception as e:
            result["errors"].append(f"{goal.goal_id}: {type(e).__name__}: {e}")
            continue
        if r and r.get("performed"):
            result["performed"] += 1
            result["states"][r["state"]] = result["states"].get(r["state"], 0) + 1
        else:
            result["skipped"] += 1
    return result
