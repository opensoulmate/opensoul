"""DEC-㉗a-② logs 最近事件卡回落形收口——摘要缺席='—'，禁 fabricate 0（真 0 禁遮蔽）。

背景（GATE-① 2026-10-20 07:23 观察件=该拍明示「下拍候选」，本拍按 DEC 自决先例
先行执行——仓内可 revert 代码件、语义与 CD-⑳/㉚/㉘b 既定纪律一致；异议回
「㉗a回落改撤」=revert 本拍回修前回落形）：
logs/page.tsx:257 `{summary?.recent_count || 0}` 摘要缺席回落 0 与
CD-⑳/㉚/㉘b「缺席禁 fabricate 0、真 0 禁遮蔽」纪律不一致——DEC-㉗a-①「复原语义
逐字一致」刻意保留了修前回落形（真源加性键恒产出，仅 summary 整体缺席才触发）。

收口：值表达式收编 typeof number 判法（CD-㉚「非 truthy」同款）——
真 0='0' 如实渲染（禁遮蔽）、真值照显、summary 缺席='—' 占位禁假 0。

范围纪律：仅 257 一处回落形；总事件卡 `summary?.total_events || displayLogs.length`
（回落=本地真计数非 fabricate 0，GATE-① 未点名）不在本拍，锁死禁误扩。
"""

import asyncio
import json
import re
import subprocess
import time as _time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "logs" / "page.tsx"


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def _code() -> str:
    return re.sub(r"//.*$", "", _src(), flags=re.M)


def _value_expr() -> str:
    """从最近事件卡值 div 逐字提取 value 表达式（禁手抄，测试吃真源形）。"""
    code = _code()
    hits = re.findall(r'text-2xl font-bold text-blue-500 mt-1">\{([^}]*)\}', code)
    assert len(hits) == 1, f"最近事件卡值表达式提取失败/不唯一: {hits}"
    return hits[0]


def _node_run(expr: str, summary_js: str) -> str:
    """表达式逐字经 node 真执行（行为桥，㉘b 先例同款）。"""
    probe = "const summary = " + summary_js + ";\nconsole.log(String(" + expr + "));"
    r = subprocess.run(["node", "-e", probe], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"node 执行失败: {r.stderr[:300]}"
    return r.stdout.strip()


# ── ① 回落形收口（源码消费面） ─────────────────────────────────


class TestFallbackForm:
    def test_fake_zero_fallback_form_gone(self):
        """修前 `{summary?.recent_count || 0}` 回落形零残留（fabricate 0 收口）。"""
        code = _code()
        assert not re.search(r"recent_count\s*\|\|\s*0", code), (
            "修前回落形 recent_count || 0 仍在场（缺席 fabricate 0 未收口）"
        )

    def test_typeof_number_ternary_present(self):
        """值表达式=typeof number 判法（非 truthy）+ 缺席 '—'（CD-㉚ 同款）。"""
        expr = _value_expr()
        assert expr == "typeof summary?.recent_count === 'number' ? summary.recent_count : '—'", (
            f"值表达式漂移: {expr!r}"
        )

    def test_scope_discipline_total_events_fallback_untouched(self):
        """范围纪律：总事件卡回落形（本地真计数非假 0）不在本拍，锁死禁误扩。"""
        assert "summary?.total_events || displayLogs.length" in _src(), (
            "总事件卡回落形被误改（超出 ㉗a回落 点名范围）"
        )


# ── ② 契约锁完整（家族既有锁前提不破坏） ────────────────────────


class TestContractLocksIntact:
    def test_verbatim_literal_for_family_locks(self):
        """test_logs_recent_count_restore/test_cd15 家族锁依赖的逐字面不被改写。"""
        assert "summary?.recent_count" in _code(), (
            "summary?.recent_count 逐字面缺席（cd15/restore 家族锁前提被破坏）"
        )

    def test_cd22_anchor_line257_intact(self):
        """cd22 真源锚点 (page.tsx, 257, 'summary?.recent_count') 行号+字面双在场。"""
        lines = _src().splitlines()
        assert len(lines) >= 257, "page.tsx 行数不足 257（锚点行号漂移）"
        assert "summary?.recent_count" in lines[256], f"cd22 锚点行 257 漂移: {lines[256]!r}"

    def test_card_face_unchanged(self):
        """卡文本/图标/接口声明零变化（㉗a-① 恢复面不动）。"""
        code = _code()
        assert "最近事件" in code, "卡文本缺席"
        assert re.search(r"(?<![.\w])Zap\b", code), "卡图标 Zap 缺席"
        m = re.search(r"interface EventSummary \{([^}]*)\}", _src())
        assert m, "EventSummary 接口未找到"
        declared = set(re.findall(r"^\s+([A-Za-z_$][\w$]*):", m.group(1), re.M))
        assert "recent_count" in declared, f"接口未声明 recent_count: {declared}"


# ── ③ 行为桥（node 真执行 + 真源真跑） ──────────────────────────


class TestNodeBridge:
    def test_value_expr_node_bridge_zero_honest_not_masked(self):
        """值表达式逐字经 node 真执行：真 0='0'（禁遮蔽 CD-⑳/㉚）、缺席='—'。"""
        expr = _value_expr()
        cases = [
            ("{recent_count: 0}", "0"),  # 真 0 如实，禁 '—' 遮蔽
            ("{recent_count: 42}", "42"),  # 真值照显
            ("undefined", "—"),  # summary 缺席禁假 0
            ("null", "—"),  # 同上
        ]
        for summary_js, expect in cases:
            got = _node_run(expr, summary_js)
            assert got == expect, (
                f"渲染形态漂移: summary={summary_js} got={got!r} expect={expect!r}"
            )

    def test_true_source_to_ui_pipeline(self, monkeypatch):
        """真源真跑→node 渲染逐值对账（行为桥）：recent_count 真值过 UI 表达式==展示。"""
        from src.api import event_stream

        now = _time.time()

        async def _fake_collect():
            return [
                {"type": "fresh", "collected_at": now},
                {"type": "recent", "timestamp": now - 3600},
                {"type": "old", "timestamp": now - 3 * 86400},
            ]

        monkeypatch.setattr(event_stream, "_collect_all_events", _fake_collect)
        resp = asyncio.run(event_stream.get_stream_summary())
        assert resp["recent_count"] == 2, f"真源计数漂移: {resp['recent_count']}"
        expr = _value_expr()
        got = _node_run(expr, json.dumps({"recent_count": resp["recent_count"]}))
        assert got == "2", f"真源真值→渲染对账不符: {got!r}"

    def test_true_zero_renders_zero_not_dash(self, monkeypatch):
        """真源真 0（全旧事件）→ 渲染 '0'，绝不 '—'（真 0 禁遮蔽纪律硬证）。"""
        from src.api import event_stream

        now = _time.time()

        async def _fake_collect():
            return [{"type": "old", "timestamp": now - 10 * 86400} for _ in range(3)]

        monkeypatch.setattr(event_stream, "_collect_all_events", _fake_collect)
        resp = asyncio.run(event_stream.get_stream_summary())
        assert resp["recent_count"] == 0
        got = _node_run(_value_expr(), json.dumps({"recent_count": resp["recent_count"]}))
        assert got == "0", f"真 0 被遮蔽: 渲染 {got!r}"
