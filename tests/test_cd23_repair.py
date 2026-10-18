#!/usr/bin/env python3
"""CD-㉓ agent recall 响应面消费键收编——results↔sources 漂移收口（真源零改动）回归锁死。

背景（CD-㉒ confirmed_drift 修复候选首件「agent recall 响应面消费键收编
（results↔sources 漂移+检索面板恒空）」）：
POST /api/agent/recall（src/api/agent.py:450，返回 rag_query 原样）真源返回恰
{answer, sources}（src/services/rag.py:53；空结果/无 key 分支 :14/:37 同形），
sources 元素恰 {id, score}——而 UI RecallResult 声明 {results, query} 两键全层
缺席：recallResults.results（agent-nodes/page.tsx 修前 .length ?? 0 / .map）恒回
落空列表=检索结果面板恒显示 0 条+「未找到相关记忆」假渲染（有结果也显无）；
recallResults.query 真源不产出=查询回显行恒空；answer 真源产出无人读。

收口（真源零改动=CD 系列 wire 权威纪律；CD-⑥/⑪/⑰「UI 消费面收编真源」
+CD-④/⑮「禁假渲染」+CD-⑦「死声明键清零」先例）：
- RecallResult 接口收编真源形 {answer, sources}（死声明键 results/query 清零）；
  RecallSource 元素收编真源键 {id, score}（死显示键 title/content/text 清零——
  修前元素渲染 item.title||item.id、item.content||item.text||JSON.stringify 全是
  真源不产出键）；
- answer 真源键真渲染（真源产出无人读→收编）；sources 列表/计数/空态全走真源键
  （修前 results.length===0 恒真=有结果也显「未找到相关记忆」假渲染）；
- 查询回显改本地真值 recallQuery（用户真输入非 fabricate；recallResults.query
  wire 消费键清零——回显不需要 wire 回传）。

断言四层（禁自嗨恒真断言）：
①源码消费面：旧漂移键零残留 + 真源键真消费 + 接口键集恰 {answer,sources}
  + 消费访问 ⊆ 真源产出键（防再发明键）；
②行为桥：rag_query 真函数真跑（桩 semantic_search+无 LLM key 零网络）两分支
  形状锁死（键恰 {answer,sources}、元素恰 {id,score}）——真源零改动锁
  （键名归一须先改本锁）；
③真源锚点：rag.py 两 return 锚点 + agent.py 透传锚点；
④census 真跑真仓：recall 调用点 findings 消费键 ⊆ 真源产出键 + results/query
  漂移 findings 归零。
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
UI = REPO / "admin-ui" / "app" / "agent-nodes" / "page.tsx"
RAG = REPO / "src" / "services" / "rag.py"
AGENT_API = REPO / "src" / "api" / "agent.py"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd23_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd23_repair"] = cdc
_spec.loader.exec_module(cdc)

# 真源产出键（src/services/rag.py rag_query 两分支并集；live 实证一致）
TRUE_SOURCE_KEYS = {"answer", "sources"}
SOURCE_ELEMENT_KEYS = {"id", "score"}

ui_src = UI.read_text(encoding="utf-8")
rag_src = RAG.read_text(encoding="utf-8")
agent_src = AGENT_API.read_text(encoding="utf-8")


# ── ① 源码消费面 ─────────────────────────────────────────────


def test_old_drift_keys_gone():
    """旧漂移消费/声明键零残留（results/query 死键 + 元素死显示键 title/content/text）。"""
    for dead in ("recallResults.results", "recallResults.query"):
        assert dead not in ui_src, f"旧漂移键残留: {dead}"
    # 元素死显示键（修前 item.title||item.id / item.content||item.text||JSON.stringify）
    for dead in ("item.title", "item.content", "item.text"):
        assert dead not in ui_src, f"元素死显示键残留: {dead}"


def test_true_source_keys_consumed():
    """真源键真消费（answer 真渲染 + sources 列表/计数/空态），禁只修一半。"""
    assert "recallResults.answer" in ui_src, "answer 真源键未收编（真源产出无人读）"
    assert ui_src.count("recallResults.sources") >= 3, "sources 真源键须消费于计数/列表/空态三处"
    # 查询回显改本地真值（用户真输入），非 wire 回传
    assert '查询: "{recallQuery}"' in ui_src, "查询回显未收编本地真值 recallQuery"


def test_interface_keys_exact_true_source():
    """RecallResult 接口键集恰 {answer, sources}（死声明键清零，CD-⑦ 纪律）。"""
    m = re.search(r"interface RecallResult \{([^}]*)\}", ui_src)
    assert m, "RecallResult 接口声明缺失"
    keys = set(re.findall(r"^\s*(\w+)\s*:", m.group(1), flags=re.M))
    assert keys == TRUE_SOURCE_KEYS, f"接口键集漂移: {keys}"


def test_source_element_keys_exact():
    """RecallSource 元素接口键集恰 {id, score}（真源元素键，死键不进声明）。"""
    m = re.search(r"interface RecallSource \{([^}]*)\}", ui_src)
    assert m, "RecallSource 接口声明缺失"
    keys = set(re.findall(r"^\s*(\w+)\s*:", m.group(1), flags=re.M))
    assert keys == SOURCE_ELEMENT_KEYS, f"元素接口键集漂移: {keys}"


def test_consumed_access_subset_true_source():
    """recallResults.* 消费访问 ⊆ 真源产出键（防再发明键）。"""
    accessed = set(re.findall(r"recallResults\.(\w+)", ui_src))
    assert accessed == TRUE_SOURCE_KEYS, f"消费访问越界/缺项: {accessed}"


def test_no_fake_fallback_masking():
    """计数/空态直读真源键（禁 ?? 0 回落遮蔽假 0，CD-⑱ 纪律）。"""
    assert "sources?.length" not in ui_src, "sources 计数带可选回落=回落遮蔽形"
    assert "sources.length ?? 0" not in ui_src, "假 0 回落形残留"


# ── ② 行为桥：rag_query 真函数真跑（零网络） ──────────────────


@pytest.fixture()
def rag_mod(monkeypatch):
    import src.services.rag as rag

    monkeypatch.setattr(
        rag,
        "settings",
        SimpleNamespace(llm_api_key=None, llm_base_url="", llm_model=""),
    )
    return rag


def test_rag_query_shape_nonempty_branch(rag_mod):
    """非空检索分支：响应键恰 {answer,sources}、sources 元素恰 {id,score}（形状锁死）。"""

    async def fake_search(q, uid, limit=5):
        return [
            {"id": "k-1", "score": 0.42, "chunk": "c1"},
            {"id": "k-2", "score": 0.31, "chunk": "c2"},
        ]

    rag_mod.semantic_search = fake_search
    out = asyncio.run(rag_mod.rag_query("q", "u", 2))
    assert set(out.keys()) == TRUE_SOURCE_KEYS, f"真源响应键漂移: {set(out.keys())}"
    assert len(out["sources"]) == 2
    for s in out["sources"]:
        assert set(s.keys()) == SOURCE_ELEMENT_KEYS, f"元素键漂移: {set(s.keys())}"
    assert out["sources"][0] == {"id": "k-1", "score": 0.42}


def test_rag_query_shape_empty_branch(rag_mod):
    """空结果分支：同形 {answer, sources: []}（修前 UI 对该形恒显 0 条假渲染）。"""

    async def fake_search(q, uid, limit=5):
        return []

    rag_mod.semantic_search = fake_search
    out = asyncio.run(rag_mod.rag_query("q", "u", 5))
    assert set(out.keys()) == TRUE_SOURCE_KEYS
    assert out["sources"] == []


# ── ③ 真源锚点 ───────────────────────────────────────────────


def test_true_source_anchors():
    """rag.py 两 return 锚点 + agent.py 透传锚点（单一源）。"""
    assert 'return {"answer": answer, "sources": sources}' in rag_src, (
        "rag.py 组合分支 return 锚点漂移"
    )
    assert 'return {"answer": "No relevant knowledge found.", "sources": []}' in rag_src, (
        "rag.py 空结果分支 return 锚点漂移"
    )
    assert 'sources.append({"id": r.get("id"), "score": r.get("score")})' in rag_src, (
        "sources 元素构造锚点漂移"
    )
    assert "result = await rag_query(req.question, current_user" in agent_src, (
        "agent.py recall 透传锚点漂移"
    )


# ── ④ census 工具真跑真仓 ─────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def test_census_recall_consumed_keys_subset_true_source(census_report):
    """recall 调用点 findings 消费键 ⊆ 真源产出键（顶层 ∪ 元素级，禁再发明键）。

    工具边界如实记：census 把元素级访问（item.id）并进调用点 findings 键集
    （CD-⑨ 元素级提取既有边界，修前修后同边界非本拍引入）——元素键 id/score
    是真源元素键（rag.py sources 构造），故断言面=顶层键 ∪ 元素键。
    """
    findings = [
        f
        for f in census_report["findings"]
        if f["page"].endswith("agent-nodes/page.tsx") and "recall" in f.get("path", "")
    ]
    allowed = TRUE_SOURCE_KEYS | SOURCE_ELEMENT_KEYS
    for f in findings:
        for k in f["key"]:
            assert k in allowed, f"census 消费键真源不产出: {f['path']} {k}"


def test_census_drift_keys_findings_gone(census_report):
    """修后 results/query 漂移 findings 归零（不再作为悬空消费键浮出）。"""
    findings = [f for f in census_report["findings"] if f["page"].endswith("agent-nodes/page.tsx")]
    for f in findings:
        for k in f["key"]:
            assert k not in {"results", "query"}, f"漂移 findings 残留: {f}"
