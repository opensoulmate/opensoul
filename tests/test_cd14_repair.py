#!/usr/bin/env python3
"""CD-⑭ 修复候选④——soma discovery/scan 重扫方法漂移收口（UI 调用方法收编真源）。

背景（CD-⑩ 深挖裁决 confirmed_drift 端点级）：
admin-ui/app/soma/page.tsx handleRescan 的 apiFetch 调用点声明 method:'POST'
（「Force rescan via POST」），而真源路由是 @router.get("/scan", response_model=ScanResult)
（src/api/soma_discovery.py api_full_scan）——CD-⑨ live 双证 POST 恒 405 空转，
「重新扫描」按钮靠 catch 兜底（"Fallback: just re-fetch"→fetchScan GET）才真刷新。
CD-⑩ 勘误：兜底 GET 非假功能（api_full_scan 每次全量扫描=真重扫），真断点=POST
调用恒 405 空转（方法漂移）。

收口（UI 消费面收编真源方法，真源零改动=CD 系列 wire 权威纪律）：
- 重扫调用点去 { method: 'POST' }（apiFetch 缺省 GET=真源路由方法）；
- 405 漂移补偿路径（catch 兜底 fetchScan）随根因一并移除——重扫直达真源 GET 即
  真重扫，错误可见（setError(e.message) 与 fetchScan 同款语义）；
- 路由方法/响应键零改动。

断言三层（禁自嗨恒真断言）：
①源码消费面：调用点缺省 GET 形 + 修前 POST 漂移形/兜底补偿形零残留；
②行为桥：调用点方法（缺省 GET）== 路由装饰器方法（方法桥=漂移复发必炸的结构性
   根因锁）+ UI 回放（GET）真跑真源 200 五键全在 + POST 405 真源零改动 + 消费键
   ⊆ ScanResult 真源字段；
③census 工具真跑真仓：discovery/scan 调用点 produced + soma unresolved_endpoint 归零。
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "soma" / "page.tsx"
API = REPO / "src" / "api" / "soma_discovery.py"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd14_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd14_repair"] = cdc
_spec.loader.exec_module(cdc)

# ScanResult 真源字段（src/api/soma_discovery.py，形状锁死=UI 契约所依赖的键集）
SCANRESULT_KEYS = {"scan_time", "processes", "cli_tools", "services", "adapters"}


def _ui() -> str:
    return PAGE.read_text(encoding="utf-8")


def _code() -> str:
    """剥行注释后的真实代码（历史记述里的调用形不算消费形）。"""
    return re.sub(r"//.*$", "", _ui(), flags=re.M)


def _api_src() -> str:
    return API.read_text(encoding="utf-8")


def _scan_callsite_lines() -> list[str]:
    lines = [
        l for l in _code().splitlines() if "apiFetch(" in l and "/api/soma/discovery/scan" in l
    ]
    assert len(lines) == 2, f"discovery/scan 调用点提取异常（提取失效=断言空转）: {lines}"
    return lines


def _callsite_methods() -> list[str]:
    """调用点声明方法（apiFetch 缺省 GET=不带 method 参数）。"""
    out = []
    for line in _scan_callsite_lines():
        m = re.search(r"method:\s*['\"]([A-Z]+)['\"]", line)
        out.append(m.group(1) if m else "GET")
    return out


def _route_method() -> str:
    m = re.search(r'@router\.(get|post|put|patch|delete)\(\s*"/scan"', _api_src())
    assert m, "路由装饰器提取异常（提取失效=断言空转）"
    return m.group(1).upper()


def _scanresult_fields() -> set[str]:
    tree = ast.parse(_api_src())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ScanResult":
            return {
                n.target.id
                for n in node.body
                if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
            }
    raise AssertionError("ScanResult 字段提取失败（提取失效=断言空转）")


# ── ① 源码消费面 ───────────────────────────────────────────────


def test_callsite_uses_default_get():
    """修后调用形：apiFetch 缺省 GET（不带 method 参数）=收编真源路由方法。"""
    assert _callsite_methods() == ["GET", "GET"]


def test_post_drift_form_gone():
    """修前 POST 漂移形零残留。"""
    code = _code()
    assert "method: 'POST'" not in code, "修前 method:'POST' 漂移形残留"
    assert "apiFetch('/api/soma/discovery/scan', { method: 'POST' })" not in code
    assert "Force rescan via POST" not in _ui()


def test_fallback_compensation_gone():
    """405 漂移补偿路径（catch 兜底 fetchScan）零残留——重扫直达真源 GET。"""
    assert "Fallback: just re-fetch" not in _ui(), "405 漂移补偿兜底残留"
    assert "await fetchScan();" not in _code().split("const handleRescan")[1].split("};")[0], (
        "handleRescan 内仍挂兜底重取（漂移补偿路径残留）"
    )


# ── ② 行为桥（真源真跑，真源零改动） ────────────────────────────


def test_callsite_method_matches_route_method():
    """方法桥（结构性根因锁）：调用点声明方法 == 路由装饰器方法——任一侧单方面
    改方法（漂移复发）此桥先炸，正是修前缺的那把锁。"""
    route = _route_method()
    assert route == "GET"
    for m in _callsite_methods():
        assert m == route


def test_ui_replay_get_returns_scanresult_keys(client):
    """UI 回放：按修后调用形（缺省 GET）真打真源路由——200 且五键全在场
    （修前重扫 POST 此形 405 空转）。"""
    resp = client.get("/api/soma/discovery/scan")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == SCANRESULT_KEYS
    assert isinstance(data["scan_time"], (int, float))
    for key in ("processes", "cli_tools", "services", "adapters"):
        assert isinstance(data[key], list), f"{key} 真源非列表: {type(data[key])}"


def test_post_still_405_route_truth_unchanged(client):
    """真源零改动：POST 仍 405（漂移修在 UI 侧=CD 系列 wire 权威纪律，
    禁反向改真源迁就消费面）。"""
    resp = client.post("/api/soma/discovery/scan")
    assert resp.status_code == 405


def test_consumed_keys_subset_of_scanresult_fields():
    """UI 消费键 ⊆ ScanResult 真源字段（禁消费真源不产出的键）。"""
    consumed = set(re.findall(r"\bdata\.([A-Za-z_$][\w$]*)", _code()))
    assert consumed, "data 消费键未提取到（提取失效=断言空转）"
    assert consumed <= _scanresult_fields(), f"消费键真源不产出: {consumed - _scanresult_fields()}"
    assert consumed == SCANRESULT_KEYS, f"五键消费面缺项: {SCANRESULT_KEYS - consumed}"


# ── ③ census 工具真跑真仓 ─────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def test_census_scan_callsite_produced(census_report):
    """/api/soma/discovery/scan 顶层键调用点 verdict=produced（方法收编后端点解析
    成功、消费键在真源产出）。嵌套元素键（processes.cpu 等）=census 嵌套形状
    提取既有边界 unresolved_shape，非端点断（本拍诚实边界，不在此锁）。"""
    hits = [
        f
        for f in census_report["findings"]
        if f["page"].endswith("soma/page.tsx") and "discovery/scan" in (f.get("path") or "")
    ]
    assert hits, "discovery/scan 调用点未被 census 提取（提取失效=断言空转）"
    top = [f for f in hits if len(f["key"]) == 1]
    assert top, "顶层键 findings 缺席（提取失效=断言空转）"
    for f in top:
        assert f["verdict"] == "produced", f"discovery/scan 顶层键非 produced: {f}"
    for f in hits:
        assert f["verdict"] != "unresolved_endpoint", f"端点解析仍断: {f}"


def test_census_soma_unresolved_endpoint_gone(census_report):
    """soma 页 unresolved_endpoint 归零（修前 10 键=POST 调用点端点解析失败）。"""
    bad = [
        f
        for f in census_report["findings"]
        if f["page"].endswith("soma/page.tsx") and f["verdict"] == "unresolved_endpoint"
    ]
    assert not bad, f"soma 页端点解析仍断: {bad}"
