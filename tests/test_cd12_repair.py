#!/usr/bin/env python3
"""CD-⑫ 修复候选②——nest stats 消费键收编（UI 消费面收编真源键）回归锁死。

背景（CD-⑩ 深挖裁决 confirmed_drift→候选②，4 键同根）：
nest/page.tsx 统计卡平铺读 total_tenants/active_tenants/suspended_tenants/
total_policies，但 GET /api/nest/stats 真源恰 {tenants, isolation} 两键嵌套
（src/api/nest.py nest_stats）——total_tenants 在嵌套层 tenants.total_tenants
（src/nest/tenant.py stats()），平铺读恒回落 filteredTenants.length（过滤页长度
≠总数=回落遮蔽假渲染）；active_tenants/suspended_tenants 全层缺失（可由 tenants.by_status 派生，TenantStatus 4 值
active/suspended/trial/expired，两卡片点名前两态）；total_policies
全层缺失。

勘误如实记（CD-⑩ 证据「isolation.stats()…无 total_policies」措辞不精确）：
真源缺 total_policies 键名但 isolation.stats() 产出 policies_count
（src/nest/isolation.py stats() 末键，语义=策略条数）——收编真源名即得，
无需 CD-⑩ 所议「补现或移除显示」二选一；修前 `policies.length ||
stats.total_policies || '-'` 靠 UI 侧列表长度兜底=非真源键假渲染。

收口（UI 消费面收编真源键，真源零改动=CD 系列 wire 权威纪律）：
- Stats 接口收编真源形（tenants/isolation 嵌套，键逐键 ⊆ 真源产出），
  死声明键 total_audit_entries（真源不产出且零消费）清零（CD-⑦/⑪ 纪律）；
- 总租户数=stats.tenants?.total_tenants（回落遮蔽 filteredTenants.length 清零）；
- 活跃/已暂停=tenants.by_status 派生（by_status 在场才计数，缺席 '-' 不 fabricate 0）；
- 隔离策略=stats.isolation?.policies_count（真源键；policies.length 兜底清零）。

断言两层（禁自嗨恒真断言）：
①源码消费面：嵌套真源键真在场 + 平铺漂移键/死声明键/回落遮蔽零残留；
②行为桥：TenantManager.stats()/IsolationEngine.stats() 真跑（真源零改动），
   UI 消费键 ⊆ 真源产出键（分层）、派生键 ⊆ TenantStatus 全集（新状态必炸）；
③census 工具真跑真仓：4 漂移键 findings 全消失 + /api/nest/stats 调用点 produced。
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "nest" / "page.tsx"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd12_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd12_repair"] = cdc
_spec.loader.exec_module(cdc)

# CD-⑩ 裁决的 4 个平铺漂移键（真源恒不在平铺层）
FLAT_DRIFT_KEYS = ("total_tenants", "active_tenants", "suspended_tenants", "total_policies")
# 死声明键（真源不产出且零消费，CD-⑦/⑪ 纪律）
DEAD_DECL_KEYS = ("total_audit_entries",)
# 真源分层产出键（src/nest/tenant.py stats() / src/nest/isolation.py stats()）
TRUE_TENANTS_KEYS = {
    "total_tenants",
    "by_tier",
    "by_status",
    "total_storage_bytes",
    "total_documents",
}
TRUE_ISOLATION_KEYS = {
    "total_access_checks",
    "blocked_attempts",
    "block_rate",
    "by_resource_type",
    "policies_count",
}


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def _code() -> str:
    """剥行注释后的真实代码（历史记述里的键名不算消费形）。"""
    return re.sub(r"//.*$", "", _src(), flags=re.M)


def _true_stats(tmp_path) -> dict:
    """真源真跑（独立 tmp 库，绝不碰生产 nest.db）：与 /api/nest/stats 同两层。"""
    from src.nest.isolation import IsolationEngine
    from src.nest.tenant import TenantManager

    mgr = TenantManager(db_path=str(tmp_path / "nest.db"))
    iso = IsolationEngine(db_path=str(tmp_path / "nest.db"))
    return {"tenants": mgr.stats(), "isolation": iso.stats()}


# ── ① 源码消费面 ───────────────────────────────────────────────


def test_ui_consumes_nested_true_source_keys():
    """收编后消费形：嵌套真源键（tenants.total_tenants/by_status/isolation.policies_count）真在场。"""
    src = _src()
    for pat in (
        r"stats\.tenants\??\.total_tenants\b",
        r"stats\.tenants\??\.by_status\b",
        r"stats\.isolation\??\.policies_count\b",
    ):
        assert re.search(pat, src), f"嵌套真源键未被消费: {pat}"


def test_flat_drift_consumption_absent():
    """平铺漂移键读法（stats.<flat>）零残留——收编后只走嵌套层。"""
    code = _code()
    for key in FLAT_DRIFT_KEYS:
        assert not re.search(rf"\bstats\.{key}\b", code), f"平铺漂移读法残留: stats.{key}"


def test_masked_fallback_gone():
    """回落遮蔽清零：修前 key 缺席时显示过滤页长度/policies 列表长度=非真源值假渲染。"""
    code = _code()
    assert "stats.total_tenants ?? filteredTenants.length" not in code, (
        "回落遮蔽残留（total_tenants）"
    )
    assert "policies.length || stats.total_policies" not in code, "回落遮蔽残留（total_policies）"


def test_dead_declaration_absent():
    """死声明键（真源不产出且零消费）声明/消费双清零。"""
    code = _code()
    for key in DEAD_DECL_KEYS:
        assert key not in code, f"死声明键残留: {key}"


# ── ② 行为桥（真源真跑，真源零改动） ────────────────────────────


def test_interface_layers_subset_of_true_source(tmp_path):
    """接口 Stats 分层声明键 ⊆ 真源分层产出键（禁声明假键）。"""
    src = _src()
    m = re.search(r"interface Stats \{", src)
    assert m, "Stats 接口未找到（提取失效=断言空转）"
    # 平衡花括号提取接口体（接口含嵌套层，[^}]* 正则不够）
    i = m.end() - 1
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                break
    body = src[i + 1 : j]
    groups = re.findall(r"(\w+)\?:\s*\{([^}]*)\}", body)
    assert groups, "嵌套层提取为空（提取失效=断言空转）"
    resp = _true_stats(tmp_path)
    for layer, inner in groups:
        assert layer in resp, f"接口层真源不产出: {layer}"
        declared = set(re.findall(r"^\s+([A-Za-z_$][\w$]*)\??:", inner, re.M))
        assert declared, f"层 {layer} 键提取为空（提取失效=断言空转）"
        assert declared <= set(resp[layer]), (
            f"层 {layer} 声明键真源不产出: {declared - set(resp[layer])}"
        )


def test_consumed_keys_subset_of_true_source(tmp_path):
    """UI 消费的每个 stats 分层键真源真产出（消费 ⊆ 真源）。未来真源形状再漂移
    （或 UI 又发明键）此桥先炸。"""
    code = _code()
    resp = _true_stats(tmp_path)
    consumed_tenants = set(re.findall(r"\bstats\.tenants\??\.([A-Za-z_$][\w$]*)", code))
    consumed_iso = set(re.findall(r"\bstats\.isolation\??\.([A-Za-z_$][\w$]*)", code))
    assert consumed_tenants, "tenants 层消费键未提取到（提取失效=断言空转）"
    assert consumed_iso, "isolation 层消费键未提取到（提取失效=断言空转）"
    for k in consumed_tenants:
        assert k in resp["tenants"], f"UI 消费键真源不产出（tenants 层）: {k}"
    for k in consumed_iso:
        assert k in resp["isolation"], f"UI 消费键真源不产出（isolation 层）: {k}"


def test_derived_status_keys_cover_tenant_status_enum(tmp_path):
    """活跃/已暂停派生键 ⊆ by_status 真源键集——派生键必须是真 TenantStatus 值
    （禁发明状态名），且两卡片各自状态在场；未来 UI 写出 enum 外状态名此桥先炸。
    （如实记：TenantStatus 恰 4 值 active/suspended/trial/expired，卡片只点名
    active/suspended 两态=卡片标签语义，trial/expired 计入总租户数卡片不重复计。）"""
    from src.nest.tenant import TenantStatus

    code = _code()
    derived = set(re.findall(r"\bby_status\??\.([A-Za-z_$][\w$]*)", code))
    assert derived, "by_status 派生键未提取到（提取失效=断言空转）"
    enum_values = {s.value for s in TenantStatus}
    assert derived <= enum_values, f"派生键写出 TenantStatus 外状态名: {derived - enum_values}"
    assert {"active", "suspended"} <= derived, f"两卡片状态键缺席: {derived}"
    # 真跑 by_status 键集 ⊆ enum（真源键形锁死）
    resp = _true_stats(tmp_path)
    assert set(resp["tenants"]["by_status"]) <= enum_values


def test_true_stats_two_layer_shape_locked(tmp_path):
    """真源响应形状锁死：恰 tenants/isolation 两层（UI 契约所依赖的形状）。"""
    resp = _true_stats(tmp_path)
    assert set(resp) == {"tenants", "isolation"}
    assert set(resp["tenants"]) == TRUE_TENANTS_KEYS
    assert set(resp["isolation"]) == TRUE_ISOLATION_KEYS


# ── ③ census 工具真跑真仓 ─────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def test_census_flat_drift_keys_gone(census_report):
    """修后 findings：4 个平铺漂移键从 nest 页 findings 全消失。"""
    findings = [f for f in census_report["findings"] if f["page"].endswith("nest/page.tsx")]
    assert findings, "nest 页 findings 为空（工具失效=断言空转）"
    bad = [
        f
        for f in findings
        if f["verdict"] in ("drift_hard", "drift_suspect") and set(f["key"]) & set(FLAT_DRIFT_KEYS)
    ]
    assert not bad, f"平铺漂移键仍在 findings: {bad}"


def test_census_stats_callsite_produced(census_report):
    """/api/nest/stats 调用点 verdict=produced（收编后真源键全在场）。"""
    hits = [
        f
        for f in census_report["findings"]
        if f["page"].endswith("nest/page.tsx") and f["path"] == "/api/nest/stats"
    ]
    assert hits, "/api/nest/stats 调用点未被 census 提取（提取失效=断言空转）"
    for f in hits:
        assert f["verdict"] == "produced", f"stats 调用点非 produced: {f}"
        assert set(f["key"]) <= {"tenants", "isolation"}, f"stats 调用点消费键越出真源两层键: {f}"
