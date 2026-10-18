"""CD-⑯ drift_suspect 18 条逐项深挖裁决——覆盖门+真源锚点核验。

三层：
1. TestVerdictCoverage：完整性门（missing/extra 拒出工件）+计数+evidence 强制
   +confirmed_drift 必带 repair_candidate+工件真落盘回读。
2. TestKeyShapeEvidence：18 条裁决引用的真源锚点逐条程序化核验（AST/源码文本，
   防无据断言——裁决说在场的键必须真的在场、说缺席的必须真的缺席）。
3. TestConfirmedDriftAnchors：3 条真断点（system bootstrap initialized/steps）的
   真源缺席面+UI 死显示面锚点（声明键/消费点）双向锁死。
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "scripts" / "census_contract_drift_deep_dive_cd16.py"
POST_CD9 = ROOT / "data" / "census_contract_drift_post_cd9.json"
ARTIFACT = ROOT / "data" / "census_contract_drift_deep_dive_cd16.json"


def _load_tool():
    spec = importlib.util.spec_from_file_location("cd16_tool", TOOL)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cd16_tool"] = mod
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


def _return_dict(relpath: str, funcname: str):
    """函数体内第一个 return dict 字面量的 AST Dict 节点（真源形状锚点）。"""
    node = _func_tree(relpath, funcname)
    for sub in ast.walk(node):
        if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
            return sub.value
    raise AssertionError(f"no return dict literal in {funcname}")


def _return_dict_keys(relpath: str, funcname: str) -> set:
    d = _return_dict(relpath, funcname)
    return {k.value for k in d.keys if isinstance(k, ast.Constant)}


def _return_dict_has_unpacking(relpath: str, funcname: str) -> bool:
    d = _return_dict(relpath, funcname)
    return any(k is None for k in d.keys)


def _class_fields(relpath: str, classname: str) -> set:
    tree = ast.parse(_src(relpath))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == classname:
            names = set()
            for sub in node.body:
                if isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    names.add(sub.target.id)
                elif isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            names.add(t.id)
            return names
    raise AssertionError(f"class {classname} not found in {relpath}")


def _method_return_keys(relpath: str, classname: str, method: str) -> set:
    tree = ast.parse(_src(relpath))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == classname:
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == method:
                    for sub2 in ast.walk(sub):
                        if isinstance(sub2, ast.Return) and isinstance(sub2.value, ast.Dict):
                            return {k.value for k in sub2.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError(f"method {classname}.{method} not found in {relpath}")


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


@pytest.fixture(scope="module")
def artifact(tool):
    return tool.build_artifact(check_only=True)


class TestVerdictCoverage:
    def test_all_18_findings_joined(self, artifact):
        assert artifact["counts_input_findings"] == 18

    def test_counts(self, artifact):
        assert artifact["counts"] == {
            "resolved_ok": 15,
            "confirmed_drift": 3,
            "ruled_false_positive": 0,
        }

    def test_evidence_mandatory(self, artifact):
        for r in artifact["verdicts"]:
            assert r.get("evidence"), f"missing evidence: {r['page']}:{r['line']}"

    def test_confirmed_drift_has_repair_candidate(self, artifact):
        drifts = [r for r in artifact["verdicts"] if r["verdict"] == "confirmed_drift"]
        assert drifts and all(r.get("repair_candidate") for r in drifts)
        assert artifact["repair_candidates"] == ["system bootstrap status 消费键收编"]

    def test_gate_rejects_missing_verdict(self, tool, monkeypatch):
        saved = dict(tool.VERDICTS)
        try:
            dropped = next(iter(saved))
            monkeypatch.setattr(tool, "VERDICTS", {k: v for k, v in saved.items() if k != dropped})
            with pytest.raises(SystemExit, match="覆盖不完整"):
                tool.build_artifact(check_only=True)
        finally:
            tool.VERDICTS.update(saved)

    def test_gate_rejects_extra_verdict(self, tool, monkeypatch):
        saved = dict(tool.VERDICTS)
        try:
            monkeypatch.setattr(
                tool,
                "VERDICTS",
                {
                    **saved,
                    ("ghost/page.tsx", 1, ("x",)): {"verdict": "resolved_ok", "evidence": "e"},
                },
            )
            with pytest.raises(SystemExit, match="覆盖不完整"):
                tool.build_artifact(check_only=True)
        finally:
            tool.VERDICTS.update(saved)

    def test_artifact_on_disk_matches_rebuild(self, tool, artifact):
        assert ARTIFACT.exists()
        on_disk = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        assert on_disk["counts"] == artifact["counts"]
        assert len(on_disk["verdicts"]) == len(artifact["verdicts"]) == 18

    def test_findings_source_is_post_cd9(self, artifact):
        post = json.loads(POST_CD9.read_text(encoding="utf-8"))
        ds = [f for f in post["findings"] if f["verdict"] == "drift_suspect"]
        assert len(ds) == 18, "post_cd9 drift_suspect 基线漂移（本工具口径 18）"
        joined = {(r["page"], r["line"], tuple(r["consumed_key"])) for r in artifact["verdicts"]}
        expected = {(f["page"], f["line"], tuple(f["key"])) for f in ds}
        assert joined == expected


class TestKeyShapeEvidence:
    """resolved_ok 15 条：裁决说在场的键必须真的在场（程序化核验禁无据断言）。"""

    def test_agents_detect_cache_spread(self):
        src = _src("src/api/agents.py")
        assert '"agents": result' in src  # _detect_cache 构造
        assert "**_detect_cache" in src  # 返回扩展=键真出线

    def test_download_task_to_dict_fields(self):
        keys = _method_return_keys("src/api/native_downloader.py", "DownloadTask", "to_dict")
        assert {"id", "status", "segments", "plugin"} <= keys

    def test_download_cache_files_element(self):
        src = _src("src/api/download_api.py")
        assert '"name": f.name' in src  # list_cache for 循环 dict 字面量
        assert '"files": sorted(' in src

    def test_healer_result_to_dict_fields(self):
        keys = _return_dict_keys("src/api/healer.py", "_result_to_dict")
        assert {"organ", "healthy", "severity"} <= keys
        src = _src("src/api/healer.py")
        assert '"organs": [_result_to_dict(r) for r in results]' in src  # cycle 返回元素形

    def test_learn_get_course_dynamic_chapters(self):
        node = _func_tree("src/api/learn.py", "get_course")
        seg = ast.get_source_segment(_src("src/api/learn.py"), node) or ""
        assert 'data["chapters"]' in seg  # 动态补键=键真在场

    def test_diagnostics_config_diff_element(self):
        src = _src("src/api/diagnostics.py")
        assert '"key": full_key' in src  # _diff dict 字面量
        assert '"default": d_val' in src and '"current": c_val' in src

    def test_diagnostics_check_all_element(self):
        src = _src("src/api/diagnostics.py")
        assert '"key": key' in src  # 嵌套 _check dict 字面量
        assert '"status": "ok" if r.status_code == 200 else "error"' in src

    def test_trajectory_event_fields(self):
        fields = _class_fields("src/trajectory/store.py", "TrajectoryEvent")
        assert {"id", "event_type", "agent_id", "status"} <= fields
        src = _src("src/trajectory/store.py")
        assert "d = asdict(self)" in src  # to_dict=asdict=字段全出
        assert '"events": [e.to_dict() for e in events]' in _src("src/api/trajectory.py")


class TestConfirmedDriftAnchors:
    """3 条真断点：真源缺席面+UI 死显示面双向锁死（说缺席的必须真的缺席）。"""

    def test_bootstrap_status_true_shape(self):
        keys = _return_dict_keys("src/api/system_overview.py", "bootstrap_status")
        assert keys == {"status", "component"}
        assert _return_dict_has_unpacking("src/api/system_overview.py", "bootstrap_status")
        assert "initialized" not in keys and "steps" not in keys

    def test_bootstrap_state_true_shape(self):
        src = _src("src/system/bootstrap.py")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "state":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                        keys = {k.value for k in sub.value.keys if isinstance(k, ast.Constant)}
                        assert keys == {
                            "bootstrapped",
                            "bootstrapped_at",
                            "version",
                            "items",
                        }
                        assert "initialized" not in keys and "steps" not in keys
                        return
        raise AssertionError("bootstrap.state property not found")

    def test_ui_dead_display_face(self):
        """CD-⑯ 真断点锚点——契约随更新（CD-⑰ 修复后翻转锁）。

        修前本锁断言死消费点/死声明键「在场」（裁决证据=死面真实存在）；
        CD-⑰ system bootstrap status 消费键收编已修复→锚点翻转为「清零」
        （死面再出现=修复回退先炸；裁决本体由 test_repair_candidates_registered
        工件锁继续锁死，断言意图零丢失）。
        """
        src = _src("admin-ui/app/system/page.tsx")
        assert "bootstrap.initialized" not in src  # 死消费点已清（CD-⑰ 收编 bootstrapped）
        assert "bootstrap?.steps" not in src  # 死消费点已清（CD-⑰ 整块重编 items）
        assert "initialized: boolean;" not in src  # 死声明键已清（CD-⑦ 纪律）
        assert "steps: Array<{ name: string; status: string; message?: string }>;" not in src
        assert "bootstrap.bootstrapped" in src  # 真源键消费在场（修复面锚点）
        assert "Object.entries(bootstrap.items)" in src  # steps 块整块处置锚点

    def test_repair_candidates_registered(self, artifact):
        drift_keys = {
            (r["page"], r["line"], tuple(r["consumed_key"]))
            for r in artifact["verdicts"]
            if r["verdict"] == "confirmed_drift"
        }
        assert drift_keys == {
            ("system/page.tsx", 453, ("initialized",)),
            ("system/page.tsx", 464, ("steps",)),
            ("system/page.tsx", 466, ("steps", "status")),
        }
        expanded = {(e["page"], tuple(e["keys"])) for e in artifact["expanded_findings"]}
        assert ("system/page.tsx", ("steps[].name", "steps[].message")) in expanded
