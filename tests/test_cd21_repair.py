#!/usr/bin/env python3
"""CD-㉑ vision chart json 端点面补齐——line/pie/scatter 三分支 /json 变体（CD-⑲ 端点级
扩面 confirmed_drift 3 条修复件）回归锁死。

背景（CD-⑲ 裁决+live --pre 双实证）：vision 页折线/饼/散点三个 tab 的 handleGenerate
url 打 /api/vision/chart/{line,pie,scatter}/json（page.tsx:127/142/150），而真源路由
全集只有 /chart/bar/json + /mindmap/json 两个 json 变体（line/pie/scatter 只有流式
二进制）——三分支生成必 404 死路径（setError 可见非静默但功能死）。

收口（端点面补齐=CD-⑲ repair_candidate 原文）：src/api/vision.py 补三个 /json 变体，
响应载荷与 bar/json 同形 7 键（chart/json 家族契约）：
- _chart_json_payload 单一源装配口（line/pie/scatter 三端点共用，F4b-㉗ 禁手抄漂移）；
- bar_chart_json 内联字面量不搬动（CD-⑲ 证据锁锚点），两形由本文件平价锁死；
- 请求守卫与各自流式兄弟同型（空输入 400 如实拒禁硬跑，G1-① 纪律）。

断言四层（禁自嗨恒真断言）：
①路由面：三个新路径真注册（UI url 字面量逐一对得上路由，死路径复活根断言）；
②行为桥：真代码真生成器真跑——响应恰 7 键/elapsed_ms int/chart_type 真值/
  image_base64 真 base64，与 bar/json 平价（键集双向锁死禁两形漂移）；
③契约面：空输入守卫与流式兄弟同码同文案（行为平价，非抄文案）、save_output
  装配 spy 直证、非 png 不 fabricate base64；
④UI 消费面：previewMeta 消费键 ⊆ 新端点产出键（防再发明键）。
"""

from __future__ import annotations

import ast
import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

REPO = Path(__file__).resolve().parent.parent
V_UI = REPO / "admin-ui" / "app" / "vision" / "page.tsx"
V_API = REPO / "src" / "api" / "vision.py"

# chart/json 家族 wire 契约（bar/json 既有 7 键，CD-⑲ 真源锚点同集）
FAMILY_KEYS = {
    "format",
    "engine",
    "chart_type",
    "size_bytes",
    "elapsed_ms",
    "output_file",
    "image_base64",
}

NEW_ENDPOINTS = {
    "line": "/chart/line/json",
    "pie": "/chart/pie/json",
    "scatter": "/chart/scatter/json",
}


def _src() -> str:
    return V_API.read_text(encoding="utf-8")


def _ui() -> str:
    return V_UI.read_text(encoding="utf-8")


def _func_tree(funcname: str):
    for node in ast.walk(ast.parse(_src())):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == funcname:
            return node
    raise AssertionError(f"function {funcname} not found in src/api/vision.py")


def _return_dict_keys(funcname: str) -> set:
    """函数体内第一个 return dict 字面量的常量键集（CD-⑲ 同款真源形状锚点）。"""
    for sub in ast.walk(_func_tree(funcname)):
        if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
            return {k.value for k in sub.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError(f"no return dict literal in {funcname}")


def _router_paths() -> set:
    paths = set()
    for node in ast.walk(ast.parse(_src())):
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


@pytest.fixture(scope="module")
def vision_api():
    import src.api.vision as mod

    return mod


@pytest.fixture(scope="module")
def real_responses(vision_api):
    """三个新端点真跑（真代码真生成器非桩）+ bar/json 平价对照。"""

    async def _run():
        line = await vision_api.line_chart_json(
            vision_api.LineChartRequest(
                x=["1", "2", "3"], series={"A": [1.0, 2.0, 3.0]}, title="cd21"
            )
        )
        pie = await vision_api.pie_chart_json(
            vision_api.PieChartRequest(labels=["a", "b"], values=[1.0, 2.0], title="cd21")
        )
        scatter = await vision_api.scatter_chart_json(
            vision_api.ScatterRequest(x=[1.0, 2.0], y=[3.0, 4.0], title="cd21")
        )
        bar = await vision_api.bar_chart_json(
            vision_api.BarChartRequest(labels=["a", "b"], values=[1.0, 2.0], title="cd21")
        )
        return {"line": line, "pie": pie, "scatter": scatter, "bar": bar}

    return asyncio.run(_run())


# ── ① 路由面（死路径复活根断言：UI url 字面量逐一对得上真源路由）──────────


def test_new_json_routes_registered():
    paths = _router_paths()
    for p in NEW_ENDPOINTS.values():
        assert p in paths, f"新路由缺席: {p}"
    # 对照面：既有 json 变体与流式兄弟照旧在场
    assert "/chart/bar/json" in paths
    assert "/mindmap/json" in paths
    assert {"/chart/line", "/chart/pie", "/chart/scatter"} <= paths


def test_ui_url_literals_resolve_to_real_routes():
    """UI 五个 chart/json url 字面量（含 mindmap）逐一对得上真源路由。"""
    ui = _ui()
    paths = _router_paths()
    for literal in (
        "/api/vision/chart/bar/json",
        "/api/vision/chart/line/json",
        "/api/vision/chart/pie/json",
        "/api/vision/chart/scatter/json",
        "/api/vision/mindmap/json",
    ):
        assert f"'{literal}'" in ui, literal
        assert literal.replace("/api/vision", "") in paths, f"UI 打的 url 无真源路由: {literal}"


# ── ② 行为桥（真代码真生成器）────────────────────────────────────────


@pytest.mark.parametrize("kind", ["line", "pie", "scatter"])
def test_response_shape_exact_family_keys(real_responses, kind):
    resp = real_responses[kind]
    assert set(resp.keys()) == FAMILY_KEYS, f"{kind} 响应键集漂移: {sorted(resp.keys())}"
    assert resp["chart_type"] == kind
    assert isinstance(resp["elapsed_ms"], int) and resp["elapsed_ms"] >= 0
    assert resp["output_file"] == ""
    if resp["format"] == "png":
        assert base64.b64decode(resp["image_base64"])[:8] == b"\x89PNG\r\n\x1a\n"


def test_parity_with_bar_inline_literal():
    """平价锁：单一源 _chart_json_payload 键集 == bar_chart_json 内联字面量键集。

    两形任一侧漂移必炸（bar 内联形是 CD-⑲ 证据锁锚点不搬动）。
    """
    assert _return_dict_keys("_chart_json_payload") == _return_dict_keys("bar_chart_json")
    assert _return_dict_keys("_chart_json_payload") == FAMILY_KEYS


def test_new_endpoints_use_single_assembly_point():
    """三个新端点真走单一源装配口（禁第二套装配形复活）。"""
    for fn in ("line_chart_json", "pie_chart_json", "scatter_chart_json"):
        node = _func_tree(fn)
        calls = {
            sub.func.id
            for sub in ast.walk(node)
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
        }
        assert "_chart_json_payload" in calls, f"{fn} 未走单一源装配口"


# ── ③ 契约面（守卫同型/装配接线/非 png 不 fabricate）────────────────


@pytest.mark.parametrize(
    ("fn", "sibling", "req", "empty_kwargs"),
    [
        ("line_chart_json", "create_line_chart", "LineChartRequest", {"x": [], "series": {}}),
        ("pie_chart_json", "create_pie_chart", "PieChartRequest", {"labels": [], "values": []}),
        ("scatter_chart_json", "create_scatter_plot", "ScatterRequest", {"x": [], "y": []}),
    ],
)
def test_guard_parity_with_streaming_sibling(vision_api, fn, sibling, req, empty_kwargs):
    """空输入守卫与流式兄弟同码同文案（行为平价断言，非抄文案锁字面）。"""
    empty = getattr(vision_api, req)(**empty_kwargs)
    with pytest.raises(HTTPException) as e_new:
        asyncio.run(getattr(vision_api, fn)(empty))
    with pytest.raises(HTTPException) as e_old:
        asyncio.run(getattr(vision_api, sibling)(empty))
    assert e_new.value.status_code == e_old.value.status_code == 400
    assert e_new.value.detail == e_old.value.detail


def test_save_output_wiring_spy(vision_api, monkeypatch):
    """save_output=True 真走 charts.save_output 装配（spy 直证），False 恒空串。"""
    seen = []
    monkeypatch.setattr(
        vision_api.charts,
        "save_output",
        lambda data, fname, fmt: seen.append(fname) or "sentinel-out",
    )
    resp = asyncio.run(
        vision_api.pie_chart_json(
            vision_api.PieChartRequest(labels=["a"], values=[1.0], save_output=True)
        )
    )
    assert resp["output_file"] == "sentinel-out"
    assert seen and seen[0].startswith("pie-")
    resp2 = asyncio.run(
        vision_api.pie_chart_json(vision_api.PieChartRequest(labels=["a"], values=[1.0]))
    )
    assert resp2["output_file"] == ""


def test_non_png_no_fabricated_base64(vision_api):
    """非 png 结果 image_base64 恒空串（与 bar/json 同语义禁 fabricate）。"""
    fake = SimpleNamespace(
        format="txt",
        engine="text",
        chart_type="bar",
        size_bytes=1,
        elapsed_seconds=0.25,
        image_data=b"x",
    )
    payload = vision_api._chart_json_payload(fake, "")
    assert payload["image_base64"] == ""
    assert payload["elapsed_ms"] == 250


# ── 不变面（新旧代码皆过=非自嗨判别例）────────────────────────────


def test_bar_json_existing_face_unchanged(vision_api):
    """bar/json 既有 7 键契约零变化（本拍不动 bar 面，控制组同过）。"""
    resp = asyncio.run(
        vision_api.bar_chart_json(vision_api.BarChartRequest(labels=["a", "b"], values=[1.0, 2.0]))
    )
    assert set(resp.keys()) == FAMILY_KEYS
    assert resp["chart_type"] == "bar"


# ── ④ UI 消费面（消费键 ⊆ 新端点产出键，防再发明键）────────────────


def test_ui_consumed_keys_subset_of_payload_keys():
    ui = _ui()
    payload_keys = _return_dict_keys("_chart_json_payload")
    # preview 头消费形（page.tsx 真源码文本）：engine/format/size_bytes/output_file
    for frag in (
        "{previewMeta.engine}",
        "previewMeta.format",
        "previewMeta.size_bytes",
        "previewMeta?.output_file",
        "data.image_base64",
    ):
        assert frag in ui, frag
    for k in ("engine", "format", "size_bytes", "output_file", "image_base64"):
        assert k in payload_keys, f"UI 消费键真源不产出: {k}"
