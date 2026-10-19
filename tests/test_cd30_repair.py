#!/usr/bin/env python3
"""CD-㉚ pulse 平均漂移收编真源（avg_drift_ms↔precision.avg_drift_ms 漂移）回归锁死。

背景（CD-㉒ 深挖裁决 repair_candidate「pulse 平均漂移收编真源」，CD-㉙ 留档
「剩 4 件 repair_candidate 逐件」按序首件）：
- avg_drift_ms：confirmed_drift——真源 avg_drift_ms 在 precision 嵌套内
  （src/pulse/timer.py:201-205 precision{avg_drift_ms,max_drift_ms,sample_size}）
  无平铺 avg_drift_ms；UI stats?.avg_drift_ms ? ... : '—'（pulse/page.tsx）恒回落 '—'
  =回落遮蔽真值（live 实证：precision.avg_drift_ms=59943.918 真值被 '—' 遮蔽）。
- active_signals：ruled_false_positive（本地构造回落真算活跃数，CD-㉒ 裁决）。

深挖扩面（同拍收口，CD-⑤ gland timestamp 同款先例）：
- 时间戳单位漂移：真源时间戳=epoch 秒（time.time() float，live 实证
  created_at=1792406287.12），UI new Date(x) 按毫秒解析→渲染 1970-01-22
  （node 实证）。5 消费点收编 fmtTs 单一源（×1000 恰一处禁手抄）。
- 接口类型漂移（LSP 逼出）：PulseSignal/TickRecord 时间戳声明 string 对真源
  float number=接口收编真源形（CD-⑦ 纪律）。

收口（UI 消费面，真源零改动=CD 系列 wire 权威纪律）：
- 平均漂移卡收编 stats.precision.avg_drift_ms 嵌套键；typeof number 判法
  （非 truthy）——真源真产出 0 显示 0.0ms 禁回落遮蔽（CD-⑳ 纪律），键缺席才 '—'；
- fmtTs 单一源收编 5 时间戳消费点；接口时间戳字段收编 number。

断言四层（禁自嗨恒真断言）：
①源码消费面：修前漂移形（stats?.avg_drift_ms / 裸 new Date(epoch) 消费）零残留、
  嵌套键真消费、真 0 显示形（typeof 判法）、fmtTs 单一源（×1000 恰一处+5 消费点）、
  接口键 ⊆ 真源产出键；
②行为桥：PulseEngine.get_stats/get_tick_history 真跑形状锁死（恒不产出平铺
  avg_drift_ms/active_signals——真源形状变了须复挖）+ 真 UI 表达式/真 fmtTs
  源码经 node 真执行吃真源真响应逐值对账（59943.9→"59943.9ms"、0→"0.0ms"、
  缺席→'—'；epoch 秒渲染 2026 非 1970）；
③深挖裁决证据锁：get_stats 返回字面量 precision 嵌套键集恰 3 键；
④census 真跑真仓：pulse 页 avg_drift_ms findings 归零 + stats 调用点消费键 ⊆ 真源。
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "pulse" / "page.tsx"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd30_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd30_repair"] = cdc
_spec.loader.exec_module(cdc)

# 真源 PulseEngine.get_stats 产出键（src/pulse/timer.py:195-206，live 实证一致）
STATS_KEYS = {
    "uptime_seconds",
    "total_ticks",
    "total_signals",
    "by_type",
    "by_status",
    "precision",
}
PRECISION_KEYS = {"avg_drift_ms", "max_drift_ms", "sample_size"}
TICK_KEYS = {"tick_id", "signal_id", "timestamp", "expected_at", "drift_ms", "latency_ms"}
SIGNAL_KEYS = {
    "signal_id",
    "name",
    "signal_type",
    "interval_ms",
    "status",
    "fire_count",
    "max_fires",
    "last_fired_at",
    "next_fire_at",
    "drift_correction",
    "created_at",
}


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def _code() -> str:
    """剥行注释后的真实代码（注释里的记述不算消费形）。"""
    return re.sub(r"//.*$", "", _src(), flags=re.M)


def _engine():
    from src.pulse.timer import PulseEngine

    return PulseEngine()


def _run_node(js: str) -> str:
    r = subprocess.run(
        ["node", "-e", js], capture_output=True, text=True, timeout=60, cwd=str(REPO)
    )
    assert r.returncode == 0, f"node 执行失败: {r.stderr}"
    return r.stdout.strip()


# ── ① 源码消费面 ───────────────────────────────────────────────


def test_flat_avg_drift_consumption_gone():
    """修前漂移形 stats?.avg_drift_ms / stats.avg_drift_ms 平铺消费零残留。"""
    code = _code()
    assert "stats?.avg_drift_ms" not in code, "平铺漂移键消费残留（修前形）"
    assert re.search(r"(?<![.\w])stats\.avg_drift_ms", code) is None, (
        "平铺漂移键消费残留（无 ?. 形）"
    )


def test_nested_precision_key_consumed_and_zero_not_masked():
    """嵌套键真消费+真 0 显示（typeof 判法非 truthy——真 0 不被 '—' 遮蔽，CD-⑳ 纪律）。"""
    code = _code()
    assert "stats?.precision?.avg_drift_ms" in code, "真源嵌套键未收编"
    assert "stats.precision.avg_drift_ms.toFixed" in code, "真值渲染未走真源键"
    # truthy 判法=回落遮蔽真 0，禁用
    assert "precision?.avg_drift_ms ?" not in code, "truthy 判法残留（真 0 会被 '—' 遮蔽）"
    assert "typeof stats?.precision?.avg_drift_ms === 'number'" in code, "真 0 显示判法未收编"


def test_fmt_ts_single_source_five_consumers():
    """时间戳单位收编：fmtTs 单一源（×1000 恰一处）+5 消费点全走本口禁手抄。"""
    code = _code()
    assert "const fmtTs = (epochSec: number) => new Date(epochSec * 1000)" in code, (
        "fmtTs 单一源未收编"
    )
    assert code.count("* 1000") == 1, "×1000 被手抄多处（单位换算须单一源）"
    uses = re.findall(r"fmtTs\(", code)
    assert len(uses) == 5, f"fmtTs 消费点应恰 5 处（修前 5 处 new Date），实际 {len(uses)}"
    # 修前裸 epoch 消费形零残留
    for pat in (r"new Date\(s\.", r"new Date\(selected\.", r"new Date\(t\."):
        assert re.search(pat, code) is None, f"裸 epoch new Date 消费残留: {pat}"


def test_interfaces_use_true_source_types():
    """接口收编真源形：时间戳字段=epoch 秒 float number（LSP 逼出的声明漂移）。"""
    src = _src()
    m = re.search(r"interface PulseSignal \{([^}]*)\}", src)
    assert m, "PulseSignal 接口未找到（提取失效=断言空转）"
    body = m.group(1)
    assert "last_fired_at: number | null" in body
    assert "next_fire_at: number | null" in body
    assert "created_at: number" in body
    m2 = re.search(r"interface TickRecord \{([^}]*)\}", src)
    assert m2, "TickRecord 接口未找到"
    assert "timestamp: number" in m2.group(1)


def test_interface_keys_subset_of_true_source():
    """PulseSignal 声明键 ⊆ 真源 /signals 元素产出键（禁声明假键）。"""
    m = re.search(r"interface PulseSignal \{([^}]*)\}", _src())
    assert m
    declared = set(re.findall(r"^\s+([A-Za-z_$][\w$]*):", m.group(1), re.M))
    assert declared, "接口键提取为空（提取失效=断言空转）"
    assert declared <= SIGNAL_KEYS, f"接口声明键真源不产出: {declared - SIGNAL_KEYS}"


# ── ② 行为桥（真源真跑，真源零改动） ────────────────────────────


def test_get_stats_shape_lock_never_flat_avg_drift():
    """get_stats 真跑形状锁死：顶层恰 6 键+precision 恰 3 键、恒不产出平铺
    avg_drift_ms/active_signals。真源形状变了（如补现平铺键）此桥先炸=CD-③
    「真源形状变了→裁决失效须复挖」证据锁。"""
    eng = _engine()
    sig = eng.create_signal("s1", "interval", 1000)
    eng.tick(sig.signal_id)
    resp = eng.get_stats()
    assert set(resp) == STATS_KEYS, f"真源形状变了须复挖: {sorted(resp)}"
    assert set(resp["precision"]) == PRECISION_KEYS, (
        f"precision 嵌套键漂移须复挖: {resp['precision']}"
    )
    assert "avg_drift_ms" not in resp, "平铺 avg_drift_ms 出现=真源形状变了须复挖"
    assert "active_signals" not in resp, "active_signals 出现=真源形状变了须复挖"


def test_tick_history_element_keys_lock():
    """get_tick_history 元素键形状锁死（TickRecord 消费面真源锚）。"""
    eng = _engine()
    sig = eng.create_signal("s1", "interval", 1000)
    eng.tick(sig.signal_id)
    ticks = eng.get_tick_history()
    assert ticks and set(ticks[0]) == TICK_KEYS, f"tick 元素键漂移须复挖: {sorted(ticks[0])}"
    assert isinstance(ticks[0]["timestamp"], float), "真源时间戳=epoch 秒 float"


def test_ui_card_expression_eats_real_source_response():
    """真 UI 卡表达式源码经 node 真执行吃真源真响应逐值对账：
    非零真值显示、真 0 显示 0.0ms 禁 '—' 遮蔽、precision 缺席才 '—'。"""
    line = next(l for l in _src().splitlines() if "avg_drift_ms.toFixed" in l)
    expr = line[line.index("{") + 1 : line.rindex("}")]
    assert "stats.precision.avg_drift_ms.toFixed" in expr, "表达式提取失效=断言空转"
    js = f"""
const render = (stats) => ({expr});
const eng = {{precision: {{avg_drift_ms: 59943.918, max_drift_ms: 1, sample_size: 1}}}};
const zero = {{precision: {{avg_drift_ms: 0, max_drift_ms: 0, sample_size: 3}}}};
const absent = {{total_ticks: 0}};
console.log(JSON.stringify([render(eng), render(zero), render(absent), render(null)]));
"""
    out = json.loads(_run_node(js))
    assert out[0] == "59943.9ms", f"非零真值被遮蔽: {out[0]}"
    assert out[1] == "0.0ms", f"真 0 未显示（回落遮蔽复发）: {out[1]}"
    assert out[2] == "—", f"键缺席应 '—': {out[2]}"
    assert out[3] == "—", f"stats 缺席应 '—': {out[3]}"


def test_fmt_ts_helper_eats_real_epoch_seconds():
    """真 fmtTs 源码经 node 真执行吃真源 epoch 秒：渲染 2026 非 1970（修前形反证）。"""
    eng = _engine()
    sig = eng.create_signal("s1", "interval", 1000)
    eng.tick(sig.signal_id)
    epoch = eng.get_tick_history()[0]["timestamp"]
    m = re.search(r"const fmtTs = \(epochSec: number\) => (.*?);", _src())
    assert m, "fmtTs 提取失效=断言空转"
    body = m.group(1)
    assert "* 1000" in body, "fmtTs 未做 epoch 秒→毫秒换算"
    js = f"""
const fmtTs = (epochSec) => {body};
console.log(JSON.stringify([fmtTs({epoch!r}), new Date({epoch!r}).getFullYear()]));
"""
    out = json.loads(_run_node(js))
    assert "2026" in out[0] or "2027" in out[0], f"epoch 秒被当毫秒（1970 回归）: {out[0]}"
    assert out[1] == 1970, f"修前形反证不成立（裸 new Date 应渲染 1970）: {out[1]}"


# ── ③ 深挖裁决证据锁 ───────────────────────────────────────────


def test_get_stats_precision_literal_three_keys():
    """get_stats 返回字面量 precision 嵌套键集恰 3 键（CD-㉒ 裁决锚点程序化复核）。"""
    src = (REPO / "src" / "pulse" / "timer.py").read_text(encoding="utf-8")
    seg = src[src.index("def get_stats(") :]
    m = re.search(r'"precision": \{([^}]*)\}', seg)
    assert m, "precision 嵌套块未找到（真源锚点漂移须复挖）"
    keys = set(re.findall(r'"([a-z_]+)":', m.group(1)))
    assert keys == PRECISION_KEYS, f"precision 键集漂移须复挖: {sorted(keys)}"


# ── ④ census 工具真跑真仓 ─────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def test_census_avg_drift_findings_gone(census_report):
    """修后 findings：pulse 页 avg_drift_ms 漂移 findings 全消失。"""
    findings = [f for f in census_report["findings"] if f["page"].endswith("pulse/page.tsx")]
    assert findings, "pulse 页 findings 为空（工具失效=断言空转）"
    bad = [f for f in findings if "avg_drift_ms" in f["key"]]
    assert not bad, f"avg_drift_ms 仍在 findings: {bad}"


def test_census_pulse_stats_callsite_keys_subset(census_report):
    """pulse stats 调用点消费键 ⊆ 真源产出键（含嵌套精度键）。

    active_signals=CD-㉒ ruled_false_positive（本地构造回落真算活跃数非缺陷），
    显式点名放行且锁死其必须走回落形（真源键在场消费=漂移复发须复挖）。
    """
    hits = [
        f
        for f in census_report["findings"]
        if f["page"].endswith("pulse/page.tsx") and "stats" in f.get("handler", "")
    ]
    consumed = {k for f in hits for k in f["key"] if "." not in k}
    flat_source = STATS_KEYS | PRECISION_KEYS
    assert consumed - {"active_signals"} <= flat_source, (
        f"stats 调用点消费键真源不产出: {consumed - {'active_signals'} - flat_source}"
    )
    if "active_signals" in consumed:
        assert "stats?.active_signals ?? signals.filter" in _code(), (
            "active_signals 消费点必须走本地构造回落形（CD-㉒ 裁决前提）"
        )
