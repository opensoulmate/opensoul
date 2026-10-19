#!/usr/bin/env python3
"""CD-㉗ knowledge-graph relations 元素键收编——source_entity_id↔source_id 漂移收口
（真源零改动）回归锁死。

背景（CD-㉒ confirmed_drift 修复候选第⑤件「knowledge-graph relations 元素键收编
（source_entity_id/target_entity_id/relation_type→source_id/target_id/type）」，
含同根扩面 EXPANDED_FINDINGS relation_type/target_entity_id）：
GET /api/graph/entities/{id}（src/api/graph.py:71 get_entity_detail →
src/services/entity.py:52 get_entity_with_relations）的 relations 元素=dict(r)
（r.* 行直出，entity.py:68），行键恰 id/source_id/target_id/type/properties/
created_at + JOIN 别名 source_name/target_name（DDL src/main.py:297-299）；
而 UI EntityDetail.relations 元素声明 source_entity_id/target_entity_id/
relation_type——真源无这些键：rel.source_entity_id（knowledge-graph/page.tsx
isSource 判定恒 false）、rel.relation_type（关系类型徽章恒空）、rel.target_entity_id
/rel.source_entity_id.slice(0,8) 对 undefined 调 slice=TypeError 渲染崩坏面。

收口（真源零改动=CD 系列 wire 权威纪律；CD-㉓/㉔/㉕/㉖「UI 消费面收编真源」先例）：
- EntityDetail.relations 元素接口收编真源行键形 {id, source_id, target_id, type}
  （键集恰 UI 真消费面 ⊆ 真源产出键）；
- isSource 判定/关系类型徽章/两端 id 切片三处消费点收编真源键（rel.source_id/
  rel.type/rel.target_id）。
注意范围：relationForm.*（POST /api/graph/relations 请求契约 RelationCreate
source_entity_id/target_entity_id/relation_type）与 GraphEdge.relation_type
（/full 图边真源键）是真契约零漂移，不在收编面。

断言四层（禁自嗨恒真断言）：
①源码消费面：修前漂移键零残留（rel.source_entity_id/rel.target_entity_id/
  rel.relation_type）+ 元素接口键集恰真消费面 + rel.* 消费访问 ⊆ 真源产出键
  + 请求契约/图边不变面锁死；
②行为桥：真 get_entity_with_relations 真跑（stub db_pool 行形按 main.py DDL
  程序化派生禁手抄）——元素键集恰 DDL 列 ∪ JOIN 别名、UI 真消费键 ⊆ 产出键、
  source_entity_id 硬缺席；真 UI 判定/切片表达式逐字提取经 node 真执行吃真源行
  （修后不炸+方向正确；修前表达式 TypeError 崩坏硬反证）；
③真源锚点：main.py relations DDL + entity.py dict(r) 直出/JOIN 别名/无重命名
  （真源零改动锁）；
④census 真跑真仓：relations.* 漂移 findings 归零 + relations 消费键 ⊆ 真源
  产出键。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UI = REPO / "admin-ui" / "app" / "knowledge-graph" / "page.tsx"
ENTITY = REPO / "src" / "services" / "entity.py"
MAIN = REPO / "src" / "main.py"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd27_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd27_repair"] = cdc
_spec.loader.exec_module(cdc)

ui_src = UI.read_text(encoding="utf-8")
entity_src = ENTITY.read_text(encoding="utf-8")
main_src = MAIN.read_text(encoding="utf-8")

# UI 真消费面（修后 relations 元素访问键集）
CONSUMED_ELEMENT_KEYS = {"id", "source_id", "target_id", "type"}

# 修前漂移消费/声明键（真源不产出）
OLD_DRIFT_LITERALS = ("rel.source_entity_id", "rel.target_entity_id", "rel.relation_type")


def _relations_ddl_columns() -> set:
    """relations 表列名从 main.py DDL 程序化派生（禁手抄防双源漂移）。"""
    start = main_src.index("CREATE TABLE IF NOT EXISTS relations (")
    seg = main_src[start:]
    seg = seg[seg.index("(") + 1 : seg.index(")")]
    cols = set()
    for part in seg.split(","):
        token = part.strip().split()
        if token:
            cols.add(token[0])
    return cols


DDL_COLS = _relations_ddl_columns()
# JOIN 别名（entity.py get_entity_with_relations SELECT ... AS source_name/target_name）
JOIN_ALIASES = {"source_name", "target_name"}
TRUE_ELEMENT_KEYS = DDL_COLS | JOIN_ALIASES


# ── ① 源码消费面 ─────────────────────────────────────────────


def test_old_drift_keys_gone():
    """修前漂移消费/声明键零残留（rel.* 三键 + 元素接口声明形）。"""
    for dead in OLD_DRIFT_LITERALS:
        assert dead not in ui_src, f"旧漂移键残留: {dead}"
    m = re.search(r"relations\?: Array<\{([^}]*)\}>;", ui_src)
    assert m, "EntityDetail.relations 元素接口声明缺失"
    block = m.group(1)
    for dead in ("source_entity_id", "target_entity_id", "relation_type", "properties"):
        assert not re.search(rf"^\s*{dead}\s*:", block, flags=re.M), f"元素接口死键残留: {dead}"


def test_interface_element_keys_exact_consumed_face():
    """元素接口键集恰 UI 真消费面 {id, source_id, target_id, type}（⊆ 真源产出键）。"""
    m = re.search(r"relations\?: Array<\{([^}]*)\}>;", ui_src)
    assert m
    keys = set(re.findall(r"^\s*(\w+)\s*:", m.group(1), flags=re.M))
    assert keys == CONSUMED_ELEMENT_KEYS, f"元素接口键集漂移: {keys}"
    assert keys <= TRUE_ELEMENT_KEYS, f"接口键越出真源产出键: {keys - TRUE_ELEMENT_KEYS}"


def test_consumed_access_subset_true_source():
    """rel.* 消费访问 ⊆ 真源产出键（防再发明键），且三消费点全收编。"""
    accessed = set(re.findall(r"\brel\.(\w+)", ui_src))
    assert accessed == CONSUMED_ELEMENT_KEYS, f"消费访问越界/缺项: {accessed}"
    assert accessed <= TRUE_ELEMENT_KEYS
    assert "rel.source_id === selectedEntity.id" in ui_src, "isSource 判定未收编真源键"
    assert "{rel.type}" in ui_src, "关系类型徽章未收编真源键"
    assert "rel.target_id.slice(0, 8)" in ui_src and "rel.source_id.slice(0, 8)" in ui_src, (
        "两端 id 切片未收编真源键"
    )


def test_request_contract_and_graph_edge_unchanged():
    """不变面：relationForm 请求契约（RelationCreate 三键）与 GraphEdge.relation_type
    （/full 图边真源键）是真契约零漂移，禁误伤。"""
    assert "relationForm.source_entity_id" in ui_src, "请求契约键被误删（RelationCreate 真键）"
    assert "relationForm.target_entity_id" in ui_src
    assert "relationForm.relation_type" in ui_src
    m = re.search(r"interface GraphEdge \{([^}]*)\}", ui_src)
    assert m and "relation_type" in m.group(1), "GraphEdge.relation_type 真源键被误删"


# ── ② 行为桥：真 get_entity_with_relations 真跑 + 真 UI 表达式 node 真执行 ──


def _fake_pool(rel_rows):
    import src.services.entity as entity_mod

    entity_row = {
        "id": "aaaaaaaa-1111",
        "user_id": "u-1",
        "name": "E1",
        "type": "person",
        "description": "d",
        "properties": "{}",
        "created_at": "2026-01-01",
        "updated_at": "2026-01-01",
    }

    class _Row(dict):
        pass

    class _Pool:
        async def fetchrow(self, sql, *args):
            return _Row(entity_row)

        async def fetch(self, sql, *args):
            return [_Row(r) for r in rel_rows]

    return entity_mod, _Pool()


def test_relation_element_shape_from_real_service(monkeypatch):
    """真函数真跑：relations 元素键集恰 DDL 列 ∪ JOIN 别名，UI 真消费键 ⊆ 产出键、
    source_entity_id 硬缺席（行形按 main.py DDL 程序化派生禁手抄）。"""
    assert DDL_COLS == {"id", "source_id", "target_id", "type", "properties", "created_at"}, (
        f"DDL 列解析漂移: {DDL_COLS}"
    )
    rel_row = {
        "id": "r-1",
        "source_id": "aaaaaaaa-1111",
        "target_id": "bbbbbbbb-2222",
        "type": "related",
        "properties": "{}",
        "created_at": "2026-01-02",
        "source_name": "E1",
        "target_name": "E2",
    }
    entity_mod, pool = _fake_pool([rel_row])
    monkeypatch.setattr(entity_mod, "db_pool", pool)
    out = asyncio.run(entity_mod.get_entity_with_relations("aaaaaaaa-1111", "u-1"))
    assert out is not None and len(out["relations"]) == 1
    elem = out["relations"][0]
    assert set(elem.keys()) == TRUE_ELEMENT_KEYS, f"元素键漂移: {set(elem.keys())}"
    assert "source_entity_id" not in elem and "relation_type" not in elem
    assert CONSUMED_ELEMENT_KEYS <= set(elem.keys()), "UI 真消费键真源不产出"
    assert elem["source_id"] == "aaaaaaaa-1111" and elem["type"] == "related"


def test_relation_empty_branch_shape(monkeypatch):
    """无关系分支：relations=[]，元素形状不适用但实体键照常（修前 UI 对空列表不炸）。"""
    entity_mod, pool = _fake_pool([])
    monkeypatch.setattr(entity_mod, "db_pool", pool)
    out = asyncio.run(entity_mod.get_entity_with_relations("aaaaaaaa-1111", "u-1"))
    assert out["relations"] == []


def _extract_ui_expressions():
    """从 UI 源逐字提取修后判定/切片表达式（禁在测试里另写一套逻辑）。"""
    m_is = re.search(r"const isSource = (rel\.\w+ === selectedEntity\.id);", ui_src)
    assert m_is, "isSource 判定表达式提取失败"
    m_slice = re.search(
        r"\{isSource \? '→' : '←'\} \{(isSource \? rel\.\w+\.slice\(0, 8\) : rel\.\w+\.slice\(0, 8\))\}",
        ui_src,
    )
    assert m_slice, "id 切片表达式提取失败"
    return m_is.group(1), m_slice.group(1)


def _node_eval(prog: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=60)


def test_ui_predicates_node_bridge_on_true_shape():
    """真 UI 表达式逐字提取经 node 真执行吃真源行形：修后不炸+方向正确。"""
    if shutil.which("node") is None:
        pytest.skip("node 不在 PATH（工具缺席非代码失败）")
    is_src, slice_src = _extract_ui_expressions()
    prog = (
        "const rel = {id:'r-1', source_id:'aaaaaaaa-1111', target_id:'bbbbbbbb-2222', "
        "type:'related', properties:'{}', created_at:'2026-01-02', "
        "source_name:'E1', target_name:'E2'};\n"
        f"function f(selectedEntity){{ const isSource = {is_src}; return {{isSource, side: {slice_src}}}; }}\n"
        "const out = [f({id:'aaaaaaaa-1111'}), f({id:'bbbbbbbb-2222'})];\n"
        "console.log(JSON.stringify(out));"
    )
    proc = _node_eval(prog)
    assert proc.returncode == 0, f"node 执行失败（修后表达式仍炸）: {proc.stderr}"
    out = json.loads(proc.stdout)
    assert out[0] == {"isSource": True, "side": "bbbbbbbb"}, f"出边方向错: {out[0]}"
    assert out[1] == {"isSource": False, "side": "aaaaaaaa"}, f"入边方向错: {out[1]}"


def test_pre_fix_predicates_crash_on_true_shape():
    """修前表达式吃真源行形=TypeError 崩坏硬反证（渲染崩坏面真断点）。"""
    if shutil.which("node") is None:
        pytest.skip("node 不在 PATH（工具缺席非代码失败）")
    prog = (
        "const rel = {id:'r-1', source_id:'aaaaaaaa-1111', target_id:'bbbbbbbb-2222', type:'related'};\n"
        "const selectedEntity = {id:'aaaaaaaa-1111'};\n"
        "try { const isSource = rel.source_entity_id === selectedEntity.id;\n"
        "  isSource ? rel.target_entity_id.slice(0,8) : rel.source_entity_id.slice(0,8);\n"
        "  console.log('NO-ERROR');\n"
        "} catch (e) { console.log('CRASH:' + e.constructor.name); }"
    )
    proc = _node_eval(prog)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "CRASH:TypeError", f"修前表达式未炸=反证失效: {proc.stdout}"


# ── ③ 真源锚点 ───────────────────────────────────────────────


def test_true_source_anchors():
    """main.py relations DDL + entity.py dict(r) 直出/JOIN 别名锚点（单一源）。"""
    assert "id TEXT PRIMARY KEY, source_id TEXT NOT NULL," in main_src, "relations DDL 锚点漂移"
    assert "target_id TEXT NOT NULL, type TEXT NOT NULL," in main_src, "relations DDL 锚点漂移"
    assert 'entity["relations"] = [dict(r) for r in relations]' in entity_src, (
        "get_entity_with_relations 元素直出锚点漂移"
    )
    assert "se.name AS source_name, te.name AS target_name" in entity_src, "JOIN 别名锚点漂移"


def test_true_source_zero_change_lock():
    """真源零改动锁：get_entity_with_relations 函数段不做键重命名（无 source_entity_id
    产出）——真源形状变了须先改本锁再改 UI。"""
    start = entity_src.index("async def get_entity_with_relations")
    seg = entity_src[start : entity_src.index("async def list_entities")]
    assert "source_entity_id" not in seg and "relation_type" not in seg, "真源被改出重命名键"
    assert "_row_to_relation" not in seg, "元素直出被改为重命名路径=真源形状变更须复挖"


# ── ④ census 工具真跑真仓 ─────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def _kg_relation_findings(census_report):
    return [
        f
        for f in census_report["findings"]
        if f["page"].endswith("knowledge-graph/page.tsx")
        and len(f["key"]) > 1
        and f["key"][0] == "relations"
    ]


def test_census_relation_drift_keys_findings_gone(census_report):
    """修后 relations.* 漂移 findings 归零（source_entity_id/target_entity_id/
    relation_type 不再作为消费键浮出）。"""
    for f in _kg_relation_findings(census_report):
        for k in f["key"]:
            assert k not in {"source_entity_id", "target_entity_id", "relation_type"}, (
                f"漂移 findings 残留: {f}"
            )


def test_census_relation_consumed_subset_true_source(census_report):
    """relations 元素消费键 ⊆ 真源产出键（元素级 ∪ relations 容器键，禁再发明键）。

    工具边界如实记：census 对链式访问（rel.target_id.slice）不提取、元素级与
    容器级并进同一 findings 键集（CD-⑨ 既有边界，修前修后同边界）——故断言面为
    提取到的 relations.* 键 ⊆ 真源键，提取不足不判假阳。
    """
    allowed = TRUE_ELEMENT_KEYS | {"relations"}
    found = set()
    for f in _kg_relation_findings(census_report):
        for k in f["key"]:
            found.add(k)
            assert k in allowed, (
                f"census 消费键真源不产出: {f['path'] if 'path' in f else f['line']} {k}"
            )
    assert {"relations", "id", "source_id"} <= found, f"census 提取面异常窄: {found}"
