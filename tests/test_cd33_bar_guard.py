"""CD-33 bar_chart_json 请求守卫补齐——与流式兄弟 /chart/bar 同型同码同文案。

修前（live 同探针双向实证，详见周期日志）：
- POST /chart/bar/json {"labels":[],"values":[]} → 200 空图表 fabricated 输出
  （流式兄弟 /chart/bar → 400 "Labels and values required"）
- POST /chart/bar/json 长度不齐 → 500 Internal Server Error 裸错
  （流式兄弟 → 400 "Labels and values must have same length"）

本拍收口 CD-21 诚实边界①「bar_chart_json 无请求守卫（既有面不动，与新三端点
守卫不对称如实记，另拍可复核）」：复核定性=同一 BarChartRequest 同一生成器、
仅响应形态不同，校验面分叉即漂移（CD 系列同根）；守卫与流式兄弟
create_bar_chart 同码同文案（行为平价断言非抄文案锁字面）。
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

REPO = Path(__file__).resolve().parent.parent
V_API = REPO / "src" / "api" / "vision.py"

FAMILY_KEYS = {
    "format",
    "engine",
    "chart_type",
    "size_bytes",
    "elapsed_ms",
    "output_file",
    "image_base64",
}


def _src() -> str:
    return V_API.read_text(encoding="utf-8")


def _func_tree(funcname: str):
    for node in ast.walk(ast.parse(_src())):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == funcname:
            return node
    raise AssertionError(f"function {funcname} not found in src/api/vision.py")


@pytest.fixture(scope="module")
def vision_api():
    import src.api.vision as mod

    return mod


# ── ① 守卫行为平价（与流式兄弟同码同文案）────────────────────────


@pytest.mark.parametrize(
    ("payload",),
    [
        ({"labels": [], "values": []},),
        ({"labels": [], "values": [1.0]},),
        ({"labels": ["a"], "values": []},),
    ],
)
def test_empty_input_guard_parity_with_streaming_sibling(vision_api, payload):
    """空输入守卫与流式兄弟 /chart/bar 同码同文案（行为平价断言，非抄文案锁字面）。"""
    req_new = vision_api.BarChartRequest(**payload)
    req_old = vision_api.BarChartRequest(**payload)
    with pytest.raises(HTTPException) as e_new:
        asyncio.run(vision_api.bar_chart_json(req_new))
    with pytest.raises(HTTPException) as e_old:
        asyncio.run(vision_api.create_bar_chart(req_old))
    assert e_new.value.status_code == e_old.value.status_code == 400
    assert e_new.value.detail == e_old.value.detail


def test_length_mismatch_guard_parity_with_streaming_sibling(vision_api):
    """长度不齐守卫与流式兄弟同码同文案（修前 bar/json 此形 500 裸错）。"""
    with pytest.raises(HTTPException) as e_new:
        asyncio.run(
            vision_api.bar_chart_json(
                vision_api.BarChartRequest(labels=["a", "b", "c"], values=[1.0, 2.0])
            )
        )
    with pytest.raises(HTTPException) as e_old:
        asyncio.run(
            vision_api.create_bar_chart(
                vision_api.BarChartRequest(labels=["a", "b", "c"], values=[1.0, 2.0])
            )
        )
    assert e_new.value.status_code == e_old.value.status_code == 400
    assert e_new.value.detail == e_old.value.detail


# ── ② 禁 fabricate（守卫先于生成器，空输入/不齐不触达生成器）────────


@pytest.mark.parametrize(
    ("payload",),
    [
        ({"labels": [], "values": []},),
        ({"labels": ["a", "b", "c"], "values": [1.0, 2.0]},),
    ],
)
def test_guard_rejects_before_generator_no_fabricated_output(vision_api, monkeypatch, payload):
    """修前反证面：空输入 200 空图表 fabricated / 不齐 500 裸错——现守卫先拒，
    charts.bar_chart 零触达（spy 直证禁硬跑）。"""
    called = []

    def _boom(**kw):
        called.append(kw)
        raise AssertionError("charts.bar_chart must not run for rejected input")

    monkeypatch.setattr(vision_api.charts, "bar_chart", _boom)
    with pytest.raises(HTTPException) as e:
        asyncio.run(vision_api.bar_chart_json(vision_api.BarChartRequest(**payload)))
    assert e.value.status_code == 400
    assert called == []


# ── ③ 不变面（新旧代码皆过=非自嗨判别例）────────────────────────


def test_valid_input_face_unchanged(vision_api):
    """合法输入 7 键契约零变化（CD-21 test_bar_json_existing_face_unchanged 同面延续）。"""
    resp = asyncio.run(
        vision_api.bar_chart_json(vision_api.BarChartRequest(labels=["a", "b"], values=[1.0, 2.0]))
    )
    assert set(resp.keys()) == FAMILY_KEYS
    assert resp["chart_type"] == "bar"
    assert resp["image_base64"]


def test_sibling_valid_input_unchanged(vision_api):
    """流式兄弟合法输入零变化（守卫对称收口不碰生成路径）。"""
    resp = asyncio.run(
        vision_api.create_bar_chart(
            vision_api.BarChartRequest(labels=["a", "b"], values=[1.0, 2.0])
        )
    )
    assert resp.status_code == 200


def test_other_json_variants_zero_regression(vision_api):
    """line/pie/scatter 三 /json 变体守卫与合法面零回归（CD-21 收口面不动）。"""
    for fn, req_cls, good, bad in [
        (
            "line_chart_json",
            "LineChartRequest",
            {"x": [1.0], "series": {"s": [1.0]}},
            {"x": [], "series": {}},
        ),
        (
            "pie_chart_json",
            "PieChartRequest",
            {"labels": ["a"], "values": [1.0]},
            {"labels": [], "values": []},
        ),
        ("scatter_chart_json", "ScatterRequest", {"x": [1.0], "y": [1.0]}, {"x": [], "y": []}),
    ]:
        resp = asyncio.run(getattr(vision_api, fn)(getattr(vision_api, req_cls)(**good)))
        assert set(resp.keys()) == FAMILY_KEYS
        with pytest.raises(HTTPException) as e:
            asyncio.run(getattr(vision_api, fn)(getattr(vision_api, req_cls)(**bad)))
        assert e.value.status_code == 400


# ── ④ 静态接线（结构面根因锁：守卫真在 handler 体内且先于生成器调用）──────


def test_guard_wired_in_handler_before_generator():
    tree = ast.parse(_src())
    fn = None
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "bar_chart_json"
        ):
            fn = node
    assert fn is not None
    body = fn.body
    # 找到 charts.bar_chart 调用的语句下标
    gen_idx = None
    guard_idx = []
    for i, stmt in enumerate(body):
        for sub in ast.walk(stmt):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "bar_chart"
            ):
                gen_idx = i
            if (
                isinstance(sub, ast.Raise)
                and isinstance(sub.exc, ast.Call)
                and getattr(sub.exc.func, "id", "") == "HTTPException"
            ):
                guard_idx.append(i)
    assert gen_idx is not None, "bar_chart 调用点必须在场"
    assert len(guard_idx) >= 2, "守卫（raise HTTPException）两道必须在场"
    assert max(guard_idx) < gen_idx, "守卫必须先于生成器调用（修前无守卫=空输入硬跑）"


def test_handler_route_still_registered():
    """路由注册零回归（/chart/bar/json 仍真注册，守卫不碰路由面）。"""
    paths = set()
    for node in ast.walk(ast.parse(_src())):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "post"
                    and dec.args
                    and isinstance(dec.args[0], ast.Constant)
                ):
                    paths.add(dec.args[0].value)
    assert "/chart/bar/json" in paths
