#!/usr/bin/env python3
"""DEC-㉗a-① logs 最近事件显示恢复——真源补现 recent_count（B1 近 24h 窗口计数）回归锁死。

背景（pending-decisions ㉗a，CD-⑮ 深挖裁决留档）：get_stream_summary 修前恰 5 键零
recent_count 产出，UI 最近事件卡恒假 0（死显示键）→CD-⑮ 按死显示面收口（卡移除）。
DEC-㉗a-① 按 DEC-㉕/㉖ 自决先例取建议 B1（近 24h 窗口计数；异议回「㉗a改A/改B2」即
revert/改道）：真源补现 recent_count 加性键 + UI 卡恢复（CD-⑮ 移除件复原语义）。

窗口语义（B1，事件时刻解释单一源 _event_ts）：
- 显式 timestamp（探针透传的源记录时刻）优先，否则 collected_at（采集时刻=采集即新近）；
- ISO 串按 fromisoformat 解析；解析失败/空值=0.0（与既有排序语义逐字一致，视为最旧
  不入 recent 窗口=宁窄勿滥）；
- recent_count = 事件时刻 >= now-RECENT_WINDOW_S 的事件数（边界闭区间含端点）。

断言三层：
①纯函数语义：_event_ts 四形态（float 优先/collected_at 兜底/ISO 解析/坏串 0.0）+
  排序单一源（_collect_all_events 重构后仍按 _event_ts 降序=行为零变化）；
②窗口计数：跨窗混合事件只数窗口内、全旧 0、边界闭区间、坏时刻不计、RECENT_WINDOW_S
  单一源常量；
③UI 恢复面：卡文本/图标/消费/接口声明/grid 5 列随真源恢复（CD-⑮ 移除件复原语义）。
"""

from __future__ import annotations

import asyncio
import re
import time as _time
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "logs" / "page.tsx"


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def _code() -> str:
    return re.sub(r"//.*$", "", _src(), flags=re.M)


# ── ① _event_ts 纯函数语义 + 排序单一源 ─────────────────────────


class TestEventTs:
    def test_explicit_float_timestamp_preferred(self):
        from src.api import event_stream

        assert event_stream._event_ts({"timestamp": 123.5, "collected_at": 999.0}) == 123.5

    def test_collected_at_fallback_when_no_timestamp(self):
        from src.api import event_stream

        assert event_stream._event_ts({"collected_at": 456.0}) == 456.0

    def test_falsy_timestamp_falls_through_to_collected_at(self):
        from src.api import event_stream

        assert event_stream._event_ts({"timestamp": 0, "collected_at": 456.0}) == 456.0
        assert event_stream._event_ts({"timestamp": "", "collected_at": 456.0}) == 456.0

    def test_iso_string_parsed(self):
        from datetime import datetime, timezone

        from src.api import event_stream

        ev = {"timestamp": "2026-10-19T12:00:00+08:00"}
        expected = datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC).timestamp() - 8 * 3600
        assert event_stream._event_ts(ev) == pytest.approx(expected, abs=1)
        assert event_stream._event_ts(ev) > 1.7e9, "解析结果非 2026 年 epoch"

    def test_unparseable_string_is_oldest_not_recent(self):
        from src.api import event_stream

        assert event_stream._event_ts({"timestamp": "not-a-time", "collected_at": 456.0}) == 0.0

    def test_missing_everything_is_zero(self):
        from src.api import event_stream

        assert event_stream._event_ts({}) == 0.0

    def test_sort_single_source_unchanged(self, monkeypatch):
        """重构回归：_collect_all_events 排序仍按 _event_ts 降序（_sort_key 收编后行为零变化）。"""
        from src.api import event_stream

        async def _fake_probes(_client):
            return [
                {"type": "a", "timestamp": 100.0},
                {"type": "b", "timestamp": 300.0},
                {"type": "c"},  # collected_at=now → 最新
                {"type": "d", "timestamp": "bad"},  # 0.0 → 最旧
            ]

        monkeypatch.setattr(event_stream, "_PROBES", [_fake_probes])
        events = asyncio.run(event_stream._collect_all_events())
        order = [e["type"] for e in events]
        assert order[0] == "c", f"采集时刻事件应排最新: {order}"
        assert order[1:] == ["b", "a", "d"], f"timestamp 降序语义被改: {order}"


# ── ② recent_count 窗口计数（B1 近 24h） ────────────────────────


class TestRecentCountWindow:
    def _summary_with(self, monkeypatch, events):
        from src.api import event_stream

        async def _fake_collect():
            return events

        monkeypatch.setattr(event_stream, "_collect_all_events", _fake_collect)
        return asyncio.run(event_stream.get_stream_summary())

    def test_only_window_events_counted(self, monkeypatch):
        from src.api import event_stream

        now = _time.time()
        events = [
            {"type": "fresh", "collected_at": now},  # 无显式时刻=采集即新近
            {"type": "recent", "timestamp": now - 3600},
            {"type": "old", "timestamp": now - 3 * 86400},
            {"type": "iso_old", "timestamp": "2026-09-01T00:00:00+08:00"},
        ]
        resp = self._summary_with(monkeypatch, events)
        assert resp["total_events"] == 4
        assert resp["recent_count"] == 2, f"窗口计数错: {resp['recent_count']}"

    def test_all_old_events_give_zero(self, monkeypatch):
        now = _time.time()
        resp = self._summary_with(
            monkeypatch,
            [{"type": "old", "timestamp": now - 10 * 86400} for _ in range(3)],
        )
        assert resp["recent_count"] == 0
        assert resp["total_events"] == 3

    def test_window_boundary_inclusive(self, monkeypatch):
        """边界闭区间：恰在 cutoff 上计入、刚过 cutoff 不计入（时钟打桩确定性）。"""
        from src.api import event_stream

        fixed = 1_800_000_000.0

        class _T:
            @staticmethod
            def time():
                return fixed

        monkeypatch.setattr(event_stream, "time", _T)
        cutoff = fixed - event_stream.RECENT_WINDOW_S
        events = [
            {"type": "edge_in", "timestamp": cutoff},
            {"type": "edge_out", "timestamp": cutoff - 0.001},
        ]

        async def _fake_collect():
            return events

        monkeypatch.setattr(event_stream, "_collect_all_events", _fake_collect)
        resp = asyncio.run(event_stream.get_stream_summary())
        assert resp["recent_count"] == 1, f"边界语义错: {resp}"

    def test_bad_timestamp_not_counted_recent(self, monkeypatch):
        """坏时刻（解析失败=最旧）不入 recent 窗口（宁窄勿滥，与排序语义同源）。"""
        resp = self._summary_with(
            monkeypatch,
            [{"type": "bad", "timestamp": "not-a-time", "collected_at": _time.time()}],
        )
        assert resp["recent_count"] == 0
        assert resp["total_events"] == 1

    def test_window_constant_single_source(self):
        from src.api import event_stream

        assert event_stream.RECENT_WINDOW_S == 24 * 3600

    def test_recent_never_exceeds_total(self, monkeypatch):
        now = _time.time()
        events = [{"type": f"e{i}", "timestamp": now - i * 86400} for i in range(5)]
        resp = self._summary_with(monkeypatch, events)
        assert 0 <= resp["recent_count"] <= resp["total_events"]


# ── ③ 形状 + UI 恢复面 ──────────────────────────────────────────


class TestShapeAndUiRestore:
    def test_summary_six_keys_including_recent_count(self):
        from src.api import event_stream

        resp = asyncio.run(event_stream.get_stream_summary())
        assert set(resp) == {
            "total_events",
            "by_organ",
            "by_type",
            "most_active_organ",
            "recent_count",
            "collected_at",
        }

    def test_alias_same_shape(self):
        from src.api import event_stream

        alias = asyncio.run(event_stream.get_summary())
        direct = asyncio.run(event_stream.get_stream_summary())
        assert set(alias) == set(direct)

    def test_recent_card_restored_verbatim(self):
        """CD-⑮ 移除件复原语义：卡文本/图标/消费逐字恢复（㉕/㉖ 复原先例）。"""
        code = _code()
        assert "最近事件" in code, "卡文本缺席"
        assert re.search(r"(?<![.\w])Zap\b", code), "卡图标 Zap 缺席"
        assert "summary?.recent_count" in code, "recent_count 消费缺席"

    def test_interface_declares_recent_count(self):
        m = re.search(r"interface EventSummary \{([^}]*)\}", _src())
        assert m, "EventSummary 接口未找到"
        declared = set(re.findall(r"^\s+([A-Za-z_$][\w$]*):", m.group(1), re.M))
        assert "recent_count" in declared, f"接口未声明 recent_count: {declared}"

    def test_grid_five_columns(self):
        assert "grid grid-cols-2 md:grid-cols-5" in _src(), "grid 未随 5 卡收编"
