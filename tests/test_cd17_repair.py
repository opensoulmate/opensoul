#!/usr/bin/env python3
"""CD-⑰ 系统初始化页 bootstrap status 消费键收编（UI 收编真源，真源零改动）回归锁死。

背景（CD-⑯ drift_suspect 18 条裁决真断点 3 条=修复候选「system bootstrap status
消费键收编」恰 1 件）：GET /api/system/bootstrap/status 真源返回
{status, component, **bootstrap.state}（system_overview.py:331-337），state 恰 4 键
{bootstrapped, bootstrapped_at, version, items}（bootstrap.py:78-84）→ 端点恰 6 键；
而 UI BootstrapStatus 声明+消费 initialized/steps 两键全层缺席（live 回读硬证：
initialized 与 steps 双缺席、bootstrapped=True 而 UI 恒走 uninitialized 分支=已初始化
系统也显「未初始化」假渲染；steps 步骤清单块 :464-478 永不渲染死显示面）。

收口（UI 消费面收编真源，真源零改动=CD 系列 wire 权威纪律，CD-④/⑮ 死显示面收口
先例；CD-⑯「steps 死显示面整块处置禁只修点名 3 键」）：
- initialized → bootstrapped（真源键，假「未初始化」渲染收口）；
- steps 块整体重编到真源 items（Record<string, bool>：键=步骤名、值=成功布尔→
  ok/error 两态；真源无 message/三态 status→message 行与 Clock 待定态随死显示清零）；
- BootstrapStatus 接口收编真源形（声明键 ⊆ 真源 6 键，CD-⑦ 死声明键清零纪律）。

断言四层（禁自嗨恒真断言）：
①源码消费面：initialized/steps/step.message 死键双清零 + bootstrapped/items 真消费 +
  接口声明键 ⊆ 真源；
②行为桥：bootstrap_status 真跑恰 6 键形状锁死恒不产出 initialized/steps（真源形状
  变了此桥先炸=CD-③「真源形状变了→裁决失效须复挖」证据锁）+ items dict[str,bool]；
③真源锚点：state 恰 4 键 + 端点 **bootstrap.state 组合单一源 + items 注解锁；
④census 真跑真仓：bootstrap status 调用点消费键收窄到真源键、死键零残留。
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "system" / "page.tsx"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd17_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd17_repair"] = cdc
_spec.loader.exec_module(cdc)

# 真源 bootstrap_status 产出键（system_overview.py:331-337，live 实证一致）
TRUE_SOURCE_KEYS = ("status", "component", "bootstrapped", "bootstrapped_at", "version", "items")
# SystemBootstrap.state 属性产出键（bootstrap.py:78-84）
STATE_KEYS = ("bootstrapped", "bootstrapped_at", "version", "items")
# 修前死显示键（CD-⑯ 裁决 3 条真断点+扩面死键）
DEAD_KEYS = ("initialized", "steps")


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def _code() -> str:
    """剥行注释后的真实代码（历史记述里的键名不算消费形）。"""
    return re.sub(r"//.*$", "", _src(), flags=re.M)


def _bootstrap_status() -> dict:
    from src.api import system_overview

    return asyncio.run(system_overview.bootstrap_status())


# ── ① 源码消费面 ───────────────────────────────────────────────


def test_dead_keys_zero_residue():
    """initialized/steps 死键声明/消费双清零（CD-⑦ 纪律）。

    'uninitialized' 状态标签（未初始化徽章语义）不属死键——词边界判定
    'un' 前缀不命中。
    """
    code = _code()
    assert not re.search(r"(?<![A-Za-z_])initialized(?![A-Za-z_])", code), (
        "initialized 死键残留（消费形或接口声明；'uninitialized' 标签合法）"
    )
    assert not re.search(r"\bsteps\b", code), "steps 死键残留（消费形或接口声明）"
    assert not re.search(r"\bstep\b", code), "step 死键残留（步骤元素消费形）"


def test_dead_message_key_removed():
    """steps[].message 死显示行随整块处置清零（真源 items 无 message）。"""
    code = _code()
    assert "step.message" not in code, "step.message 死显示行残留"
    m = re.search(r"interface BootstrapStatus \{([^}]*)\}", _src())
    assert m, "BootstrapStatus 接口未找到（提取失效=断言空转）"
    assert "message" not in m.group(1), "接口死声明键 message 残留"


def test_bootstrapped_and_items_truly_consumed():
    """bootstrapped/items 真消费在场（修后消费面=真源键）。"""
    code = _code()
    assert "bootstrap.bootstrapped" in code, "bootstrapped 未被消费（真源键收编缺失）"
    assert "Object.entries(bootstrap.items)" in code, "items 未被整块消费（步骤块未重编到真源）"
    assert "bootstrap?.items" in code, "items 渲染门缺失（步骤块整块处置不完整）"


def test_bootstrap_status_interface_subset_of_true_source():
    """BootstrapStatus 声明键 ⊆ 真源 bootstrap_status 产出键（禁声明假键）。"""
    m = re.search(r"interface BootstrapStatus \{([^}]*)\}", _src())
    assert m, "BootstrapStatus 接口未找到（提取失效=断言空转）"
    declared = set(re.findall(r"^\s+([A-Za-z_$][\w$]*):", m.group(1), re.M))
    assert declared, "接口键提取为空（提取失效=断言空转）"
    resp = _bootstrap_status()
    for k in declared:
        assert k in resp, f"接口声明键真源不产出: {k}"
    assert "bootstrapped" in declared and "items" in declared, (
        f"接口未收编真源核心键（声明键={declared}）"
    )


# ── ② 行为桥（真源实跑，真源零改动） ────────────────────────────


def test_bootstrap_status_shape_lock():
    """bootstrap_status 真跑形状锁死：恰 6 键、恒不产出 initialized/steps。

    未来真源形状变了（如补现 steps）此桥先炸=「真源形状变了须复挖」证据锁。
    """
    resp = _bootstrap_status()
    assert set(resp) == set(TRUE_SOURCE_KEYS), f"真源形状变了须复挖: {sorted(resp)}"
    for dead in DEAD_KEYS:
        assert dead not in resp, f"{dead} 出现=真源形状变了须复挖"


def test_items_shape_dict_str_bool():
    """items 真源形=dict[str, bool]（UI Object.entries 两态渲染的契约依据）。"""
    resp = _bootstrap_status()
    items = resp["items"]
    assert isinstance(items, dict), f"items 非 dict: {type(items)}"
    for k, v in items.items():
        assert isinstance(k, str) and isinstance(v, bool), f"items 元素形漂移: {k!r}={v!r}"


def test_state_property_four_keys():
    """SystemBootstrap.state 属性恰 4 键（端点组合的唯一内层源）。"""
    from src.api import system_overview

    state = system_overview.bootstrap.state
    assert set(state) == set(STATE_KEYS), f"state 形状变了须复挖: {sorted(state)}"


# ── ③ 真源锚点（源码级，禁无据断言） ────────────────────────────


def test_endpoint_unpacks_state_single_source():
    """端点经 **bootstrap.state 组合（声明从实现派生：6 键=2 字面+4 state）。"""
    src = (REPO / "src" / "api" / "system_overview.py").read_text(encoding="utf-8")
    m = re.search(r"async def bootstrap_status\(\).*?return \{(.*?)\}", src, re.S)
    assert m, "bootstrap_status 端点未找到（提取失效=断言空转）"
    body = m.group(1)
    assert "**bootstrap.state" in body, "端点不再经 state 组合（单一源漂移须复挖）"
    assert '"status"' in body and '"component"' in body, "端点字面键漂移须复挖"


def test_items_annotation_locked():
    """items 注解=dict[str, bool]（UI 两态渲染契约的真源锚点）。"""
    src = (REPO / "src" / "system" / "bootstrap.py").read_text(encoding="utf-8")
    assert "items: dict[str, bool]" in src, "items 注解漂移须复挖（UI 两态渲染契约失效）"


# ── ④ census 工具真跑真仓 ─────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def test_census_bootstrap_dead_keys_gone(census_report):
    """修后 findings：initialized/steps 从 system 页 findings 全消失。"""
    findings = [f for f in census_report["findings"] if f["page"].endswith("system/page.tsx")]
    assert findings, "system 页 findings 为空（工具失效=断言空转）"
    keys = {k for f in findings for k in f["key"]}
    for dead in DEAD_KEYS:
        assert dead not in keys, f"{dead} 仍在 findings: {keys}"


def test_census_bootstrap_consumed_keys_subset_of_true_source(census_report):
    """bootstrap status 调用点消费键 ⊆ 真源 6 键（修前 initialized/steps 真断点收口）。

    提取层对 **bootstrap.state 动态形状如实报 drift_suspect（CD-⑯ 15 条
    resolved_ok 同类提取边界自述）——键级由②行为桥定型，此处锁消费键不出真源集。
    """
    hits = [
        f
        for f in census_report["findings"]
        if f["page"].endswith("system/page.tsx") and f["path"] == "/api/system/bootstrap/status"
    ]
    assert hits, "bootstrap status 调用点未被 census 提取（提取失效=断言空转）"
    consumed = {k for f in hits for k in f["key"]}
    assert consumed == {"bootstrapped", "items"}, f"消费键集漂移: {consumed}"
    assert consumed <= set(TRUE_SOURCE_KEYS), (
        f"消费键真源不产出: {consumed - set(TRUE_SOURCE_KEYS)}"
    )
