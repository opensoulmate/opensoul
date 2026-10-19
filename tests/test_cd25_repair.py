#!/usr/bin/env python3
"""CD-25 benchmark 运行结果目标列表收编真源——organs↔organs_benchmarked/results[].organ
漂移收口（真源零改动）回归锁死。

背景（CD-22 confirmed_drift 修复候选「benchmark 运行结果目标列表收编真源」+ 同根扩面）：
运行/快速基准响应真源=run_benchmark 返回（src/benchmark/engine.py:166-172）：
顶层键恰 run_id/organs_benchmarked/iterations/concurrency/results——organs_benchmarked
是计数非数组、真源无 organs 数组，目标名在 results[].organ；而 UI 头行
lastResult.organs?.join(', ')（benchmark/page.tsx）恒 undefined→目标列表段恒空渲染
（显示行为损失，真源 results[].organ 可得目标名无人拼）。
同根扩面（普查未收、同拍收口=CD-2 根因修先例）：历史行真源=get_runs -> benchmark_runs
行 dict（organs 列=json.dumps(targets) 串非数组、完成时间真源键 completed_at 非
finished_at），UI run.organs?.join(', ') 对 JSON 串调 .join=渲染必炸 TypeError
（历史 tab 有行即崩），且 || '全部' 是真源不产出的 fabricate。

收口（真源零改动=CD 系列 wire 权威纪律；CD-23/24「UI 消费面收编真源」+CD-7「死声明键
清零」先例）：
- BenchmarkRun 按两真源形拆分：BenchmarkRunResult（run/quick 响应形）+
  BenchmarkRunRow（benchmark_runs 行形，organs: string=JSON 串）；死声明键
  finished_at/organs(string[])/历史行 results 清零；
- 目标列表改从真源 results[].organ 派生拼接（修前 lastResult.organs 死键）；
- 历史行 organs JSON 串收编 runOrgansLabel 单一读取口（JSON.parse 后 join），
  '全部' fabricate 清零。

断言四层（禁自嗨恒真断言）：
1. 源码消费面：修前死键消费零残留+真源派生/收编在场+接口键集恰两真源键集
   +消费访问 ⊆ 真源产出键（防再发明键）；
2. 行为桥：engine 真函数真跑（真 run_benchmark 打真 stub HTTP、tmp 库隔离绝不碰
   生产库）响应/行两真源形形状锁死+真 UI helper（page.tsx 提取真源码经 node 真执行）
   吃真源真 JSON 串精确对账+修前 string.join 形必炸反证；
3. 真源锚点：engine 返回/INSERT/DDL/get_runs 锚点；
4. census 真跑真仓：organs 漂移 findings 归零+消费键 ⊆ 真源产出键。
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UI = REPO / "admin-ui" / "app" / "benchmark" / "page.tsx"
BE = REPO / "src" / "benchmark" / "engine.py"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd25_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd25_repair"] = cdc
_spec.loader.exec_module(cdc)

# 真源产出键（src/benchmark/engine.py：run_benchmark 返回 + benchmark_runs 行 dict）
RUN_RESULT_KEYS = {"run_id", "organs_benchmarked", "iterations", "concurrency", "results"}
RUN_ROW_KEYS = {
    "run_id",
    "organs",
    "iterations",
    "concurrency",
    "started_at",
    "completed_at",
    "status",
}
RESULT_ELEMENT_KEYS = {
    "organ",
    "label",
    "iterations",
    "success",
    "errors",
    "latency",
    "throughput_rps",
    "total_duration_ms",
    "timestamp",
}

ui_src = UI.read_text(encoding="utf-8")
be_src = BE.read_text(encoding="utf-8")


def _code() -> str:
    """剥行注释后的真实代码（注释里的键名记述不算消费/声明形，CD-20/24 同款）。"""
    return re.sub(r"//.*$", "", ui_src, flags=re.M)


def _iface_keys(name: str) -> set:
    m = re.search(r"interface " + name + r" \{([^}]*)\}", ui_src)
    assert m, f"{name} 接口声明缺失（提取失效=断言空转）"
    return set(re.findall(r"^\s*(\w+)\s*:", m.group(1), flags=re.M))


def _last_result_block() -> str:
    seg = ui_src[ui_src.index("{/* Last Result */}") : ui_src.index("{/* Latest Tab */}")]
    assert "lastResult.results.map((r) => r.organ)" in seg, "运行结果块提取失效（断言空转）"
    return seg


def _history_block() -> str:
    seg = ui_src[ui_src.index("{/* History Tab */}") :]
    assert "runOrgansLabel(run.organs)" in seg, "历史块提取失效（断言空转）"
    return seg


# ── (1) 源码消费面 ─────────────────────────────────────────────


def test_dead_organs_key_consumption_gone():
    """修前死键消费零残留（lastResult.organs/.organs?.join/'全部' fabricate 全清零）。"""
    code = _code()
    assert "lastResult.organs" not in code, "lastResult.organs 死键消费残留（真源无 organs 数组）"
    assert ".organs?.join" not in code, "organs?.join 修前漂移读残留"
    assert "|| '全部'" not in code, "'全部' fabricate 残留（真源不产出禁编造）"
    assert "finished_at" not in code, "finished_at 死声明键残留（真源键=completed_at）"


def test_target_list_derived_from_true_source_results():
    """目标列表改从真源 results[].organ 派生拼接（显示行为恢复的真消费）。"""
    seg = _last_result_block()
    assert "lastResult.results.map((r) => r.organ).join(', ')" in seg, (
        "目标列表未从 results[].organ 派生"
    )
    assert "runOrgansLabel(run.organs)" in _history_block(), "历史行 organs JSON 串未收编"
    helper = (
        "const runOrgansLabel = (raw: string) => (JSON.parse(raw) as string[]).join(', ') || '-';"
    )
    assert helper in ui_src, "runOrgansLabel 读取口缺失/形状漂移"


def test_interfaces_exact_two_true_source_shapes():
    """两接口键集恰各真源键集（死声明键清零，CD-7 纪律；BenchmarkRun 按两真源形拆分）。"""
    assert _iface_keys("BenchmarkRunResult") == RUN_RESULT_KEYS
    assert _iface_keys("BenchmarkRunRow") == RUN_ROW_KEYS
    code = _code()
    assert "BenchmarkRun[]" not in code and "BenchmarkRun | null" not in code, (
        "旧合并接口 BenchmarkRun 消费残留"
    )


def test_consumed_access_subset_true_source():
    """消费访问 ⊆ 真源产出键（防再发明键），两块按各自真源形分判。"""
    lr_keys = set(re.findall(r"lastResult\.(\w+)", _last_result_block()))
    assert lr_keys <= RUN_RESULT_KEYS, f"运行结果块消费键越界: {lr_keys - RUN_RESULT_KEYS}"
    run_keys = set(re.findall(r"run\.(\w+)", _history_block()))
    assert run_keys <= RUN_ROW_KEYS, f"历史块消费键越界: {run_keys - RUN_ROW_KEYS}"


# ── (2) 行为桥：engine 真函数真跑 + 真 UI helper node 真执行 ──────


class _OKHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # 静默
        pass


@pytest.fixture(scope="module")
def true_shapes(tmp_path_factory):
    """两真源形真跑（真 run_benchmark 打真 stub HTTP + 真 get_runs，tmp 库绝不碰生产库）。"""
    import src.benchmark.engine as engine_mod

    server = ThreadingHTTPServer(("127.0.0.1", 0), _OKHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    tmp = tmp_path_factory.mktemp("bench_cd25")
    orig = engine_mod.DB_PATH
    engine_mod.DB_PATH = tmp / "benchmark.db"
    try:
        eng = engine_mod.BenchmarkEngine()
        eng._base_url = f"http://127.0.0.1:{server.server_address[1]}"
        target = next(iter(engine_mod.BENCHMARK_TARGETS))
        import asyncio

        resp = asyncio.run(eng.run_benchmark(organs=[target], iterations=2, concurrency=1))
        rows = eng.get_runs()
    finally:
        engine_mod.DB_PATH = orig
        server.shutdown()
    assert rows, "get_runs 无行（行为桥空转）"
    return resp, rows[0], target


def test_run_response_shape_locked(true_shapes):
    """run/quick 响应形锁死：顶层键恰 5 键+无 organs 数组硬反证+目标名真在 results[].organ。"""
    resp, _, target = true_shapes
    assert set(resp.keys()) == RUN_RESULT_KEYS, f"响应键漂移: {set(resp.keys())}"
    assert "organs" not in resp, "organs 数组卷土重来（真源是 organs_benchmarked 计数）"
    assert resp["organs_benchmarked"] == 1
    assert [r["organ"] for r in resp["results"]] == [target]


def test_run_row_shape_locked(true_shapes):
    """benchmark_runs 行形锁死：恰 7 键+organs 是 JSON 串非数组+completed_at 真源键。"""
    _, row, target = true_shapes
    assert set(row.keys()) == RUN_ROW_KEYS, f"历史行键漂移: {set(row.keys())}"
    assert isinstance(row["organs"], str), "organs 列应为 JSON 串（真源 json.dumps(targets)）"
    assert json.loads(row["organs"]) == [target]
    assert "finished_at" not in row, "finished_at 卷土重来（真源键=completed_at）"


def test_ui_helper_exec_on_true_shapes(true_shapes):
    """真 UI helper 源码经 node 真执行吃真源真 JSON 串，目标列表精确对账。"""
    if shutil.which("node") is None:
        pytest.skip("node 不在 PATH（工具缺席非代码失败）")
    _, row, target = true_shapes
    m = re.search(r"const runOrgansLabel = \(raw: string\) => [^;]+;", ui_src)
    assert m, "runOrgansLabel 提取失败（提取失效=断言空转）"
    js = m.group(0).replace(": string", "").replace(" as string[]", "")
    assert "JSON.parse" in js, "helper JS 化失败"
    prog = (
        js
        + "\nconst fs = require('fs');\n"
        + "const cases = JSON.parse(fs.readFileSync(0, 'utf8'));\n"
        + "process.stdout.write(JSON.stringify(cases.map(c => runOrgansLabel(c))));\n"
    )
    proc = subprocess.run(
        ["node", "-e", prog],
        input=json.dumps([row["organs"]]),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"node 执行失败: {proc.stderr}"
    assert json.loads(proc.stdout) == [target], f"目标列表漂移: {proc.stdout}"


def test_old_string_join_form_crashes_on_true_shape(true_shapes):
    """修前形 run.organs?.join 对真源 JSON 串必炸 TypeError（历史 tab 有行即崩硬反证）。"""
    if shutil.which("node") is None:
        pytest.skip("node 不在 PATH（工具缺席非代码失败）")
    _, row, _ = true_shapes
    prog = (
        "const raw = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
        "process.stdout.write(String(raw?.join(', ')));\n"
    )
    proc = subprocess.run(
        ["node", "-e", prog],
        input=json.dumps(row["organs"]),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0, "修前形竟然不炸（真源形变了须复挖）"
    assert "TypeError" in proc.stderr, f"炸法漂移: {proc.stderr}"


# ── (3) 真源锚点 ───────────────────────────────────────────────


def test_true_source_anchors():
    """engine 返回/INSERT/DDL/get_runs 锚点（被改=本裁决/收编失效须复挖）。"""
    assert '"organs_benchmarked": len(results),' in be_src, "organs_benchmarked 计数锚点漂移"
    assert '"run_id": run_id,' in be_src, "run 响应 run_id 锚点漂移"
    assert "json.dumps(targets)" in be_src, "benchmark_runs.organs JSON 串写入锚点漂移"
    ddl = be_src[be_src.index("CREATE TABLE IF NOT EXISTS benchmark_runs") :]
    ddl = ddl[: ddl.index('""")')]
    assert "organs      TEXT NOT NULL," in ddl, "benchmark_runs organs 列锚点漂移"
    assert "completed_at REAL" in ddl, "completed_at 真源键锚点漂移"
    assert "finished_at" not in ddl, "DDL 冒出 finished_at（真源零改动锁）"
    assert "SELECT * FROM benchmark_runs" in be_src, "get_runs 读面锚点漂移"


# ── (4) census 工具真跑真仓 ────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def test_census_organs_drift_findings_gone(census_report):
    """修后 benchmark 页 organs 漂移 findings 归零+run/quick 消费键 ⊆ 真源产出键。

    工具边界如实记：census 对 r.latency.* 嵌套访问提取出 'la' 残片（CD-9/24 同族
    嵌套提取既有边界，修前修后同边界非本拍引入）——'la' 按工具残片如实放行。
    """
    hits = [f for f in census_report["findings"] if f["page"].endswith("benchmark/page.tsx")]
    assert hits, "benchmark findings 缺席（提取失效=断言空转）"
    for f in hits:
        assert not (f["key"] and f["key"][0] == "organs"), f"organs 漂移 findings 残留: {f['key']}"
    run_hits = [
        f
        for f in hits
        if f.get("handler", "").startswith(("benchmark.run_benchmark", "benchmark.quick_benchmark"))
    ]
    allowed = RESULT_ELEMENT_KEYS | {"la"}
    for f in run_hits:
        assert f["key"][0] in RUN_RESULT_KEYS, f"run/quick 消费键越界: {f['key']}"
        leaves = set(f["key"][1:])
        assert leaves <= allowed, f"census 消费键真源不产出: {f['key']}"


def test_census_produced_face_unchanged(census_report):
    """targets/latest/runs 三取数调用点 produced 不变面（收编零伤及顶层取数）。"""
    produced = {
        f.get("path")
        for f in census_report["findings"]
        if f["page"].endswith("benchmark/page.tsx") and f["verdict"] == "produced"
    }
    assert "/api/benchmark/targets" in produced
    assert "/api/benchmark/latest" in produced
    assert "/api/benchmark/history/runs" in produced
