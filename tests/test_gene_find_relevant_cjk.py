"""find_relevant中文检索盲区回归——CJK bigram覆盖度取代whitespace子串匹配

缺口后果（"usage排序公式启发式"遗留销账）：旧公式关键词命中是
`any(kw in name for kw in task.split())`——whitespace分词对中文**整句一个
token**（中文无空格），而技能名=提炼时刻的任务描述（task_description[:50]）：
换一种说法复述同一任务（「清理下日志」vs「帮我把日志文件清理一下」）→子串
匹配永不命中→相关性增益(+0.5/+0.3)从不触发→find_relevant退化成"只按
success_rate排"=与任务无关的推荐（进化链：学了→检索不到→不被复用）。
反面：英文靠空格分词能命中，却被the/a等停用词刷分（'a' in 任意含a名字=+0.5）。

修复：CJK bigram + 拉丁词token覆盖度（_relevance_tokens/_token_coverage），
四因子评分 0.45*relevance + 0.35*trust + 0.10*evidence + 0.10*recency，
每行带relevance_breakdown可观测。本文件钉住：中文改述命中、英文词token、
停用词不刷分、recency同分tiebreak、breakdown透出。
"""

import sqlite3
import time

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


class TestCjkParaphraseRetrieval:
    def test_chinese_paraphrase_ranks_matching_skill_first(self, learner):
        """中文改述（无整句相等）仍命中相关技能，且排在高usage无关技能前。

        旧行为实证：query「清理下日志」不在名字「帮我把日志文件清理一下」里
        →双方都0相关性增益→无关技能靠usage反超（bug形态）。
        """
        calls = _calls("read_file", "write_file")
        target = learner.extract_from_execution("s1", "帮我把日志文件清理一下", calls, success=True)
        other = learner.extract_from_execution("s2", "查询天气情况", calls, success=True)
        for _ in range(6):  # 无关技能堆usage（旧公式下必反超）
            learner.record_usage(other.skill_id, success=True)

        rows = learner.find_relevant("清理下日志", limit=3)
        assert rows, "检索为空"
        assert rows[0]["skill_id"] == target.skill_id, (
            f"中文改述未命中相关技能（检索失灵复发）: {[(r['name'], r['relevance_score']) for r in rows]}"
        )
        # 相关性确实贡献了分数（不是碰巧trust排序）
        assert rows[0]["relevance_breakdown"]["name_coverage"] > 0

    def test_shared_bigram_only_is_partial_not_full(self, learner):
        """只共享部分bigram的技能拿到部分覆盖分，且随共享度单调排序。"""
        calls = _calls("read_file", "write_file")
        hi = learner.extract_from_execution("s1", "日志文件清理工具", calls, success=True)
        lo = learner.extract_from_execution("s2", "日志文件归档策略", calls, success=True)
        rows = learner.find_relevant("清理日志文件", limit=3)
        by_id = {r["skill_id"]: r for r in rows}
        assert by_id[hi.skill_id]["relevance_breakdown"]["name_coverage"] > 0
        assert by_id[lo.skill_id]["relevance_breakdown"]["name_coverage"] > 0
        # 「清理」bigram只在hi的名字里 → hi相关性更高
        assert (
            by_id[hi.skill_id]["relevance_breakdown"]["relevance"]
            > by_id[lo.skill_id]["relevance_breakdown"]["relevance"]
        )
        assert rows[0]["skill_id"] == hi.skill_id


class TestLatinTokens:
    def test_word_tokens_case_insensitive(self, learner):
        """英文任务按词token命中（大小写不敏感）。"""
        calls = _calls("terminal", "read_file")
        target = learner.extract_from_execution("s1", "Deploy frontend app", calls, success=True)
        learner.extract_from_execution("s2", "Database migration", calls, success=True)
        rows = learner.find_relevant("FRONTEND deploy", limit=3)
        assert rows[0]["skill_id"] == target.skill_id
        assert rows[0]["relevance_breakdown"]["name_coverage"] == 1.0

    def test_stopwords_do_not_inflate(self, learner):
        """停用词/单字母不再刷分：query「a file」只按file判命中（旧公式'a'命中
        任意含a名字=+0.5假相关）。"""
        calls = _calls("terminal", "read_file")
        target = learner.extract_from_execution("s1", "file upload", calls, success=True)
        other = learner.extract_from_execution("s2", "weather report", calls, success=True)
        rows = learner.find_relevant("a file", limit=3)
        by_id = {r["skill_id"]: r for r in rows}
        assert by_id[other.skill_id]["relevance_breakdown"]["name_coverage"] == 0.0
        assert by_id[target.skill_id]["relevance_breakdown"]["name_coverage"] == 1.0
        assert rows[0]["skill_id"] == target.skill_id


class TestRecencyFactor:
    def test_recently_used_wins_tiebreak(self, learner):
        """同相关性同成败时，最近用过的排前（recency因子，30天特征时间）。"""
        calls = _calls("read_file", "write_file")
        fresh = learner.extract_from_execution("s1", "共用词任务甲", calls, success=True)
        stale = learner.extract_from_execution("s2", "共用词任务乙", calls, success=True)
        with sqlite3.connect(learner.db_path) as conn:
            conn.execute(
                "UPDATE learned_skills SET last_used_at = ? WHERE skill_id = ?",
                (time.time() - 90 * 86400, stale.skill_id),
            )
            conn.commit()
        rows = learner.find_relevant("共用词", limit=3)
        assert rows[0]["skill_id"] == fresh.skill_id
        by_id = {r["skill_id"]: r for r in rows}
        assert (
            by_id[fresh.skill_id]["relevance_breakdown"]["recency"]
            > by_id[stale.skill_id]["relevance_breakdown"]["recency"]
        )


class TestBreakdownObservability:
    def test_breakdown_fields_present(self, learner):
        """每行带relevance_breakdown（四因子裸值+两路覆盖率）——排序可解释。"""
        learner.extract_from_execution(
            "s1", "遥测任务甲", _calls("terminal", "read_file"), success=True
        )
        rows = learner.find_relevant("遥测任务甲")
        assert rows
        b = rows[0]["relevance_breakdown"]
        for key in (
            "name_coverage",
            "description_coverage",
            "relevance",
            "trust",
            "evidence",
            "recency",
        ):
            assert key in b, f"breakdown缺{key}"
        assert 0.0 <= b["relevance"] <= 1.0
        assert 0.0 <= b["recency"] <= 1.0
