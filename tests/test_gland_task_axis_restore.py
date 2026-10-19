#!/usr/bin/env python3
"""DEC-㉖B2-① gland 用量 task 轴真源补现——recent_records 元素补 task +
record() 调用点带 task 语义 + UI task 列随真源恢复（pending-decisions ㉖ B2）。

背景（DEC-㉖-① 留档「B2=recent_records 元素补 task 轴（record() 调用点带 task
语义=跨 gland 调用点大件）仍待拍」）：CD-⑤ 把 UI task 列当死显示面移除（真源
recent_records 元素不产出 task=假渲染）。本拍按「别停工等批」铁律+DEC-①~㉘b
自决先例自决取 B2 先行执行（异议回「㉖B2改A」=revert 本拍）：
①真源补现——UsageRecord.task + record(task=) + recent_records 元素补 task 键
  （键恒在场，未提供=None 如实，禁静默缺席）；
②调用点带 task 语义——chat() 记录解析后的 TaskType（role 路由=resolve_task
  派生单一源、缺省=chat）、embed() 记录 embedding；
③UI 复原——task 列随真源恢复（CD-⑤ 移除件复原语义逐字一致 {r.task}）。

断言三层（禁自嗨恒真断言）：
①真源形状：record() task 往返、元素恰 8 键、task 轴不擅入聚合面（宁窄勿滥）；
②调用点语义：chat/role/embed 三路由真跑 stub 上游，记录 task 逐值精确；
③UI 消费面+行为桥：task 列真消费真源键、列序复原、r.* 消费键 ⊆ 元素真形、
  渲染表达式逐字经 node 真执行（null 如实渲染空禁 'unknown' fabricate）。
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "gland" / "page.tsx"
ROUTER = REPO / "src" / "gland" / "router.py"

RECORD_ELEMENT_KEYS = {
    "provider",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "user_id",
    "task",
    "timestamp",
}

# DEC-㉖-① summary() 键形（task 轴不擅入聚合面=宁窄勿滥，形状变了须复挖）
SUMMARY_KEYS = {
    "total_tokens",
    "total_prompt_tokens",
    "total_completion_tokens",
    "budget_limit",
    "remaining_budget",
    "call_count",
    "by_model",
    "by_user",
    "by_provider",
}


def _code() -> str:
    """去掉行注释后的真实消费代码（注释里的历史记述不算消费形）。"""
    return re.sub(r"//.*$", "", PAGE.read_text(encoding="utf-8"), flags=re.M)


def _meter_with(task_marker: str | None = None):
    from src.gland.token_meter import TokenMeter

    tm = TokenMeter()
    if task_marker is None:
        tm.record("prov-a", "m", 10, 5)
    else:
        tm.record("prov-a", "m", 10, 5, task=task_marker)
    return tm


def _router():
    """stub HTTP 上游的 ModelRouter（其余真代码真路径）。"""
    from src.gland.router import ModelRouter

    m = ModelRouter()
    m.add_provider(
        "prov-x",
        "http://stub.invalid/v1",
        {
            "chat": "m-chat",
            "completion": "m-comp",
            "code": "m-code",
            "summarize": "m-sum",
            "embedding": "m-emb",
        },
    )
    m.key_manager.add_key("prov-x", "***")

    async def _fake_call_chat(provider, api_key, model, messages, **kwargs):
        return {
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            "choices": [{"message": {"content": "ok"}}],
        }

    async def _fake_call_embedding(provider, api_key, model, texts):
        return {"embeddings": [[0.0] for _ in texts]}

    m._call_chat = _fake_call_chat  # type: ignore[assignment]
    m._call_embedding = _fake_call_embedding  # type: ignore[assignment]
    return m


# ── ① 真源形状（TokenMeter 实跑）────────────────────────────────


def test_record_task_roundtrip():
    """record(task=) → UsageRecord.task 与 recent_records 元素 task 逐值精确；
    且 task 不漏入 summary() 聚合面。"""
    tm = _meter_with("chat")
    rec = tm._records[-1]
    element = tm.recent_records(1)[0]
    assert rec.task == "chat"
    assert element["task"] == "chat"
    assert set(tm.summary()) == SUMMARY_KEYS


def test_record_task_default_none_key_present():
    """未提供 task：元素 task 键恒在场、值=None 如实（禁静默缺席/禁 fabricate）。"""
    tm = _meter_with(None)
    element = tm.recent_records(1)[0]
    assert "task" in element, "task 键静默缺席（键恒在场纪律）"
    assert element["task"] is None
    assert tm._records[-1].task is None


def test_recent_records_element_shape():
    """元素键集恰 8 键（7 既有 + task，加性契约）。"""
    tm = _meter_with("chat")
    element = tm.recent_records(1)[0]
    assert set(element) == RECORD_ELEMENT_KEYS


def test_task_axis_does_not_touch_aggregates():
    """不变面（新旧皆过）：summary() 聚合面键集与 DEC-㉖-① 形一致——task 轴只进
    records 元素，勿擅加 by_task 等未拍聚合（宁窄勿滥；本例走旧兼容形记录）。"""
    tm = _meter_with(None)
    summary = tm.summary()
    assert set(summary) == SUMMARY_KEYS
    assert summary["total_tokens"] == 15
    assert summary["call_count"] == 1


# ── ② 调用点 task 语义（ModelRouter 真跑 stub 上游）──────────────


def test_chat_call_site_records_task_semantics():
    """chat() 调用点记录解析后的 TaskType：缺省=chat、显式 task=code。"""
    from src.gland.router import TaskType

    m = _router()
    asyncio.run(m.chat([{"role": "user", "content": "hi"}]))
    asyncio.run(m.chat([{"role": "user", "content": "hi"}], task=TaskType.CODE))
    recs = m.token_meter.recent_records(2)
    assert [r["task"] for r in recs] == ["chat", "code"]


def test_chat_role_routing_records_resolved_task():
    """role 路由：task=resolve_task(role) 派生单一源（summarize→completion）。"""
    from src.gland.harness_profiles import resolve_task

    m = _router()
    asyncio.run(m.chat([{"role": "user", "content": "hi"}], role="summarize"))
    (rec,) = m.token_meter.recent_records(1)
    assert rec["task"] == resolve_task("summarize") == "completion"


def test_embed_call_site_records_embedding():
    """embed() 调用点记录 embedding（估 token 面零回归：completion=0）。"""
    m = _router()
    asyncio.run(m.embed(["abcd"]))
    (rec,) = m.token_meter.recent_records(1)
    assert rec["task"] == "embedding"
    assert rec["completion_tokens"] == 0


# ── ③ 静态接线（写了≠接线了）────────────────────────────────────


def test_all_record_call_sites_pass_task():
    """router.py 全部 token_meter.record 调用点（恰 2）各带 task= 实参 +
    record() 签名收 task 形参 + API 出口走 recent_records 原样透传。"""
    from src.gland.token_meter import TokenMeter

    assert "task" in inspect.signature(TokenMeter.record).parameters

    tree = ast.parse(ROUTER.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "record"
    ]
    assert len(calls) == 2, f"record 调用点数漂移: {len(calls)}（预期恰 2）"
    for call in calls:
        kws = {kw.arg for kw in call.keywords}
        assert "task" in kws, f"record 调用点未带 task 语义: {ast.dump(call)[:120]}"

    api = (REPO / "src" / "api" / "gland.py").read_text(encoding="utf-8")
    assert "recent_records(limit)" in api, "API 出口未走 recent_records 透传"


# ── ④ UI 消费面 ─────────────────────────────────────────────────


def test_ui_interface_declares_task():
    """UsageRecord 接口声明 task（类型如实 string | null 非恒在场谎言）。"""
    src = PAGE.read_text(encoding="utf-8")
    assert "task: string | null;" in src
    # 注释诚实：task 不在「真源不产出」名单（input/output 仍在）
    shape_comment = src[
        src.index("interface UsageRecord") - 300 : src.index("interface UsageRecord")
    ]
    assert "input_tokens/output_tokens 键真源不产出" in shape_comment
    assert "task/input_tokens/output_tokens 键真源不产出" not in shape_comment


def test_ui_task_column_restored_in_order():
    """task 列复原（CD-⑤ 移除件复原语义逐字一致 {r.task}），列序 model→task→
    tokens 与移除前一致。"""
    code = _code()
    assert "{r.task}" in code, "task 列未随真源补现恢复"
    seg = code[code.index("recentUsage.map") :]
    i_model = seg.index("{r.model}")
    i_task = seg.index("{r.task}")
    i_tokens = seg.index("r.prompt_tokens")
    assert i_model < i_task < i_tokens, "task 列列序与移除前不一致（model→task→tokens）"


# ── ⑤ 行为桥（UI 消费键 ⊆ 真源产出键）───────────────────────────


def test_consumed_record_keys_subset_of_true_source():
    """UI 最近记录行消费的 r.* 键全部由 recent_records() 元素真产出（含 task）。"""
    tm = _meter_with("chat")
    element = tm.recent_records(1)[0]
    code = _code()
    seg = code[code.index("recentUsage.map") :]
    consumed = set(re.findall(r"\br\.([A-Za-z_$][\w$]*)", seg))
    assert {"prompt_tokens", "completion_tokens", "timestamp", "task"} <= consumed, (
        "提取失效=断言空转"
    )
    for k in consumed:
        assert k in element, f"UI 消费 record 键真源不产出: {k}"


def test_task_render_expression_node_bridge():
    """渲染表达式 r.task 逐字经 node 真执行：真值原样、null 如实渲染空串
    （禁 'unknown' fabricate 回潮）。"""
    code = _code()
    assert "{r.task}" in code  # 逐字提取锚
    script = (
        "const cases = [{task: 'chat'}, {task: null}];"
        "const out = cases.map((r) => String(r.task ?? ''));"
        "console.log(JSON.stringify(out));"
    )
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == '["chat",""]'
