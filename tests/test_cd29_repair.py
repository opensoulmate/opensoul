#!/usr/bin/env python3
"""CD-㉙ learn 测验统计卡死显示收口（total_quizzes 真源零产出恒假 0，真源零改动）回归锁死。

背景（CD-㉒ 深挖裁决 repair_candidate「learn 测验统计卡收编/真源补现」，CD-㉘ 留档
「剩 4 件 repair_candidate 逐件」按序首件）：learn 页统计卡组 4 卡逐卡深挖定型：
- total_quizzes（「测验题」卡）：confirmed_drift——engine.stats 返回键恰
  total_courses/total_chapters/completed_chapters/pending_chapters/reviewing_courses/
  by_status（src/learn/course_engine.py:578-588）无 total_quizzes（quiz 无统计轴，
  仅 get_quiz 逐课口）；UI LearnStats 声明+消费 {stats?.total_quizzes ?? 0} 恒回落 0
  =假 0 遮蔽（CD-⑱ 回落假 0 同型）。
- total_courses/total_chapters/completed_chapters：resolved_ok——真源键在场。

收口（UI 消费面，真源零改动=CD 系列 wire 权威纪律，CD-⑮ 最近事件卡死显示移除先例）：
- 「测验题」死显示卡（假 0 载体）移除禁假渲染，LearnStats.total_quizzes 死声明键
  清零（CD-⑦ 纪律）；grid md:grid-cols-4→3（余 3 卡填行）；
- 恢复显示=真源补现候选 pending-decisions ㉙（quiz 统计轴语义需拍板）不自决。

断言四层（禁自嗨恒真断言）：
①源码消费面：total_quizzes 死键声明/消费双清零 + 「测验题」死卡移除（章节测验
  独立面字样保留锁死）+ LearnStats 声明键 ⊆ 真源 + grid 收 3 列；
②行为桥：CourseEngine(tmp).stats 真跑形状锁死恒不产出 total_quizzes（㉙ 取 B 须
  显式改本锁）+ quiz 数据真在章节层（真源补现可行性证据）而 stats 无 quiz 统计轴；
③深挖裁决证据锁：stats 返回字面量键集恰 6 键（裁决锚点程序化复核）；
④census 真跑真仓：total_quizzes findings 全消失 + learn stats 调用点消费键收窄。
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "learn" / "page.tsx"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd29_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd29_repair"] = cdc
_spec.loader.exec_module(cdc)

# 真源 engine.stats 产出键（src/learn/course_engine.py:578-588，live 实证一致）
TRUE_SOURCE_KEYS = (
    "total_courses",
    "total_chapters",
    "completed_chapters",
    "pending_chapters",
    "reviewing_courses",
    "by_status",
)


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def _code() -> str:
    """剥行注释后的真实代码（历史/注释里的键名不算消费形）。"""
    return re.sub(r"//.*$", "", _src(), flags=re.M)


def _stats_consumed_keys() -> set[str]:
    """UI 对 stats 对象的首跳消费键（修后=total_courses/total_chapters/completed_chapters）。"""
    return set(re.findall(r"(?<![.\w])stats\??\.([A-Za-z_$][\w$]*)", _code()))


def _engine(tmp_path):
    from src.learn.course_engine import CourseEngine

    return CourseEngine(storage_dir=tmp_path / "learn", db_path=tmp_path / "learn" / "learn.db")


# ── ① 源码消费面 ───────────────────────────────────────────────


def test_total_quizzes_zero_residue():
    """total_quizzes 死显示键声明/消费双清零（CD-⑦ 纪律）。"""
    assert "total_quizzes" not in _code(), "total_quizzes 死显示键残留（消费形或接口声明）"


def test_dead_quiz_stats_card_removed():
    """「测验题」统计死卡（假 0 载体）整体移除禁假渲染；章节测验独立面字样保留。"""
    code = _code()
    assert "测验题</div>" not in code, "「测验题」统计死卡标签残留"
    # 章节测验独立面（空态/生成提示）不是本件对象，禁误删
    assert "该章节暂无测验题" in code, "章节测验空态独立面被误删"
    assert "测验题目" in code, "AI 生成提示独立面被误删"


def test_learn_stats_interface_subset_of_true_source(tmp_path):
    """LearnStats 声明键 ⊆ 真源 engine.stats 产出键（禁声明假键）。"""
    m = re.search(r"interface LearnStats \{([^}]*)\}", _src())
    assert m, "LearnStats 接口未找到（提取失效=断言空转）"
    declared = set(re.findall(r"^\s+([A-Za-z_$][\w$]*):", m.group(1), re.M))
    assert declared, "接口键提取为空（提取失效=断言空转）"
    resp = _engine(tmp_path).stats()
    for k in declared:
        assert k in resp, f"接口声明键真源不产出: {k}"


def test_stat_grid_fills_three_columns():
    """死卡移除后统计卡组 3 卡填 3 列（不留空洞列，CD-⑮ 同款）。"""
    assert "grid grid-cols-2 md:grid-cols-3" in _src(), "统计卡组 grid 未随卡数收编"


# ── ② 行为桥（真源真跑，真源零改动） ────────────────────────────


def test_learn_stats_shape_lock_never_produces_total_quizzes(tmp_path):
    """engine.stats 真跑形状锁死：恰 6 键、恒不产出 total_quizzes。

    未来真源形状变了（如补现 total_quizzes）此桥先炸=CD-③「真源形状变了→
    裁决失效须复挖」证据锁；㉙ 拍板取 B 时须显式改本锁。
    """
    eng = _engine(tmp_path)
    c = eng.create_course("c1")
    eng.add_chapter(c.course_id, "ch1", "body")
    resp = eng.stats()
    assert set(resp) == set(TRUE_SOURCE_KEYS), f"真源形状变了须复挖: {sorted(resp)}"
    assert "total_quizzes" not in resp, "total_quizzes 出现=真源形状变了须复挖"


def test_quiz_data_exists_but_no_stats_axis(tmp_path):
    """quiz 数据真在章节层（get_quiz 可取回）而 stats 无 quiz 统计轴。

    ㉙「真源补现」可行性证据——统计原料在数据层真实存在，只是 stats 未产出计数键；
    拍板取 B 时=加性键 total_quizzes=sum(len(ch.quiz))（语义随拍）。
    """
    eng = _engine(tmp_path)
    c = eng.create_course("c1")
    q = {"question": "1+1=?", "options": ["1", "2"], "correct_index": 1}
    ch = eng.add_chapter(c.course_id, "ch1", "body", quiz=[q, dict(q)])
    assert ch is not None
    got = eng.get_quiz(c.course_id, ch.chapter_id)
    assert got is not None and len(got) == 2, "quiz 数据未真落章节层（提取失效=断言空转）"
    resp = eng.stats()
    assert "total_quizzes" not in resp, "stats 出现 quiz 统计轴=㉙ 裁决失效须复挖"


# ── ③ 深挖裁决证据锁 ───────────────────────────────────────────


def test_stats_return_literal_exact_six_keys():
    """stats 返回字面量键集恰 6 键（CD-㉒ 裁决锚点程序化复核，禁无据断言）。"""
    src = (REPO / "src" / "learn" / "course_engine.py").read_text(encoding="utf-8")
    start = src.index("def stats(")
    seg = src[start : start + 1200]
    keys = set(re.findall(r'"([a-z_]+)":', seg))
    assert keys == set(TRUE_SOURCE_KEYS), f"stats 返回键集漂移须复挖: {sorted(keys)}"
    assert "total_quizzes" not in seg, "stats 出现 total_quizzes=真源形状变了须复挖"


# ── ④ census 工具真跑真仓 ─────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def test_census_total_quizzes_findings_gone(census_report):
    """修后 findings：total_quizzes 从 learn 页 findings 全消失。"""
    findings = [f for f in census_report["findings"] if f["page"].endswith("learn/page.tsx")]
    assert findings, "learn 页 findings 为空（工具失效=断言空转）"
    bad = [f for f in findings if "total_quizzes" in f["key"]]
    assert not bad, f"total_quizzes 仍在 findings: {bad}"


def test_census_learn_stats_callsite_keys_shrunk(census_report):
    """learn stats 调用点消费键收窄到真源键（修前含 total_quizzes 4 键→修后 3 键）。"""
    hits = [
        f
        for f in census_report["findings"]
        if f["page"].endswith("learn/page.tsx") and "stats" in f.get("handler", "")
    ]
    consumed = {k for f in hits for k in f["key"] if "." not in k}
    if hits:
        assert "total_quizzes" not in consumed, f"stats 调用点仍消费死键: {consumed}"
    # 无论 census 是否定位到 handler，UI stats 消费键恒 ⊆ 真源键（独立双证）
    assert _stats_consumed_keys() <= set(TRUE_SOURCE_KEYS), (
        f"UI stats 消费键真源不产出: {_stats_consumed_keys() - set(TRUE_SOURCE_KEYS)}"
    )
