#!/usr/bin/env python3
"""CD-⑪ 修复候选①——logs stream summary 消费键收编（UI 消费面收编真源键）回归锁死。

背景（CD-⑩ 深挖裁决 confirmed_drift→候选① + EXPANDED_FINDINGS 同义异名对）：
logs/page.tsx 页脚/统计卡消费 buffer_size/organ_probes/sse_clients 三键，
但取数打在 GET /api/events/stream/summary（get_stream_summary 恰 5 键
total_events/by_organ/by_type/most_active_organ/collected_at，无三键中任一）
=打错面：修前「缓冲: undefined」「探针: undefined」假渲染、SSE 客户端卡恒
`|| 0` 假 0。真源=GET /api/events/health（event_stream.py stream_health
返回 {status, component, buffer_size, probes_configured, sse_subscribers}）。
勘误如实记：CD-⑩ 证据写「GET /api/events/stream/health」路径不实——
stream_health 装饰器 @router.get(\"/health\") 挂载 prefix=/api/events，
live openapi 实证真路径=/api/events/health（无 /stream/health），本拍按真路径收口。
CD-⑩ EXPANDED_FINDINGS 明令「修复件收编时按真源名对齐，禁再手抄同义名」：
organ_probes→probes_configured、sse_clients→sse_subscribers。

收口（UI 消费面收编真源键，真源零改动=CD 系列 wire 权威纪律）：
- fetchStreamSummary 取数改指真源 /api/events/health；
- 三键全改真源名（buffer_size 原名在场不动、probes_configured/sse_subscribers）；
- StreamSummary 接口收编真源形，死声明键 last_refresh（真源不产出且零消费）
  双清零（CD-⑦ 死显示键声明/消费双清零纪律）。

断言两层（禁自嗨恒真断言）：
①源码消费面：真源键真在场 + 同义异名漂移键/死声明键零残留 + 取数真指 health；
②census 工具真跑真仓：3 个漂移键 findings 全消失 + health 调用点 verdict=produced；
③行为桥：stream_health() 真跑（真源零改动），UI 消费键 ⊆ 真源产出键；
   旧 summary 面真跑锁死（恒不产出三键=禁回指）。
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "logs" / "page.tsx"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd11_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd11_repair"] = cdc
_spec.loader.exec_module(cdc)

# CD-⑩ 裁决的 3 个打错面漂移键（真源 /stream/summary 恒不产出）
WRONG_FACE_DRIFT_KEYS = ("buffer_size", "organ_probes", "sse_clients")
# 真源 /api/events/health 产出键（event_stream.py stream_health）
TRUE_SOURCE_KEYS = ("buffer_size", "probes_configured", "sse_subscribers")
# CD-⑩ EXPANDED_FINDINGS 同义异名对（禁再手抄同义名）+ 死声明键
SYNONYM_DRIFT_KEYS = ("organ_probes", "sse_clients", "last_refresh")


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def _code() -> str:
    """剥行注释后的真实代码（历史记述里的键名不算消费形）。"""
    return re.sub(r"//.*$", "", _src(), flags=re.M)


# ── ① 源码消费面 ───────────────────────────────────────────────


def test_ui_fetches_true_health_face():
    """取数改指真源：/api/events/health，旧 summary 打错面零残留。"""
    src = _src()
    assert "apiFetch('/api/events/health')" in src, "未改指真源 /api/events/health"
    assert "/api/events/stream/summary" not in _code(), "旧打错面取数残留"


def test_ui_consumes_true_source_keys():
    """三键消费全改真源名（buffer_size 原名在场、两同义异名按真源名对齐）。"""
    src = _src()
    for key in TRUE_SOURCE_KEYS:
        assert re.search(rf"streamSummary\??\.{key}\b", src), f"真源键未被消费: {key}"


def test_synonym_drift_keys_and_dead_declaration_absent():
    """同义异名漂移键（organ_probes/sse_clients）+死声明键（last_refresh）零残留。"""
    code = _code()
    for key in SYNONYM_DRIFT_KEYS:
        assert key not in code, f"漂移键残留（消费形或接口声明）: {key}"


def test_interface_keys_subset_of_true_source():
    """接口 StreamSummary 声明键 ⊆ 真源 stream_health 产出键（禁声明假键）。"""
    m = re.search(r"interface StreamSummary \{([^}]*)\}", _src())
    assert m, "StreamSummary 接口未找到（提取失效=断言空转）"
    declared = set(re.findall(r"^\s+([A-Za-z_$][\w$]*):", m.group(1), re.M))
    assert declared, "接口键提取为空（提取失效=断言空转）"
    from src.api import event_stream

    resp = asyncio.run(event_stream.stream_health())
    for k in declared:
        assert k in resp, f"接口声明键真源不产出: {k}"


# ── ② 行为桥（真源实跑，真源零改动） ────────────────────────────


def test_stream_health_real_run_produces_consumed_keys():
    """UI 消费的每个 streamSummary 键 stream_health() 真产出（消费 ⊆ 真源）。

    提取=streamSummary.<key> 点访问全集；真源=stream_health() 真跑回包。
    未来真源形状再漂移（或 UI 又发明键）此桥先炸。
    """
    from src.api import event_stream

    resp = asyncio.run(event_stream.stream_health())
    consumed = set(re.findall(r"\bstreamSummary\??\.([A-Za-z_$][\w$]*)", _code()))
    assert consumed, "UI 未提取到消费键（提取失效=断言空转）"
    for k in consumed:
        assert k in resp, f"UI 消费键真源不产出: {k}"


def test_wrong_face_summary_never_produces_drift_keys(monkeypatch):
    """旧 summary 面锁死：真跑恒不产出三键（禁把取数改回 /stream/summary）。"""
    from src.api import event_stream

    async def _fake_collect():
        return []

    monkeypatch.setattr(event_stream, "_collect_all_events", _fake_collect)
    summary = asyncio.run(event_stream.get_stream_summary())
    hit = set(summary) & set(WRONG_FACE_DRIFT_KEYS)
    assert not hit, f"summary 面出现三键（真源形状变了→CD-⑩ 裁决失效须复挖）: {hit}"


# ── ③ census 工具真跑真仓 ─────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def test_census_wrong_face_drift_keys_gone(census_report):
    """修后 findings：同义异名漂移键/死声明键从 logs 页 findings 全消失，
    打错面 callsite（/api/events/stream/summary）整体消失。"""
    findings = [f for f in census_report["findings"] if f["page"].endswith("logs/page.tsx")]
    assert findings, "logs 页 findings 为空（工具失效=断言空转）"
    bad = [f for f in findings if set(f["key"]) & set(SYNONYM_DRIFT_KEYS)]
    assert not bad, f"漂移键仍在 findings: {bad}"
    wrong_face = [f for f in findings if f["path"] == "/api/events/stream/summary"]
    assert not wrong_face, f"打错面 callsite 残留: {wrong_face}"


def test_census_health_callsite_produced(census_report):
    """health 调用点（打错面收口后的取数）verdict=produced（真源键全在场）。"""
    hits = [
        f
        for f in census_report["findings"]
        if f["page"].endswith("logs/page.tsx") and f["path"] == "/api/events/health"
    ]
    assert hits, "health 调用点未被 census 提取（提取失效=断言空转）"
    for f in hits:
        assert f["verdict"] == "produced", f"health 调用点非 produced: {f}"
    consumed = {k for f in hits for k in f["key"]}
    assert consumed <= set(TRUE_SOURCE_KEYS), f"消费键越出真源键集: {consumed}"
