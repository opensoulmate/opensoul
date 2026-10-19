#!/usr/bin/env python3
"""CD-26 cortex 推理步骤收编真源——reasoning_steps↔reasoning 漂移收口（真源零改动）回归锁死。

背景（CD-22 confirmed_drift 修复候选「cortex 推理步骤收编真源」第④件）：
cortex think 返回体=LLM JSON 直通（chain_of_thought._call_llm json.loads）+
reflections，动态形状按 SYSTEM_PROMPT 声明面定型——声明键恰
{understanding,analysis,reasoning,verification,answer,confidence}，其中 reasoning
是字符串（step-by-step logical deduction），无 reasoning_steps 数组；而 UI
ThinkResult 声明 reasoning_steps?: string[] 并以
thinkResult.reasoning_steps && .length>0 渲染「推理步骤」块——恒 falsy=块永不渲染
（显示行为损失），真源 reasoning 字符串无人读。同根死声明键 question（真源不产出、
全页零消费）一并按 CD-7「死声明键清零」收口。

收口（真源零改动=CD 系列 wire 权威纪律；CD-23/24/25「UI 消费面收编真源」+CD-7
「死声明键清零」先例）：
- ThinkResult 声明键收编真源产出形（reasoning?: string + answer/confidence），
  死声明键 reasoning_steps/question 双清零；
- 推理步骤块改收编真源 reasoning 字符串逐字渲染（whitespace-pre-wrap 保留 LLM 自身
  步骤结构，不 fabricate 步骤数组——真源根本不产出数组）。

断言四层（禁自嗨恒真断言）：
1. 源码消费面：修前死键声明/消费零残留+真源收编在场+接口键集恰真源消费面
   +消费访问 ⊆ 真源产出键（防再发明键）；
2. 行为桥：真 ChainOfThought.think 打真 stub HTTP LLM 上游（tmp 零生产接触）返回
   prompt 声明形——结果键集恰声明键+reflections、reasoning 真字符串、
   reasoning_steps 硬缺席；prompt 声明键集单一源解析；真 UI 渲染谓词经 node 真执行
   吃真结果（修前谓词恒假=块永不渲染硬反证、修后谓词真值）；
3. 真源锚点：SYSTEM_PROMPT reasoning 声明行+reflection 真实消费+真源零改动锁
   （chain_of_thought.py 不产出 reasoning_steps）；
4. census 真跑真仓：cortex.think 消费键漂移归零+消费键 ⊆ 真源产出键+produced 面零伤及。
"""

from __future__ import annotations

import asyncio
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
UI = REPO / "admin-ui" / "app" / "cortex" / "page.tsx"
BE = REPO / "src" / "cortex" / "chain_of_thought.py"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd26_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd26_repair"] = cdc
_spec.loader.exec_module(cdc)

# 真源产出键（SYSTEM_PROMPT 声明面 + think() 增补 reflections）
PROMPT_KEYS = {"understanding", "analysis", "reasoning", "verification", "answer", "confidence"}
PRODUCED_KEYS = PROMPT_KEYS | {"reflections"}
UI_CONSUMED_KEYS = {"reasoning", "answer", "confidence"}  # UI 真消费面

ui_src = UI.read_text(encoding="utf-8")
be_src = BE.read_text(encoding="utf-8")


def _code() -> str:
    """剥行注释后的真实代码（注释里的键名记述不算消费/声明形，CD-20/24/25 同款）。"""
    return re.sub(r"//.*$", "", ui_src, flags=re.M)


def _iface_keys(name: str) -> set:
    m = re.search(r"interface " + name + r" \{([^}]*)\}", ui_src)
    assert m, f"{name} 接口声明缺失（提取失效=断言空转）"
    return set(re.findall(r"^\s*(\w+)\??\s*:", m.group(1), flags=re.M))


def _think_block() -> str:
    seg = ui_src[ui_src.index("{thinkResult.reasoning && (") : ui_src.index("{thinkResult.answer")]
    assert "{thinkResult.reasoning}" in seg, "推理步骤块提取失效（断言空转）"
    return seg


# ── (1) 源码消费面 ─────────────────────────────────────────────


def test_dead_declared_keys_cleared():
    """修前死键双清零：reasoning_steps 声明/消费零残留+question 死声明键清零。"""
    code = _code()
    assert "reasoning_steps" not in code, "reasoning_steps 死键残留（真源只产 reasoning 字符串）"
    assert "thinkResult.question" not in code, "question 死键消费残留（真源不产出 question）"
    assert "question" not in _iface_keys("ThinkResult"), "question 死声明键残留（CD-7 纪律）"


def test_interface_keys_exact_consumed_face():
    """ThinkResult 接口键集恰 UI 真消费面 ⊆ 真源产出键（死键清零+防再发明键）。"""
    keys = _iface_keys("ThinkResult")
    assert keys == UI_CONSUMED_KEYS, f"接口键漂移: {keys}"
    assert keys <= PRODUCED_KEYS, f"接口键越界真源不产出: {keys - PRODUCED_KEYS}"


def test_reasoning_rendered_verbatim_from_true_source():
    """推理步骤块收编真源 reasoning 字符串逐字渲染（不 fabricate 步骤数组）。"""
    seg = _think_block()
    assert "{thinkResult.reasoning}" in seg, "reasoning 真源键未收编渲染"
    assert "whitespace-pre-wrap" in seg, "字符串逐字渲染（保留换行）未收编"
    assert ".map(" not in seg, "步骤数组渲染残留（真源不产出数组，禁 fabricate 结构）"
    consumed = set(re.findall(r"thinkResult\.(\w+)", _code()))
    assert consumed <= PRODUCED_KEYS, f"消费键越界真源不产出: {consumed - PRODUCED_KEYS}"


# ── (2) 行为桥：真 think 打真 stub HTTP LLM + 真 UI 谓词 node 真执行 ──


class _LLMHandler(BaseHTTPRequestHandler):
    records: list = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _LLMHandler.records.append(body)
        content = json.dumps(
            {
                "understanding": "U",
                "analysis": "A",
                "reasoning": "1. 第一步\n2. 第二步",
                "verification": "V",
                "answer": "ANSWER",
                "confidence": 0.9,
            },
            ensure_ascii=False,
        )
        payload = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # 静默
        pass


@pytest.fixture(scope="module")
def true_result():
    """真 ChainOfThought.think 打真 stub HTTP LLM 上游（零生产接触）。"""
    from src.cortex.chain_of_thought import ChainOfThought

    _LLMHandler.records = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LLMHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cot = ChainOfThought(
            base_url=f"http://127.0.0.1:{server.server_address[1]}", api_key="k", model="m"
        )
        result = asyncio.run(cot.think("测试问题"))
    finally:
        server.shutdown()
    assert _LLMHandler.records, "stub 未收到请求（行为桥空转）"
    return result


def test_think_returns_prompt_declared_shape(true_result):
    """结果键集恰 prompt 声明键+reflections、reasoning 真字符串、reasoning_steps 硬缺席。"""
    assert set(true_result.keys()) == PRODUCED_KEYS, f"结果键漂移: {set(true_result.keys())}"
    assert isinstance(true_result["reasoning"], str) and true_result["reasoning"]
    assert "reasoning_steps" not in true_result, "reasoning_steps 卷土重来（真源只产 reasoning）"
    assert true_result["reflections"] == 0
    assert true_result["answer"] == "ANSWER"


def test_prompt_declaration_single_source(true_result):
    """SYSTEM_PROMPT 声明键集单一源解析=恰 6 键、reasoning_steps 不在声明面、发送面同源。"""
    from src.cortex.chain_of_thought import ChainOfThought

    declared = set(re.findall(r'"(\w+)"\s*:', ChainOfThought.SYSTEM_PROMPT))
    assert declared == PROMPT_KEYS, f"prompt 声明键漂移: {declared}"
    assert "reasoning_steps" not in ChainOfThought.SYSTEM_PROMPT
    sent = _LLMHandler.records[0]["messages"][0]["content"]
    assert sent == ChainOfThought.SYSTEM_PROMPT, "发送 prompt 与声明面不同源"


def test_pre_fix_predicate_falsy_on_true_shape(true_result):
    """真 UI 渲染谓词经 node 真执行吃真结果：修前谓词恒假（块永不渲染硬反证）、修后真值。"""
    if shutil.which("node") is None:
        pytest.skip("node 不在 PATH（工具缺席非代码失败）")
    m = re.search(r"\{thinkResult\.(\w+) && \(", ui_src)
    assert m, "渲染谓词提取失败（提取失效=断言空转）"
    assert m.group(1) == "reasoning", f"渲染谓词键漂移: {m.group(1)}"
    prog = (
        "const raw = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
        "const pre = Boolean(raw.reasoning_steps && raw.reasoning_steps.length > 0);\n"
        f'const post = Boolean(raw["{m.group(1)}"]);\n'
        "process.stdout.write(JSON.stringify({pre, post}));\n"
    )
    proc = subprocess.run(
        ["node", "-e", prog],
        input=json.dumps(true_result, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"node 执行失败: {proc.stderr}"
    out = json.loads(proc.stdout)
    assert out["pre"] is False, "修前谓词竟然为真（真源形变了须复挖）"
    assert out["post"] is True, "修后谓词为假（收编失效）"


# ── (3) 真源锚点 ───────────────────────────────────────────────


def test_true_source_anchors():
    """SYSTEM_PROMPT reasoning 声明行+reflection 真实消费+真源零改动锁。"""
    lines = be_src.splitlines()
    assert '"reasoning": "step-by-step logical deduction",' in lines[16], "reasoning 声明行锚点漂移"
    assert 'result.get("reasoning", "")' in be_src, "reflection 真实消费锚点漂移"
    assert "reasoning_steps" not in be_src, "真源冒出 reasoning_steps（真源零改动锁）"


# ── (4) census 工具真跑真仓 ────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def test_census_reasoning_steps_findings_gone(census_report):
    """修后 cortex 页 reasoning_steps 漂移 findings 归零+think 消费键 ⊆ 真源产出键。"""
    hits = [f for f in census_report["findings"] if f["page"].endswith("cortex/page.tsx")]
    assert hits, "cortex findings 缺席（提取失效=断言空转）"
    for f in hits:
        assert "reasoning_steps" not in f["key"], f"reasoning_steps 漂移 findings 残留: {f['key']}"
    think_hits = [f for f in hits if f.get("handler", "").startswith("cortex.think")]
    assert think_hits, "think 消费面 findings 缺席（提取失效=断言空转）"
    for f in think_hits:
        assert set(f["key"]) <= UI_CONSUMED_KEYS, f"think 消费键越界: {f['key']}"


def test_census_produced_face_unchanged(census_report):
    """stats/plan/agent/creativity 四取数调用点 produced 不变面（收编零伤及顶层取数）。"""
    produced = {
        f.get("path")
        for f in census_report["findings"]
        if f["page"].endswith("cortex/page.tsx") and f["verdict"] == "produced"
    }
    assert "/api/cortex/stats" in produced
    assert "/api/cortex/plan" in produced
    assert "/api/cortex/agent" in produced
    assert "/api/brain/creativity" in produced
