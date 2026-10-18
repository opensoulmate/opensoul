#!/usr/bin/env python3
"""CD-⑤ 修复候选②——gland 页消费键收编（UI 消费面收编真源键）回归锁死。

背景（CD-③ 深挖裁决 confirmed_drift→候选②，「一候选一拍」第②件）：
gland/page.tsx 四组消费键对真源漂移——
①测试回执：data.message（真源 test_provider {status,provider,model,reply} 无
  message）|| 回落恒吃「连接成功」，模型 reply 永不显示=回落遮蔽型 drift；
②usage 面板 by_provider 元素：UI 按 {input,output,requests} 对象消费，真源
  token_meter.summary() by_provider=dict[str,int] 元素=int → 三键恒 undefined，
  各 Provider 用量恒显示 0；
③深挖扩面（普查启发式漏收，如实记）：UsageSummary 顶层 4 键（total_input_tokens/
  total_output_tokens/total_requests/budget_used_pct）真源不产出（真源=
  total_tokens/call_count/budget_limit/remaining_budget/by_model/by_user/
  by_provider）——总请求数/总Token数恒 0、预算条恒 0%；UsageRecord 元素
  （input_tokens/output_tasks/task）对真源 recent_records 元素
  （prompt_tokens/completion_tokens/...）漂移+timestamp 真源=epoch 秒被当毫秒
  解析落 1970；keys 面板 keyData.keys||keyData.slots 双回落对真源 dict[provider,
  槽位列表] 恒 []=密钥列表恒空（k.active 亦非真源键，真源=is_valid）。

收口（UI 消费面收编真源键，真源零改动=CD 系列 wire 权威纪律）：
- 测试回执消费 {status,provider,model,reply}（reply 真显示）；
- UsageSummary/UsageRecord/KeySlot 接口收编真源形状，消费点全改真源键；
- by_provider 元素=int 直接消费（入/出/请求拆分真源不产出→不渲染禁编造）；
- 预算使用率=真源键派生（total_tokens/budget_limit）；
- timestamp=epoch 秒 ×1000；task 列/入出拆分行死显示面收口（恢复=真源补现
  候选 pending-decisions ㉕ 同族）。

断言三层（禁自嗨恒真断言）：
①源码消费面：真源键真在场 + 漂移键零残留（消费形）；
②census 工具真跑真仓（scripts/census_contract_drift.py）：4 个漂移键
  （message/input/output/requests）全部从 findings 消失、残留 findings 键
  ⊆ 真源键（census 对变量返回 handler 的形状提取盲区如实记，行为桥补位）；
③行为桥（真源实跑）：UI 消费的每个键由真 producer 真产出——TokenMeter.summary()/
  recent_records()、KeyManager.status()、ModelRouter.test_provider（stub HTTP
  上游，其余真代码）逐键对账。
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "gland" / "page.tsx"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd5_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd5_repair"] = cdc
_spec.loader.exec_module(cdc)

# CD-③ 裁决+深挖扩面的漂移键（消费形，注释里的历史记述不算）
DRIFT_RECEIPT_KEYS = ("message",)
DRIFT_USAGE_KEYS = ("input", "output", "requests")
DRIFT_SUMMARY_KEYS = (
    "total_input_tokens",
    "total_output_tokens",
    "total_requests",
    "budget_used_pct",
)
DRIFT_RECORD_KEYS = ("input_tokens", "output_tokens", "task")
DRIFT_KEY_FIELDS = ("active",)

# 真源键（行为桥对账基准）
TEST_RESULT_KEYS = {"status", "provider", "model", "reply"}


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def _code() -> str:
    """去掉行注释后的真实消费代码（注释里的历史记述不算消费形）。"""
    return re.sub(r"//.*$", "", _src(), flags=re.M)


# ── ① 源码消费面 ───────────────────────────────────────────────


def test_test_receipt_consumes_true_keys():
    """测试回执收编 {status,provider,model,reply}，reply 真显示（修前恒「连接成功」）。"""
    code = _code()
    for expr in ("data.provider", "data.status", "data.model", "data.reply"):
        assert expr in code, f"真源回执键未被消费: {expr}"
    # 回落遮蔽收口：msg 主消费 reply/model（filter(Boolean).join），非 data.message||常量
    assert "data.reply" in code and ".filter(Boolean).join" in code


def test_receipt_drift_key_absent():
    """data.message 消费形零残留（真源无此键=回落遮蔽根因）。"""
    code = _code()
    for key in DRIFT_RECEIPT_KEYS:
        assert re.search(rf"\bdata\.{key}\b", code) is None, f"回执漂移键残留: {key}"


def test_usage_summary_consumes_true_keys():
    """UsageSummary 收编真源 summary() 形：total_tokens/call_count/budget_limit。"""
    code = _code()
    for expr in (
        "usage?.call_count",
        "usage?.total_tokens",
        "usage.total_tokens",
        "usage.budget_limit",
        "usage?.by_provider",
    ):
        assert expr in code, f"真源 summary 键未被消费: {expr}"
    # 预算使用率=真源键派生（total_tokens/budget_limit），非 budget_used_pct
    assert "budgetUsedPct" in code
    assert "(usage.total_tokens / usage.budget_limit) * 100" in code


def test_usage_summary_drift_keys_absent():
    """summary 4 个漂移键消费形零残留（修前总请求数/总Token恒 0、预算条恒 0%）。"""
    code = _code()
    for key in DRIFT_SUMMARY_KEYS:
        for pat in (rf"\busage\??\.{key}\b", rf"^\s+{key}: number;"):
            assert re.search(pat, code, re.M) is None, f"summary 漂移键残留: {key} ({pat})"


def test_by_provider_element_is_int_consumption():
    """by_provider 元素=int 直接消费（修前按 {input,output,requests} 对象消费恒 undefined）。"""
    code = _code()
    assert "Object.entries(usage?.by_provider || {}).map(([name, tokens])" in code
    assert "const total = tokens || 0;" in code
    # 三键消费形零残留
    for key in DRIFT_USAGE_KEYS:
        for pat in (rf"\bdata\.{key}\b", rf"\bd\.{key}\b"):
            assert re.search(pat, code) is None, f"by_provider 元素漂移键残留: {key} ({pat})"


def test_usage_record_consumes_true_keys():
    """UsageRecord 收编真源 recent_records 元素形（prompt/completion_tokens）。"""
    code = _code()
    for expr in ("r.prompt_tokens", "r.completion_tokens", "formatTime(r.timestamp)"):
        assert expr in code, f"真源 record 键未被消费: {expr}"
    for key in DRIFT_RECORD_KEYS:
        assert re.search(rf"\br\.{key}\b", code) is None, f"record 漂移键残留: {key}"


def test_keys_panel_consumes_true_shape():
    """keys 面板收编真源 dict[provider, 槽位列表] 展平形（修前双回落恒 []）。"""
    code = _code()
    assert "Object.entries(keyData).forEach" in code
    assert "k.is_valid" in code
    assert "k.masked_key" in code
    # 旧双回落与非真源键零残留
    assert "keyData.keys" not in code and "keyData.slots" not in code
    for key in DRIFT_KEY_FIELDS:
        assert re.search(rf"\bk\.{key}\b", code) is None, f"keys 漂移键残留: {key}"


def test_dead_display_faces_removed():
    """真源不产出的显示面死面清零：入/出拆分行、请求计数、task 列禁假渲染。"""
    code = _code()
    assert "入: " not in code, "入/出拆分行残留（真源 summary 无拆分）"
    assert "请求</span>" not in code, "by_provider 请求计数残留（真源不产出）"
    assert "{r.task}" not in code, "task 列残留（真源不产出）"


def test_timestamp_epoch_seconds_conversion():
    """timestamp 真源=epoch 秒 → ×1000 转毫秒（修前 new Date(ts) 落 1970）。"""
    code = _code()
    assert "new Date(ts * 1000)" in code
    assert "new Date(ts)" not in code


# ── ② census 工具真跑真仓 ─────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def test_census_drift_keys_gone(census_report):
    """修后 findings：4 个漂移键（message/input/output/requests）全部消失。

    修前实证（CD-③ 工件+本拍 --pre 复跑）：gland findings=message:149 +
    input/output/requests（挂 /api/gland/test=工具归属漂移 CD-③ 已记）共 4 条。
    """
    keys = {
        tuple(f["key"]) for f in census_report["findings"] if f["page"].endswith("gland/page.tsx")
    }
    for k in DRIFT_RECEIPT_KEYS + DRIFT_USAGE_KEYS:
        assert (k,) not in keys, f"漂移键仍在 findings: {k}"


def test_census_residual_findings_subset_of_true_source(census_report):
    """残留 findings 键 ⊆ 真源 test_provider 键（census 形状提取盲区如实记）。

    test_connection 返回变量 result（dynamic=True 形状提取失败），消费键被标
    unresolved_shape——真产出与否由行为桥（test_provider 实跑）判定，此断言锁
    「残留不超出真源键集」（未来 UI 又发明键必炸）。
    """
    keys = {
        k
        for f in census_report["findings"]
        if f["page"].endswith("gland/page.tsx")
        for k in f["key"]
    }
    assert keys, "gland findings 为空=提取失效断言空转（修前有 4 条）"
    assert keys <= TEST_RESULT_KEYS, f"findings 出现真源外键: {keys - TEST_RESULT_KEYS}"


# ── ③ 行为桥（真源实跑）─────────────────────────────────────────


def test_summary_consumed_keys_subset_of_true_source():
    """行为桥：UI 消费的每个 summary 键 TokenMeter.summary() 真产出；by_provider 元素=int。"""
    from src.gland.token_meter import TokenMeter

    tm = TokenMeter(budget_limit=1000)
    tm.record("prov-a", "model-x", 10, 5)
    summary = tm.summary()
    consumed = set(re.findall(r"\busage\??\.([A-Za-z_$][\w$]*)", _code()))
    assert consumed, "UI 未提取到 summary 消费键（提取失效=断言空转）"
    for k in consumed:
        assert k in summary, f"UI 消费 summary 键真源不产出: {k}"
    for v in summary["by_provider"].values():
        assert isinstance(v, int), f"by_provider 元素非 int: {v!r}"
    assert isinstance(summary["total_tokens"], int)
    assert isinstance(summary["call_count"], int)


def test_record_consumed_keys_subset_of_true_source():
    """行为桥：UI 消费的 record 字段 recent_records() 元素真产出（timestamp=epoch 秒）。"""
    from src.gland.token_meter import TokenMeter

    tm = TokenMeter()
    rec = tm.record("prov-a", "model-x", 10, 5)
    element = tm.recent_records(1)[0]
    universe = {
        "provider",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "user_id",
        "timestamp",
    } | set(DRIFT_RECORD_KEYS)
    consumed = set(re.findall(r"\br\.([A-Za-z_$][\w$]*)", _code())) & universe
    assert {"prompt_tokens", "completion_tokens", "timestamp"} <= consumed, "提取失效=断言空转"
    for k in consumed:
        assert k in element, f"UI 消费 record 键真源不产出: {k}"
    assert element["timestamp"] == pytest.approx(rec.timestamp)
    assert isinstance(element["timestamp"], float), "timestamp 真源=epoch 秒 float"


def test_keyslot_consumed_fields_subset_of_true_source():
    """行为桥：UI 消费的槽位字段 KeyManager.status() 元素真产出（provider=字典键）。"""
    from src.gland.key_manager import KeyManager

    km = KeyManager()
    km.add_key("prov-k", "sk-1234567890abcd")
    shape = km.status()
    assert set(shape) == {"prov-k"}
    element = shape["prov-k"][0]
    src = _code()
    pushed = set(
        re.findall(
            r"\b(?:provider|masked_key|is_valid|fail_count|last_checked)\b",
            src[src.index("flat.push") : src.index("setKeys(flat)")],
        )
    )
    assert pushed, "提取失效=断言空转"
    for k in pushed - {"provider"}:  # provider=字典键非槽位字段
        assert k in element, f"UI 消费槽位字段真源不产出: {k}"


def test_test_result_consumed_keys_subset_of_true_source():
    """行为桥：UI 消费的回执键 ModelRouter.test_provider 真产出（stub HTTP 上游）。"""

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "pong"}}]}

    class _Client:
        async def post(self, *args, **kwargs):
            return _Resp()

    from src.gland.router import ModelRouter

    m = ModelRouter()
    # 测试桩替换 HTTP 上游（其余真代码真路径；类型面为 AsyncClient，桩 duck-type post）
    m._http_client = _Client()  # type: ignore[assignment]
    m.add_provider("prov-t", "http://stub.invalid/v1", {"chat": "model-x"})
    m.key_manager.add_key("prov-t", "sk-1234567890abcd")
    res = asyncio.run(m.test_provider("prov-t"))
    assert set(res) == TEST_RESULT_KEYS, f"真源回执键集漂移: {set(res)}"
    assert res["reply"] == "pong"

    src = _code()
    seg = src[src.index("const handleTestProvider") : src.index("const handleAddProvider")]
    consumed = set(re.findall(r"\bdata\.([A-Za-z_$][\w$]*)", seg))
    assert {"provider", "status", "model", "reply"} <= consumed, "提取失效=断言空转"
    for k in consumed:
        assert k in res, f"UI 消费回执键真源不产出: {k}"
