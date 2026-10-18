#!/usr/bin/env python3
"""CD-⑦ 修复候选④——limb windows 元素容器收编（UI 消费面收编真源容器形）回归锁死。

背景（CD-③ 深挖裁决 confirmed_drift→候选④，4 候选末件）：limb/page.tsx:130
setWindows(Array.isArray(data.windows) ? data.windows : []) 按 {windows:[...]}
容器形消费，而真源 GET /api/limb/rpa/windows = list_windows() -> list[WindowInfo]
裸数组（src/api/limb_rpa.py:677-678 response_model=list[WindowInfo]）——
对裸数组取 .windows 恒 undefined 恒回落 []（Windows 列表永不渲染）。

收口（UI 消费面收编真源容器形，真源零改动=CD 系列 wire 权威纪律）：
- fetchWindows 收编裸数组（Array.isArray(data) ? data : []）。
- 深挖扩面（CD-⑤/⑥ 先例）：UI WindowInfo 接口声明 app? 且渲染消费 w.app——
  真源元素七键 id/title/x/y/width/height/focused 根本不产出 app（死显示键，
  条件渲染恒假），接口收编真源七键形+死显示面收口（CD-④ 纪律：真源不产出禁假渲染）。

断言两层（禁自嗨恒真断言）：
①源码消费面：裸数组消费真在场 + data.windows/w.app 漂移消费形零残留 +
  接口声明键集==真源 WindowInfo 字段集 + UI 元素消费键 ⊆ 真源元素键（行为桥）。
②census 工具真跑真仓（scripts/census_contract_drift.py）：unresolved_shape 的
  ("windows",) finding 消失 + tasks/templates/history/screenshot/ocr 五组
  produced findings 零回归。
③行为桥真 API 全链：真 router 真 list_windows（xdotool 真解析分支、桩子进程输出）
  GET /api/limb/rpa/windows 响应=裸数组 + 元素键集恰真源七键（真源容器形硬证）。
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "limb" / "page.tsx"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd7_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd7_repair"] = cdc
_spec.loader.exec_module(cdc)

TRUE_SOURCE_KEYS = {"id", "title", "x", "y", "width", "height", "focused"}


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def _code() -> str:
    """消费代码（行注释里的历史记述不算消费形）。"""
    return re.sub(r"//.*$", "", _src(), flags=re.M)


# ── ① 源码消费面 ───────────────────────────────────────────────


def test_windows_container_collected_to_bare_array():
    """收编本体：fetchWindows 对真源裸数组直接 Array.isArray(data) 消费。"""
    code = _code()
    assert "Array.isArray(data) ? data : []" in code, "裸数组容器形消费未收编"
    assert "data.windows" not in code, "旧 {windows:[...]} 容器形消费残留"


def test_dead_app_key_zero_residual():
    """深挖扩面死显示键收口：真源不产出的 app 键声明/消费双清零。"""
    code = _code()
    assert "w.app" not in code, "死显示键 w.app 消费残留"
    m = re.search(r"interface WindowInfo \{([^}]*)\}", code)
    assert m, "WindowInfo 接口声明未找到（提取失效=断言空转）"
    assert not re.search(r"^\s+app\??\s*:", m.group(1), re.M), "死显示键 app 接口声明残留"


def test_window_interface_matches_true_source_shape():
    """接口收编真源形状：WindowInfo 声明键集==真源 pydantic WindowInfo 字段集。"""
    from src.api.limb_rpa import WindowInfo as TrueWindowInfo

    m = re.search(r"interface WindowInfo \{([^}]*)\}", _code())
    assert m, "WindowInfo 接口声明未找到（提取失效=断言空转）"
    declared = set(re.findall(r"^\s+([A-Za-z_$][\w$]*)\??\s*:", m.group(1), re.M))
    true_keys = set(TrueWindowInfo.model_fields)
    assert true_keys == TRUE_SOURCE_KEYS, f"真源字段集变了（裁决证据锁）: {sorted(true_keys)}"
    assert declared == true_keys, (
        f"接口声明键集与真源不一致: declared={sorted(declared)} true={sorted(true_keys)}"
    )


def test_ui_element_consumed_keys_subset_of_true_source():
    """行为桥（源码面）：UI 消费的每个 w.<key> 真源元素都产出（消费 ⊆ 真源）。"""
    from src.api.limb_rpa import WindowInfo as TrueWindowInfo

    code = _code()
    consumed = set(re.findall(r"\bw\.([A-Za-z_$][\w$]*)", code))
    assert consumed, "UI 未提取到元素消费键（提取失效=断言空转）"
    for k in consumed:
        assert k in TrueWindowInfo.model_fields, f"UI 元素消费键真源不产出: {k}"


# ── ② census 工具真跑真仓 ─────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def test_census_unresolved_windows_key_gone(census_report):
    """修后 findings：("windows",) unresolved_shape 消失（修前在 findings=真断点）。"""
    limb_keys = {
        tuple(f["key"]) for f in census_report["findings"] if f["page"].endswith("limb/page.tsx")
    }
    assert ("windows",) not in limb_keys, "容器漂移键仍在 findings"


def test_census_limb_produced_keys_zero_regression(census_report):
    """produced 面零回归：tasks/templates/history/screenshot.base64/ocr.text 照旧。"""
    limb = [f for f in census_report["findings"] if f["page"].endswith("limb/page.tsx")]
    got = {(tuple(f["key"]), f["verdict"]) for f in limb}
    for key in (("tasks",), ("templates",), ("history",), ("base64",), ("text",)):
        assert (key, "produced") in got, f"produced 面回归: {key}"


# ── ③ 行为桥真 API 全链 ───────────────────────────────────────


def test_windows_endpoint_returns_bare_array(monkeypatch):
    """真 router 真 list_windows：响应=裸数组（容器形硬证）+元素键集恰真源七键。

    xdotool 真解析分支全程真代码（仅子进程输出打桩）：search→两窗口、
    getwindowname/getwindowgeometry/getactivewindow→真解析出 WindowInfo 行。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.limb_rpa as limb_rpa

    async def fake_run(*args, **kwargs):
        a = list(args)
        cmd = a[1] if len(a) > 1 else ""
        if cmd == "search":
            return 0, "101\n102\n", ""
        if cmd == "getwindowname":
            return 0, f"cd7-win-title-{a[-1]}\n", ""
        if cmd == "getwindowgeometry":
            return 0, "X=10\nY=20\nWIDTH=640\nHEIGHT=480\n", ""
        if cmd == "getactivewindow":
            return 0, "101", ""
        return 1, "", ""

    monkeypatch.setattr(limb_rpa, "_tool_for_windows", lambda: "xdotool")
    monkeypatch.setattr(limb_rpa, "_run", fake_run)

    app = FastAPI()
    app.include_router(limb_rpa.router, prefix="/api/limb/rpa")
    client = TestClient(app)
    resp = client.get("/api/limb/rpa/windows")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list), f"真源响应不是裸数组（容器形漂移）: {type(body)}"
    assert len(body) == 2
    for el in body:
        assert set(el.keys()) == TRUE_SOURCE_KEYS, f"元素键集漂移: {sorted(el.keys())}"
    assert body[0]["id"] == "101" and body[0]["focused"] is True
    assert body[1]["id"] == "102" and body[1]["focused"] is False

    # 行为桥：UI 消费键 ⊆ 真源元素键（未来真源形状再漂移/ UI 再发明键此桥先炸）
    consumed = set(re.findall(r"\bw\.([A-Za-z_$][\w$]*)", _code()))
    assert consumed, "UI 未提取到元素消费键（提取失效=断言空转）"
    for k in consumed:
        assert k in body[0], f"UI 元素消费键真源响应不产出: {k}"
