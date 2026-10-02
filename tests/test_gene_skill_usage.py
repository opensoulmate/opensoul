"""gene技能使用遥测闭环：record_usage从死代码变成FAIL执行路径的真实记账点。

钉三件事：
1. 失败执行计入usage（此前只成功计数→success_rate恒1.0=指标说谎）；
2. find_relevant/recommend的success_rate如实回填（此前行里没这列恒get缺省0.0）；
3. 排序失败不抬名次（旧公式usage*0.1失败反而加分）。

单元测试tmp db直跑SkillLearner；API测试走live服务（conftest client→:8090）。
"""

import json
import sqlite3
import uuid

import pytest

from src.gene.skill_learner import SkillLearner


def _tool_calls(*names, **arg_overrides):
    """构造extract_from_execution吃的tool_calls形状。"""
    out = []
    for n in names:
        args = {"command": "echo hi", "content": "x" * 20}
        args.update(arg_overrides)
        out.append({"function": {"name": n, "arguments": args}})
    return out


@pytest.fixture
def learner(tmp_path):
    return SkillLearner(db_path=str(tmp_path / "skills.db"))


class TestFailCountsIntoUsage:
    def test_fail_on_existing_family_dilutes_success_rate(self, learner):
        """失败执行也是"使用"：usage+1、success不动→success_rate从1.0掉到0.5。"""
        calls = _tool_calls("read_file", "write_file")
        s1 = learner.extract_from_execution("sess-1", "遥测任务A", calls, success=True)
        assert s1.usage_count == 1 and s1.success_count == 1

        learner.extract_from_execution("sess-2", "遥测任务A", calls, success=False)

        rows = learner.find_relevant("遥测任务A", limit=5)
        row = next(r for r in rows if r["skill_id"] == s1.skill_id)
        assert row["usage_count"] == 2  # 失败计入使用
        assert row["success_count"] == 1  # 成功数不动
        assert row["success_rate"] == 0.5  # 如实回填，不再恒0.0/1.0

    def test_fail_updates_last_used_at(self, learner):
        calls = _tool_calls("terminal", "read_file")
        s1 = learner.extract_from_execution("sess-1", "遥测任务B", calls, success=True)
        with sqlite3.connect(learner.db_path) as conn:
            before = conn.execute(
                "SELECT last_used_at FROM learned_skills WHERE skill_id = ?", (s1.skill_id,)
            ).fetchone()[0]
        learner.extract_from_execution("sess-2", "遥测任务B", calls, success=False)
        with sqlite3.connect(learner.db_path) as conn:
            after = conn.execute(
                "SELECT last_used_at FROM learned_skills WHERE skill_id = ?", (s1.skill_id,)
            ).fetchone()[0]
        assert after >= before  # 使用时刻被刷新

    def test_fail_without_skill_row_is_noop(self, learner):
        """没成功提炼过的技能族：FAIL只留审计，不落learned_skills行（没技能可归因）。"""
        learner.extract_from_execution(
            "sess-fail", "遥测任务C", _tool_calls("terminal", "read_file"), success=False
        )
        with sqlite3.connect(learner.db_path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM learned_skills").fetchone()[0]
        assert n == 0
        # FAIL留痕照旧（mem0 §1.1失败可见不受影响）
        assert [e["event"] for e in learner.audit_tail()] == ["FAIL"]

    def test_fail_still_writes_fail_audit_after_usage_wiring(self, learner):
        """接线record_usage后FAIL审计语义不变：[FAIL, ADD]族谱照旧。"""
        calls = _tool_calls("read_file", "write_file")
        s1 = learner.extract_from_execution("sess-1", "遥测任务D", calls, success=True)
        learner.extract_from_execution("sess-2", "遥测任务D", calls, success=False)
        assert [e["event"] for e in learner.history(s1.skill_id)] == ["FAIL", "ADD"]


class TestRecordUsageEntry:
    def test_record_usage_returns_updated_counts(self, learner):
        s1 = learner.extract_from_execution(
            "sess-1", "遥测任务E", _tool_calls("terminal", "read_file"), success=True
        )
        res = learner.record_usage(s1.skill_id, success=True)
        assert res["updated"] is True
        assert res["usage_count"] == 2 and res["success_count"] == 2
        assert res["success_rate"] == 1.0

        res2 = learner.record_usage(s1.skill_id, success=False)
        assert res2["usage_count"] == 3 and res2["success_count"] == 2
        assert res2["success_rate"] == pytest.approx(2 / 3)

    def test_record_usage_missing_skill_is_noop(self, learner):
        res = learner.record_usage("skill_nonexistent", success=True)
        assert res["updated"] is False
        assert res["usage_count"] == 0 and res["success_rate"] == 0.0
        with sqlite3.connect(learner.db_path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM learned_skills").fetchone()[0]
        assert n == 0  # 不落新行

    def test_record_usage_does_not_write_audit(self, learner):
        """设计钉：USAGE遥测量大不进审计表（审计记"进化变更"，计数留痕在UPDATE审计）。"""
        s1 = learner.extract_from_execution(
            "sess-1", "遥测任务F", _tool_calls("terminal", "read_file"), success=True
        )
        n_before = len(learner.audit_tail())
        learner.record_usage(s1.skill_id, success=True)
        assert len(learner.audit_tail()) == n_before


class TestRankingHonesty:
    def test_failures_do_not_inflate_ranking(self, learner):
        """旧公式 success*0.3 + usage*0.1 会让失败多的技能反而排前——失败只稀释成功率。"""
        calls = _tool_calls("terminal", "read_file")
        # 甲：1次成功（rate 1.0）；乙：1次成功+3次失败（usage 4, rate 0.25）
        sa = learner.extract_from_execution("s1", "共用词任务甲", calls, success=True)
        sb = learner.extract_from_execution("s2", "共用词任务乙", calls, success=True)
        for i in range(3):
            learner.extract_from_execution(f"sf{i}", "共用词任务乙", calls, success=False)

        rows = learner.find_relevant("共用词", limit=5)
        ids = [r["skill_id"] for r in rows]
        assert sa.skill_id in ids and sb.skill_id in ids
        # 乙usage更多（4 > 1）但失败稀释了成功率——不得排在甲前
        assert ids.index(sa.skill_id) < ids.index(sb.skill_id)
        row_b = next(r for r in rows if r["skill_id"] == sb.skill_id)
        assert row_b["success_rate"] == 0.25

    def test_success_rate_field_present_in_find_relevant(self, learner):
        learner.extract_from_execution(
            "s1", "遥测任务G", _tool_calls("terminal", "read_file"), success=True
        )
        rows = learner.find_relevant("遥测任务G")
        assert rows and "success_rate" in rows[0]
        assert rows[0]["success_rate"] == 1.0


class TestApiWiring:
    """live服务（:8090）上的router接线验证：POST /skill/{id}/usage + recommend回填。"""

    def _payload(self, marker, success=True):
        return {
            "task_description": marker,
            "execution_log": json.dumps(
                [
                    {
                        "function": {
                            "name": "read_file",
                            "arguments": {"path": "a.txt", "content": "x" * 20},
                        }
                    },
                    {
                        "function": {
                            "name": "write_file",
                            "arguments": {"path": "b.txt", "content": "x" * 20},
                        }
                    },
                ]
            ),
            "success": success,
        }

    def test_usage_endpoint_recommend_rate_via_api(self, client):
        marker = f"遥测接线测试-{uuid.uuid4().hex[:8]}"
        r1 = client.post("/api/gene/skill/extract", json=self._payload(marker))
        assert r1.status_code == 200, r1.text
        skill_id = r1.json()["skill_id"]

        # 使用回执（失败）→ success_rate 1.0 → 0.5
        ru = client.post(f"/api/gene/skill/{skill_id}/usage", json={"success": False})
        assert ru.status_code == 200, ru.text
        du = ru.json()
        assert du["updated"] is True
        assert du["usage_count"] == 2 and du["success_count"] == 1
        assert du["success_rate"] == 0.5

        # recommend不再恒0.0（修复前此处恒返回get缺省0.0）
        rr = client.post("/api/gene/skill/recommend", json={"task_description": marker})
        assert rr.status_code == 200
        rec = next(s for s in rr.json()["skills"] if s["skill_id"] == skill_id)
        assert rec["success_rate"] == 0.5

        # 未知技能404（不静默吞）
        ru404 = client.post("/api/gene/skill/skill_nonexistent/usage", json={"success": True})
        assert ru404.status_code == 404

        rd = client.delete(f"/api/gene/skill/{skill_id}?reason=usage-test-cleanup")
        assert rd.status_code == 200

    def test_fail_extract_via_api_counts_into_usage(self, client):
        """FAIL执行路径（extract success=false）真实调用record_usage——API面实证。"""
        marker = f"遥测接线测试-{uuid.uuid4().hex[:8]}"
        r1 = client.post("/api/gene/skill/extract", json=self._payload(marker))
        assert r1.status_code == 200
        skill_id = r1.json()["skill_id"]

        rf = client.post("/api/gene/skill/extract", json=self._payload(marker, success=False))
        assert rf.status_code == 400  # 没提炼出技能（契约不变）

        rh = client.get(f"/api/gene/skill/{skill_id}/history")
        assert rh.status_code == 200
        assert [e["event"] for e in rh.json()["events"]][:2] == ["FAIL", "ADD"]

        rr = client.post("/api/gene/skill/recommend", json={"task_description": marker})
        rec = next(s for s in rr.json()["skills"] if s["skill_id"] == skill_id)
        assert rec["usage_count"] == 2  # FAIL计入usage
        assert rec["success_rate"] == 0.5

        rd = client.delete(f"/api/gene/skill/{skill_id}?reason=usage-test-cleanup")
        assert rd.status_code == 200
