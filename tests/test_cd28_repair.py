#!/usr/bin/env python3
"""CD-㉘ knowledge-graph 实体类型收编真源——type↔entity_type 漂移收口
（真源零改动）回归锁死。

背景（CD-㉒ confirmed_drift 修复候选「knowledge-graph 实体类型收编真源
（type↔entity_type 漂移）」，含同根扩面 EXPANDED_FINDINGS e.type 列表/选项面）：
entities 行键 type 被 _row_to_entity 重命名为 entity_type（src/services/
entity.py:13 d["entity_type"] = d.pop("type", "")）——响应面无 type 键；
而 UI EntityDetail 声明 type: string 并消费 selectedEntity.type
（knowledge-graph/page.tsx 详情图标/类型徽章/徽章文本）、e.type（列表过滤/
搜索/列表渲染/关系表单选项）——真源全层无该键：类型徽章/着色恒 undefined
（显示行为损失）、e.type.toLowerCase() 对 undefined 调 toLowerCase=搜索即
页崩 TypeError 渲染崩坏面。

收口（真源零改动=CD 系列 wire 权威纪律；CD-㉓~㉗「UI 消费面收编真源」先例）：
- EntityDetail 接口收编真源键 entity_type: string（死声明键 type 清零）；
- 12 消费点收编真源键 entity_type（列表过滤/搜索/列表渲染×3/详情图标/
  详情徽章×2/关系表单选项×2/接口声明）。
注意范围：rel.type（relations 元素真源键，CD-㉗ 收编面）、GraphEdge.
relation_type（/full 图边真源键）、GraphNode.node_type、relationForm.*
（POST /api/graph/relations 请求契约 RelationCreate）是真契约零漂移，
不在收编面。

断言四层（禁自嗨恒真断言）：
①源码消费面：修前漂移键零残留（selectedEntity.type/entity.type/e.type）
  + 接口键集恰真消费面 + entity* 消费访问 ⊆ 真源产出键 + 真契约不变面锁死；
②行为桥：真 _row_to_entity/get_entity_with_relations 真跑——实体键含
  entity_type 且 type 硬缺席；真 UI 过滤/搜索表达式逐字提取经 node 真执行
  吃真源实体（修后不炸+方向正确；修前 e.type.toLowerCase() TypeError 崩坏
  硬反证）；
③真源锚点：entity.py _row_to_entity 重命名锚点（真源零改动锁）；
④census 真跑真仓：knowledge-graph 顶层 type 漂移 findings 归零。
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

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd28_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd28_repair"] = cdc
_spec.loader.exec_module(cdc)

ui_src = UI.read_text(encoding="utf-8")
entity_src = ENTITY.read_text(encoding="utf-8")

# 修前漂移消费/声明键字面量（真源不产出）
OLD_DRIFT_LITERALS = ("selectedEntity.type", "entity.type", "e.type")

# 真源产出键（_row_to_entity 重命名后实体 dict 的类型键）
TRUE_TYPE_KEY = "entity_type"


# ── ① 源码消费面 ─────────────────────────────────────────────


def test_old_drift_keys_gone():
    """修前漂移消费/声明键零残留（三字面量 + EntityDetail 接口死键 type）。
    注意：relations 元素的 type: string 是 CD-㉗ 收编面真源键，只查顶层段。"""
    for dead in OLD_DRIFT_LITERALS:
        assert dead not in ui_src, f"旧漂移键残留: {dead}"
    m = re.search(r"interface EntityDetail \{([^}]*)\}", ui_src)
    assert m, "EntityDetail 接口声明缺失"
    block = m.group(1)
    top_level = block.split("relations?:")[0]
    assert not re.search(r"^\s*type\s*:", top_level, flags=re.M), "接口死键 type 残留"
    assert re.search(r"^\s*entity_type\s*:", top_level, flags=re.M), "接口未收编 entity_type"


def test_interface_key_true_source_produced():
    """EntityDetail 类型键恰真源产出键 entity_type（⊆ _row_to_entity 产出面）。
    顶层段（relations 元素段的 type 是 CD-㉗ 真源键不在收编面）。"""
    m = re.search(r"interface EntityDetail \{([^}]*)\}", ui_src)
    assert m
    top_level = m.group(1).split("relations?:")[0]
    keys = set(re.findall(r"^\s*(\w+)\s*:", top_level, flags=re.M))
    assert TRUE_TYPE_KEY in keys, f"接口键集缺真源键: {keys}"
    assert "type" not in keys, "接口键集残留漂移键 type"
    # 真源产出面（entity.py 重命名后 dict 键）：entity_type 必在、type 必不在
    assert 'd["entity_type"] = d.pop("type", "")' in entity_src, "真源重命名锚点漂移"


def test_consumed_access_all_collected():
    """12 消费点全收编真源键（列表过滤/搜索/列表渲染×3/详情图标/详情徽章×2/
    关系表单选项×2/接口声明）。"""
    must_have = [
        "e.entity_type !== typeFilter",
        "e.entity_type.toLowerCase().includes(q)",
        "getColor(entity.entity_type)",
        "{entity.entity_type}",
        "getColor(selectedEntity.entity_type)",
        "{selectedEntity.entity_type}",
        "({e.entity_type})",
    ]
    for lit in must_have:
        assert lit in ui_src, f"消费点未收编真源键: {lit}"
    # 消费访问计数：selectedEntity.entity_type 4 处（图标/徽章×2/文本）
    # + e.entity_type 4 处（过滤/搜索/选项×2）
    assert ui_src.count("selectedEntity.entity_type") == 4, "详情面消费点计数漂移"
    assert ui_src.count("e.entity_type") == 4, "e.entity_type 消费点计数漂移"


def test_true_contract_keys_unchanged():
    """不变面：rel.type（relations 元素真源键）与 GraphEdge.relation_type
    （/full 图边真源键）、GraphNode.node_type、relationForm 请求契约禁误伤。"""
    assert "{rel.type}" in ui_src, "rel.type 真源键被误删（CD-㉗ 收编面）"
    m = re.search(r"interface GraphEdge \{([^}]*)\}", ui_src)
    assert m and "relation_type" in m.group(1), "GraphEdge.relation_type 真源键被误删"
    m = re.search(r"interface GraphNode \{([^}]*)\}", ui_src)
    assert m and "node_type" in m.group(1), "GraphNode.node_type 真源键被误删"
    assert "relationForm.relation_type" in ui_src, "请求契约键被误删（RelationCreate 真键）"
    assert "relationForm.source_entity_id" in ui_src
    assert "relationForm.target_entity_id" in ui_src


# ── ② 行为桥：真 _row_to_entity/get_entity_with_relations 真跑 + node 真执行 ──


def _fake_pool():
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
            return []

    return entity_mod, _Pool()


def test_entity_shape_from_real_service(monkeypatch):
    """真函数真跑：实体 dict 含 entity_type 且 type 硬缺席（响应面无 type 键
    的修前证据反面）。"""
    entity_mod, pool = _fake_pool()
    monkeypatch.setattr(entity_mod, "db_pool", pool)
    out = asyncio.run(entity_mod.get_entity_with_relations("aaaaaaaa-1111", "u-1"))
    assert out is not None
    assert TRUE_TYPE_KEY in out, "真源不产出 entity_type"
    assert "type" not in out, "真源仍产出 type=重命名被改掉（真源零改动锁破）"
    assert out["entity_type"] == "person"


def _extract_ui_expressions():
    """从 UI 源逐字提取修后过滤/搜索表达式（禁在测试里另写一套逻辑）。"""
    m = re.search(
        r"if \((typeFilter !== 'all' && e\.\w+ !== typeFilter)\) return false;",
        ui_src,
    )
    assert m, "过滤表达式提取失败"
    m2 = re.search(
        r"return (e\.name\.toLowerCase\(\)\.includes\(q\) \|\| e\.\w+\.toLowerCase\(\)\.includes\(q\));",
        ui_src,
    )
    assert m2, "搜索表达式提取失败"
    return m.group(1), m2.group(1)


def _node_eval(prog: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=60)


def test_ui_predicates_node_bridge_on_true_shape():
    """真 UI 表达式逐字提取经 node 真执行吃真源实体：修后不炸+方向正确。"""
    if shutil.which("node") is None:
        pytest.skip("node 不在 PATH（工具缺席非代码失败）")
    filt_src, search_src = _extract_ui_expressions()
    prog = (
        "const e = {id:'aaaaaaaa-1111', name:'E1', entity_type:'person', "
        "description:'d', properties:{}};\n"
        "function pass(typeFilter){ return !(" + filt_src + "); }\n"
        "function hit(q){ return " + search_src + "; }\n"
        # UI 管线里 q=search.toLowerCase() 在表达式前（逐字提取面外），此处同真管线喂小写 q
        "const out = [pass('all'), pass('person'), pass('concept'), "
        "hit('per'), hit('e1'), hit('zzz')];\n"
        "console.log(JSON.stringify(out));"
    )
    proc = _node_eval(prog)
    assert proc.returncode == 0, f"node 执行失败（修后表达式仍炸）: {proc.stderr}"
    out = json.loads(proc.stdout)
    assert out == [True, True, False, True, True, False], f"过滤/搜索方向错: {out}"


def test_pre_fix_predicates_crash_on_true_shape():
    """修前表达式吃真源实体=TypeError 崩坏硬反证（搜索即页崩真断点；
    selectedEntity.type 读 undefined=类型徽章恒空显示行为损失）。"""
    if shutil.which("node") is None:
        pytest.skip("node 不在 PATH（工具缺席非代码失败）")
    prog = (
        "const e = {id:'aaaaaaaa-1111', name:'E1', entity_type:'person'};\n"
        "try { e.name.toLowerCase().includes('x') || e.type.toLowerCase().includes('x');\n"
        "  console.log('NO-ERROR');\n"
        "} catch (err) { console.log('CRASH:' + err.constructor.name); }\n"
        "const selectedEntity = {entity_type:'person'};\n"
        "console.log('BADGE:' + String(selectedEntity.type));"
    )
    proc = _node_eval(prog)
    assert proc.returncode == 0
    assert proc.stdout.splitlines()[0] == "CRASH:TypeError", f"修前搜索未炸=反证失效: {proc.stdout}"
    assert proc.stdout.splitlines()[1] == "BADGE:undefined", f"修前徽章未恒空: {proc.stdout}"


# ── ③ 真源锚点 ───────────────────────────────────────────────


def test_true_source_anchors():
    """entity.py _row_to_entity 重命名锚点（单一源）。"""
    assert 'd["entity_type"] = d.pop("type", "")' in entity_src, "重命名锚点漂移"
    assert "def _row_to_entity(row) -> dict:" in entity_src, "_row_to_entity 锚点漂移"
    assert "return [_row_to_entity(r) for r in rows]" in entity_src, "list_entities 同口锚点漂移"


def test_true_source_zero_change_lock():
    """真源零改动锁：_row_to_entity 重命名语义不变（type 进 entity_type 出）
    ——真源形状变了须先改本锁再改 UI。"""
    start = entity_src.index("def _row_to_entity")
    seg = entity_src[start : entity_src.index("async def create_entity")]
    assert 'd["entity_type"] = d.pop("type", "")' in seg, "重命名被改掉=真源形状变更须复挖"
    assert 'd["type"]' not in seg, "真源反向产出 type=响应面键形变更须复挖"


# ── ④ census 工具真跑真仓 ─────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def test_census_type_drift_findings_gone(census_report):
    """修后 knowledge-graph 顶层 type 漂移 findings 归零（type 不再作为
    实体消费键浮出；rel.type 是 relations.* 元素键不在本断言面）。"""
    hits = [
        f
        for f in census_report["findings"]
        if f["page"].endswith("knowledge-graph/page.tsx") and f["key"] == ["type"]
    ]
    assert hits == [], f"顶层 type 漂移 findings 残留: {hits}"


def test_census_entity_type_consumed(census_report):
    """真源键 entity_type 已进消费面（census 提取到 knowledge-graph 实体
    entity_type 键=收编真落地可观测）。"""
    kg_keys = {
        k
        for f in census_report["findings"]
        if f["page"].endswith("knowledge-graph/page.tsx")
        for k in f["key"]
    }
    assert "entity_type" in kg_keys, f"census 未提取到 entity_type 消费键: {kg_keys}"
