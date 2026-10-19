#!/usr/bin/env python3
"""CD-24 benchmark results 元素延迟键收编真源——latency 嵌套平铺漂移收口（真源零改动）回归锁死。

背景（CD-22 confirmed_drift 修复候选「benchmark results 元素延迟键收编真源
（latency.* 平铺漂移）」+ 同根扩面 p50/p95/p99/min/success_rate）：
运行/快速基准结果元素真源=_result_to_dict（src/benchmark/engine.py:356-374），
延迟键全在 latency 嵌套层（min/max/avg/p50/p95/p99），success 是成功计数非率、
真源全层无 success_rate 键；而 UI 平铺读 r.avg_ms/r.p50_ms/r.p95_ms/r.p99_ms/
r.min_ms/r.success_rate（benchmark/page.tsx 运行结果块）——全键恒 undefined
（formatMs(undefined) 假渲染+数值着色分支恒假+成功率着色分支恒假）。
另一真源面：latest 元素=get_latest -> benchmarks 行 dict（延迟键恰平铺）——
UI 平铺读 avg_ms/p50/... 是真源在场键零漂移（不变面），唯 success_rate 同根
漂移（行有 success 计数+iterations，无率键）。

收口（真源零改动=CD 系列 wire 权威纪律；CD-6/11/17/23「UI 消费面收编真源」
+CD-7「死声明键清零」+CD-5「真源键派生」先例）：
- 元素接口按两个真源形拆分：BenchmarkResultItem（_result_to_dict 形，latency
  嵌套）+LatestBenchmarkRow（benchmarks 行形，平铺）；死声明键 success_rate/
  concurrency/total_time_ms 清零（真源不产出禁编造）；
- 运行结果块元素消费收编 latency 嵌套键；success_rate 真源不产出→success/
  iterations 派生（successRate helper 单一派生源，CD-5 预算使用率同款派生）；
- latest 块平铺延迟键真源在场不动（不变面），success_rate 同根收口走同一派生。

断言四层（禁自嗨恒真断言）：
1. 源码消费面：运行结果块平铺漂移读零残留+嵌套键真消费+success_rate 全页清零
   +接口键集恰真源键集+消费访问 ⊆ 真源产出键（防再发明键）+latest 不变面；
2. 行为桥：engine 真函数真跑两真源形形状锁死（_result_to_dict 嵌套形+get_latest
   平铺行形，tmp 库隔离绝不碰生产库）+真 UI helper（page.tsx 提取真源码经 node
   真执行）吃真源真数据派生率精确对账；
3. 真源锚点：engine _result_to_dict/get_latest/DDL 锚点；
4. census 真跑真仓：results 元素链漂移 findings 归零+消费键 ⊆ 真源产出键。
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UI = REPO / "admin-ui" / "app" / "benchmark" / "page.tsx"
BE = REPO / "src" / "benchmark" / "engine.py"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd24_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd24_repair"] = cdc
_spec.loader.exec_module(cdc)

# 真源产出键（src/benchmark/engine.py：_result_to_dict 元素 + get_latest benchmarks 行）
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
LATENCY_KEYS = {"min_ms", "max_ms", "avg_ms", "p50_ms", "p95_ms", "p99_ms"}
LATEST_ROW_KEYS = {
    "id",
    "run_id",
    "organ",
    "label",
    "iterations",
    "success",
    "errors",
    "min_ms",
    "max_ms",
    "avg_ms",
    "p50_ms",
    "p95_ms",
    "p99_ms",
    "rps",
    "total_ms",
    "timestamp",
}
FLAT_LATENCY_DRIFT = {"avg_ms", "p50_ms", "p95_ms", "p99_ms", "min_ms", "success_rate"}

ui_src = UI.read_text(encoding="utf-8")
be_src = BE.read_text(encoding="utf-8")


def _code() -> str:
    """剥行注释后的真实代码（注释里的键名记述不算消费/声明形，CD-20 同款）。"""
    return re.sub(r"//.*$", "", ui_src, flags=re.M)


def _iface_keys(name: str) -> set:
    m = re.search(r"interface " + name + r" \{([^}]*)\}", ui_src)
    assert m, f"{name} 接口声明缺失（提取失效=断言空转）"
    return set(re.findall(r"^\s*(\w+)\s*:", m.group(1), flags=re.M))


def _run_block() -> str:
    seg = ui_src[ui_src.index("lastResult.results.map") : ui_src.index("{/* Latest Tab */}")]
    assert "r.organ" in seg, "运行结果块提取失效（断言空转）"
    return seg


def _latest_block() -> str:
    seg = ui_src[ui_src.index("{/* Latest Tab */}") : ui_src.index("{/* History Tab */}")]
    assert "latestEntries.map" in seg and "formatTs(r.timestamp)" in seg, (
        "最新结果块提取失效（断言空转）"
    )
    return seg


# ── (1) 源码消费面 ─────────────────────────────────────────────


def test_run_block_flat_drift_reads_gone_nested_consumed():
    """运行结果块平铺漂移读零残留+latency 嵌套键真消费（禁只修一半）。"""
    seg = _run_block()
    for k in FLAT_LATENCY_DRIFT:
        assert f"r.{k}" not in seg, f"平铺漂移读残留: r.{k}"
    for k in ("avg_ms", "p50_ms", "p95_ms", "p99_ms", "min_ms"):
        assert f"r.latency.{k}" in seg, f"latency 嵌套键未收编: r.latency.{k}"
    assert seg.count("r.latency.avg_ms") >= 2, "avg_ms 须消费于着色+显示两处"


def test_latest_block_flat_keys_kept_true_source():
    """latest 块平铺延迟键=真源在场键（benchmarks 行）零变化不变面+同根 success_rate 收口。"""
    seg = _latest_block()
    assert "formatMs(r.avg_ms)" in seg, "latest 平铺 avg_ms 真源在场键被误改"
    for k in ("p50_ms", "p95_ms", "p99_ms", "min_ms"):
        assert f"r.{k}" in seg, f"latest 平铺真源键被误改: r.{k}"
    assert "r.success_rate" not in seg, "latest 块 success_rate 漂移残留"


def test_success_rate_dead_key_cleared_and_derived():
    """success_rate 死键全页清零；派生单一源 helper（success 计数/iterations）。"""
    code = _code()
    assert "success_rate" not in code, "success_rate 死键残留（真源不产出禁编造）"
    assert "total_time_ms" not in code, (
        "total_time_ms 死声明键残留（真源键=total_duration_ms/total_ms）"
    )
    helper = "const successRate = (success: number, iterations: number) => success / iterations;"
    assert helper in ui_src, "successRate 派生 helper 缺失/形状漂移"
    assert ui_src.count("successRate(r.success, r.iterations)") == 3, (
        "派生须恰 3 消费点（运行块着色+显示+latest 显示）"
    )


def test_interfaces_exact_true_source_shapes():
    """三接口键集恰各真源键集（死声明键清零，CD-7 纪律）。"""
    assert _iface_keys("BenchmarkLatency") == LATENCY_KEYS
    assert _iface_keys("BenchmarkResultItem") == RESULT_ELEMENT_KEYS
    assert _iface_keys("LatestBenchmarkRow") == LATEST_ROW_KEYS


def test_consumed_access_subset_true_source():
    """元素消费访问 ⊆ 真源产出键（防再发明键），两块按各自真源形分判。"""
    run_keys = set(re.findall(r"r\.(\w+)", _run_block()))
    assert run_keys <= RESULT_ELEMENT_KEYS, f"运行块消费键越界: {run_keys - RESULT_ELEMENT_KEYS}"
    lat_keys = set(re.findall(r"r\.latency\.(\w+)", _run_block()))
    assert lat_keys <= LATENCY_KEYS, f"latency 层消费键越界: {lat_keys - LATENCY_KEYS}"
    latest_keys = set(re.findall(r"r\.(\w+)", _latest_block()))
    assert latest_keys <= LATEST_ROW_KEYS, f"latest 块消费键越界: {latest_keys - LATEST_ROW_KEYS}"


# ── (2) 行为桥：engine 真函数真跑 + 真 UI helper node 真执行 ──────


@pytest.fixture(scope="module")
def true_shapes(tmp_path_factory):
    """两真源形真跑（真 _result_to_dict + 真 get_latest，tmp 库绝不碰生产库）。"""
    import src.benchmark.engine as engine_mod

    tmp = tmp_path_factory.mktemp("bench_cd24")
    orig = engine_mod.DB_PATH
    engine_mod.DB_PATH = tmp / "benchmark.db"
    try:
        eng = engine_mod.BenchmarkEngine()
        result = engine_mod.BenchmarkResult(
            organ="liver",
            label="liver-test",
            iterations=10,
            success_count=9,
            error_count=1,
            min_ms=1.0,
            max_ms=9.0,
            avg_ms=4.0,
            p50_ms=3.0,
            p95_ms=8.0,
            p99_ms=9.0,
            throughput_rps=2.0,
            total_duration_ms=100.0,
            timestamp=123.0,
        )
        elem = eng._result_to_dict(result)
        eng._save_result(result, "run_cd24")
        latest = eng.get_latest()
    finally:
        engine_mod.DB_PATH = orig
    return elem, latest["liver"]


def test_result_element_shape_locked(true_shapes):
    """运行结果元素形锁死：恰 9 键+latency 嵌套恰 6 键+无平铺 avg_ms（真源零改动锁）。"""
    elem, _ = true_shapes
    assert set(elem.keys()) == RESULT_ELEMENT_KEYS, f"元素键漂移: {set(elem.keys())}"
    assert set(elem["latency"].keys()) == LATENCY_KEYS, (
        f"latency 键漂移: {set(elem['latency'].keys())}"
    )
    assert "avg_ms" not in elem, "平铺 avg_ms 卷土重来（真源在 latency 嵌套内）"


def test_latest_row_shape_locked(true_shapes):
    """latest 元素形锁死：恰 benchmarks 行 15 键（延迟键平铺=UI latest 块不变面依据）。"""
    _, row = true_shapes
    assert set(row.keys()) == LATEST_ROW_KEYS, f"latest 行键漂移: {set(row.keys())}"
    assert row["success"] == 9 and row["iterations"] == 10


def test_ui_helper_exec_on_true_shapes(true_shapes):
    """真 UI helper 源码经 node 真执行吃真源真数据，派生率精确对账（success/iterations）。"""
    if shutil.which("node") is None:
        pytest.skip("node 不在 PATH（工具缺席非代码失败）")
    elem, row = true_shapes
    m = re.search(
        r"const successRate = \(success: number, iterations: number\) => [^;]+;",
        ui_src,
    )
    assert m, "successRate 提取失败（提取失效=断言空转）"
    js = m.group(0).replace(": number", "")
    assert "successRate" in js and "=>" in js, "helper JS 化失败"
    prog = (
        js
        + "\nconst fs = require('fs');\n"
        + "const cases = JSON.parse(fs.readFileSync(0, 'utf8'));\n"
        + "process.stdout.write(JSON.stringify(cases.map(c => successRate(c.success, c.iterations))));\n"
    )
    cases = [
        {"success": elem["success"], "iterations": elem["iterations"]},
        {"success": row["success"], "iterations": row["iterations"]},
    ]
    proc = subprocess.run(
        ["node", "-e", prog],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"node 执行失败: {proc.stderr}"
    out = json.loads(proc.stdout)
    assert out == [0.9, 0.9], f"派生率漂移: {out}"


# ── (3) 真源锚点 ───────────────────────────────────────────────


def test_true_source_anchors():
    """engine 两真源形锚点+DDL 平铺列锚点（被改=本裁决/收编失效须复挖）。"""
    assert '"latency": {' in be_src, "_result_to_dict latency 嵌套锚点漂移"
    assert '"avg_ms": r.avg_ms,' in be_src, "latency.avg_ms 构造锚点漂移"
    assert '"success": r.success_count,' in be_src, "success 计数构造锚点漂移"
    assert 'return {r["organ"]: dict(r) for r in rows}' in get_latest_src(), (
        "get_latest 行 dict 锚点漂移"
    )
    ddl = be_src[be_src.index("CREATE TABLE IF NOT EXISTS benchmarks") :]
    ddl = ddl[: ddl.index('""")')]
    for col in (
        "avg_ms",
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "min_ms",
        "max_ms",
        "success",
        "iterations",
    ):
        assert col in ddl, f"benchmarks DDL 平铺列缺席: {col}"
    assert "success_rate" not in ddl, "DDL 冒出 success_rate（真源零改动锁）"


def get_latest_src() -> str:
    seg = be_src[be_src.index("def get_latest") :]
    return seg[: seg.index("def get_comparison")]


# ── (4) census 工具真跑真仓 ────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def test_census_results_chain_flat_drift_gone(census_report):
    """修后 results 元素链 findings 平铺漂移键归零+消费键 ⊆ 真源产出键。

    工具边界如实记：census 对 r.latency.* 嵌套访问提取出 'la' 残片与 'latency'
    两键（CD-9 同族嵌套提取既有边界，修前修后同边界非本拍引入）——'la' 按工具
    残片如实放行，其余须 ⊆ 真源元素键。
    """
    hits = [
        f
        for f in census_report["findings"]
        if f["page"].endswith("benchmark/page.tsx") and f["key"] and f["key"][0] == "results"
    ]
    assert hits, "results 元素链 findings 缺席（提取失效=断言空转）"
    allowed = RESULT_ELEMENT_KEYS | {"la"}
    for f in hits:
        leaves = set(f["key"][1:])
        assert not (leaves & FLAT_LATENCY_DRIFT), f"平铺漂移 findings 残留: {f['key']}"
        assert leaves <= allowed, f"census 消费键真源不产出: {f['key']}"


def test_census_produced_face_unchanged(census_report):
    """targets/latest/runs 三取数调用点 produced 不变面（收编零伤及顶层取数）。"""
    produced = {
        (f["page"], f.get("path"))
        for f in census_report["findings"]
        if f["page"].endswith("benchmark/page.tsx") and f["verdict"] == "produced"
    }
    paths = {p for _, p in produced}
    assert "/api/benchmark/targets" in paths
    assert "/api/benchmark/latest" in paths
    assert "/api/benchmark/history/runs" in paths
