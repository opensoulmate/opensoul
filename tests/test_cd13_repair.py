#!/usr/bin/env python3
"""CD-⑬ 修复候选③——hippo decay/simulate 方法漂移修复（UI 调用方法收编真源）。

背景（CD-⑩ 深挖裁决 confirmed_drift，键级+端点级双条同根）：
admin-ui/app/hippo/page.tsx handleSimulate 的 apiFetch 调用点不带 method 参数
（apiFetch 缺省 GET），而真源路由是 @router.post("/decay/simulate")
（src/api/hippo.py simulate_decay）——CD-⑨ live 双证 GET 恒 405，「模拟衰减」
按钮点击必进 catch（setError），simResult 永不置位 → 模拟结果面板（simResult &&
渲染 retention/should_archive/should_forget/strategy）永不出现=消费路径死。
键级证据：四键真源全在场（simulate_decay 返回 dict），缺陷在方法不在键。

收口（UI 消费面收编真源方法，真源零改动=CD 系列 wire 权威纪律）：
- 调用点补 { method: 'POST' }（与真源路由方法对齐）；路由方法/响应键零改动。

断言两层（禁自嗨恒真断言）：
①源码消费面：调用点声明 POST + 修前缺省 GET 形零残留；
②行为桥：调用点方法 == 路由装饰器方法（方法桥=漂移复发必炸的结构性根因锁）+
   UI 回放（POST + simForm 三查询参）真跑真源 200 面板键全在 + GET 405 真源
   零改动 + 消费键 ⊆ simulate_decay 返回键；
③census 工具真跑真仓：decay/simulate 调用点 produced（unresolved_endpoint 解析）。
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "hippo" / "page.tsx"
API = REPO / "src" / "api" / "hippo.py"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd13_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd13_repair"] = cdc
_spec.loader.exec_module(cdc)

# 模拟结果面板消费键（page.tsx simResult && 面板；CD-⑩ 键级裁决四键真源全在场）
PANEL_KEYS = ("retention", "should_archive", "should_forget", "strategy")
# simulate_decay 返回 dict 全键（src/api/hippo.py，形状锁死）
RETURN_KEYS = {
    "retention",
    "should_archive",
    "should_forget",
    "strategy",
    "simulated_age_hours",
    "importance",
    "access_count",
}


def _ui() -> str:
    return PAGE.read_text(encoding="utf-8")


def _code() -> str:
    """剥行注释后的真实代码（历史记述里的调用形不算消费形）。"""
    return re.sub(r"//.*$", "", _ui(), flags=re.M)


def _api_src() -> str:
    return API.read_text(encoding="utf-8")


def _callsite_line() -> str:
    lines = [
        l for l in _code().splitlines() if "apiFetch(" in l and "/api/hippo/decay/simulate" in l
    ]
    assert len(lines) == 1, f"decay/simulate 调用点提取异常（提取失效=断言空转）: {lines}"
    return lines[0]


def _callsite_method() -> str:
    line = _callsite_line()
    m = re.search(r"method:\s*['\"]([A-Z]+)['\"]", line)
    assert m, f"调用点未声明 method（apiFetch 缺省 GET=修前漂移形）: {line}"
    return m.group(1)


def _route_method() -> str:
    m = re.search(r'@router\.(get|post|put|patch|delete)\(\s*"/decay/simulate"', _api_src())
    assert m, "路由装饰器提取异常（提取失效=断言空转）"
    return m.group(1).upper()


def _return_dict_keys() -> set[str]:
    src = _api_src()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "simulate_decay":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                    return {
                        k.value
                        for k in sub.value.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    }
    raise AssertionError("simulate_decay 返回 dict 提取失败（提取失效=断言空转）")


# ── ① 源码消费面 ───────────────────────────────────────────────


def test_callsite_declares_post_method():
    """修后调用形：apiFetch 带 method:'POST'（收编真源路由方法）。"""
    assert _callsite_method() == "POST"


def test_default_get_form_gone():
    """修前缺省 GET 形（无 method 参数的裸调用）零残留。"""
    code = _code()
    assert not re.search(r"apiFetch\(\s*`/api/hippo/decay/simulate\?[^`]*`\s*\)", code), (
        "缺省 GET 裸调用形残留"
    )


# ── ② 行为桥（真源真跑，真源零改动） ────────────────────────────


def test_callsite_method_matches_route_method():
    """方法桥（结构性根因锁）：调用点声明方法 == 路由装饰器方法——任一侧单方面
    改方法（漂移复发）此桥先炸，正是修前缺的那把锁。"""
    assert _callsite_method() == _route_method()


def test_panel_consumption_subset_of_return_keys():
    """面板消费键 ⊆ simulate_decay 真源返回键（禁消费真源不产出的键）。"""
    consumed = set(re.findall(r"\bsimResult\.([A-Za-z_$][\w$]*)", _code()))
    assert consumed, "simResult 消费键未提取到（提取失效=断言空转）"
    assert consumed <= _return_dict_keys(), (
        f"面板消费键真源不产出: {consumed - _return_dict_keys()}"
    )
    assert set(PANEL_KEYS) <= consumed, f"面板四键消费缺席: {set(PANEL_KEYS) - consumed}"


def test_ui_replay_post_returns_panel_keys(will_client):
    """UI 回放：按修后调用形（POST + simForm 三查询参）真打真源路由——200 且
    面板四键全在场（修前此形 405，模拟面板永不渲染）。"""
    resp = will_client.post(
        "/api/hippo/decay/simulate",
        params={"age_hours": "24", "importance": "0.5", "access_count": "0"},
    )
    assert resp.status_code == 200
    data = resp.json()
    for key in PANEL_KEYS:
        assert key in data, f"面板消费键真源不产出: {key}"
    # 参数真透传（UI 三输入框 → 响应回显键）
    assert data["simulated_age_hours"] == 24.0
    assert data["importance"] == 0.5
    assert data["access_count"] == 0


def test_get_still_405_route_truth_unchanged(will_client):
    """真源零改动：GET 仍 405（漂移修在 UI 侧=CD 系列 wire 权威纪律，
    禁反向改真源迁就消费面）。"""
    resp = will_client.get("/api/hippo/decay/simulate")
    assert resp.status_code == 405


def test_return_shape_locked(will_client):
    """simulate_decay 返回形状锁死（UI 契约所依赖的键集）。"""
    resp = will_client.post("/api/hippo/decay/simulate")
    assert resp.status_code == 200
    assert set(resp.json()) == RETURN_KEYS


# ── ③ census 工具真跑真仓 ─────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def test_census_decay_simulate_callsite_produced(census_report):
    """/api/hippo/decay/simulate 调用点 verdict=produced（方法收编后端点解析成功、
    消费键全在真源产出）。"""
    hits = [
        f
        for f in census_report["findings"]
        if f["page"].endswith("hippo/page.tsx") and "decay/simulate" in (f.get("path") or "")
    ]
    assert hits, "decay/simulate 调用点未被 census 提取（提取失效=断言空转）"
    for f in hits:
        assert f["verdict"] == "produced", f"decay/simulate 调用点非 produced: {f}"


def test_census_hippo_unresolved_endpoint_gone(census_report):
    """hippo 页 unresolved_endpoint 归零（修前 (retention,) 一条=消费路径死）。"""
    bad = [
        f
        for f in census_report["findings"]
        if f["page"].endswith("hippo/page.tsx") and f["verdict"] == "unresolved_endpoint"
    ]
    assert not bad, f"hippo 页端点解析仍断: {bad}"
