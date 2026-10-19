#!/usr/bin/env python3
"""DEDUP-①（DEC-㉕-① 自决取 B）——dedup 报告元素时间/类型显示恢复（真源补现）。

pending-decisions ㉕（CD-④ 留档「恢复时间/类型显示需真源形状变更，不自决另拍」）：
按「别停工等批」铁律 + DEC-①~㉓ 自决先例自决取 B 先行执行（异议回「㉕改A」
即 revert 回不显示时间/类型、「㉕改B2」等即改道）。

收口：
- 真源补现（get_duplicate_report）：元素 item 内补 created_at/content_type
  （knowledge 真列直出零 fabrication，SELECT 补 content_type 列）——裁决"删 A
  还是删 B"按时间保早/保新的核心语义（㉕ 权衡原文）。平铺形（created_at_a/
  content_type 顶层）仍不产出（CD-④ 裁决主轴零变化）。
- UI 恢复（dedup/page.tsx）：时间行/formatTime（CD-④ 移除件复原，语义逐字
  一致）+类型徽章消费嵌套新键；缺失如实略禁 'unknown' fabrication（CD-④
  fabrication 纪律不变）。
- 裁决勘误（历史裁决文本不改写）：CD-③「created_at_*/content_type 真源根本
  不产出」→「报告字面量不产出、DB 真列在场」。

断言三层（禁自嗨恒真断言）：
①真源行为桥：get_duplicate_report 真跑逐值对账（逐 item 精确值不互串）；
②UI 消费面：嵌套键真消费+缺失如实略+'unknown' 禁回潮+平铺形禁回潮；
③行为桥：UI 消费元素键 ⊆ 真源产出键（真源形状再漂移此桥先炸）。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "dedup" / "page.tsx"


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def _rows():
    """SELECT 行形（含真列 created_at/content_type，逐 item 值可区分）。"""
    return [
        {
            "id": "a1",
            "user_id": "u1",
            "title": "同一标题",
            "content": "同一内容",
            "created_at": "2026-10-01 08:00:00",
            "content_type": "text",
        },
        {
            "id": "b2",
            "user_id": "u1",
            "title": "同一标题",
            "content": "同一内容",
            "created_at": "2026-10-02 09:30:00",
            "content_type": "markdown",
        },
    ]


def _run_report(rows, monkeypatch, user_id=None):
    from src.services import deduplication

    seen_sql = []

    async def fake_fetch(sql, *args):
        seen_sql.append(sql)
        return rows

    monkeypatch.setattr(deduplication, "db_pool", SimpleNamespace(fetch=fake_fetch))
    report = asyncio.run(deduplication.get_duplicate_report(user_id))
    return report, seen_sql


# ── ① 真源补现本体（get_duplicate_report）────────────────────────


class TestTrueSourceRestoration:
    def test_items_carry_created_at_and_content_type_verbatim(self, monkeypatch):
        """真列直出：逐 item 精确值（t1/t2、text/markdown 不互串=逐值对账）。"""
        report, _ = _run_report(_rows(), monkeypatch)
        assert len(report) == 1
        el = report[0]
        assert el["item_a"]["created_at"] == "2026-10-01 08:00:00"
        assert el["item_a"]["content_type"] == "text"
        assert el["item_b"]["created_at"] == "2026-10-02 09:30:00"
        assert el["item_b"]["content_type"] == "markdown"

    def test_select_includes_content_type_column_both_branches(self, monkeypatch):
        """SELECT 补 content_type 列（user 过滤/全库两分支同形）。"""
        for uid in ("u1", None):
            _, sqls = _run_report(_rows(), monkeypatch, user_id=uid)
            assert len(sqls) == 1
            assert "content_type" in sqls[0], f"SELECT 未补 content_type 列: {uid}"

    def test_missing_values_pass_through_without_fabrication(self, monkeypatch):
        """真列值缺失（None/''）原样透传禁 fabricate（'unknown'/'-' 只属显示层判法）。"""
        rows: list[dict] = _rows()
        rows[0]["content_type"] = None
        rows[1]["content_type"] = ""
        rows[0]["created_at"] = None
        report, _ = _run_report(rows, monkeypatch)
        el = report[0]
        assert el["item_a"]["content_type"] is None
        assert el["item_b"]["content_type"] == ""
        assert el["item_a"]["created_at"] is None

    def test_top_level_shape_and_flat_keys_unchanged(self, monkeypatch):
        """既有契约零变化：顶层恰 3 键、平铺形仍不产出（CD-④ 裁决主轴锁死）。"""
        report, _ = _run_report(_rows(), monkeypatch)
        el = report[0]
        assert set(el.keys()) == {"item_a", "item_b", "similarity"}
        for flat_key in (
            "id_a",
            "id_b",
            "title_a",
            "title_b",
            "content_type",
            "created_at_a",
            "created_at_b",
        ):
            assert flat_key not in el, f"平铺形回潮: {flat_key}"
        # dedup 主删除面 details 元素契约零变化（范围纪律：本拍只动报告面）
        assert set(el["item_a"].keys()) == {"id", "title", "created_at", "content_type"}


# ── ② UI 消费面 ─────────────────────────────────────────────────


class TestUiConsumption:
    def test_time_rows_restored_on_nested_keys(self):
        """时间行恢复：formatTime（CD-④ 移除件复原）只喂嵌套真源键。"""
        code = re.sub(r"//.*$", "", _src(), flags=re.M)
        assert "function formatTime(" in code
        assert "formatTime(pair.item_a.created_at)" in code
        assert "formatTime(pair.item_b.created_at)" in code
        # 平铺形禁回潮（CD-④ 裁决主轴）
        assert "formatTime(pair.created_at_a)" not in code
        assert "formatTime(pair.created_at_b)" not in code

    def test_type_badge_restored_guarded_no_unknown(self):
        """类型徽章恢复：缺失如实略（条件渲染）禁 'unknown' fabrication 回潮。"""
        code = re.sub(r"//.*$", "", _src(), flags=re.M)
        assert "pair.item_a.content_type" in code
        assert "pair.item_b.content_type" in code
        # 条件渲染守卫在场（缺失不渲染徽章）
        assert "pair.item_a.content_type ?" in code
        assert "pair.item_b.content_type ?" in code
        assert "'unknown'" not in code and '"unknown"' not in code

    def test_interface_declares_restored_keys(self):
        """接口声明收编真源形（元素 4 键，平铺容器接口仍零容忍）。"""
        src = _src()
        item_iface = src.split("interface DuplicateItem {", 1)[1].split("}", 1)[0]
        for key in ("id", "title", "created_at", "content_type"):
            assert re.search(rf"^\s+{key}\s*:", item_iface, re.M), f"元素接口缺键: {key}"
        pair_iface = src.split("interface DuplicatePair {", 1)[1].split("}", 1)[0]
        for key in ("created_at_a", "created_at_b", "content_type"):
            assert not re.search(rf"^\s+{key}\s*:", pair_iface, re.M), f"平铺容器接口回潮: {key}"

    def test_search_filter_consumes_restored_type_key(self):
        """过滤面随真源补现回潮（CD-④ 移除前语义复原）：content_type 带空值守卫。"""
        code = re.sub(r"//.*$", "", _src(), flags=re.M)
        assert "(p.item_a.content_type || '')" in code
        assert "(p.item_b.content_type || '')" in code


# ── ③ 行为桥（UI 消费 ⊆ 真源产出）────────────────────────────────


class TestBehaviorBridge:
    def test_ui_consumed_item_keys_subset_of_true_source(self, monkeypatch):
        """UI 消费的每个元素键 get_duplicate_report 真产出（消费 ⊆ 真源）。

        真源实跑取元素键集 + UI 消费键从源码提取（pair.item_X.<key>）——
        未来真源形状再漂移（或 UI 又发明键）此桥先炸。
        """
        report, _ = _run_report(_rows(), monkeypatch)
        item_shape = set(report[0]["item_a"].keys())
        src = _src()
        consumed = set(re.findall(r"\bpair\.item_[ab]\.([A-Za-z_$][\w$]*)", src))
        assert consumed, "UI 未提取到元素消费键（提取失效=断言空转）"
        for k in consumed:
            assert k in item_shape, f"UI 消费键真源不产出: {k}"

    def test_displayed_values_come_verbatim_from_row(self, monkeypatch):
        """显示值=行值逐字（真列直出零改写）：显示层只做 formatTime/条件渲染。"""
        report, _ = _run_report(_rows(), monkeypatch)
        el = report[0]
        src = _src()
        # 时间行直接喂 created_at（无中间改写键）
        for side in ("item_a", "item_b"):
            assert f"formatTime(pair.{side}.created_at)" in src
            assert el[side]["created_at"] in ("2026-10-01 08:00:00", "2026-10-02 09:30:00")
        # 类型徽章直接渲染 content_type（无映射表/无兜底串）
        for side in ("item_a", "item_b"):
            assert f"pair.{side}.content_type}}" in src
