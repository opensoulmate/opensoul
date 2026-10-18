"""CD-⑨ census 工具提取精度收口回归测试（tmp 夹具全隔离，零生产接触）。

5 修复面（CD-⑥/⑦/⑧ 留档 4 bug + 深挖扩面）：
① typed 赋值 `const data: T = await apiFetch(...)` 不进 _ASSIGN_RE（CD-⑥）
② 模板串内 call_end 截断/路径丢尾段（_extract_first_arg 不感知字面量 +
   _normalize_path ${...} 不匹配嵌套花括号）（CD-⑥）
③ 注释/字符串字面文本被误提取消费键（CD-⑦ 注释盲区）
④ setX(data) 整体直传形零消费键提取（CD-⑧）
⑤ `?.` 可选链访问整类漏报 + `??` 假别名（深挖扩面）
工具若退化（任一 bug 复发）对应用例必炸。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parent.parent / "scripts" / "census_contract_drift.py"
spec = importlib.util.spec_from_file_location("census_contract_drift_cd9", _TOOL)
assert spec is not None and spec.loader is not None
cdc = importlib.util.module_from_spec(spec)
sys.modules["census_contract_drift_cd9"] = cdc
spec.loader.exec_module(cdc)


def calls(src: str):
    return cdc.extract_ui_callsites(src)


def keys_of(site):
    return {tuple(k) for k in site.keys}


# ── ① typed 赋值提取 ────────────────────────────────────────────

TYPED_UI = """'use client';
export default function P() {
  const load = async () => {
    const data = await apiFetch('/api/trajectory/sessions');
    const s1 = data.sessions;
    const data: SessionDetail = await apiFetch(`/api/trajectory/sessions/${encodeURIComponent(id)}/events`);
    const e1 = data.events;
  };
}
"""


def test_typed_assignment_callsite_extracted():
    """typed 赋值必须进提取面（修前整 callsite 不可见）。"""
    sites = calls(TYPED_UI)
    assert len(sites) == 2, f"callsite 数不对（typed 形漏提取）: {len(sites)}"
    paths = [s.path for s in sites]
    assert "/api/trajectory/sessions/{}/events" in paths, paths


def test_typed_reassignment_keys_not_misattributed():
    """typed 形下 events 键禁错挂 list_sessions（CD-⑥ 留档误挂形态硬反证）。"""
    sites = calls(TYPED_UI)
    by_path = {s.path: s for s in sites}
    first = by_path["/api/trajectory/sessions"]
    second = by_path["/api/trajectory/sessions/{}/events"]
    assert ("events",) not in keys_of(first), "events 键被错挂 list_sessions（bug 复发）"
    assert keys_of(first) == {("sessions",)}
    assert keys_of(second) == {("events",)}


def test_untyped_form_contract_unchanged():
    """无注解形提取契约逐值不变（修复禁伤旧面）。"""
    ui = """
    const data = await apiFetch('/api/trajectory/sessions');
    const a = data.sessions;
    const t = data.total;
    """
    sites = calls(ui)
    assert len(sites) == 1
    assert sites[0].path == "/api/trajectory/sessions"
    assert keys_of(sites[0]) == {("sessions",), ("total",)}


def test_typed_with_function_type_annotation():
    """类型注解含括号（() => void）不吞调用点（open_idx 取 match 尾括号）。"""
    ui = """
    const f: () => void = await apiFetch('/api/trajectory/sessions');
    const a = f.sessions;
    """
    sites = calls(ui)
    assert len(sites) == 1, "函数类型注解形漏提取"
    assert sites[0].path == "/api/trajectory/sessions"
    assert keys_of(sites[0]) == {("sessions",)}


# ── ② 模板串字面量感知截取 ──────────────────────────────────────


def test_template_expr_string_paren_keeps_tail():
    """模板表达式内字符串里的 ')' 禁截断 call_end（修前 path='/a' 丢尾段）。"""
    sites = calls("const x = await apiFetch(`/a/${fn(')', 2)}/tail`);")
    assert sites[0].path == "/a/{}/tail", sites[0].path


def test_template_double_brace_object_no_stray_tail():
    """${{...}} 对象形禁杂散 '}' 尾段（修前 '/a/{} }/events'）。"""
    sites = calls("const x = await apiFetch(`/a/${ {k: v} }/events`);")
    assert sites[0].path == "/a/{}/events", sites[0].path


def test_nested_template_expr_keeps_tail():
    """模板表达式内嵌套模板串禁截断（修前 path='/a'）。"""
    sites = calls("const x = await apiFetch(`/a/${cond ? `/inner/${y}` : z}/tail`);")
    assert sites[0].path == "/a/{}/tail", sites[0].path


def test_method_option_after_template_still_read():
    """模板串后 options 的 method 取值照读（method 字面量在字符串里须见原文）。"""
    sites = calls("const x = await apiFetch(`/a/${id}/events`, { method: 'POST' });")
    assert sites[0].method == "POST"
    assert sites[0].path == "/a/{}/events"


def test_plain_interpolation_unchanged():
    """朴素 ${id} 形提取契约不变（修复禁伤旧面）。"""
    sites = calls(
        "const x = await apiFetch(`/sessions/${encodeURIComponent(sessionId)}/events?limit=500`);"
    )
    assert sites[0].path == "/sessions/{}/events"


# ── ③ 注释/字符串字面文本非消费 ─────────────────────────────────


def test_comment_phantom_keys_not_extracted():
    """// /* */ JSX {/* */} 注释内 data.键 禁提取消费键（CD-⑦ 注释盲区）。"""
    ui = """const data = await apiFetch('/api/trajectory/sessions');
// const bad = data.line_phantom;
const ok = data.sessions;
/* data.block_phantom */
{/* data.jsx_phantom */}
const v = data.real_key;
"""
    keys = keys_of(calls(ui)[0])
    for phantom in ("line_phantom", "block_phantom", "jsx_phantom"):
        assert (phantom,) not in keys, f"注释内字面文本被误提取: {phantom}"
    assert ("sessions",) in keys and ("real_key",) in keys


def test_string_literal_phantom_not_extracted():
    """字符串字面量里的 data.键 同型禁提取（URL 串实证形）。"""
    ui = """const data = await apiFetch('/api/trajectory/sessions');
const u = 'http://example.com/data.str_key';
const v = data.real_key;
"""
    keys = keys_of(calls(ui)[0])
    assert ("str_key",) not in keys
    assert ("real_key",) in keys


def test_template_expression_code_still_extracted():
    """模板 ${...} 表达式内是真代码——禁过度遮蔽（反向守卫）。"""
    ui = """const data = await apiFetch('/api/trajectory/sessions');
const t = `${data.real_from_expr}`;
"""
    assert ("real_from_expr",) in keys_of(calls(ui)[0])


def test_key_line_numbers_preserved():
    """注释遮蔽保行号（等长空白），key_lines 必须指向真代码行。"""
    ui = """// comment line 1
/* block
   comment */
const data = await apiFetch('/api/trajectory/sessions');
const v = data.real_key;
"""
    site = calls(ui)[0]
    assert site.line == 4, f"callsite 行号漂移: {site.line}"
    assert site.key_lines[("real_key",)] == 5, site.key_lines


# ── ④ setX(data) 整体直传形 ────────────────────────────────────


def test_whole_passthrough_top_level_keys():
    """setDedupResult(data) 整体直传→状态变量消费键必须提取（修前零键）。"""
    ui = """const data = await apiFetch('/api/knowledge/deduplicate');
setDedupResult(data);
const n = dedupResult.merged;
const t = dedupResult.total;
"""
    keys = keys_of(calls(ui)[0])
    assert {("merged",), ("total",)} <= keys, keys


def test_whole_passthrough_chain_element_keys():
    """链式容器 details.slice(0,5).map((d)) → 元素键（details,title）；内建成员禁误收。"""
    ui = """const data = await apiFetch('/api/knowledge/deduplicate');
setDedupResult(data);
const rows = dedupResult.details.slice(0, 5).map((d, i) => d.title + d.similarity);
const n = dedupResult.details.length;
"""
    keys = keys_of(calls(ui)[0])
    assert ("details", "title") in keys, keys
    assert ("details", "similarity") in keys
    assert not any(k[-1] in ("slice", "map", "length") for k in keys), keys


def test_whole_passthrough_destructure_keys():
    """const { merged } = dedupResult 解构形消费键提取。"""
    ui = """const data = await apiFetch('/api/knowledge/deduplicate');
setDedupResult(data);
const { merged, total } = dedupResult;
"""
    keys = keys_of(calls(ui)[0])
    assert {("merged",), ("total",)} <= keys, keys


def test_setter_other_args_not_aliased():
    """setDedupResult(null)/setDedupResult(prev) 禁造别名（只认响应变量）。"""
    ui = """const data = await apiFetch('/api/knowledge/deduplicate');
setDedupResult(null);
setDedupResult(prev);
const v = data.k1;
"""
    keys = keys_of(calls(ui)[0])
    assert ("k1",) in keys
    assert ("null",) not in keys and ("prev",) not in keys


# ── ⑤ ?. 可选链 + ?? 假别名（深挖扩面）──────────────────────────


def test_optional_chain_consumption_extracted():
    """data?.k / data.list?.map 全类可选链消费键提取（修前整类漏报）。"""
    ui = """const data = await apiFetch('/api/x');
const b = data?.opt_key;
const rows = data.list?.map((r) => r.item_key);
"""
    keys = keys_of(calls(ui)[0])
    assert {("opt_key",), ("list",), ("list", "item_key")} <= keys, keys


def test_nullish_fallback_not_phantom_key():
    """data.x ?? fallback 与 data ?? fallback 的 fallback 都不是响应键（?? 假别名
    收口：修前 [?.]{1,2} 把 '??' 当访问符，fallback 被挂成假键路径）。"""
    ui = """const data = await apiFetch('/api/x');
const v = data.real_key ?? fallback_name;
const w = data ?? fallback_name;
const q = w.k2;
"""
    keys = keys_of(calls(ui)[0])
    assert ("real_key",) in keys
    assert not any("fallback_name" in k for k in keys), f"?? 右操作数被误收: {keys}"


# ── 归属精度（新提取面逼出的切段/遮蔽假阳收口）───────────────────


def test_segment_breaks_at_non_apifetch_reassign():
    """同名变量被非 apiFetch 再赋值（const data: T = await res.json()）必须断段，
    后续消费禁错挂上一 callsite（pipeline uploadResult 形态）。"""
    ui = """const data = await apiFetch('/api/x/history');
const a = data.k1;
const data: Run = await res.json();
setUploadResult(data);
const s = uploadResult.status;
"""
    sites = calls(ui)
    assert len(sites) == 1
    assert ("status",) not in keys_of(sites[0]), "再赋值后消费错挂上一 callsite（bug 复发）"
    assert ("k1",) in keys_of(sites[0])


def test_arrow_param_shadow_setter_not_aliased():
    """箭头形参遮蔽（.then((data) => setViewingFile(data))）禁建别名——
    形参 data 非本 callsite 变量（workspace viewingFile 形态）。"""
    ui = """const data = await apiFetch('/api/dir');
const e = data.rows;
apiFetch('/api/file').then((data) => setViewingFile(data));
const c = viewingFile.content;
"""
    sites = calls(ui)
    assert ("content",) not in keys_of(sites[0]), "箭头形参遮蔽误建别名（bug 复发）"
    assert ("rows",) in keys_of(sites[0])


# ── 集成：compare 真源对账 + 真仓烟测 ───────────────────────────

MAIN_PY = """
from fastapi import FastAPI
from src.api.kn import router as kn_router

app = FastAPI()
app.include_router(kn_router, prefix="/api/knowledge", tags=["knowledge"])
"""

KN_API = """
router = APIRouter()


@router.post("/deduplicate")
async def deduplicate():
    return {
        "total": 2,
        "duplicates_found": 1,
        "duplicates_removed": 1,
        "removed_ids": ["x"],
        "details": [{"id": "a", "title": "t", "duplicate_of": "b", "similarity": 1.0}],
    }
"""

UI = """
const data = await apiFetch('/api/knowledge/deduplicate', { method: 'POST' });
setDedupResult(data);
const n = dedupResult.merged;
const t = dedupResult.total;
const rows = dedupResult.details.slice(0, 5).map((d) => d.title);
"""


def test_compare_whole_passthrough_end_to_end(tmp_path):
    """整体直传形走 compare 全链：真源键 produced、真源不产出键 drift（CD-⑧ 面）。"""
    src_root = tmp_path / "src"
    (src_root / "api").mkdir(parents=True)
    (src_root / "main.py").write_text(MAIN_PY, encoding="utf-8")
    (src_root / "api" / "kn.py").write_text(KN_API, encoding="utf-8")
    ui_root = tmp_path / "ui"
    ui_root.mkdir()
    (ui_root / "page.tsx").write_text(UI, encoding="utf-8")
    r = cdc.compare(ui_root, src_root)
    verdicts = {tuple(f["key"]): f["verdict"] for f in r["findings"]}
    assert verdicts.get(("total",)) == "produced"
    assert verdicts.get(("details",)) == "produced"
    assert verdicts.get(("details", "title")) == "produced"
    assert verdicts.get(("merged",)) in ("drift_hard", "drift_suspect"), verdicts.get(("merged",))


def test_real_repo_pipeline_typed_callsite_visible():
    """真仓烟测：pipeline 页 typed 调用点（CD-⑥ 以无注解形绕开的活形）现在真被提取。"""
    page = Path(__file__).resolve().parent.parent / "admin-ui" / "app" / "pipeline" / "page.tsx"
    code = page.read_text(encoding="utf-8")
    sites = [s for s in calls(code) if s.path == "/api/pipeline/history"]
    assert sites, "typed 调用点仍未提取（bug ① 复发）"
    keys = keys_of(sites[0])
    assert {("pipelines",), ("total",)} <= keys, keys
