#!/usr/bin/env python3
"""CD-⑧ DedupResult 面漂移复核+收口——UI 消费面收编 POST /deduplicate 响应真源键。

背景（CD-④ 诚实边界②留档件「DedupResult 面（POST /deduplicate 响应）另有未裁决
漂移——UI `merged` 键真源不产出、`details` 元素=dict 渲染必炸 React child——
CD-③ 普查未列，另拍复核」——复核即切片本体，复核产出两处收口同拍落）：
真源 deduplicate_knowledge 返回字面量
  {total, duplicates_found, duplicates_removed, removed_ids, details?}，
  details 元素 = {id, title, duplicate_of, similarity}（dict，services/deduplication.py
字面量全解析）；修前 UI DedupResult 接口
  {duplicates_found, duplicates_removed, merged, details: string[]}：
①merged 死显示键——真源根本不产出，「已合并」统计恒空白（CD-④「真源不产出
  禁假渲染」同型）；
②details 元素按 string 渲染 {d}——真源元素是 dict，React 渲染 dict child 必炸
  「Objects are not valid as a React child」=点「自动去重」出结果面板即整页崩
  （硬断点，比 CD-④ 恒空白更重）。

收口（UI 消费面收编真源键，真源零改动=CD 系列 wire 权威纪律）：
- DedupResult 收编真源形 {total, duplicates_found, duplicates_removed,
  removed_ids, details?: DedupDetail[]}；DedupDetail 收编元素 dict 形。
- merged 死显示键收口→真源键 total（扫描条目）显示（CD-⑤「真源键派生显示」同款）。
- details 元素按 dict 真源键渲染（title/id/duplicate_of/similarity），crash 形 {d} 清零。

断言两层（禁自嗨恒真断言）：
①源码消费面：真源键真在场 + merged 死键/str 元素形/crash 形零残留 + 消费键 ⊆ 真源；
②行为桥（真 API 全链，tmp 全隔离）：POST /api/dedup/deduplicate 真端点真产出
  键集恰真源形、details 元素真 dict（crash 类锁死）、UI 消费键逐键 ⊆ 响应键。
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "dedup" / "page.tsx"

# 真源键（行为桥对账基准，services/deduplication.py 字面量全解析）
TRUE_RESULT_KEYS = {
    "total",
    "duplicates_found",
    "duplicates_removed",
    "removed_ids",
    "details",
}
TRUE_DETAIL_KEYS = {"id", "title", "duplicate_of", "similarity"}


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def _code() -> str:
    """剥行注释=只看消费代码（历史记述注释不算消费形，CD-④ 同款）。"""
    return re.sub(r"//.*$", "", _src(), flags=re.M)


# ── ① 源码消费面 ───────────────────────────────────────────────


def test_result_interface_covers_true_source_shape():
    """接口收编真源形：顶层键集恰真源五键（details 可选=早期返回分支无该键）。"""
    src = _src()
    m = re.search(r"interface DedupResult \{(.*?)\}", src, re.S)
    assert m, "DedupResult 接口不在场"
    fields = set(re.findall(r"^\s+(\w+)\??:", m.group(1), re.M))
    assert fields == TRUE_RESULT_KEYS, f"接口键集漂移: {fields}"
    assert re.search(r"details\?: DedupDetail\[\];", src), "details 可选 dict 数组形未收编"


def test_detail_interface_element_dict_shape():
    """details 元素=dict 形收编：DedupDetail 字段恰真源元素四键。"""
    src = _src()
    m = re.search(r"interface DedupDetail \{(.*?)\}", src, re.S)
    assert m, "DedupDetail 接口不在场（修前元素按 string[]）"
    fields = set(re.findall(r"^\s+(\w+):", m.group(1), re.M))
    assert fields == TRUE_DETAIL_KEYS, f"元素键集漂移: {fields}"
    assert "details: string[]" not in src, "修前 str[] 元素形残留"


def test_merged_dead_key_removed():
    """merged 死显示键收口：真源不产出的键禁假渲染（CD-④ 纪律）。"""
    code = _code()
    assert re.search(r"\bmerged\b", code) is None, "merged 死键消费残留"
    assert "已合并" not in code, "「已合并」死显示残留"


def test_total_true_key_display():
    """收口显示面改走真源键 total（扫描条目）。"""
    code = _code()
    assert "dedupResult.total" in code, "真源键 total 未被消费"
    assert "扫描条目" in code, "total 显示面未落"


def test_details_element_renders_dict_keys_not_object():
    """元素渲染收编 dict 真源键；crash 形 {d}（渲染 dict child）清零。"""
    code = _code()
    for expr in ("d.title", "d.id", "d.duplicate_of", "d.similarity"):
        assert expr in code, f"元素真源键未被消费: {expr}"
    assert re.search(r"\{d\}", code) is None, "crash 形 {d} 渲染 dict child 残留"


def test_consumed_keys_subset_of_true_source():
    """消费 ⊆ 真源：UI 消费的每个顶层键/元素键真源产出（发明键此断言先炸）。"""
    code = _code()
    top = set(re.findall(r"\bdedupResult\.([A-Za-z_$][\w$]*)", code))
    seg_start = code.index("dedupResult.details.slice")
    seg = code[seg_start : seg_start + 600]
    detail = set(re.findall(r"\bd\.([A-Za-z_$][\w$]*)", seg))
    assert top, "顶层消费键提取失效=断言空转"
    assert detail, "元素消费键提取失效=断言空转"
    for k in top:
        assert k in TRUE_RESULT_KEYS, f"顶层发明键（真源不产出）: {k}"
    for k in detail:
        assert k in TRUE_DETAIL_KEYS, f"元素发明键（真源不产出）: {k}"


# ── ② 行为桥（真 API 全链，tmp 全隔离绝不写生产库）──────────────


ROWS = [
    {
        "id": "dup-x1",
        "user_id": "u1",
        "title": "同一标题",
        "content": "同一内容",
        "created_at": "t1",
    },
    {
        "id": "dup-y2",
        "user_id": "u1",
        "title": "同一标题",
        "content": "同一内容",
        "created_at": "t2",
    },
]


@pytest.fixture()
def env(monkeypatch):
    import src.api.user as user_mod
    import src.services.deduplication as dedup_mod

    deleted: list[tuple] = []

    async def fake_fetch(sql, *args):
        return ROWS

    async def fake_delete(kid, owner):
        deleted.append((kid, owner))
        return True

    monkeypatch.setattr(dedup_mod, "db_pool", SimpleNamespace(fetch=fake_fetch))
    monkeypatch.setattr(dedup_mod, "delete_knowledge", fake_delete)

    import src.api.dedup as dedup_api

    app = FastAPI()
    app.include_router(dedup_api.router, prefix="/api/dedup")
    app.dependency_overrides[user_mod.get_current_user] = lambda: uuid.uuid4()
    client = TestClient(app)
    client.deleted = deleted  # type: ignore[attr-defined]
    return client


def test_api_result_keyset_exact(env):
    """真端点响应键集恰真源五键（早期返回/全量返回共面收口后全量路径）。"""
    r = env.post("/api/dedup/deduplicate")
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data) == TRUE_RESULT_KEYS, f"响应键集漂移: {set(data)}"


def test_details_element_is_dict_not_str(env):
    """crash 类锁死：details 元素真 dict（修前 UI 按 string 渲染必炸 React child）。"""
    r = env.post("/api/dedup/deduplicate")
    data = r.json()
    assert data["details"], "details 为空=断言空转"
    for el in data["details"]:
        assert isinstance(el, dict), f"details 元素非 dict: {type(el)}"
        assert set(el) == TRUE_DETAIL_KEYS, f"元素键集漂移: {set(el)}"
        assert not isinstance(el, str)


def test_counts_and_removed_ids_real(env):
    """计数真源语义：发现 1/删除 1/removed_ids=真删 id/total=扫描 2 条。"""
    r = env.post("/api/dedup/deduplicate")
    data = r.json()
    assert data["total"] == 2
    assert data["duplicates_found"] == 1
    assert data["duplicates_removed"] == 1
    assert data["removed_ids"] == ["dup-y2"]
    assert env.deleted == [("dup-y2", "u1")], "主删除面真被消费（H1-① 唯一删除路径）"


def test_early_return_shape_guarded(env, monkeypatch):
    """早期返回分支无 details 键——UI 守卫 dedupResult.details && 在场不裸读。"""
    import src.services.deduplication as dedup_mod

    async def one_row(sql, *args):
        return ROWS[:1]

    monkeypatch.setattr(dedup_mod, "db_pool", SimpleNamespace(fetch=one_row))
    r = env.post("/api/dedup/deduplicate")
    data = r.json()
    assert "details" not in data, "早期返回分支凭空产出 details=fabricate"
    assert set(data) == {"total", "duplicates_found", "duplicates_removed", "removed_ids"}
    code = _code()
    assert "dedupResult.details &&" in code, "details 守卫缺失（缺键分支会裸读）"


def test_ui_consumed_keys_subset_of_api_response(env):
    """消费 ⊆ 真源（端到端）：UI 顶层消费键逐键真端点真产出。"""
    r = env.post("/api/dedup/deduplicate")
    data = r.json()
    code = _code()
    top = set(re.findall(r"\bdedupResult\.([A-Za-z_$][\w$]*)", code))
    for k in top:
        assert k in data, f"UI 消费键端点不产出: {k}"


def test_duplicates_report_face_zero_regression(env):
    """GET /duplicates 顶层键（CD-④ produced 面）零回归。"""
    r = env.get("/api/dedup/duplicates")
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data) == {"total_pairs", "duplicates"}
    assert isinstance(data["duplicates"], list)
