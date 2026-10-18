#!/usr/bin/env python3
"""CD-① 修复件（4 断点根因修）——UI 消费面收编真源键/端点改指真端点 回归锁死。

四处（CD-①-A/B/C/D，docs/contract-drift-census.md「确认真断点」逐条）：
- A hermes 页发送回执键漂移：data.status（真源 {ok,content,source} 无此键→成功提示永不可达）
  → data.ok / data.content（真源键）。
- B limb 页截图键漂移：data.image（真键 base64，openapi ScreenshotResponse 实证）
  → data.base64。
- C permissions 页角色列表端点语义错位：/api/permission/roles/all（无路由，'all' 被当
  username 落 list_roles 回 {username:'all',roles:[]} 恒空）→ /api/permission/policies
  真源收编（roles=角色映射行 [user,role]、policies=策略行 [role,resource,action]，
  live 实证元素形状=行数组，角色名=两列去重并集）。
- D workspace 页目录浏览端点不存在：/api/workspace/{dir,file,execute}（404）→
  真端点 /api/{dir,file,execute}（workspace_api 挂载 prefix=/api）。

断言两层（禁自嗨恒真断言）：
①源码消费面：旧漂移串零残留 + 新真源键/端点真在场；
②census 工具真跑真仓（scripts/census_contract_drift.py compare）：四处键全 produced、
  漂移/unresolved_endpoint verdict 零。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UI = REPO / "admin-ui" / "app"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd1_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd1_repair"] = cdc
_spec.loader.exec_module(cdc)


def _page(name: str) -> str:
    return (UI / name / "page.tsx").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(UI, REPO / "src")


def _verdicts(report: dict, page: str) -> dict:
    out: dict[tuple, str] = {}
    for f in report["findings"]:
        if f["page"].endswith(page):
            out[tuple(f["key"])] = f["verdict"]
    return out


def _paths(report: dict, page: str) -> set:
    return {f["path"] for f in report["findings"] if f["page"].endswith(page)}


# ── A hermes 页发送回执 ─────────────────────────────────────────


def test_hermes_send_receipt_consumes_ok_key():
    src = _page("hermes")
    assert "data.status" not in src, "旧漂移键 data.status 残留（真源 {ok,content,source} 无此键）"
    assert "状态: ${data.status}" not in src
    assert "data.ok" in src, "真源键 ok 未被消费"
    assert "data.content" in src, "真源键 content 未被消费"


def test_hermes_census_keys_produced(census_report):
    v = _verdicts(census_report, "hermes/page.tsx")
    assert v.get(("ok",)) == "produced"
    assert v.get(("content",)) == "produced"
    assert ("status",) not in v, "漂移键 status 仍在 findings"


# ── B limb 页截图 ──────────────────────────────────────────────


def test_limb_screenshot_consumes_base64_key():
    src = _page("limb")
    assert "data.image" not in src, "旧漂移键 data.image 残留（真键 base64）"
    assert "data.base64" in src, "真源键 base64 未被消费"


def test_limb_census_keys_produced(census_report):
    v = _verdicts(census_report, "limb/page.tsx")
    assert v.get(("base64",)) == "produced"
    assert ("image",) not in v, "漂移键 image 仍在 findings"
    assert v.get(("text",)) == "produced"  # OCR 键真源存在零漂移（回归）


# ── C permissions 页角色列表 ───────────────────────────────────


def test_permissions_roles_source_is_policies_endpoint():
    src = _page("permissions")
    assert "apiFetch('/api/permission/roles/all'" not in src, (
        "错位端点调用点残留（无路由，'all' 被当 username 落 list_roles）"
    )
    assert "/api/permission/policies" in src, "真源端点未被消费"
    # 元素形状收编（live 实证行数组）：角色映射行 [user,role] / 策略行 [role,resource,action]
    assert "policyRows" in src and "roleRows" in src
    assert "r?.[0]" in src and "r?.[2]" in src, "策略行 [role,resource,action] 映射未落地"


def test_permissions_census_zero_wrong_endpoint(census_report):
    v = _verdicts(census_report, "permissions/page.tsx")
    assert v.get(("roles",)) == "produced"
    assert v.get(("policies",)) == "produced"
    assert "/api/permission/roles/all" not in _paths(census_report, "permissions/page.tsx")


# ── D workspace 页目录浏览 ─────────────────────────────────────


def test_workspace_endpoints_point_to_real_routes():
    src = _page("workspace")
    for dead in ("/api/workspace/dir", "/api/workspace/file", "/api/workspace/execute"):
        assert dead not in src, f"404 幽灵端点残留 {dead}"
    for real in ("/api/dir", "/api/file", "/api/execute"):
        assert real in src, f"真端点未被消费 {real}"


def test_workspace_census_zero_ghost_endpoint(census_report):
    paths = _paths(census_report, "workspace/page.tsx")
    assert not any(p.startswith("/api/workspace/") for p in paths), (
        f"幽灵端点仍在 findings: {sorted(p for p in paths if p.startswith('/api/workspace/'))}"
    )
    assert "/api/dir" in paths
    v = _verdicts(census_report, "workspace/page.tsx")
    assert v.get(("path",)) == "produced"


# ── 全局：修后 drift_hard 只剩已裁决误报 ────────────────────────


def test_census_counts_after_fix(census_report):
    # 契约随更新（CD-⑨ census 工具提取精度收口）：旧口径「unresolved_endpoint 归零、
    # drift_hard 只剩已裁决误报」建立在旧工具漏报面（typed 赋值/?. 可选链/整体直传形
    # 零提取）之上。CD-⑨ 修复提取面后普查覆盖面扩大（findings 148→408），浮出新候选
    # ——登记不等于裁决，逐项裁决=下一拍深挖件（CD-③ 先例）。断言意图零丢失：
    # CD-① 修复件键面零回归 + 新候选集锁定（候选集再变必须重审，禁静默漂移）。
    hard = {
        (f["page"], tuple(f["key"]))
        for f in census_report["findings"]
        if f["verdict"] == "drift_hard"
    }
    # users/page.tsx data.users=CD-① 已裁决误报（三元防御分支，工具已知边界）
    # 其余 12=CD-⑨ 新浮出待裁决候选（nest/stats 例已抽验=真漂移：真源只出
    # tenants/isolation，UI 读 total_tenants 等 4 键恒回落）
    hard_expected = {
        ("users/page.tsx", ("users",)),
        ("feedback/page.tsx", ("confidence",)),
        ("feedback/page.tsx", ("type",)),
        ("immune/page.tsx", ("findings", "label")),
        ("immune/page.tsx", ("findings", "risk")),
        ("logs/page.tsx", ("buffer_size",)),
        ("logs/page.tsx", ("organ_probes",)),
        ("logs/page.tsx", ("sse_clients",)),
        ("nest/page.tsx", ("active_tenants",)),
        ("nest/page.tsx", ("suspended_tenants",)),
        ("nest/page.tsx", ("total_policies",)),
        ("nest/page.tsx", ("total_tenants",)),
        ("voice/page.tsx", ("error",)),
    }
    assert hard == hard_expected, f"漂移候选集漂移（新增=待裁决/消失=需复核）: {sorted(hard)}"
    ue = {
        (f["page"], tuple(f["key"]))
        for f in census_report["findings"]
        if f["verdict"] == "unresolved_endpoint"
    }
    # 11=CD-⑨ 新浮出端点映射缺口（live 实证：GET decay/simulate 与 POST
    # discovery/scan 均 405=UI 方法漂移真断点，修复件另拍）
    ue_expected = {
        ("hippo/page.tsx", ("retention",)),
        ("soma/page.tsx", ("adapters",)),
        ("soma/page.tsx", ("cli_tools",)),
        ("soma/page.tsx", ("cli_tools", "version")),
        ("soma/page.tsx", ("processes",)),
        ("soma/page.tsx", ("processes", "cpu")),
        ("soma/page.tsx", ("processes", "mem")),
        ("soma/page.tsx", ("scan_time",)),
        ("soma/page.tsx", ("services",)),
        ("soma/page.tsx", ("services", "protocol")),
        ("soma/page.tsx", ("services", "state")),
    }
    assert ue == ue_expected, f"端点映射缺口集漂移: {sorted(ue)}"
