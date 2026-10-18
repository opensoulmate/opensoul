"""CD-⑲ unresolved_path 7 条逐项深挖裁决——覆盖门+真源锚点核验。

三层：
1. TestVerdictCoverage：完整性门（missing/extra 拒出工件）+计数+evidence 强制
   +confirmed_drift 必带 repair_candidate+工件真落盘回读。
2. TestSourceAnchors：7 条裁决引用的真源锚点逐条程序化核验（AST/源码文本，
   防无据断言——裁决说在场的键必须真的在场、说缺席的必须真的缺席）。
3. TestExpandedFindings：3 条端点级扩面（line/pie/scatter /json 404 死路径）
   的 UI url 字面量在场+真源路由缺席双向锁死。
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "scripts" / "census_contract_drift_deep_dive_cd19.py"
POST_CD9 = ROOT / "data" / "census_contract_drift_post_cd9.json"
ARTIFACT = ROOT / "data" / "census_contract_drift_deep_dive_cd19.json"

V_UI = "admin-ui/app/vision/page.tsx"
M_UI = "admin-ui/app/marketplace/page.tsx"
V_API = "src/api/vision.py"
M_API = "src/api/marketplace.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("cd19_tool", TOOL)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cd19_tool"] = mod
    spec.loader.exec_module(mod)
    return mod


def _src(relpath: str) -> str:
    return (ROOT / relpath).read_text(encoding="utf-8")


def _func_tree(relpath: str, funcname: str):
    tree = ast.parse(_src(relpath))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == funcname:
            return node
    raise AssertionError(f"function {funcname} not found in {relpath}")


def _return_dict_keys(relpath: str, funcname: str) -> set:
    """函数体内第一个 return dict 字面量的常量键集（真源形状锚点）。"""
    node = _func_tree(relpath, funcname)
    for sub in ast.walk(node):
        if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
            return {k.value for k in sub.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError(f"no return dict literal in {funcname}")


def _router_paths(relpath: str) -> set:
    """模块内 @router.<method>(<literal>) 装饰器路径全集（真源路由面锚点）。"""
    paths = set()
    for node in ast.walk(ast.parse(_src(relpath))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in ("get", "post", "put", "delete")
                    and dec.args
                    and isinstance(dec.args[0], ast.Constant)
                ):
                    paths.add(dec.args[0].value)
    return paths


def _unresolved_path_identities() -> set:
    post = json.loads(POST_CD9.read_text(encoding="utf-8"))
    return {
        (f["page"], f["line"], tuple(f["key"]))
        for f in post["findings"]
        if f["verdict"] == "unresolved_path"
    }


# ── 1. 覆盖门 ──────────────────────────────────────────────────────────


class TestVerdictCoverage:
    def test_join_covers_all_unresolved_path_findings(self):
        mod = _load_tool()
        art = mod.build_artifact(check_only=True)
        got = {(r["page"], r["line"], tuple(r["consumed_key"])) for r in art["verdicts"]}
        assert got == _unresolved_path_identities()
        assert len(art["verdicts"]) == 7

    def test_counts_reconcile(self):
        art = _load_tool().build_artifact(check_only=True)
        assert art["counts_input_findings"] == 7
        assert art["counts"] == {
            "resolved_ok": 6,
            "confirmed_drift": 1,
            "ruled_false_positive": 0,
        }
        assert sum(art["counts"].values()) == art["counts_input_findings"]

    def test_every_verdict_has_evidence(self):
        art = _load_tool().build_artifact(check_only=True)
        for r in art["verdicts"]:
            assert r.get("evidence"), (r["page"], r["line"])

    def test_confirmed_drift_requires_repair_candidate(self):
        art = _load_tool().build_artifact(check_only=True)
        drift = [r for r in art["verdicts"] if r["verdict"] == "confirmed_drift"]
        assert len(drift) == 1
        assert drift[0]["consumed_key"] == ["elapsed_ms"]
        for r in drift:
            assert r.get("repair_candidate"), (r["page"], r["line"])

    def test_completeness_gate_rejects_missing(self, monkeypatch):
        mod = _load_tool()
        trimmed = dict(mod.VERDICTS)
        trimmed.pop(("marketplace/page.tsx", 82, ("sources",)))
        monkeypatch.setattr(mod, "VERDICTS", trimmed)
        with pytest.raises(SystemExit, match="missing"):
            mod.build_artifact(check_only=True)

    def test_completeness_gate_rejects_extra(self, monkeypatch):
        mod = _load_tool()
        bloated = dict(mod.VERDICTS)
        bloated[("ghost/page.tsx", 1, ("ghost",))] = {"verdict": "resolved_ok", "evidence": "x"}
        monkeypatch.setattr(mod, "VERDICTS", bloated)
        with pytest.raises(SystemExit, match="extra"):
            mod.build_artifact(check_only=True)

    def test_artifact_on_disk_matches_builder(self):
        mod = _load_tool()
        on_disk = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        assert on_disk == mod.build_artifact(check_only=True)
        assert on_disk["source_artifact"] == "data/census_contract_drift_post_cd9.json"
        assert "unresolved_shape 109" in on_disk["scope"]


# ── 2. 真源锚点（说在场的键必须真的在场、说缺席的必须真的缺席）──


class TestSourceAnchors:
    def test_marketplace_both_endpoints_produce_sources(self):
        # 裁决说：三元双端点均 return {"sources": ...} → 键真在场
        assert "sources" in _return_dict_keys(M_API, "list_skill_sources")
        assert "sources" in _return_dict_keys(M_API, "list_agent_sources")

    def test_bar_json_produces_all_consumed_keys(self):
        # 裁决说：bar/json 产出全部 6 个消费键 + chart_type
        keys = _return_dict_keys(V_API, "bar_chart_json")
        assert keys >= {
            "format",
            "engine",
            "chart_type",
            "size_bytes",
            "elapsed_ms",
            "output_file",
            "image_base64",
        }

    def test_mindmap_json_produces_reachable_keys(self):
        keys = _return_dict_keys(V_API, "mindmap_json")
        assert keys >= {"format", "engine", "size_bytes", "output_file", "image_base64"}

    def test_mindmap_elapsed_sibling_drift_anchor(self):
        # confirmed_drift 根因锚点：mindmap/json 产出 elapsed_seconds 而无 elapsed_ms
        # （兄弟端点 bar/json 反向）——若后端键名归一或 UI 换读，本锚点先炸=裁决失效须复挖
        keys = _return_dict_keys(V_API, "mindmap_json")
        assert "elapsed_seconds" in keys
        assert "elapsed_ms" not in keys
        bar_keys = _return_dict_keys(V_API, "bar_chart_json")
        assert "elapsed_ms" in bar_keys
        assert "elapsed_seconds" not in bar_keys

    def test_ui_consumption_anchors(self):
        ui = _src(V_UI)
        # 假 0 回落遮蔽锚点（契约随更新，CD-⑰ 先例：修前「在场」证据锁翻转「清零」）：
        # CD-⑳ 收口后 `previewMeta.elapsed_ms || 0` 假 0 形清零——复发=收口失效此锚点先炸
        assert "previewMeta.elapsed_ms || 0" not in ui
        # resolved_ok 消费点锚点
        assert "data.image_base64" in ui
        assert "data.format === 'png'" in ui
        assert "previewMeta.size_bytes" in ui
        assert "previewMeta?.output_file" in ui
        assert "{previewMeta.engine}" in ui

    def test_ui_url_variable_branch_anchors(self):
        # census 提取边界根因：url 为变量（if/else 链五分支），字面端点在赋值行
        ui = _src(V_UI)
        for literal in (
            "/api/vision/chart/bar/json",
            "/api/vision/chart/line/json",
            "/api/vision/chart/pie/json",
            "/api/vision/chart/scatter/json",
            "/api/vision/mindmap/json",
        ):
            assert f"'{literal}'" in ui, literal
        m_ui = _src(M_UI)
        assert "'/api/marketplace/skills/sources'" in m_ui
        assert "'/api/marketplace/agents/sources'" in m_ui
        assert "Array.isArray(data.sources)" in m_ui


# ── 3. 端点级扩面（line/pie/scatter /json 404 死路径）──


class TestExpandedFindings:
    def test_expanded_all_confirmed_drift_with_repair_candidate(self):
        art = _load_tool().build_artifact(check_only=True)
        exp = art["expanded_findings"]
        assert len(exp) == 3
        for e in exp:
            assert e["verdict"] == "confirmed_drift"
            assert e.get("repair_candidate")
            assert e.get("note")

    def test_missing_json_endpoints_absent_from_router(self):
        # 说缺席的必须真的缺席：line/pie/scatter 的 /json 变体真源路由不存在
        paths = _router_paths(V_API)
        assert "/chart/line/json" not in paths
        assert "/chart/pie/json" not in paths
        assert "/chart/scatter/json" not in paths
        # 对照面：可达两端点真在场
        assert "/chart/bar/json" in paths
        assert "/mindmap/json" in paths
        # 二进制流式端点在场（修复候选「补齐 or 改指」的真源现状锚点）
        assert {"/chart/line", "/chart/pie", "/chart/scatter"} <= paths

    def test_expanded_ui_url_literals_present(self):
        ui = _src(V_UI)
        assert "url = '/api/vision/chart/line/json'" in ui
        assert "url = '/api/vision/chart/pie/json'" in ui
        assert "url = '/api/vision/chart/scatter/json'" in ui
