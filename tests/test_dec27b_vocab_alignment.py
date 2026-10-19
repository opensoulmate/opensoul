#!/usr/bin/env python3
"""DEC-㉗b-① logs 页「问题类事件」卡——生产词表闭集收编（错误/告警卡词表对齐取 B）回归锁死。

背景（pending-decisions ㉗b，CD-⑮ 深挖裁决留档件）：错误/告警卡按 by_type.error +
by_type.alert 精确键计数，而生产 type 词表全量枚举零 'error'/'alert' 精确键→卡恒 0；
真实问题类事件用异名词表（health_alert/security/component_error/*_rejected 等，
live 实证 health_alert:1 在场而卡显示 0=读者语义漏计）。本拍按「别停工等批」铁律＋
DEC-①~㉘a 自决先例自决取 B（UI 收编生产词表闭集）先行执行（异议回「㉗b改A」=revert
回精确查询形）：
- 问题类事件卡（标签随语义收编改「问题类事件」）计数 = PROBLEM_TYPE_KEYS 闭集
  （单一源，security 族＋失败/告警族，含 ip_blacklisted（生产词表实证在场，比
  pending-decisions 建议清单多收一键如实记））∪ 预留语义类 error/alert（A 裁决
  「生产面启用即自动生效」语义保留=B 的严格超集，非 B 字面替换）∪ 后缀族 *_rejected；
- 未列 key 一律不计（宁窄勿滥）；真源零改动（by_type 开放 dict 不变，CD 系列 wire
  权威纪律）。

断言四层（禁自嗨恒真断言）：
①源码消费面：修前 by_type?.error/.alert 直查形清零＋计数 helper 单一源＋卡真消费
  ＋标签「问题类事件」（「错误/告警」清零）；
②词表锚点：闭集常量恰=批准闭集（扩/缩漂移先炸）＋闭集逐键生产证据（词表字面量
  或源码字面量）＋*_rejected 后缀族生产证据＋预留语义类保留（A 语义不丢）；
③行为桥：problemEventCount 源码逐字提取经 node 真执行七形态（含修前公式对照反证
  ＋宁窄勿滥）＋真源真跑 by_type→node 计数==独立重算（禁口径漂移/禁真值遮蔽）；
④真源零变化面：get_stream_summary 形状恰 6 键不变（CD-⑮/DEC-㉗a-① 契约延续）。
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "logs" / "page.tsx"

# 批准闭集（pending-decisions ㉗b 建议清单 + ip_blacklisted 生产词表实证补收）
APPROVED_CLOSED_SET = {
    "error",
    "alert",
    "security",
    "intrusion_detected",
    "brute_force",
    "ip_blocked",
    "ip_blacklisted",
    "permission_denied",
    "content_blocked",
    "request_blocked",
    "health_alert",
    "component_error",
    "pipeline_blocked",
    "webhook_failed",
}
RESERVED_SEMANTIC_CLASSES = {"error", "alert"}
# 生产词表字面量面已实证在场的问题类键（webhook_failed 在 link_gateway 动态调用面）
PRODUCED_LITERAL_KEYS = {
    "security",
    "intrusion_detected",
    "brute_force",
    "ip_blocked",
    "ip_blacklisted",
    "permission_denied",
    "content_blocked",
    "request_blocked",
    "health_alert",
    "component_error",
    "pipeline_blocked",
}


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def _code() -> str:
    """剥行注释后的真实代码（历史记述/裁决注释里的键名不算消费形）。"""
    return re.sub(r"//.*$", "", _src(), flags=re.M)


def _helper_src() -> str:
    """问题类计数面源码逐字提取（常量+helper，node 可执行形）。"""
    m = re.search(
        r"(const PROBLEM_TYPE_KEYS[\s\S]*?function problemEventCount\([\s\S]*?\n\})",
        _src(),
    )
    assert m, "问题类计数面提取失败（helper 缺席或提取失效=断言空转）"
    return m.group(1)


def _closed_set_from_source() -> set[str]:
    m = re.search(r"const PROBLEM_TYPE_KEYS = new Set<string>\(\[(.*?)\]\)", _src(), re.S)
    assert m, "PROBLEM_TYPE_KEYS 常量提取失败（断言空转）"
    body = re.sub(r"//.*$", "", m.group(1), flags=re.M)
    return set(re.findall(r"'([a-z_.]+)'", body))


def _producer_type_vocabulary() -> set[str]:
    """生产 type 词表字面量枚举（test_cd15 同口径：event_stream 探针字面量+push_event 字面量）。"""
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


def _src_literal(key: str) -> bool:
    """key 以字面量形态出现在 src（动态调用面证据，如 link_gateway event_type='webhook_failed'）。"""
    for p in (REPO / "src").rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        if f'"{key}"' in text or f"'{key}'" in text:
            return True
    return False


# ── ① 源码消费面 ─────────────────────────────────────────────────


def test_old_direct_query_form_cleared():
    """修前 by_type?.error/.alert 精确键直查形清零（回归先炸）。"""
    code = _code()
    for key in ("error", "alert"):
        assert not re.search(rf"by_type\??\.{key}\b", code), f"修前精确键直查形回潮: by_type.{key}"
    assert "(summary?.by_type?.error || 0) + (summary?.by_type?.alert || 0)" not in _src(), (
        "修前计数公式回潮"
    )


def test_card_label_and_consume_point():
    """卡标签「问题类事件」（「错误/告警」清零）＋计数 helper 被卡真消费恰 1 处。"""
    code = _code()
    assert "问题类事件" in code, "问题类事件卡标签缺席"
    assert "错误/告警" not in code, "修前卡标签残留（语义已收编）"
    assert code.count("problemEventCount(summary?.by_type)") == 1, "计数消费点数漂移（恰 1 处）"


def test_counting_helper_single_source():
    """problemEventCount 恰一处定义（单一源，禁第二套计数判法漂移）。"""
    code = _code()
    assert code.count("function problemEventCount(") == 1, "计数 helper 定义数漂移（恰 1 处）"
    assert "PROBLEM_TYPE_SUFFIX" in code, "后缀族单一源常量缺席"


def test_reserved_semantic_classes_preserved():
    """预留语义类 error/alert 保留在闭集（A 裁决「生产面启用即自动生效」语义不丢）。"""
    closed = _closed_set_from_source()
    assert RESERVED_SEMANTIC_CLASSES <= closed, "预留语义类被移出计数面（A 语义丢失）"


# ── ② 词表锚点 ─────────────────────────────────────────────────


def test_closed_set_exact_approved():
    """闭集常量恰=批准闭集（扩/缩漂移先炸=改道须先改本锁）。"""
    closed = _closed_set_from_source()
    assert closed == APPROVED_CLOSED_SET, (
        f"闭集漂移: 多={closed - APPROVED_CLOSED_SET} 少={APPROVED_CLOSED_SET - closed}"
    )


def test_closed_set_production_evidence():
    """闭集逐键生产证据：词表字面量（11 键）或 src 字面量（webhook_failed 动态调用面）。

    闭集若混入无生产证据的键=凭空扩集（宁窄勿滥纪律）；生产证据键若被移出=漏计回潮。
    """
    vocab = _producer_type_vocabulary()
    assert PRODUCED_LITERAL_KEYS <= vocab, (
        f"生产词表证据键缩水（提取失效或词表漂移）: {PRODUCED_LITERAL_KEYS - vocab}"
    )
    for key in APPROVED_CLOSED_SET:
        assert key in vocab or _src_literal(key), f"闭集键无生产证据（凭空扩集）: {key}"


def test_suffix_family_production_evidence():
    """*_rejected 后缀族生产证据：src 至少 3 个不同 *_rejected 事件名字面量。"""
    hits: set[str] = set()
    for p in (REPO / "src").rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        hits.update(re.findall(r"['\"]([a-z_]+_rejected)['\"]", text))
    assert len(hits) >= 3, f"后缀族生产证据缩水（提取失效=断言空转）: {sorted(hits)}"


# ── ③ 行为桥（node 真执行 + 真源真跑） ──────────────────────────


def test_problem_event_count_real_node_execution():
    """problemEventCount 源码逐字提取经 node 真执行：七形态（修前公式对照反证+宁窄勿滥）。"""
    helper = _helper_src()
    probe = (
        helper
        + "\nconst cases = ["
        # 修前恒 0 反证：异名词表真计数（修前公式 error+alert=0）
        + "[{health_alert: 1}, 1], "
        + "[{security: 2, intrusion_detected: 1}, 3], "
        # 预留语义类保留（A 语义）
        + "[{error: 2, alert: 1}, 3], "
        # 后缀族
        + "[{ws_chat_rejected: 5, terminal_ws_rejected: 1}, 6], "
        # 宁窄勿滥：未列 key 一律不计
        + "[{memory_decay: 10, llm_call: 3}, 0], "
        # 混合形（含非数值值不 fabricate）
        + "[{health_alert: 1, security: 2, error: 1, foo_rejected: 2, memory: 9, weird: 'x'}, 6], "
        # 缺席形
        + "[undefined, 0], [null, 0], [{}, 0]];"
        + "\nconst out = cases.map(([bt, e]) => [problemEventCount(bt), e]);"
        + "\nconsole.log(JSON.stringify(out));"
    )
    r = subprocess.run(["node", "-e", probe], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"node 执行失败: {r.stderr[:300]}"
    import json

    out = json.loads(r.stdout.strip())
    for got, expect in out:
        assert got == expect, f"问题类计数形态漂移: got={got!r} expect={expect!r}"


def test_real_summary_by_type_counts_independently_recomputed():
    """真源真跑 by_type→node 计数==独立重算（禁口径漂移）＋修前公式对照（真值不被遮蔽）。"""
    from src.api import event_stream

    resp = asyncio.run(event_stream.get_stream_summary())
    by_type = resp["by_type"]
    helper = _helper_src()
    import json

    probe = helper + "\nconsole.log(problemEventCount(" + json.dumps(by_type) + "))"
    r = subprocess.run(["node", "-e", probe], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"node 执行失败: {r.stderr[:300]}"
    node_count = int(r.stdout.strip())

    # 独立重算（同规格判法，独立实现）
    expect = 0
    for k, v in by_type.items():
        if k in APPROVED_CLOSED_SET or k.endswith("_rejected"):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                expect += v
    assert node_count == expect, f"node 计数与独立重算漂移: {node_count} != {expect}"

    legacy = (by_type.get("error") or 0) + (by_type.get("alert") or 0)
    assert node_count >= legacy, "计数面比修前公式更窄（预留语义类丢失）"
    if "health_alert" in by_type:
        assert node_count >= by_type["health_alert"], "health_alert 真值被遮蔽（读者语义漏计未修）"


# ── ④ 真源零变化面 ─────────────────────────────────────────────


def test_summary_shape_unchanged_six_keys():
    """真源零改动：get_stream_summary 仍恰 6 键（CD-⑮/DEC-㉗a-① 契约延续）。"""
    from src.api import event_stream

    resp = asyncio.run(event_stream.get_stream_summary())
    assert set(resp) == {
        "total_events",
        "by_organ",
        "by_type",
        "most_active_organ",
        "recent_count",
        "collected_at",
    }, f"真源形状变了须复挖: {sorted(resp)}"
