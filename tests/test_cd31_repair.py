#!/usr/bin/env python3
"""CD-31 connectors 扫描服务面收编真源——name/host/port↔process_name/local_address/local_port
漂移收口（真源零改动）回归锁死。

背景（CD-22 confirmed_drift 修复候选第 9 件「connectors 扫描服务面收编真源
（name/host/port→process_name/local_address/local_port）」）：
GET /api/soma/discovery/scan（src/api/soma_discovery.py:1472 api_full_scan →
ScanResult.services:list[ServiceInfo]）的 ServiceInfo 字段恰
protocol/local_address/local_port/state/process_name/pid/description
（soma_discovery.py:182-189）；而 connectors/page.tsx ServiceInfo 接口声明
name/host/port 并消费 s.name||s.host（:462）、{s.host}:{s.port}（:463）——真源无这三键：
服务名/地址段/端口段恒 undefined（地址段裸冒号、显示行为损失）。

收口（真源零改动=CD 系列 wire 权威纪律；CD-23~30「UI 消费面收编真源」先例）：
- ServiceInfo 接口收编真源键形 {process_name, local_address, local_port, protocol}
  （键集恰 UI 真消费面 ⊆ 真源产出键）；
- 两处消费点收编真源键（s.process_name||s.local_address 回落语义保留、
  {s.local_address}:{s.local_port}）。
注意范围：ProcessInfo 面（p.name/p.memory 漂移=repair_candidate 第 10 件+EXPANDED
p.memory）不在本拍收编面，测试锁死禁误扩。

断言四层（禁自嗨恒真断言）：
①源码消费面：修前漂移键零残留 + 元素接口键集恰真消费面 + s.* 消费访问 ⊆ 真源产出键
  + 进程面范围纪律锁死；
②行为桥：真 ServiceInfo 模型 model_dump 真跑——元素键集恰 7 字段、UI 真消费键 ⊆ 产出键、
  name/host/port 硬缺席；真 UI 渲染表达式逐字提取经 node 真执行吃真源元素（修后不炸+
  渲染正确+process_name 缺省回落地址）；修前表达式吃真源=undefined 假渲染硬反证；
③真源锚点：soma_discovery.py ServiceInfo 字段集 + 零改动锁（无 name/host/port 重命名）；
④census 真跑真仓：services.* 漂移键 findings 归零 + services 元素消费键 ⊆ 真源产出键。
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
UI = REPO / "admin-ui" / "app" / "connectors" / "page.tsx"
SD = REPO / "src" / "api" / "soma_discovery.py"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd31_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd31_repair"] = cdc
_spec.loader.exec_module(cdc)

ui_src = UI.read_text(encoding="utf-8")
sd_src = SD.read_text(encoding="utf-8")

# UI 真消费面（修后 services 元素访问键集）
CONSUMED_ELEMENT_KEYS = {"process_name", "local_address", "local_port", "protocol"}

# 修前漂移消费/声明键（真源不产出）
OLD_DRIFT_FORMS = ("s.name || s.host", "{s.host}:{s.port}")
DRIFT_KEYS = {"name", "host", "port"}


def _serviceinfo_fields() -> dict:
    """ServiceInfo 字段名→声明行（从 soma_discovery.py 类体程序化派生，禁手抄）。"""
    seg = sd_src[sd_src.index("class ServiceInfo") : sd_src.index("class ScanResult")]
    fields = {}
    for m in re.finditer(r"^    (\w+)(\??): (.+)$", seg, flags=re.M):
        fields[m.group(1)] = m.group(0)
    return fields


TRUE_FIELDS = _serviceinfo_fields()
TRUE_ELEMENT_KEYS = set(TRUE_FIELDS)


def _service_map_block() -> str:
    m = re.search(r"scanResult\.services\.map\(\(s, i\) => \((.*?)\)\)\}", ui_src, re.S)
    assert m, "services.map 渲染块提取失败"
    return m.group(1)


# ── ① 源码消费面 ─────────────────────────────────────────────


def test_old_drift_keys_gone():
    """修前漂移消费/声明键零残留（两消费点字面量 + 元素接口声明形）。"""
    for dead in OLD_DRIFT_FORMS:
        assert dead not in ui_src, f"旧漂移消费残留: {dead}"
    m = re.search(r"interface ServiceInfo \{([^}]*)\}", ui_src)
    assert m, "ServiceInfo 接口声明缺失"
    block = m.group(1)
    for dead in ("name", "host", "port"):
        assert not re.search(rf"^\s*{dead}\s*:", block, flags=re.M), f"接口死键残留: {dead}"


def test_interface_element_keys_exact_consumed_face():
    """元素接口键集恰 UI 真消费面（⊆ 真源产出键）。"""
    m = re.search(r"interface ServiceInfo \{([^}]*)\}", ui_src)
    assert m
    keys = set(re.findall(r"^\s*(\w+)[?]?\s*:", m.group(1), flags=re.M))
    assert keys == CONSUMED_ELEMENT_KEYS, f"接口键集漂移: {keys}"
    assert keys <= TRUE_ELEMENT_KEYS, f"接口键越出真源产出键: {keys - TRUE_ELEMENT_KEYS}"


def test_consumed_access_subset_true_source():
    """services 元素 s.* 消费访问 ⊆ 真源产出键（防再发明键），且两消费点全收编。"""
    accessed = set(re.findall(r"\bs\.(\w+)", _service_map_block()))
    assert accessed == CONSUMED_ELEMENT_KEYS, f"消费访问越界/缺项: {accessed}"
    assert accessed <= TRUE_ELEMENT_KEYS
    assert "s.process_name || s.local_address" in ui_src, "服务名回落未收编真源键"
    assert "{s.local_address}:{s.local_port}" in ui_src, "地址段未收编真源键"


def test_process_face_scope_lock():
    """范围纪律：ProcessInfo 面（repair_candidate 第 10 件 p.name/p.memory）未动，
    本拍只收服务面——禁误扩也禁漏修（进程面仍留漂移待拍）。"""
    assert "p.name" in ui_src, "进程面被误扩（第 10 件未拍）"
    assert "p.memory.toFixed" in ui_src
    block = re.search(r"interface ProcessInfo \{([^}]*)\}", ui_src)
    assert block and "name" in block.group(1), "ProcessInfo 面被改动=超出本拍范围"


# ── ② 行为桥：真 ServiceInfo 模型真跑 + 真 UI 表达式 node 真执行 ──


def _real_service(**over):
    import src.api.soma_discovery as sd_mod

    base = dict(
        protocol="tcp",
        local_address="127.0.0.1",
        local_port=8090,
        state="LISTEN",
        process_name="python3",
        pid=4242,
    )
    base.update(over)
    return sd_mod.ServiceInfo(**base)


def test_service_element_shape_from_real_model():
    """真模型 model_dump：元素键集恰 7 字段，UI 真消费键 ⊆ 产出键、name/host/port 硬缺席。"""
    dump = _real_service().model_dump()
    assert set(TRUE_FIELDS) == {
        "protocol",
        "local_address",
        "local_port",
        "state",
        "process_name",
        "pid",
        "description",
    }, f"真源字段集漂移: {set(TRUE_FIELDS)}"
    assert set(dump) == set(TRUE_FIELDS)
    assert not (DRIFT_KEYS & set(dump)), f"真源被改出漂移键: {DRIFT_KEYS & set(dump)}"
    assert CONSUMED_ELEMENT_KEYS <= set(dump), "UI 真消费键真源不产出"
    assert dump["local_port"] == 8090 and isinstance(dump["local_port"], int)
    assert dump["process_name"] == "python3"
    null_dump = _real_service(process_name=None).model_dump()
    assert null_dump["process_name"] is None and null_dump["local_address"] == "127.0.0.1"


def _node_eval(prog: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=60)


def _extract_ui_expressions():
    """从 UI 源逐字提取修后渲染表达式（禁在测试里另写一套逻辑）。"""
    m_name = re.search(r"\{(s\.process_name \|\| s\.local_address)\}", ui_src)
    assert m_name, "服务名渲染表达式提取失败"
    m_addr = re.search(r"\{(s\.local_address)\}:\{(s\.local_port)\}", ui_src)
    assert m_addr, "地址段渲染表达式提取失败"
    return m_name.group(1), m_addr.group(1), m_addr.group(2)


def test_ui_render_node_bridge_on_true_shape():
    """真 UI 表达式逐字提取经 node 真执行吃真源元素：修后不炸+渲染正确+缺省回落。"""
    if shutil.which("node") is None:
        pytest.skip("node 不在 PATH（工具缺席非代码失败）")
    name_expr, addr_k, port_k = _extract_ui_expressions()
    prog = (
        "function f(s){ return {name: String(" + name_expr + "), "
        "addr: String(" + addr_k + ") + ':' + String(" + port_k + ")}; }\n"
        "const withName = f({process_name:'python3', local_address:'127.0.0.1', local_port:8090});\n"
        "const noName = f({process_name:null, local_address:'0.0.0.0', local_port:22});\n"
        "console.log(JSON.stringify([withName, noName]));"
    )
    proc = _node_eval(prog)
    assert proc.returncode == 0, f"node 执行失败（修后表达式仍炸）: {proc.stderr}"
    out = json.loads(proc.stdout)
    assert out[0] == {"name": "python3", "addr": "127.0.0.1:8090"}, f"渲染错: {out[0]}"
    assert out[1] == {"name": "0.0.0.0", "addr": "0.0.0.0:22"}, f"缺省回落错: {out[1]}"


def test_pre_fix_render_fabricates_undefined():
    """修前表达式吃真源元素=undefined 假渲染硬反证（服务名恒空/地址段裸冒号真断点）。"""
    if shutil.which("node") is None:
        pytest.skip("node 不在 PATH（工具缺席非代码失败）")
    prog = (
        "const s = {process_name:'python3', local_address:'127.0.0.1', local_port:8090};\n"
        "const name = String(s.name || s.host);\n"
        "const addr = String(s.host) + ':' + String(s.port);\n"
        "console.log(JSON.stringify({name, addr}));"
    )
    proc = _node_eval(prog)
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out == {"name": "undefined", "addr": "undefined:undefined"}, f"修前反证失效: {out}"


# ── ③ 真源锚点 ───────────────────────────────────────────────


def test_true_source_serviceinfo_field_anchors():
    """soma_discovery.py ServiceInfo 字段集锚点（单一源）。"""
    assert "    local_address: str" in TRUE_FIELDS.get("local_address", ""), (
        "local_address 锚点漂移"
    )
    assert "    local_port: int" in TRUE_FIELDS.get("local_port", ""), "local_port 锚点漂移"
    assert "process_name" in TRUE_FIELDS and "None" in TRUE_FIELDS["process_name"]


def test_true_source_zero_change_lock():
    """真源零改动锁：ServiceInfo 类体无 name/host/port 字段（无重命名收口）——
    真源形状变了须先改本锁再改 UI。"""
    seg = sd_src[sd_src.index("class ServiceInfo") : sd_src.index("class ScanResult")]
    for dead in ("name:", "host", "port:"):
        assert dead not in seg.replace("local_address", "").replace("local_port", "").replace(
            "process_name", ""
        ), f"真源被改出漂移键: {dead}"
    assert "_service_wire" not in seg, "元素直出被改为重命名路径=真源形状变更须复挖"


# ── ④ census 工具真跑真仓 ─────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def _connector_service_findings(census_report):
    return [
        f
        for f in census_report["findings"]
        if f["page"].endswith("connectors/page.tsx")
        and len(f["key"]) > 1
        and f["key"][0] == "services"
    ]


def test_census_service_drift_keys_findings_gone(census_report):
    """修后 services.* 漂移键 findings 归零（name/host/port 不再作为消费键浮出）。"""
    for f in _connector_service_findings(census_report):
        for k in f["key"]:
            assert k not in DRIFT_KEYS, f"漂移 findings 残留: {f}"


def test_census_service_consumed_subset_true_source(census_report):
    """services 元素消费键 ⊆ 真源产出键（禁再发明键）。
    工具边界如实记：census 对元素级与容器级并进同一 findings 键集（CD-9 既有边界，
    修前修后同边界）——断言面为提取到的 services.* 键 ⊆ 真源键，提取不足不判假阳。"""
    allowed = TRUE_ELEMENT_KEYS | {"services"}
    found = set()
    for f in _connector_service_findings(census_report):
        for k in f["key"]:
            found.add(k)
            assert k in allowed, f"census 消费键真源不产出: {f['line']} {k}"
    assert "local_address" in found or "local_port" in found or "process_name" in found, (
        f"census 提取面异常窄（真源键应浮出）: {found}"
    )
