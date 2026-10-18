"""CD-⑩ 新浮出候选逐项深挖裁决——覆盖门+真源锚点核验。

三层：
1. TestVerdictCoverage：完整性门（missing/extra 拒出工件）+计数+evidence 强制
   +endpoint 面双向对账。
2. TestKeyShapeEvidence：24 条裁决引用的真源锚点逐条程序化核验（AST/源码文本，
   防无据断言——裁决说在场的键必须真的在场、说缺席的必须真的缺席）。
3. TestEndpointDriftEvidence：两条端点方法漂移的锚点核验（路由装饰器 vs UI 调用点
   方法），含 CD-⑨「fallback 不重扫=假功能」勘误锚点（api_full_scan 每次全量扫描）。
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "scripts" / "census_contract_drift_deep_dive_cd10.py"
POST_CD9 = ROOT / "data" / "census_contract_drift_post_cd9.json"


def _load_tool():
    spec = importlib.util.spec_from_file_location("cd10_tool", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cd10_tool"] = mod
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


def _return_dict_keys(relpath: str, funcname: str):
    """函数体内第一个 return dict 字面量的键集（真源形状锚点）。"""
    node = _func_tree(relpath, funcname)
    for sub in ast.walk(node):
        if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
            return {k.value for k in sub.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError(f"no return dict literal in {funcname}")


def _class_fields(relpath: str, classname: str):
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


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


@pytest.fixture(scope="module")
def artifact(tool):
    return tool.build_artifact(check_only=True)


class TestVerdictCoverage:
    def test_artifact_covers_every_target_finding(self, artifact):
        post = json.loads(POST_CD9.read_text(encoding="utf-8"))
        targets = [
            f for f in post["findings"] if f["verdict"] in ("drift_hard", "unresolved_endpoint")
        ]
        assert len(targets) == 24
        assert artifact["counts_input_findings"] == 24
        assert len(artifact["verdicts"]) == 24
        joined = {(r["page"], r["line"], tuple(r["consumed_key"])) for r in artifact["verdicts"]}
        expected = {(f["page"], f["line"], tuple(f["key"])) for f in targets}
        assert joined == expected

    def test_verdict_counts_add_up(self, artifact):
        c = artifact["counts"]
        assert c == {"resolved_ok": 14, "confirmed_drift": 8, "ruled_false_positive": 2}
        assert sum(c.values()) == 24
        assert artifact["endpoint_counts"] == {"confirmed_drift": 2}

    def test_every_verdict_carries_evidence_and_domain(self, artifact):
        for r in artifact["verdicts"] + artifact["endpoint_verdicts"]:
            assert r["evidence"].strip(), r
            assert r["verdict"] in ("resolved_ok", "confirmed_drift", "ruled_false_positive")
            if r["verdict"] == "confirmed_drift":
                assert r.get("repair_candidate"), r

    def test_repair_candidates_are_four_ordered_slices(self, artifact):
        assert artifact["repair_candidates"] == [
            "hippo decay/simulate 方法漂移修复",
            "logs stream summary 消费键收编",
            "nest stats 消费键收编",
            "soma discovery/scan 重扫方法漂移收口",
        ]

    def test_completeness_gate_refuses_missing_verdict(self, tool):
        saved = dict(tool.VERDICTS)
        try:
            tool.VERDICTS.pop(("feedback/page.tsx", 303, ("confidence",)))
            with pytest.raises(SystemExit, match="覆盖不完整"):
                tool.build_artifact(check_only=True)
        finally:
            tool.VERDICTS.clear()
            tool.VERDICTS.update(saved)

    def test_completeness_gate_refuses_extra_verdict(self, tool):
        saved = dict(tool.VERDICTS)
        try:
            tool.VERDICTS[("no-such/page.tsx", 1, ("x",))] = {
                "verdict": "resolved_ok",
                "evidence": "x",
            }
            with pytest.raises(SystemExit, match="覆盖不完整"):
                tool.build_artifact(check_only=True)
        finally:
            tool.VERDICTS.clear()
            tool.VERDICTS.update(saved)

    def test_endpoint_gate_refuses_missing_endpoint_verdict(self, tool):
        saved = dict(tool.ENDPOINT_VERDICTS)
        try:
            tool.ENDPOINT_VERDICTS.pop(("hippo/page.tsx", 207, "GET", "/api/hippo/decay/simulate"))
            with pytest.raises(SystemExit, match="endpoint verdict 覆盖不完整"):
                tool.build_artifact(check_only=True)
        finally:
            tool.ENDPOINT_VERDICTS.clear()
            tool.ENDPOINT_VERDICTS.update(saved)

    def test_endpoint_gate_refuses_confirmed_drift_without_candidate(self, tool):
        saved = dict(tool.ENDPOINT_VERDICTS)
        try:
            key = ("soma/page.tsx", 521, "POST", "/api/soma/discovery/scan")
            tool.ENDPOINT_VERDICTS[key] = {"verdict": "confirmed_drift", "evidence": "x"}
            with pytest.raises(SystemExit, match="repair_candidate"):
                tool.build_artifact(check_only=True)
        finally:
            tool.ENDPOINT_VERDICTS.clear()
            tool.ENDPOINT_VERDICTS.update(saved)

    def test_artifact_roundtrip_shape(self, tool, tmp_path, monkeypatch):
        monkeypatch.setattr(tool, "OUT", tmp_path / "cd10.json")
        out = tool.build_artifact(check_only=False)
        data = json.loads((tmp_path / "cd10.json").read_text(encoding="utf-8"))
        assert data["counts"] == out["counts"]
        assert data["source_artifact"] == "data/census_contract_drift_post_cd9.json"
        assert len(data["endpoint_verdicts"]) == 2
        assert len(data["expanded_findings"]) == 2


class TestKeyShapeEvidence:
    """裁决 evidence 引用的真源锚点逐条核验（说在场的键必须真在场）。"""

    def test_feedback_entry_has_type_and_confidence(self):
        from src.models.feedback import FeedbackEntry

        fields = set(FeedbackEntry.model_fields.keys())
        assert {"type", "title", "content", "confidence", "evidence", "tags"} <= fields

    def test_feedback_response_model_declares_entries(self):
        src = _src("src/api/feedback.py")
        assert "response_model=FeedbackExtractResponse" in src
        assert "entries=entries" in src

    def test_immune_findings_element_literal_has_three_keys(self):
        node = _func_tree("src/api/immune.py", "moderate_text")
        for sub in ast.walk(node):
            if isinstance(sub, ast.Dict) and len(sub.keys) == 3:
                keys = {k.value for k in sub.keys if isinstance(k, ast.Constant)}
                if keys == {"type", "label", "risk"}:
                    return
        raise AssertionError("immune findings element dict literal {type,label,risk} not found")

    def test_stream_summary_produces_exactly_five_keys(self):
        keys = _return_dict_keys("src/api/event_stream.py", "get_stream_summary")
        assert keys == {
            "total_events",
            "by_organ",
            "by_type",
            "most_active_organ",
            "collected_at",
        }
        assert not {"buffer_size", "organ_probes", "sse_clients"} & keys

    def test_stream_health_has_the_real_names(self):
        keys = _return_dict_keys("src/api/event_stream.py", "stream_health")
        assert {"buffer_size", "probes_configured", "sse_subscribers"} <= keys

    def test_nest_stats_response_is_two_nested_keys(self):
        keys = _return_dict_keys("src/api/nest.py", "nest_stats")
        assert keys == {"tenants", "isolation"}

    def test_nest_manager_stats_has_total_but_not_active_suspended(self):
        keys = _return_dict_keys("src/nest/tenant.py", "stats")
        assert "total_tenants" in keys
        assert "active_tenants" not in keys
        assert "suspended_tenants" not in keys

    def test_isolation_stats_has_no_total_policies(self):
        keys = _return_dict_keys("src/nest/isolation.py", "stats")
        assert "total_policies" not in keys

    def test_enterprise_users_list_is_bare_array(self):
        src = _src("src/api/enterprise.py")
        assert '@router.get("/users/list", response_model=list[UserListItem])' in src
        ui = _src("admin-ui/app/users/page.tsx")
        assert "Array.isArray(data) ? data : data.users || []" in ui

    def test_voice_error_key_is_locally_constructed(self):
        ui = _src("admin-ui/app/voice/page.tsx")
        assert "setTestResult({ error: e.message })" in ui
        node = _func_tree("src/api/voice.py", "synthesize_json")
        for sub in ast.walk(node):
            if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                keys = {k.value for k in sub.value.keys if isinstance(k, ast.Constant)}
                assert "error" not in keys

    def test_hippo_simulate_response_has_consumed_keys(self):
        keys = _return_dict_keys("src/api/hippo.py", "simulate_decay")
        assert {"retention", "should_archive", "should_forget", "strategy"} <= keys

    def test_soma_scanresult_fields_cover_consumed_keys(self):
        scan = _class_fields("src/api/soma_discovery.py", "ScanResult")
        assert {"scan_time", "processes", "cli_tools", "services", "adapters"} <= scan
        assert {"cpu", "mem"} <= _class_fields("src/api/soma_discovery.py", "ProcessInfo")
        assert "version" in _class_fields("src/api/soma_discovery.py", "CLITool")
        assert {"protocol", "state"} <= _class_fields("src/api/soma_discovery.py", "ServiceInfo")


class TestEndpointDriftEvidence:
    def test_hippo_route_and_ui_method_aligned(self):
        """契约随更新（CD-⑬ 修复候选③ hippo decay/simulate 方法漂移修复落）：
        VERDICTS 证据「调用点 apiFetch 缺省 GET 恒 405」是 CD-⑨ 快照的历史记录
        （裁决工件冻结不改），本锚点改锁修复后状态——调用点声明 POST 与真源路由
        对齐（方法漂移复发此锚先炸）。"""
        src = _src("src/api/hippo.py")
        assert '@router.post("/decay/simulate")' in src
        assert '@router.get("/decay/simulate")' not in src
        ui = _src("admin-ui/app/hippo/page.tsx")
        code = "\n".join(l.split("//")[0] for l in ui.splitlines())
        line = [l for l in code.splitlines() if "decay/simulate" in l and "apiFetch" in l][0]
        assert "method: 'POST'" in line

    def test_soma_route_and_ui_method_aligned(self):
        """契约随更新（CD-⑭ 修复候选④ soma discovery/scan 重扫方法漂移收口落）：
        ENDPOINT_VERDICTS 证据「重扫调用点 method:'POST' 恒 405 空转」是 CD-⑨
        快照的历史记录（裁决工件冻结不改），本锚点改锁修复后状态——重扫调用点
        收编真源 GET（apiFetch 缺省 GET 形），方法漂移复发此锚先炸。"""
        src = _src("src/api/soma_discovery.py")
        assert '@router.get("/scan", response_model=ScanResult)' in src
        assert '@router.post("/scan"' not in src
        ui = _src("admin-ui/app/soma/page.tsx")
        code = "\n".join(l.split("//")[0] for l in ui.splitlines())
        lines = [l for l in code.splitlines() if "discovery/scan" in l and "apiFetch" in l]
        assert lines, "discovery/scan 调用点提取异常（提取失效=断言空转）"
        for l in lines:
            assert "method:" not in l, f"调用点显式声明方法（修前 POST 漂移形残留）: {l}"

    def test_soma_api_full_scan_really_rescans(self):
        """CD-⑨「fallback GET 不重扫=假功能」勘误锚点：api_full_scan 每次全量扫描。"""
        node = _func_tree("src/api/soma_discovery.py", "api_full_scan")
        names = {
            n.func.id
            for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert {"scan_processes", "scan_cli_tools", "scan_services"} <= names
        ui = _src("admin-ui/app/soma/page.tsx")
        # 契约随更新（CD-⑭）：修前 catch 兜底「Fallback: just re-fetch」是 POST 405
        # 的漂移补偿路径，随方法收编一并移除——重扫直达真源 GET 即真重扫
        # （勘误本体「api_full_scan 每次全量扫描」不变）。
        assert "Fallback: just re-fetch" not in ui
        assert "Force rescan via POST" not in ui
