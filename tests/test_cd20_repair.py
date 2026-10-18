#!/usr/bin/env python3
"""CD-⑳ vision preview elapsed 收编——UI 消费面收编兄弟端点真源键（真源零改动）回归锁死。

背景（CD-⑲ 留档修复候选首件「vision preview elapsed 收编（一候选一拍）」）：
vision 预览头 previewMeta.elapsed_ms || 0（page.tsx:368）对 mindmap tab 恒回落 0
=假 0 遮蔽真值——兄弟端点契约漂移：bar_chart_json 产出 elapsed_ms（int ms，
src/api/vision.py:208 int(result.elapsed_seconds*1000)）而 mindmap_json 产出
elapsed_seconds（float s，:254）无 elapsed_ms（CD-⑲ confirmed_drift 1 条，
elapsed_seconds 真在场却无人读，CD-⑱「回落遮蔽假 0」同型）。

收口（真源零改动=CD 系列 wire 权威纪律；CD-⑥/CD-⑪/CD-⑰「UI 消费面收编真源」
+CD-④/CD-⑮「禁假渲染」先例）：
- previewElapsedLabel 纯函数逐端点收编真源键：elapsed_ms（bar/json 真源键）直读、
  elapsed_seconds（mindmap/json 真源键）×1000 换算显示 ms；
- 两键皆缺席=真源不产出→返回空串，ms 段整体不渲染禁 fabricate 0（假 0 清零；
  真源真产出 0 才显示 0=真 0 非假 0）。

断言四层（禁自嗨恒真断言）：
①源码消费面：假 0 形清零 + 兄弟真源键双收编（禁只修一半）+ 缺席不 fabricate +
  消费访问 ⊆ 真源产出键（防再发明键）；
②行为桥：bar/mindmap 两真源真跑形状锁死（真源零改动锁：键名归一须先改本锁）
  + 真 UI helper（page.tsx 提取真源码经 node 真执行）吃真源真响应输出精确等于
  真值换算（修前恒假 0 的直接反面）；
③真源锚点：vision.py 两端点源码锚点（单一源）；
④census 真跑真仓：vision findings 消费键 ⊆ 真源产出键并集 + elapsed 漂移 findings
  归零（工具边界如实记：helper 形参 m.* 访问 census 不提取=CD-⑨ 同族既有工具
  边界，非本拍引入——helper 消费形由①②层锁死）。
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
V_UI = REPO / "admin-ui" / "app" / "vision" / "page.tsx"
V_API = REPO / "src" / "api" / "vision.py"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd20_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd20_repair"] = cdc
_spec.loader.exec_module(cdc)

P2 = chr(124) * 2  # || （绕 patch/写入通道竖线坑，chr(124) 字节构造）
FAKE_ZERO_LITERAL = "previewMeta.elapsed_ms " + P2 + " 0"

# 两可达真源端点产出键并集（src/api/vision.py bar_chart_json + mindmap_json，
# live 实证一致：bar 200 键含 elapsed_ms=237、mindmap 200 键含 elapsed_seconds）
TRUE_SOURCE_KEYS = {
    "format",
    "engine",
    "chart_type",
    "size_bytes",
    "elapsed_ms",
    "elapsed_seconds",
    "output_file",
    "image_base64",
}


def _src() -> str:
    return V_UI.read_text(encoding="utf-8")


def _code() -> str:
    """剥行注释后的真实代码（记述/注释里的键名不算消费形）。"""
    return re.sub(r"//.*$", "", _src(), flags=re.M)


def _helper_text() -> str:
    m = re.search(
        r"const previewElapsedLabel = \(m: Record<string, any>\): string => \{.*?\n\};",
        _src(),
        re.S,
    )
    assert m, "previewElapsedLabel 提取失败（提取失效=断言空转）"
    return m.group(0)


def _helper_js() -> str:
    """真 UI helper 源码（剥 TS 类型注解后可被 node 真执行）。"""
    js = _helper_text().replace(": Record<string, any>", "").replace("): string =>", ") =>")
    assert "previewElapsedLabel" in js and "=>" in js, "helper JS 化失败（断言空转）"
    return js


@pytest.fixture(scope="module")
def true_source_responses():
    """两真源端点真跑（真代码真生成器，非桩）。"""
    from src.api.vision import (
        BarChartRequest,
        MindMapRequest,
        bar_chart_json,
        mindmap_json,
    )

    bar = asyncio.run(
        bar_chart_json(BarChartRequest(labels=["a", "b"], values=[1, 2], title="cd20"))
    )
    mm = asyncio.run(mindmap_json(MindMapRequest(root={"label": "root"}, title="cd20")))
    return bar, mm


def _run_helper(cases):
    """page.tsx 真 helper 源码经 node 真执行（真 UI 行为桥）。"""
    if shutil.which("node") is None:
        pytest.skip("node 不在 PATH（工具缺席非代码失败）")
    prog = (
        "const fs = require('fs');\n"
        + _helper_js()
        + "\nconst cases = JSON.parse(fs.readFileSync(0, 'utf8'));\n"
        + "process.stdout.write(JSON.stringify(cases.map(previewElapsedLabel)));\n"
    )
    proc = subprocess.run(
        ["node", "-e", prog],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"node 执行失败: {proc.stderr}"
    return json.loads(proc.stdout)


# ── ① 源码消费面 ───────────────────────────────────────────────


def test_fake_zero_fallback_cleared():
    """修前假 0 形（previewMeta.elapsed_ms || 0）清零 + helper 内禁假 0 回落。"""
    code = _code()
    assert FAKE_ZERO_LITERAL not in code, "假 0 回落遮蔽形残留（修前缺陷复发）"
    helper = _helper_text()
    assert P2 + " 0" not in helper, "helper 内假 0 回落残留"
    assert "??" not in helper, "helper 内假 0 回落残留"


def test_sibling_true_source_keys_both_consumed():
    """兄弟真源键双收编（禁只修一半）：elapsed_ms 直读 + elapsed_seconds ×1000 换算。"""
    helper = _helper_text()
    assert "m.elapsed_ms" in helper, "bar/json 真源键 elapsed_ms 未收编"
    assert "m.elapsed_seconds" in helper, "mindmap/json 真源键 elapsed_seconds 未收编"
    assert "* 1000" in helper, "elapsed_seconds→ms 换算缺失（单位漂移）"


def test_absent_key_renders_nothing_not_fake_zero():
    """两键皆缺席=真源不产出→空串不渲染 ms 段，禁 fabricate 0（CD-④/⑮ 纪律）。"""
    helper = _helper_text()
    assert ": null" in helper, "缺席=null 派生路径缺失"
    assert "return ms !== null" in helper, "缺席守卫缺失（会回落假渲染）"
    assert "'';" in helper, "缺席空串出口缺失（死显示清零落点）"


def test_display_consumes_helper_not_raw_key():
    """显示点真消费 helper，修前裸 drifted 回落形双清零。"""
    code = _code()
    assert "previewElapsedLabel(previewMeta)" in code, "显示点未消费 helper"
    assert "previewMeta.elapsed_ms" not in code, "裸 drifted 直读形残留（绕过收编）"
    assert "ms}`" in _helper_text() or "${ms}ms" in _helper_text(), "ms 显示段落点缺失"


def test_consumed_access_subset_of_true_source_keys():
    """消费访问 ⊆ 真源产出键（防再发明键=此桥先炸）。"""
    code = _code()
    consumed = set(re.findall(r"previewMeta\??\.([A-Za-z_$][\w$]*)", code))
    assert consumed, "previewMeta 消费键为空（提取失效=断言空转）"
    assert consumed <= TRUE_SOURCE_KEYS, (
        f"previewMeta 消费访问真源不产出: {consumed - TRUE_SOURCE_KEYS}"
    )
    helper_keys = set(re.findall(r"\bm\.([A-Za-z_$][\w$]*)", _helper_text()))
    assert helper_keys == {"elapsed_ms", "elapsed_seconds"}, f"helper 消费键集漂移: {helper_keys}"


# ── ② 行为桥（真源真跑 + 真 UI helper 真执行） ─────────────────


def test_bar_json_shape_lock_elapsed_ms(true_source_responses):
    """bar/json 真跑形状锁死：elapsed_ms int 在场、无 elapsed_seconds（真源零改动锁）。"""
    bar, _ = true_source_responses
    assert "elapsed_ms" in bar, "bar/json 真源键 elapsed_ms 缺席（形状变了须复挖）"
    assert isinstance(bar["elapsed_ms"], int), "elapsed_ms 类型漂移须复挖"
    assert "elapsed_seconds" not in bar, "bar/json 出现 elapsed_seconds=真源形状变了须复挖"


def test_mindmap_json_shape_lock_elapsed_seconds(true_source_responses):
    """mindmap/json 真跑形状锁死：elapsed_seconds 在场、无 elapsed_ms（兄弟漂移真源零改动锁）。

    未来后端键名归一（mindmap 补 elapsed_ms 或统一改名）此桥先炸=CD-⑲ 裁决
    「兄弟键漂移」根因仍在真源的证据锁；改道须显式改本锁并复挖 CD-⑲。
    """
    _, mm = true_source_responses
    assert "elapsed_seconds" in mm, "mindmap/json 真源键 elapsed_seconds 缺席（形状变了须复挖）"
    assert isinstance(mm["elapsed_seconds"], (int, float)), "elapsed_seconds 类型漂移须复挖"
    assert "elapsed_ms" not in mm, "mindmap/json 出现 elapsed_ms=真源形状变了须复挖"


def test_ui_helper_executes_on_true_source_shapes(true_source_responses):
    """真 helper 吃真源真响应输出精确等于真值换算（修前恒假 0 的直接反面）。"""
    bar, mm = true_source_responses
    bar_input = {k: v for k, v in bar.items() if k != "image_base64"}
    mm_input = {k: v for k, v in mm.items() if k != "image_base64"}
    out = _run_helper([bar_input, mm_input, {}, {"elapsed_ms": 0}])
    assert out[0] == " · " + str(bar["elapsed_ms"]) + "ms", f"bar 段输出漂移: {out[0]!r}"
    expect_mm = " · " + str(round(mm["elapsed_seconds"] * 1000)) + "ms"
    assert out[1] == expect_mm, f"mindmap 段输出漂移: {out[1]!r}"
    assert out[2] == "", f"缺席段应空串禁假 0: {out[2]!r}"
    assert out[3] == " · 0ms", f"真源真产出 0 应显示真 0: {out[3]!r}"


def test_mindmap_true_value_not_masked_by_zero(true_source_responses):
    """mindmap 真值非 0 时显示禁假 0（修前恒 0ms 遮蔽真值的行为反证）。"""
    _, mm = true_source_responses
    if round(mm["elapsed_seconds"] * 1000) <= 0:
        pytest.skip("本轮真源真耗时舍入为 0（真 0 非假 0，无遮蔽可证）")
    mm_input = {k: v for k, v in mm.items() if k != "image_base64"}
    out = _run_helper([mm_input])
    assert out[0] != " · 0ms", "真值非 0 仍显 0=假 0 遮蔽复发"
    assert out[0].endswith("ms") and out[0][3:-2].isdigit(), f"显示段形态漂移: {out[0]!r}"


# ── ③ 真源锚点（单一源） ───────────────────────────────────────


def test_true_source_anchors():
    """vision.py 两端点源码锚点：真源形状变更=先炸此锁再改 UI。"""
    api_src = V_API.read_text(encoding="utf-8")
    assert '@router.post("/chart/bar/json")' in api_src, "bar/json 端点锚点漂移"
    assert "async def bar_chart_json" in api_src, "bar/json handler 锚点漂移"
    assert '"elapsed_ms": int(result.elapsed_seconds * 1000)' in api_src, "elapsed_ms 产出锚点漂移"
    assert '@router.post("/mindmap/json")' in api_src, "mindmap/json 端点锚点漂移"
    assert "async def mindmap_json" in api_src, "mindmap/json handler 锚点漂移"
    assert '"elapsed_seconds": result.get("elapsed_seconds", 0)' in api_src, (
        "elapsed_seconds 产出锚点漂移"
    )


# ── ④ census 工具真跑真仓 ─────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def test_census_vision_consumed_keys_subset_true_source(census_report):
    """vision 页 findings 消费键 ⊆ 真源产出键并集（禁再发明键）。"""
    findings = [f for f in census_report["findings"] if f["page"].endswith("vision/page.tsx")]
    for f in findings:
        for k in f["key"]:
            assert k in TRUE_SOURCE_KEYS, f"census 消费键真源不产出: {f['path']} {k}"


def test_census_elapsed_drift_findings_gone(census_report):
    """修后 elapsed 漂移 findings 归零（elapsed_ms/elapsed_seconds 不再作为悬空消费键浮出）。

    工具边界如实记：previewElapsedLabel 的形参 m.* 访问 census 不提取（CD-⑨ 同族
    既有工具边界，修前修后同边界非本拍引入）——helper 消费形由①②层源码/行为桥锁死。
    """
    findings = [f for f in census_report["findings"] if f["page"].endswith("vision/page.tsx")]
    for f in findings:
        for k in f["key"]:
            assert k not in {"elapsed_ms", "elapsed_seconds"}, f"elapsed 漂移 findings 残留: {f}"
