"""CD-㉒ unresolved_shape 109 条逐项深挖裁决——覆盖门+真源锚点核验。

四层：
1. TestVerdictCoverage：完整性门（missing/extra 拒出工件）+计数+evidence 强制
   +confirmed_drift 必带 repair_candidate+工件真落盘回读+109 条 join 精确对账。
2. TestSourceAnchors：全行 anchors 逐条程序化核验（锚点行真含声明子串）+坏锚点拒出
   工件（防无据断言）+AST 级「说在场的键真在场、说缺席的键真缺席」硬反证。
3. TestKnownDrifts：15 条 confirmed_drift 与 3 条 ruled_false_positive 逐条定型锁死（已收口件 landed 锚点随 CD-15/23/24/25/26/27/28/29/30/31 落地翻转）。
4. TestExpandedFindings：6 条扩面登记的 UI 消费字面量在场（已收口件翻转清零）+repair_candidate 强制。
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "scripts" / "census_contract_drift_deep_dive_cd22.py"
POST_CD9 = ROOT / "data" / "census_contract_drift_post_cd9.json"
ARTIFACT = ROOT / "data" / "census_contract_drift_deep_dive_cd22.json"

_V = "resolved_ok"
_D = "confirmed_drift"
_F = "ruled_false_positive"


def _load_tool():
    spec = importlib.util.spec_from_file_location("cd22_tool", TOOL)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cd22_tool"] = mod
    spec.loader.exec_module(mod)
    return mod


def _src(relpath: str) -> str:
    return (ROOT / relpath).read_text(encoding="utf-8")


def _func_node(relpath: str, funcname: str):
    tree = ast.parse(_src(relpath))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == funcname:
            return node
    raise AssertionError(f"function {funcname} not found in {relpath}")


def _all_return_dict_keys(relpath: str, funcname: str) -> set:
    """函数体内全部 return dict 字面量的常量键并集（真源形状锚点）。"""
    node = _func_node(relpath, funcname)
    keys: set = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
            keys |= {k.value for k in sub.value.keys if isinstance(k, ast.Constant)}
    return keys


def _func_src(relpath: str, funcname: str) -> str:
    node = _func_node(relpath, funcname)
    seg = ast.get_source_segment(_src(relpath), node)
    assert seg is not None
    return seg


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


@pytest.fixture(scope="module")
def artifact(tool):
    return tool.build_artifact(check_only=True)


class TestVerdictCoverage:
    def test_counts_and_total(self, artifact):
        assert artifact["counts_input_findings"] == 109
        assert artifact["counts"] == {
            "resolved_ok": 91,
            "confirmed_drift": 15,
            "ruled_false_positive": 3,
        }

    def test_join_exactly_covers_findings(self, artifact):
        post = json.loads(POST_CD9.read_text(encoding="utf-8"))
        findings = [f for f in post["findings"] if f["verdict"] == "unresolved_shape"]
        want = {(f["page"], f["line"], tuple(f["key"])) for f in findings}
        got = {(r["page"], r["line"], tuple(r["consumed_key"])) for r in artifact["verdicts"]}
        assert got == want
        assert len(artifact["verdicts"]) == 109

    def test_every_row_has_evidence_and_valid_verdict(self, artifact):
        for r in artifact["verdicts"]:
            assert r.get("evidence"), (r["page"], r["line"], r["consumed_key"])
            assert r["verdict"] in (_V, _D, _F)
            if r["verdict"] == _D:
                assert r.get("repair_candidate"), (r["page"], r["line"])

    def test_completeness_gate_rejects_missing(self, tool, monkeypatch):
        victim = ("agent.recall:450", "results")
        assert victim in tool.SHAPE_VERDICTS
        monkeypatch.setitem(
            tool.SHAPE_VERDICTS, victim, {**tool.SHAPE_VERDICTS[victim], "verdict": _V}
        )
        saved = tool.SHAPE_VERDICTS.pop(victim)
        try:
            with pytest.raises(SystemExit, match="完整性门"):
                tool.build_artifact(check_only=True)
        finally:
            tool.SHAPE_VERDICTS[victim] = saved

    def test_completeness_gate_rejects_extra(self, tool, monkeypatch):
        monkeypatch.setitem(
            tool.SHAPE_VERDICTS,
            ("ghost.handler:1", "nope"),
            {"verdict": _V, "evidence": "x", "anchors": []},
        )
        with pytest.raises(SystemExit, match="完整性门"):
            tool.build_artifact(check_only=True)

    def test_evidence_mandatory(self, tool, monkeypatch):
        victim = ("agent.recall:450", "results")
        saved = tool.SHAPE_VERDICTS[victim]
        monkeypatch.setitem(tool.SHAPE_VERDICTS, victim, {**saved, "evidence": ""})
        with pytest.raises(SystemExit, match="缺 evidence"):
            tool.build_artifact(check_only=True)

    def test_confirmed_drift_requires_repair_candidate(self, tool, monkeypatch):
        victim = ("agent.recall:450", "results")
        saved = tool.SHAPE_VERDICTS[victim]
        monkeypatch.setitem(
            tool.SHAPE_VERDICTS,
            victim,
            {k: v for k, v in saved.items() if k != "repair_candidate"},
        )
        with pytest.raises(SystemExit, match="repair_candidate"):
            tool.build_artifact(check_only=True)

    def test_artifact_written_and_reread(self, tool):
        assert ARTIFACT.exists(), "工件必须真落盘"
        on_disk = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        fresh = tool.build_artifact(check_only=True)
        assert on_disk["counts"] == fresh["counts"]
        assert on_disk["counts_input_findings"] == fresh["counts_input_findings"]
        assert len(on_disk["verdicts"]) == len(fresh["verdicts"]) == 109
        assert on_disk["scope"].startswith("unresolved_shape")

    def test_repair_candidates_merge_rows_and_expanded(self, artifact):
        row_cands = {r["repair_candidate"] for r in artifact["verdicts"] if "repair_candidate" in r}
        exp_cands = {e["repair_candidate"] for e in artifact["expanded_findings"]}
        assert set(artifact["repair_candidates"]) == row_cands | exp_cands
        # 已收口件也在列（repair_status 标注 landed）
        assert any("已收口" in c for c in artifact["repair_candidates"])


class TestSourceAnchors:
    def test_all_row_anchors_verified(self, tool, artifact):
        """全 109 行 anchors 逐条对当前真源程序化核验（build 亦强制，此处独立复核）。"""
        checked = 0
        for r in artifact["verdicts"]:
            assert r.get("anchors"), (r["page"], r["line"], r["consumed_key"])
            for path, line, sub in r["anchors"]:
                text = (ROOT / path).read_text(encoding="utf-8", errors="replace").splitlines()
                assert 1 <= line <= len(text), (path, line)
                assert sub in text[line - 1], (path, line, sub, text[line - 1])
                checked += 1
        assert checked >= 109  # 每行至少一锚点，多锚点行另计

    def test_bad_anchor_refuses_artifact(self, tool):
        with pytest.raises(SystemExit, match="真源锚点核验失败"):
            tool.verify_anchors([("src/main.py", 1, "不可能出现的子串zzz")])

    # ── AST 级硬反证：说缺席的键真缺席（形状提取边界的裁决定型）──

    def test_rag_query_has_no_results_key(self):
        keys = _all_return_dict_keys("src/services/rag.py", "rag_query")
        assert keys == {"answer", "sources"}
        assert "results" not in keys

    def test_benchmark_run_return_has_no_organs_and_flat_latency(self):
        keys = _all_return_dict_keys("src/benchmark/engine.py", "run_benchmark")
        assert "organs_benchmarked" in keys and "results" in keys
        assert "organs" not in keys
        elem = _all_return_dict_keys("src/benchmark/engine.py", "_result_to_dict")
        assert "organ" in elem and "latency" in elem
        assert "avg_ms" not in elem  # avg_ms 在 latency 嵌套内非平铺

    def test_learn_stats_has_no_total_quizzes(self):
        keys = _all_return_dict_keys("src/learn/course_engine.py", "stats")
        assert {"total_courses", "total_chapters", "completed_chapters"} <= keys
        assert "total_quizzes" not in keys

    def test_pulse_get_stats_nests_avg_drift(self):
        keys = _all_return_dict_keys("src/pulse/timer.py", "get_stats")
        assert {"total_ticks", "total_signals", "precision"} <= keys
        assert "avg_drift_ms" not in keys and "active_signals" not in keys

    def test_stream_summary_exact_six_keys(self):
        """真源形状锚点：恰 6 键含 recent_count（DEC-㉗a-① 补现后；勘误：修前恰 5 键）。"""
        keys = _all_return_dict_keys("src/api/event_stream.py", "get_stream_summary")
        assert keys == {
            "total_events",
            "by_organ",
            "by_type",
            "most_active_organ",
            "recent_count",
            "collected_at",
        }
        assert "recent_count" in keys

    def test_organs_config_element_has_no_status(self):
        seg = _func_src("src/api/config_api.py", "list_organs_config")
        assert '"key"' in seg and '"enabled"' in seg and '"config"' in seg
        assert '"status"' not in seg

    def test_entity_type_renamed_away(self):
        seg = _func_src("src/services/entity.py", "_row_to_entity")
        assert 'd["entity_type"] = d.pop("type", "")' in seg

    def test_relations_schema_has_no_source_entity_id(self):
        src = _src("src/main.py")
        start = src.index("CREATE TABLE IF NOT EXISTS relations")
        seg = src[start : start + 400]
        assert "source_id TEXT NOT NULL" in seg
        assert "source_entity_id" not in seg

    def test_scan_models_field_sets(self):
        seg = _func_src("src/api/soma_discovery.py", "__init__")  # 占位防误删文件级
        assert seg
        src = _src("src/api/soma_discovery.py")
        pi = src[src.index("class ProcessInfo") : src.index("class CLITool")]
        assert "pid: int" in pi and "mem: float" in pi
        assert "name:" not in pi and "memory" not in pi
        si = src[src.index("class ServiceInfo") : src.index("class ScanResult")]
        assert "local_address: str" in si and "local_port: int" in si
        assert "host" not in si.replace("local_address", "") and "port:" not in si.replace(
            "local_port", ""
        )


class TestKnownDrifts:
    DRIFT_KEYS = {
        ("agent.recall:450", "results"),
        ("benchmark.quick_benchmark:100", "organs"),
        ("benchmark.run_benchmark:81", "organs"),
        ("benchmark.quick_benchmark:100", "results.avg_ms"),
        ("benchmark.run_benchmark:81", "results.avg_ms"),
        ("cortex.think:128", "reasoning_steps"),
        ("event_stream.get_summary:822", "recent_count"),
        ("graph.get_entity_detail:71", "type"),
        ("graph.get_entity_detail:71", "relations.source_entity_id"),
        ("learn.learn_stats:425", "total_quizzes"),
        ("pulse.get_stats:160", "avg_drift_ms"),
        ("soma_discovery.api_full_scan:1472", "processes.name"),
        ("soma_discovery.api_full_scan:1472", "services.host"),
        ("soma_discovery.api_full_scan:1472", "services.name"),
        ("soma_discovery.api_full_scan:1472", "services.port"),
    }
    FALSE_POSITIVE_KEYS = {
        ("config_api.list_organs_config:47", "organs.status"),
        ("pulse.get_stats:160", "active_signals"),
        ("soma_discovery.api_database_tables:1694", "tables"),
    }

    def _rows(self, artifact):
        return {(r["census_handler"], ".".join(r["consumed_key"])): r for r in artifact["verdicts"]}

    def test_drift_set_exact(self, artifact):
        rows = self._rows(artifact)
        drift = {k for k, r in rows.items() if r["verdict"] == _D}
        assert drift == self.DRIFT_KEYS

    def test_false_positive_set_exact(self, artifact):
        rows = self._rows(artifact)
        fp = {k for k, r in rows.items() if r["verdict"] == _F}
        assert fp == self.FALSE_POSITIVE_KEYS

    def test_recent_count_repair_landed(self, artifact):
        rows = self._rows(artifact)
        r = rows[("event_stream.get_summary:822", "recent_count")]
        assert r["verdict"] == _D
        assert r.get("repair_status") == "landed@DEC-㉗a-①"
        assert "已收口" in r["repair_candidate"]
        # 修复真落地（DEC-㉗a-① 真源补现+卡恢复；勘误：CD-⑮ 时为「UI 无 recent_count 消费」）
        ui = _src("admin-ui/app/logs/page.tsx")
        assert "summary?.recent_count" in ui
        assert "最近事件" in ui

    def test_agent_recall_repair_landed(self, artifact):
        rows = self._rows(artifact)
        r = rows[("agent.recall:450", "results")]
        assert r["verdict"] == _D
        assert r.get("repair_status") == "landed@CD-㉓"
        assert "已收口" in r["repair_candidate"]
        # 修复真落地：当前 UI 已无修前死键消费、真源键已收编
        assert "recallResults.query" not in _src("admin-ui/app/agent-nodes/page.tsx")
        assert "recallResults.results" not in _src("admin-ui/app/agent-nodes/page.tsx")
        assert "recallResults.sources" in _src("admin-ui/app/agent-nodes/page.tsx")

    def test_benchmark_latency_repair_landed(self, artifact):
        rows = self._rows(artifact)
        for handler in ("benchmark.run_benchmark:81", "benchmark.quick_benchmark:100"):
            r = rows[(handler, "results.avg_ms")]
            assert r["verdict"] == _D
            assert r.get("repair_status") == "landed@CD-㉔"
            assert "已收口" in r["repair_candidate"]
        # 修复真落地：当前 UI 运行结果块已无平铺漂移读、latency 嵌套键已收编
        ui = _src("admin-ui/app/benchmark/page.tsx")
        assert "r.success_rate" not in ui
        assert "r.latency.avg_ms" in ui

    def test_benchmark_organs_repair_landed(self, artifact):
        rows = self._rows(artifact)
        for handler in ("benchmark.run_benchmark:81", "benchmark.quick_benchmark:100"):
            r = rows[(handler, "organs")]
            assert r["verdict"] == _D
            assert r.get("repair_status") == "landed@CD-㉕"
            assert "已收口" in r["repair_candidate"]
        # 修复真落地：当前 UI 已无修前死键消费、真源 results[].organ 派生已收编
        ui = _src("admin-ui/app/benchmark/page.tsx")
        assert "lastResult.organs" not in ui
        assert "lastResult.results.map((r) => r.organ)" in ui

    def test_cortex_reasoning_repair_landed(self, artifact):
        rows = self._rows(artifact)
        r = rows[("cortex.think:128", "reasoning_steps")]
        assert r["verdict"] == _D
        assert r.get("repair_status") == "landed@CD-㉖"
        assert "已收口" in r["repair_candidate"]
        # 修复真落地：当前 UI 已无修前死键消费、真源 reasoning 字符串已收编
        ui = _src("admin-ui/app/cortex/page.tsx")
        assert "reasoning_steps" not in ui
        assert "thinkResult.reasoning" in ui

    def test_kg_relations_repair_landed(self, artifact):
        rows = self._rows(artifact)
        r = rows[("graph.get_entity_detail:71", "relations.source_entity_id")]
        assert r["verdict"] == _D
        assert r.get("repair_status") == "landed@CD-㉗"
        assert "已收口" in r["repair_candidate"]
        # 修复真落地：当前 UI 已无修前漂移键消费、真源行键 source_id/type 已收编
        ui = _src("admin-ui/app/knowledge-graph/page.tsx")
        assert "rel.source_entity_id" not in ui
        assert "rel.target_entity_id" not in ui
        assert "rel.relation_type" not in ui
        assert "rel.source_id === selectedEntity.id" in ui
        assert "{rel.type}" in ui

    def test_kg_entity_type_repair_landed(self, artifact):
        rows = self._rows(artifact)
        r = rows[("graph.get_entity_detail:71", "type")]
        assert r["verdict"] == _D
        assert r.get("repair_status") == "landed@CD-㉘"
        assert "已收口" in r["repair_candidate"]
        # 修复真落地：当前 UI 已无修前漂移键消费、真源键 entity_type 已收编
        ui = _src("admin-ui/app/knowledge-graph/page.tsx")
        assert "selectedEntity.type" not in ui
        assert "entity.type" not in ui
        assert "selectedEntity.entity_type" in ui

    def test_learn_total_quizzes_repair_landed(self, artifact):
        rows = self._rows(artifact)
        r = rows[("learn.learn_stats:425", "total_quizzes")]
        assert r["verdict"] == _D
        assert r.get("repair_status") == "landed@CD-㉙"
        assert "已收口" in r["repair_candidate"]
        # 修复真落地：当前 UI 已无 total_quizzes 死显示消费（死显示卡移除+死声明键清零）
        ui = _src("admin-ui/app/learn/page.tsx")
        assert "total_quizzes" not in ui
        # 残留「测验题」字样只许在章节测验独立面（空态/生成提示），统计卡死显示禁复发
        assert "测验题</div>" not in ui

    def test_pulse_avg_drift_repair_landed(self, artifact):
        rows = self._rows(artifact)
        r = rows[("pulse.get_stats:160", "avg_drift_ms")]
        assert r["verdict"] == _D
        assert r.get("repair_status") == "landed@CD-㉚"
        assert "已收口" in r["repair_candidate"]
        # 修复真落地：当前 UI 已无平铺漂移键消费、真源 precision 嵌套键已收编
        ui = _src("admin-ui/app/pulse/page.tsx")
        assert "stats?.avg_drift_ms" not in ui
        assert "stats?.precision?.avg_drift_ms" in ui

    def test_connectors_services_repair_landed(self, artifact):
        rows = self._rows(artifact)
        for sk in ("services.host", "services.name", "services.port"):
            r = rows[("soma_discovery.api_full_scan:1472", sk)]
            assert r["verdict"] == _D
            assert r.get("repair_status") == "landed@CD-31"
            assert "已收口" in r["repair_candidate"]
        # 修复真落地：当前 UI 已无修前漂移键消费、真源键已收编
        ui = _src("admin-ui/app/connectors/page.tsx")
        assert "s.name || s.host" not in ui
        assert "{s.host}:{s.port}" not in ui
        assert "s.process_name || s.local_address" in ui
        assert "{s.local_address}:{s.local_port}" in ui
        # 范围纪律（随 CD-32 翻转）：进程名面已由 CD-32 收口（第 10 件），改锁收口后状态
        assert "p.name" not in ui and "p.memory.toFixed" not in ui

    def test_connectors_processes_repair_landed(self, artifact):
        rows = self._rows(artifact)
        r = rows[("soma_discovery.api_full_scan:1472", "processes.name")]
        assert r["verdict"] == _D
        assert r.get("repair_status") == "landed@CD-32"
        assert "已收口" in r["repair_candidate"]
        # 修复真落地：当前 UI 已无修前漂移键消费、真源键已收编
        ui = _src("admin-ui/app/connectors/page.tsx")
        assert "p.name" not in ui
        assert "p.memory" not in ui
        assert "p.command || p.executable" in ui
        assert "p.mem.toFixed" in ui

    def test_every_drift_has_ui_symptom_anchor(self, artifact):
        rows = self._rows(artifact)
        for key in self.DRIFT_KEYS:
            r = rows[key]
            assert r["repair_candidate"], key
            if r.get("repair_status"):
                # 已收口件（CD-⑮）：症状面已移除，验证归 test_recent_count_repair_landed
                # 的「当前 UI 无消费」缺席断言，不再要求症状锚点
                continue
            # 每条未收口漂移至少一锚点落在 UI 消费面（显示行为损失的证据侧）
            assert any(a[0].startswith("admin-ui/") for a in r["anchors"]), key


class TestExpandedFindings:
    UI_LITERALS = [
        ("admin-ui/app/agent-nodes/page.tsx", "recallResults.sources"),
    ]
    # 已收口件（CD-㉔/CD-㉗/CD-㉘）：修前消费字面量「在场」证据锁按 CD-17 先例翻转「清零」
    LANDED_ABSENT_LITERALS = [
        ("admin-ui/app/benchmark/page.tsx", "r.success_rate"),
        # CD-32 进程面收编：修前 p.memory.toFixed TypeError 崩坏面清零
        ("admin-ui/app/connectors/page.tsx", "p.memory.toFixed"),
        ("admin-ui/app/knowledge-graph/page.tsx", "rel.relation_type"),
        ("admin-ui/app/knowledge-graph/page.tsx", "rel.target_entity_id"),
        ("admin-ui/app/knowledge-graph/page.tsx", "selectedEntity.type"),
        ("admin-ui/app/knowledge-graph/page.tsx", "e.type"),
        # CD-㉚ 时间戳 epoch 秒单位收口：修前裸 epoch new Date 消费清零
        ("admin-ui/app/pulse/page.tsx", "new Date(s."),
        ("admin-ui/app/pulse/page.tsx", "new Date(selected."),
        ("admin-ui/app/pulse/page.tsx", "new Date(t."),
    ]

    def test_expanded_all_confirmed_with_candidate(self, artifact):
        exp = artifact["expanded_findings"]
        assert len(exp) == 6
        for e in exp:
            assert e["verdict"] == _D
            assert e["repair_candidate"]
            assert e["note"]

    def test_expanded_ui_literals_present(self):
        for path, sub in self.UI_LITERALS:
            assert sub in _src(path), (path, sub)

    def test_expanded_landed_literals_cleared(self):
        for path, sub in self.LANDED_ABSENT_LITERALS:
            assert sub not in _src(path), (path, sub)

    def test_expanded_not_in_findings_join(self, tool, artifact):
        assert all("consumed_key" not in e for e in artifact["expanded_findings"])
        # 完整性门只对 109 findings 生效：扩面不得被误 join
        assert artifact["counts_input_findings"] == len(artifact["verdicts"])
