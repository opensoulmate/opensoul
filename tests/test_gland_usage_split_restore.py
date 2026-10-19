#!/usr/bin/env python3
"""DEC-㉖-① gland 用量显示拆分恢复——真源补现 入/出 token 拆分（B1）回归锁死。

背景（pending-decisions ㉖，CD-⑤ 诚实边界②）：TokenMeter.summary() 只聚合
total_tokens（入/出未拆）、by_provider 元素=int，CD-⑤ 按 wire 权威纪律把
「入: x / 出: y」拆分行、by_provider 入/出/请求拆分行当死显示面移除（修前恒
0/undefined 假渲染）。本拍按 DEC-①~㉕ 自决先例取 B1（入/出两轴，token_meter
内部改）先行执行（异议回「㉖改A」=revert）：真源补现两轴+元素升对象，UI 拆分
行随真源恢复（㉕ 同款正向恢复非新增发明）。

范围诚实边界：B2=recent_records 元素补 task 轴（record() 调用点带 task 语义=
跨 gland 调用点大件）仍待拍——task 列维持禁渲染（test_cd5 锁维持）。

断言三层（禁自嗨恒真断言）：
①真源形状：summary() 两轴真产出、by_provider 元素恰 {prompt,completion,total,
  requests}、逐 provider 与顶层两轴求和自洽（prompt+completion==total）；
②UI 消费面：恢复行真在场且消费真源键、非真源键（input/output、task）零残留；
③行为桥：UI 消费的每个键由真 producer 真产出（TokenMeter 实跑逐键对账）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "gland" / "page.tsx"

BY_PROVIDER_ELEMENT_KEYS = {"prompt", "completion", "total", "requests"}


def _code() -> str:
    """去掉行注释后的真实消费代码（注释里的历史记述不算消费形）。"""
    return re.sub(r"//.*$", "", PAGE.read_text(encoding="utf-8"), flags=re.M)


# ── ① 真源形状（TokenMeter 实跑）────────────────────────────────


def test_summary_has_split_axes():
    """summary() 加 total_prompt_tokens/total_completion_tokens 两轴真产出。"""
    from src.gland.token_meter import TokenMeter

    tm = TokenMeter()
    tm.record("prov-a", "m", 10, 5)
    tm.record("prov-a", "m", 3, 2)
    summary = tm.summary()
    assert summary["total_prompt_tokens"] == 13
    assert summary["total_completion_tokens"] == 7
    assert summary["total_tokens"] == 20
    # 两轴自洽：入+出==总量（不许三值互斥漂移）
    assert (
        summary["total_prompt_tokens"] + summary["total_completion_tokens"]
        == summary["total_tokens"]
    )


def test_by_provider_element_shape():
    """by_provider 元素恰 {prompt,completion,total,requests} 对象（㉖ B 指定形）。"""
    from src.gland.token_meter import TokenMeter

    tm = TokenMeter()
    tm.record("prov-a", "m", 10, 5)
    element = tm.summary()["by_provider"]["prov-a"]
    assert set(element) == BY_PROVIDER_ELEMENT_KEYS
    assert element == {"prompt": 10, "completion": 5, "total": 15, "requests": 1}


def test_by_provider_multi_call_accumulation():
    """多调用跨 model 累加：两轴/total/requests 逐键精确。"""
    from src.gland.token_meter import TokenMeter

    tm = TokenMeter()
    tm.record("prov-a", "m1", 10, 5)
    tm.record("prov-a", "m2", 3, 2)
    tm.record("prov-b", "m1", 1, 1)
    by = tm.summary()["by_provider"]
    assert by["prov-a"] == {"prompt": 13, "completion": 7, "total": 20, "requests": 2}
    assert by["prov-b"] == {"prompt": 1, "completion": 1, "total": 2, "requests": 1}


def test_by_provider_is_snapshot_not_live_ref():
    """usage_by_provider() 返回快照（就地改返回值不污染真源，dict 拷贝纪律）。"""
    from src.gland.token_meter import TokenMeter

    tm = TokenMeter()
    tm.record("prov-a", "m", 10, 5)
    out = tm.usage_by_provider()
    out["prov-a"]["prompt"] = 999
    out["new"] = {}
    assert tm.summary()["by_provider"]["prov-a"]["prompt"] == 10
    assert "new" not in tm.summary()["by_provider"]


def test_zero_usage_split_axes_zero():
    """零调用时两轴=0（不 fabricate 非零拆分）。"""
    from src.gland.token_meter import TokenMeter

    summary = TokenMeter().summary()
    assert summary["total_prompt_tokens"] == 0
    assert summary["total_completion_tokens"] == 0
    assert summary["by_provider"] == {}


# ── ② UI 消费面（恢复行真在场+真源键消费+非真源键零残留）────────────


def test_ui_restores_top_level_split_line():
    """「入: x / 出: y」拆分行随真源恢复（CD-⑤ 移除件复原，消费真源两轴键）。"""
    code = _code()
    assert "入: {formatNumber(usage?.total_prompt_tokens || 0)}" in code
    assert "出: {formatNumber(usage?.total_completion_tokens || 0)}" in code


def test_ui_restores_by_provider_split_lines():
    """by_provider 入/出/请求拆分行随真源恢复（消费元素真源键）。"""
    code = _code()
    for expr in (
        "data.prompt",
        "data.completion",
        "data.requests",
        "data.total",
        "入: {formatNumber(data.prompt || 0)}",
        "出: {formatNumber(data.completion || 0)}",
    ):
        assert expr in code, f"by_provider 拆分行真源键未被消费: {expr}"


def test_ui_no_fabricated_keys():
    """非真源键零残留：input/output（元素旧漂移形）、task 列（B2 待拍）。"""
    code = _code()
    for pat in (r"\bdata\.input\b", r"\bdata\.output\b", r"\bd\.input\b", r"\bd\.output\b"):
        assert re.search(pat, code) is None, f"非真源元素键残留: {pat}"
    assert "{r.task}" not in code, "task 列残留（真源不产出，B2 待拍）"
    # 顶层旧漂移键不复活（㉕/CD-⑤ 纪律）
    for key in ("total_input_tokens", "total_output_tokens", "total_requests", "budget_used_pct"):
        assert re.search(rf"\busage\??\.{key}\b", code) is None, f"summary 旧漂移键复活: {key}"


def test_ui_interface_declares_true_shape():
    """UsageSummary 接口声明真源形（两轴+元素对象），禁发明键。"""
    src = PAGE.read_text(encoding="utf-8")
    assert "total_prompt_tokens: number;" in src
    assert "total_completion_tokens: number;" in src
    assert (
        "by_provider: Record<string, { prompt: number; completion: number; total: number; requests: number }>;"
        in src
    )


# ── ③ 行为桥（UI 消费键 ⊆ 真源产出键，TokenMeter 实跑）──────────────


def test_consumed_summary_keys_subset_of_true_source():
    """UI 消费的 usage.* 键全部由 summary() 真产出。"""
    from src.gland.token_meter import TokenMeter

    tm = TokenMeter(budget_limit=100)
    tm.record("prov-a", "m", 1, 1)
    summary = tm.summary()
    consumed = set(re.findall(r"\busage\??\.([A-Za-z_$][\w$]*)", _code()))
    assert consumed, "提取失效=断言空转"
    for k in consumed:
        assert k in summary, f"UI 消费 summary 键真源不产出: {k}"


def test_consumed_element_keys_subset_of_true_source():
    """UI 消费的 by_provider 元素键（data.*/d.*）全部由元素真形产出。

    提取域收窄到 by_provider map 块（data 亦是测试回执段变量名，全域提取会误吃
    data.model 等回执键——测试作者自纠）。
    """
    from src.gland.token_meter import TokenMeter

    tm = TokenMeter()
    tm.record("prov-a", "m", 1, 1)
    element = tm.summary()["by_provider"]["prov-a"]
    code = _code()
    seg = code[code.index("Object.entries(usage?.by_provider") : code.index("Recent Usage Records")]
    consumed = set(re.findall(r"\bdata\.([A-Za-z_$][\w$]*)", seg)) | set(
        re.findall(r"\bd\.([A-Za-z_$][\w$]*)", seg)
    )
    assert consumed, "提取失效=断言空转"
    for k in consumed:
        assert k in element, f"UI 消费元素键真源不产出: {k}"
    assert BY_PROVIDER_ELEMENT_KEYS <= consumed, "拆分行消费面提取不全=断言空转"


def test_summary_aggregate_matches_elements():
    """顶层两轴==逐 provider 两轴求和（两处聚合同源不漂移）。"""
    from src.gland.token_meter import TokenMeter

    tm = TokenMeter()
    tm.record("prov-a", "m", 10, 5)
    tm.record("prov-b", "m2", 3, 2)
    summary = tm.summary()
    by = summary["by_provider"]
    assert sum(e["prompt"] for e in by.values()) == summary["total_prompt_tokens"]
    assert sum(e["completion"] for e in by.values()) == summary["total_completion_tokens"]
    assert sum(e["total"] for e in by.values()) == summary["total_tokens"]
