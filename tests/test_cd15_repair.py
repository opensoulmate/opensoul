#!/usr/bin/env python3
"""CD-⑮ 最近事件卡 recent_count 深挖+收口（UI 死显示面收口，真源零改动）回归锁死。

背景（CD-⑭ 留档候选首件「最近事件卡 recent_count 深挖（CD-⑪ 留档「get_summary
5 键无 recent_count，最近事件卡疑恒 0」）」）：logs/page.tsx 统计卡组 4+1 卡逐卡
深挖裁决（行为实证补 census unresolved_shape 工具边界——/api/events/summary 为
委托别名 get_summary→get_stream_summary，形状提取失败层裁决不了键级，真跑定型）：
- recent_count（最近事件卡）：confirmed_drift——真源 get_stream_summary 恰 5 键
  total_events/by_organ/by_type/most_active_organ/collected_at（源码+live 双实证），
  全仓零产出点；UI 接口声明+消费 {summary?.recent_count || 0} 恒 undefined 恒假 0。
- total_events（总事件卡）/by_organ 派生（活跃器官卡）：resolved_ok——真源键在场。
- by_type.error+by_type.alert（错误/告警卡）：深挖扩面发现（census 层漏收）——
  生产 type 词表全量枚举零 'error'/'alert' 精确键→恒 0；但 by_type 是开放 dict
  （Record<string,number>），键查询形状合法、未来生产者产出即生效（event_bridge
  严重度映射即按 ("error","alert","failure") 词表设计=预留语义类）→真 0 非假 0，
  非死显示键；「错误/告警」标签与生产词表错位（真实问题类事件 health_alert/
  security/component_error/*_rejected 等用异名，live 实证 health_alert:1 在场而
  卡显示 0）=读者语义漏计，修法涉语义设计非 wire 形状收编→留档 pending-decisions
  ㉗b 拍板件不自决，本拍卡面维持现状（测试锁死不许误删/误改）。

收口（UI 消费面，真源零改动=CD 系列 wire 权威纪律，CD-④ 死显示面收口先例）：
- 最近事件卡（recent_count 死显示）移除禁假 0，EventSummary.recent_count 死声明
  键双清零（CD-⑦ 纪律）；grid md:grid-cols-5→4（余 4 卡填行）；
- 恢复显示=真源补现候选 pending-decisions ㉗a（窗口计数语义需拍板）不自决。

断言四层（禁自嗨恒真断言）：
①源码消费面：recent_count/死卡零残留 + EventSummary 声明键 ⊆ 真源 + 词表卡保留；
②行为桥：get_stream_summary/get_summary 真跑 5 键形状锁死恒不产出 recent_count +
   消费键 ⊆ 真源 + by_type 开放 dict 查询 well-formed（合成 'error' 事件真计数
   =真 0 非假 0 裁决的行为证明）；
③深挖裁决证据锁：生产 type 词表零 'error'/'alert' 精确键 + health_alert 异名在场
   + event_bridge 预留语义类词表在场（任一变化=㉗b 裁决失效须复挖）；
④census 真跑真仓：recent_count findings 全消失 + summary 调用点消费键收窄到真源键。

2026-10-20 勘误（DEC-㉗a-① 真源补现后契约随更新，历史裁决文本不改写）：
㉗a 已按建议默认 B1（近 24h 窗口计数）先行执行（pending-decisions 自决先例，
异议回「㉗a改A/改B2」即 revert/改道）——get_stream_summary 补现 recent_count
（加性键）+ 最近事件卡恢复（CD-⑮ 移除件复原语义）+ grid 回 5 列。本文件
①②④ 断言面随契约翻转（recent_count 恒不产出→真产出、死卡移除→卡恢复），
㉗b 词表裁决面（③）零变化锁死不动。「全仓零产出点」勘误为「修前零产出」。
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
_spec = importlib.util.spec_from_file_location("cdc_cd15_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd15_repair"] = cdc
_spec.loader.exec_module(cdc)

# 真源 get_stream_summary 产出键（event_stream.py，live 实证一致；DEC-㉗a-① 补现
# recent_count 后 6 键——勘误：修前恰 5 键）
TRUE_SOURCE_KEYS = (
    "total_events",
    "by_organ",
    "by_type",
    "most_active_organ",
    "recent_count",
    "collected_at",
)
# ㉗b 词表裁决的两造：UI 精确查询键 vs 生产词表
QUERIED_TYPE_KEYS = ("error", "alert")


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def _code() -> str:
    """剥行注释后的真实代码（历史记述里的键名不算消费形）。"""
    return re.sub(r"//.*$", "", _src(), flags=re.M)


def _summary_consumed_keys() -> set[str]:
    """UI 对 summary 对象的首跳消费键（排除 l.summary 等 LogEntry 字段访问）。"""
    return set(re.findall(r"(?<![.\w])summary\??\.([A-Za-z_$][\w$]*)", _code()))


# ── ① 源码消费面 ───────────────────────────────────────────────


def test_recent_count_restored_as_true_source_key():
    """DEC-㉗a-① 真源补现后 recent_count 声明/消费双在场（契约随更新，勘误见模块 docstring）。

    修前（CD-⑮）断言双清零；真源已补现 recent_count（get_stream_summary 加性键），
    UI 接口声明+消费随之恢复——键真源不产出的禁令翻转为「键必须真消费真产出」。
    """
    code = _code()
    assert "recent_count" in code, "recent_count 真源键消费/声明缺席（㉗a 恢复件被误删）"
    assert re.search(r"summary\??\.recent_count", code), "recent_count 未被 summary 真消费"


def test_recent_card_restored():
    """最近事件卡随真源补现恢复（CD-⑮ 移除件复原语义，卡文本/图标逐字一致）。"""
    code = _code()
    assert "最近事件" in code, "最近事件卡缺席（㉗a 恢复件被误删）"
    assert re.search(r"(?<![.\w])Zap\b", code), "卡图标 Zap 缺席（复原语义不完整）"


def test_event_summary_interface_subset_of_true_source():
    """EventSummary 声明键 ⊆ 真源 get_stream_summary 产出键（禁声明假键）。"""
    m = re.search(r"interface EventSummary \{([^}]*)\}", _src())
    assert m, "EventSummary 接口未找到（提取失效=断言空转）"
    declared = set(re.findall(r"^\s+([A-Za-z_$][\w$]*):", m.group(1), re.M))
    assert declared, "接口键提取为空（提取失效=断言空转）"
    from src.api import event_stream

    resp = asyncio.run(event_stream.get_stream_summary())
    for k in declared:
        assert k in resp, f"接口声明键真源不产出: {k}"


def test_stat_grid_fills_five_columns():
    """卡恢复后统计卡组 5 卡填 5 列（不留空洞列；勘误：CD-⑮ 收口后曾 4 列）。"""
    assert "grid grid-cols-2 md:grid-cols-5" in _src(), "统计卡组 grid 未随卡数收编"


def test_by_type_error_alert_query_kept_pending_decision():
    """㉗b 拍板前错误/告警卡查询形维持现状锁死（禁误删/误改=拍板改道须先改本锁）。"""
    code = _code()
    for key in QUERIED_TYPE_KEYS:
        assert re.search(rf"by_type\??\.{key}\b", code), f"错误/告警卡查询键被改动: {key}"


# ── ② 行为桥（真源实跑；勘误：CD-⑮ 拍为「真源零改动」，DEC-㉗a-① 起真源已补现 recent_count） ──


def test_summary_shape_lock_six_keys_with_recent_count():
    """get_stream_summary 真跑形状锁死：恰 6 键含 recent_count（DEC-㉗a-① 补现后）。

    勘误（历史裁决文本不改写）：CD-⑮ 时锁「恰 5 键、恒不产出 recent_count」
    （真源形状变了→裁决失效须复挖）；㉗a 已按建议默认 B1 取 B 补现，本锁按
    证据锁纪律显式改锁为补现后形状——再变（键集漂移/recent_count 消失）仍先炸。
    """
    from src.api import event_stream

    resp = asyncio.run(event_stream.get_stream_summary())
    assert set(resp) == set(TRUE_SOURCE_KEYS), f"真源形状变了须复挖: {sorted(resp)}"
    assert isinstance(resp["recent_count"], int), "recent_count 须为整数计数"
    assert 0 <= resp["recent_count"] <= resp["total_events"], (
        "recent_count 须落在窗口计数语义内（0 <= recent <= total）"
    )


def test_alias_face_same_shape_as_true_source():
    """UI 打的 /api/events/summary（get_summary 别名）与真源同形状（别名委托锁）。"""
    from src.api import event_stream

    alias = asyncio.run(event_stream.get_summary())
    direct = asyncio.run(event_stream.get_stream_summary())
    assert set(alias) == set(direct) == set(TRUE_SOURCE_KEYS), "别名面与真源形状漂移"


def test_consumed_summary_keys_subset_of_true_source():
    """UI summary 消费键 ⊆ 真源产出键（修后=total_events/by_type/by_organ/recent_count）。

    提取=summary.<key> 首跳点访问全集；未来 UI 又发明键此桥先炸。
    """
    consumed = _summary_consumed_keys()
    assert consumed, "UI 未提取到消费键（提取失效=断言空转）"
    assert consumed <= set(TRUE_SOURCE_KEYS), (
        f"UI 消费键真源不产出: {consumed - set(TRUE_SOURCE_KEYS)}"
    )


def test_by_type_open_dict_query_wellformed(monkeypatch):
    """by_type 开放 dict 查询 well-formed：合成 'error'/'health_alert' 事件真计数。

    ㉗b 裁决「真 0 非假 0（非死显示键）」的行为证明——查询键合法、事件真出现即计入；
    修前修后同证（本拍卡面零改动），与 recent_count（键根本不存在）裁决分野锁死。
    """
    from src.api import event_stream

    async def _fake_collect():
        return [
            {"type": "error", "organ": "vein"},
            {"type": "error", "organ": "vein"},
            {"type": "health_alert", "organ": "vital"},
        ]

    monkeypatch.setattr(event_stream, "_collect_all_events", _fake_collect)
    resp = asyncio.run(event_stream.get_stream_summary())
    assert resp["by_type"].get("error") == 2, "开放 dict 查询对 'error' 事件不计数=非 well-formed"
    assert resp["by_type"].get("health_alert") == 1, "异名词表事件未入 by_type"
    assert "alert" not in resp["by_type"], "词表裁决锚点漂移（'alert' 精确键出现须复挖）"


# ── ③ 深挖裁决证据锁（㉗b 词表裁决锚点） ───────────────────────


def _producer_type_vocabulary() -> set[str]:
    """生产 type 词表全量枚举：event_stream 探针字面量 + push_event 字面量。

    动态（变量）形态不入词表集（无法静态定值）——裁决只对字面量生产面下结论，
    与 ㉗b「动态词表（_audit/link_gateway evt.event_type）零 'error'/'alert'
    调用点」的人工枚举证据互补（见 pending-decisions ㉗b）。
    """
    vocab: set[str] = set()
    vocab.update(
        re.findall(
            r'"type":\s*"([a-z_.]+)"',
            (REPO / "src/api/event_stream.py").read_text(encoding="utf-8"),
        )
    )
    for p in (REPO / "src").rglob("*.py"):
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines):
            if "push_event(" in line and "def push_event" not in line:
                window = "\n".join(lines[i : i + 12])
                vocab.update(re.findall(r"['\"]type['\"]\s*:\s*['\"]([a-z_.]+)['\"]", window))
    return vocab


def test_producer_vocabulary_never_emits_error_alert():
    """生产 type 词表零 'error'/'alert' 精确键（㉗b 裁决核心锚点）。

    若未来生产者新增 'error'/'alert' 类事件：此锁先炸=「恒 0」裁决失效须复挖。
    """
    vocab = _producer_type_vocabulary()
    assert len(vocab) > 40, f"词表提取面缩水（提取失效=断言空转）: {len(vocab)}"
    hit = vocab & set(QUERIED_TYPE_KEYS)
    assert not hit, f"生产词表出现 error/alert 精确键（㉗b 裁决失效须复挖）: {hit}"


def test_health_alert_alias_class_present():
    """异名词表锚点：真实告警类事件以 'health_alert' 生产（live 实证同名）。

    ㉗b「读者语义漏计」证据——告警类在场但不入 by_type.alert 精确查询。
    """
    assert "health_alert" in _producer_type_vocabulary(), "health_alert 词表锚点漂移须复挖"


def test_event_bridge_reserved_severity_vocabulary():
    """event_bridge 严重度映射按 ("error","alert","failure") 预留语义类设计。

    ㉗b「非死显示键（真 0 非假 0）」证据——error/alert 是协议预留类，
    查询形合法、生产面未启用；映射若被移除=裁决依据失效须复挖。
    """
    src = (REPO / "src/nerve/event_bridge.py").read_text(encoding="utf-8")
    assert 'event_type in ("error", "alert", "failure")' in src, "预留语义类词表锚点漂移须复挖"


# ── ④ census 工具真跑真仓 ─────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def test_census_recent_count_finding_alongside_siblings(census_report):
    """DEC-㉗a-① 后 recent_count 与兄弟键同列（alias 形状提取失败层，非漂移复燃）。

    勘误（历史裁决文本不改写）：CD-⑮ 修后断言 findings 全消失（死键移除）；真源补现+
    卡恢复后消费键回 findings——与 total_events/by_type/by_organ 同层同因（/api/events/
    summary 为委托别名，census 形状提取不了键级），无 recent_count 专属漂移判。
    """
    findings = [f for f in census_report["findings"] if f["page"].endswith("logs/page.tsx")]
    assert findings, "logs 页 findings 为空（工具失效=断言空转）"
    summary_hits = [f for f in findings if f["path"] == "/api/events/summary"]
    assert summary_hits, "summary 调用点未被 census 提取（提取失效=断言空转）"
    keys = {k for f in summary_hits for k in f["key"]}
    assert "recent_count" in keys, "recent_count 未随消费恢复进 findings（提取面漂移）"
    assert all(f.get("note", "").find("形状提取失败") >= 0 for f in summary_hits), (
        f"summary findings 层级漂移（应全为形状提取失败层）: {[f.get('note') for f in summary_hits]}"
    )


def test_census_summary_callsite_keys_restored_to_true_source(census_report):
    """summary 调用点消费键=真源键集（修前 4 键→CD-⑮ 收窄 3 键→DEC-㉗a-① 回 4 键）。"""
    hits = [
        f
        for f in census_report["findings"]
        if f["page"].endswith("logs/page.tsx") and f["path"] == "/api/events/summary"
    ]
    assert hits, "summary 调用点未被 census 提取（提取失效=断言空转）"
    consumed = {k for f in hits for k in f["key"]}
    assert consumed == {"total_events", "by_type", "by_organ", "recent_count"}, (
        f"消费键集漂移: {consumed}"
    )
