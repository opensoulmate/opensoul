"""find_relevant冷启动盲区回归——相关性先于usage窗口（P0检索缺口）

缺口后果：find_relevant 先按 usage_count DESC 开 LIMIT*3 窗口、再做相关性
评分——库里高usage旧技能占满窗口后，**新学技能（usage=1/2）再相关也查不到**：
live API实锤（135行库中12条(2,2)旧测试行占满9窗口→刚提炼的技能recommend
查无此技能→test_gene_skill_usage两个API测试StopIteration）。对"进化闭环"
是断链：学了→检索不到→永远不被复用→success_rate无从生长。

修复：候选全集扫描、相关性/评分对全集做、最后才按relevance_score截limit。
"""

import pytest

from src.gene.skill_learner import SkillLearner


def _calls(*names):
    return [
        {"function": {"name": n, "arguments": {"path": "a.txt", "content": "x" * 20}}}
        for n in names
    ]


@pytest.fixture
def learner(tmp_path):
    return SkillLearner(db_path=str(tmp_path / "skills.db"))


class TestColdStart:
    def test_new_skill_found_despite_high_usage_others(self, learner):
        """20条高usage旧技能占满旧窗口后，新学技能仍能被检索到（修复前查无）"""
        for i in range(20):
            s = learner.extract_from_execution(
                f"old-{i}",
                f"旧任务甲{i:02d}",
                _calls("terminal", "read_file"),
                success=True,
            )
            for _ in range(3):
                learner.record_usage(s.skill_id, success=True)  # usage 4, 压过新行
        new = learner.extract_from_execution(
            "new", "全新独有任务乙", _calls("read_file", "write_file"), success=True
        )
        rows = learner.find_relevant("全新独有任务乙", limit=3)
        assert rows, "新学技能检索不到（冷启动盲区复发）"
        assert rows[0]["skill_id"] == new.skill_id  # 相关性主导排第一

    def test_relevance_beats_usage_when_keywords_match(self, learner):
        """关键词命中的新行排在高usage但不相关的旧行前（评分公式不变，候选集修复）"""
        for i in range(10):
            s = learner.extract_from_execution(
                f"old-{i}",
                f"无关旧任务{i:02d}",
                _calls("terminal", "read_file"),
                success=True,
            )
            learner.record_usage(s.skill_id, success=True)
        new = learner.extract_from_execution(
            "new", "目标检索任务丙", _calls("read_file", "write_file"), success=True
        )
        rows = learner.find_relevant("目标检索任务丙", limit=3)
        assert rows[0]["skill_id"] == new.skill_id

    def test_limit_still_applies(self, learner):
        """全集评分后limit照旧截断（返回条数不膨胀）"""
        for i in range(10):
            learner.extract_from_execution(
                f"t-{i}", f"同族任务{i}", _calls("terminal", "read_file"), success=True
            )
        rows = learner.find_relevant("同族任务", limit=3)
        assert 0 < len(rows) <= 3

    def test_empty_table_returns_empty(self, learner):
        """空库→空列表（全集扫描不改变空契约）"""
        assert learner.find_relevant("任意任务") == []
