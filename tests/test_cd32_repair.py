#!/usr/bin/env python3
"""CD-32 connectors 扫描进程面收编真源——name↔command/executable + memory↔mem
漂移收口（真源零改动）回归锁死。

背景（CD-22 confirmed_drift 修复候选第 10 件「connectors 扫描进程名收编真源
（name→command/executable）」，10 open repair_candidate 末件）：
GET /api/soma/discovery/scan（src/api/soma_discovery.py api_full_scan →
ScanResult.processes:list[ProcessInfo]）的 ProcessInfo 字段恰
pid/user/cpu/mem/command/executable/description（soma_discovery.py:165-172）；
而 connectors/page.tsx ProcessInfo 接口声明 name/cmd/memory 并消费 p.name（:431
恒 undefined=进程名列恒空渲染）与 p.memory.toFixed(1)（:433 对 undefined 调
toFixed=TypeError 渲染崩坏面，EXPANDED_FINDINGS 同根登记）。

收口（真源零改动=CD 系列 wire 权威纪律；CD-23~31「UI 消费面收编真源」先例）：
- ProcessInfo 接口收编真源键形 {pid, command, executable, cpu, mem}
  （键集恰 UI 真消费面 ⊆ 真源产出键；死声明键 name/cmd/memory 清零 CD-⑦ 纪律）；
- 两处消费点收编真源键（p.command||p.executable（键序照登记候选 name→command/executable）、
  p.mem.toFixed——同根 EXPANDED p.memory 崩坏面随主件同拍清零）。
注意范围：services 面（CD-31 已收口）不在本拍改动面，测试锁死禁误扩。

断言四层（禁自嗨恒真断言）：
①源码消费面：修前漂移键零残留 + 元素接口键集恰真消费面 + p.* 消费访问 ⊆ 真源产出键
  + services 面范围纪律锁死；
②行为桥：真 ProcessInfo 模型 model_dump 真跑——元素键集恰 7 字段、UI 真消费键 ⊆ 产出键、
  name/cmd/memory 硬缺席；真 UI 渲染表达式逐字提取经 node 真执行吃真源元素（修后不炸+
  渲染正确+executable 回落）；修前表达式吃真源=undefined 假渲染/toFixed TypeError 崩坏
  硬反证；
③真源锚点：soma_discovery.py ProcessInfo 字段集 + 零改动锁（无 name/cmd/memory 重命名）；
④census 真跑真仓：processes.* 漂移键 findings 归零 + processes 元素消费键 ⊆ 真源产出键。
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
_spec = importlib.util.spec_from_file_location("cdc_cd32_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd32_repair"] = cdc
_spec.loader.exec_module(cdc)

ui_src = UI.read_text(encoding="utf-8")
sd_src = SD.read_text(encoding="utf-8")

# UI 真消费面（修后 processes 元素访问键集）
CONSUMED_ELEMENT_KEYS = {"pid", "command", "executable", "cpu", "mem"}

# 修前漂移消费/声明键（真源不产出）
OLD_DRIFT_FORMS = ("p.name", "p.memory", "p.cmd")
DRIFT_KEYS = {"name", "cmd", "memory"}


def _processinfo_fields() -> dict:
    """ProcessInfo 字段名→声明行（从 soma_discovery.py 类体程序化派生，禁手抄）。"""
    seg = sd_src[sd_src.index("class ProcessInfo") : sd_src.index("class CLITool")]
    fields = {}
    for m in re.finditer(r"^    (\w+)(\??): (.+)$", seg, flags=re.M):
        fields[m.group(1)] = m.group(0)
    return fields


TRUE_FIELDS = _processinfo_fields()
TRUE_ELEMENT_KEYS = set(TRUE_FIELDS)


def _process_map_block() -> str:
    m = re.search(r"processes\.slice\(0, 30\)\.map\(\(p\) => \((.*?)\)\)\}", ui_src, re.S)
    assert m, "processes.map 渲染块提取失败"
    return m.group(1)


# ── ① 源码消费面 ─────────────────────────────────────────────


def test_old_drift_keys_gone():
    """修前漂移消费/声明键零残留（两消费点字面量 + 元素接口声明形）。
    判法=词边界正则（"p.cmd" 是 "p.command" 前缀子串，裸 in 判定会自伤）。"""
    for dead in OLD_DRIFT_FORMS:
        assert not re.search(rf"{re.escape(dead)}\b", ui_src), f"旧漂移消费残留: {dead}"
    m = re.search(r"interface ProcessInfo \{([^}]*)\}", ui_src)
    assert m, "ProcessInfo 接口声明缺失"
    block = m.group(1)
    for dead in DRIFT_KEYS:
        assert not re.search(rf"^\s*{dead}\s*:", block, flags=re.M), f"接口死键残留: {dead}"


def test_interface_element_keys_exact_consumed_face():
    """元素接口键集恰 UI 真消费面（⊆ 真源产出键）。"""
    m = re.search(r"interface ProcessInfo \{([^}]*)\}", ui_src)
    assert m
    keys = set(re.findall(r"^\s*(\w+)[?]?\s*:", m.group(1), flags=re.M))
    assert keys == CONSUMED_ELEMENT_KEYS, f"接口键集漂移: {keys}"
    assert keys <= TRUE_ELEMENT_KEYS, f"接口键越出真源产出键: {keys - TRUE_ELEMENT_KEYS}"


def test_consumed_access_subset_true_source():
    """processes 元素 p.* 消费访问 ⊆ 真源产出键（防再发明键），且两消费点全收编。"""
    accessed = set(re.findall(r"\bp\.(\w+)", _process_map_block()))
    assert accessed == CONSUMED_ELEMENT_KEYS, f"消费访问越界/缺项: {accessed}"
    assert accessed <= TRUE_ELEMENT_KEYS
    assert "p.command || p.executable" in ui_src, "进程名未收编真源键"
    assert "p.mem.toFixed" in ui_src, "内存段未收编真源键（修前 p.memory.toFixed 崩坏面）"


def test_services_face_scope_lock():
    """范围纪律：services 面（CD-31 已收口）未动——本拍只收进程面，禁误扩禁漏修。"""
    assert "s.process_name || s.local_address" in ui_src, "services 面被误改（CD-31 收口面）"
    assert "{s.local_address}:{s.local_port}" in ui_src
    block = re.search(r"interface ServiceInfo \{([^}]*)\}", ui_src)
    assert block and "process_name" in block.group(1), "ServiceInfo 面被改动=超出本拍范围"


# ── ② 行为桥：真 ProcessInfo 模型真跑 + 真 UI 表达式 node 真执行 ──


def _real_process(**over):
    import src.api.soma_discovery as sd_mod

    base = dict(
        pid=4242,
        user="climbing",
        cpu=1.5,
        mem=2.5,
        command="/usr/bin/python3 app.py --flag",
        executable="/usr/bin/python3",
    )
    base.update(over)
    return sd_mod.ProcessInfo(**base)


def test_process_element_shape_from_real_model():
    """真模型 model_dump：元素键集恰 7 字段，UI 真消费键 ⊆ 产出键、name/cmd/memory 硬缺席。"""
    dump = _real_process().model_dump()
    assert set(TRUE_FIELDS) == {
        "pid",
        "user",
        "cpu",
        "mem",
        "command",
        "executable",
        "description",
    }, f"真源字段集漂移: {set(TRUE_FIELDS)}"
    assert set(dump) == set(TRUE_FIELDS)
    assert not (DRIFT_KEYS & set(dump)), f"真源被改出漂移键: {DRIFT_KEYS & set(dump)}"
    assert CONSUMED_ELEMENT_KEYS <= set(dump), "UI 真消费键真源不产出"
    assert dump["mem"] == 2.5 and isinstance(dump["mem"], float)
    assert dump["command"] == "/usr/bin/python3 app.py --flag"
    null_dump = _real_process(executable=None).model_dump()
    assert null_dump["executable"] is None and null_dump["command"]


def _node_eval(prog: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=60)


def _extract_ui_expressions():
    """从 UI 源逐字提取修后渲染表达式（禁在测试里另写一套逻辑）。"""
    m_name = re.search(r"\{(p\.command \|\| p\.executable)\}", ui_src)
    assert m_name, "进程名渲染表达式提取失败"
    m_mem = re.search(r"\{(p\.mem\.toFixed\(1\))\}", ui_src)
    assert m_mem, "内存段渲染表达式提取失败"
    return m_name.group(1), m_mem.group(1)


def test_ui_render_node_bridge_on_true_shape():
    """真 UI 表达式逐字提取经 node 真执行吃真源元素：修后不炸+渲染正确+executable 回落。"""
    if shutil.which("node") is None:
        pytest.skip("node 不在 PATH（工具缺席非代码失败）")
    name_expr, mem_expr = _extract_ui_expressions()
    prog = (
        "function f(p){ return {name: String("
        + name_expr
        + "), mem: String("
        + mem_expr
        + ")}; }\n"
        "const full = f({pid:4242, cpu:1.5, mem:2.5, command:'/usr/bin/python3 app.py --flag', "
        "executable:'/usr/bin/python3'});\n"
        "const noCmd = f({pid:4242, cpu:1.5, mem:0.0, command:'', executable:'/bin/foo'});\n"
        "console.log(JSON.stringify([full, noCmd]));"
    )
    proc = _node_eval(prog)
    assert proc.returncode == 0, f"node 执行失败（修后表达式仍炸）: {proc.stderr}"
    out = json.loads(proc.stdout)
    assert out[0] == {"name": "/usr/bin/python3 app.py --flag", "mem": "2.5"}, f"渲染错: {out[0]}"
    assert out[1] == {"name": "/bin/foo", "mem": "0.0"}, f"executable 回落错: {out[1]}"


def test_pre_fix_render_fabricates_undefined_and_crashes():
    """修前表达式吃真源元素=undefined 假渲染+toFixed TypeError 崩坏硬反证。"""
    if shutil.which("node") is None:
        pytest.skip("node 不在 PATH（工具缺席非代码失败）")
    prog = (
        "const p = {pid:4242, cpu:1.5, mem:2.5, command:'/usr/bin/python3 app.py', "
        "executable:'/usr/bin/python3'};\n"
        "const name = String(p.name);\n"
        "console.log(JSON.stringify({name}));\n"
        "try { p.memory.toFixed(1); console.log('MEM_NO_THROW'); }\n"
        "catch (e) { console.log('MEM_THROW:' + e.constructor.name); }"
    )
    proc = _node_eval(prog)
    assert proc.returncode == 0
    out = proc.stdout
    assert '"name":"undefined"' in out, f"修前反证失效（进程名不再假渲染）: {out}"
    assert "MEM_THROW:TypeError" in out, f"修前反证失效（p.memory.toFixed 不再崩）: {out}"
    assert "MEM_NO_THROW" not in out


# ── ③ 真源锚点 ───────────────────────────────────────────────


def test_true_source_processinfo_field_anchors():
    """soma_discovery.py ProcessInfo 字段集锚点（单一源）。"""
    assert "    command: str" in TRUE_FIELDS.get("command", ""), "command 锚点漂移"
    assert "    mem: float" in TRUE_FIELDS.get("mem", ""), "mem 锚点漂移"
    assert "executable" in TRUE_FIELDS and "None" in TRUE_FIELDS["executable"]


def test_true_source_zero_change_lock():
    """真源零改动锁：ProcessInfo 类体无 name/cmd/memory 字段（无重命名收口）——
    真源形状变了须先改本锁再改 UI。"""
    assert set(TRUE_FIELDS) == {
        "pid",
        "user",
        "cpu",
        "mem",
        "command",
        "executable",
        "description",
    }, f"真源被改出字段漂移: {set(TRUE_FIELDS)}"
    assert not (DRIFT_KEYS & set(TRUE_FIELDS)), "真源被改出漂移键"
    assert "_process_wire" not in sd_src[sd_src.index("class ProcessInfo") :], (
        "元素直出被改为重命名路径=真源形状变更须复挖"
    )


# ── ④ census 工具真跑真仓 ─────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def _connector_process_findings(census_report):
    return [
        f
        for f in census_report["findings"]
        if f["page"].endswith("connectors/page.tsx")
        and len(f["key"]) > 1
        and f["key"][0] == "processes"
    ]


def test_census_process_drift_keys_findings_gone(census_report):
    """修后 processes.* 漂移键 findings 归零（name/cmd/memory 不再作为消费键浮出）。"""
    for f in _connector_process_findings(census_report):
        for k in f["key"]:
            assert k not in DRIFT_KEYS, f"漂移 findings 残留: {f}"


def test_census_process_consumed_subset_true_source(census_report):
    """processes 元素消费键 ⊆ 真源产出键（禁再发明键）。
    工具边界如实记：census 对元素级与容器级并进同一 findings 键集（CD-9 既有边界，
    修前修后同边界）——断言面为提取到的 processes.* 键 ⊆ 真源键，提取不足不判假阳。"""
    allowed = TRUE_ELEMENT_KEYS | {"processes"}
    found = set()
    for f in _connector_process_findings(census_report):
        for k in f["key"]:
            found.add(k)
            assert k in allowed, f"census 消费键真源不产出: {f['line']} {k}"
    assert "command" in found or "executable" in found or "mem" in found, (
        f"census 提取面异常窄（真源键应浮出）: {found}"
    )
