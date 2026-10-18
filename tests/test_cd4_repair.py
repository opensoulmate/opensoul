#!/usr/bin/env python3
"""CD-④ 修复候选①——dedup 元素键收编（UI 消费面收编真源嵌套元素键）回归锁死。

背景（CD-③ 深挖裁决 confirmed_drift 8 键→候选①）：dedup/page.tsx DuplicatePair
接口按平铺 8 键形（id_a/id_b/title_a/title_b/content_type/created_at_a/
created_at_b/similarity）消费，而真源 get_duplicate_report 元素=嵌套形
{item_a:{id,title}, item_b:{id,title}, similarity}（services/deduplication.py
字面量全解析，tests/test_contract_drift_deep_dive.py 证据锁同源）——
8 键仅 similarity 在场：标题/ID 全空渲染（title_a||id_a 双双 undefined）、
删除按钮 id 失效（onRemove(undefined)）、搜索过滤 p.id_a.toLowerCase() 对
undefined 必炸（输入即页崩）、content_type 徽章恒 'unknown' 假渲染。

收口（UI 消费面收编真源键，真源零改动=CD 系列 wire 权威纪律）：
- DuplicatePair 收编嵌套形 {item_a:{id,title}, item_b:{id,title}, similarity}，
  消费点全改 item_a/item_b 嵌套访问（标题/ID/删除按钮/过滤/React key）。
- created_at_*/content_type 真源根本不产出（CD-③ 裁决原文）→ 死显示面收口
  （时间行/formatTime/'unknown' 徽章移除禁假渲染），时间/类型显示恢复=真源补现
  候选 pending-decisions ㉕（knowledge 真列在场，涉证据锁"真源形状变了→裁决
  失效须复挖"，需拍板）。

断言两层（禁自嗨恒真断言）：
①源码消费面：嵌套真源键真在场 + 平铺漂移键零残留 + 死显示面零残留；
②census 工具真跑真仓（scripts/census_contract_drift.py）：7 个平铺漂移键
  全部从 findings 消失 + 消费键集合 ⊆ 真源元素形状（行为桥=UI 消费的每个键
  get_duplicate_report 真产出，get_duplicate_report 实跑取证）。
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "dedup" / "page.tsx"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd4_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd4_repair"] = cdc
_spec.loader.exec_module(cdc)

# CD-③ 裁决的 7 个平铺漂移键（similarity 为真源在场键不在列）
FLAT_DRIFT_KEYS = (
    "id_a",
    "id_b",
    "title_a",
    "title_b",
    "content_type",
    "created_at_a",
    "created_at_b",
)


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


# ── ① 源码消费面 ───────────────────────────────────────────────


def test_ui_consumes_nested_element_keys():
    """收编本体：标题/ID/删除按钮/React key 全走真源嵌套键。"""
    src = _src()
    for expr in (
        "pair.item_a.title",
        "pair.item_b.title",
        "pair.item_a.id",
        "pair.item_b.id",
        "p.item_a.id",
        "p.item_b.id",
    ):
        assert expr in src, f"真源嵌套键未被消费: {expr}"
    # React key 也收编嵌套 id（修前平铺 pairKey 双 undefined）
    assert "${pair.item_a.id}-${pair.item_b.id}" in src


def test_flat_drift_keys_absent_from_ui():
    """平铺漂移键消费形零残留（注释里的历史记述不算消费形）。"""
    src = _src()
    for key in FLAT_DRIFT_KEYS:
        # 消费形=点访问（pair./p./data. 前缀）或接口字段声明
        for pat in (
            rf"\bpair\.{key}\b",
            rf"\bp\.{key}\b",
            rf"\bdata\.{key}\b",
            rf"^\s+{key}: string;",
        ):
            assert re.search(pat, src, re.M) is None, f"平铺漂移键残留: {key} ({pat})"


def test_delete_buttons_pass_real_ids():
    """删除按钮 id 失效收口：onRemove 传真源 item id。"""
    src = _src()
    assert "onRemove(pair.item_b.id)" in src
    assert "onRemove(pair.item_a.id)" in src
    # 本地移除过滤同样收编（修前平铺键恒 undefined=过滤恒 no-op 形）
    assert "p.item_a.id !== id && p.item_b.id !== id" in src


def test_search_filter_consumes_real_keys_with_guards():
    """过滤收口：真源键带空值守卫（修前平铺 id 裸 toLowerCase 必炸=搜索即页崩）。"""
    src = _src()
    assert "(p.item_a.title || '')" in src
    assert "(p.item_b.title || '')" in src
    assert "(p.item_a.id || '')" in src
    assert "(p.item_b.id || '')" in src


def test_dead_display_faces_removed():
    """真源不产出的显示面死面清零：时间行/formatTime/'unknown' 徽章禁假渲染。"""
    # 断言只看消费代码（行注释里的历史记述不算消费形）
    code = re.sub(r"//.*$", "", _src(), flags=re.M)
    assert "formatTime" not in code, "formatTime 死助手残留（时间显示真源无产出）"
    assert "created_at" not in code, "时间显示消费残留"
    assert "'unknown'" not in code and '"unknown"' not in code, "content_type 假徽章残留"


# ── ② census 工具真跑真仓 ─────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def test_census_flat_drift_keys_gone(census_report):
    """修后 findings：7 个平铺漂移键全部消失（修前 title_a/title_b 在 findings=confirmed_drift）。"""
    keys = {
        tuple(f["key"]) for f in census_report["findings"] if f["page"].endswith("dedup/page.tsx")
    }
    for k in FLAT_DRIFT_KEYS:
        assert ("duplicates", k) not in keys, f"漂移键仍在 findings: {k}"
    # 顶层真源键消费照旧（produced 面零回归）
    assert ("duplicates",) in keys
    assert ("total_pairs",) in keys


def test_consumed_keys_subset_of_true_source_shape(monkeypatch):
    """行为桥：UI 消费的每个元素键 get_duplicate_report 真产出（消费 ⊆ 真源）。

    真源实跑（tmp 数据形状=SELECT 行）：元素={item_a:{id,title},item_b:{id,title},
    similarity}；UI 消费键全集从中提取（pair./p. 点访问）逐键断言在场——
    未来真源形状再漂移（或 UI 又发明键）此桥先炸。
    """
    from src.services import deduplication

    rows = [
        {
            "id": "a1",
            "user_id": "u1",
            "title": "同一标题",
            "content": "同一内容",
            "created_at": "t1",
            "content_type": "text",
        },
        {
            "id": "b2",
            "user_id": "u1",
            "title": "同一标题",
            "content": "同一内容",
            "created_at": "t2",
            "content_type": "text",
        },
    ]

    async def fake_fetch(sql, *args):
        return rows

    monkeypatch.setattr(deduplication, "db_pool", SimpleNamespace(fetch=fake_fetch))
    report = asyncio.run(deduplication.get_duplicate_report())
    assert len(report) == 1
    el = report[0]

    src = _src()
    pair_keys = set(re.findall(r"\bpair\.([A-Za-z_$][\w$]*)", src))
    item_keys = set(re.findall(r"\bpair\.item_[ab]\.([A-Za-z_$][\w$]*)", src))
    pair_keys -= {"item_a", "item_b"}  # 元素容器本身
    assert pair_keys, "UI 未提取到消费键（提取失效=断言空转）"
    assert item_keys, "UI 未提取到元素消费键（提取失效=断言空转）"
    for k in pair_keys:
        assert k in el, f"UI 消费键真源不产出: {k}"
    for k in item_keys:
        assert k in el["item_a"] and k in el["item_b"], f"元素消费键真源不产出: {k}"
