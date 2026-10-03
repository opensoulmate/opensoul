"""gene审计账本retention三层裁剪（dev-reports挂账#4：真实学习审计只增不减的膨胀治理）。

三层：过龄FAIL→per-skill上限→全局上限；回滚语义红线=每技能最近一次状态变更事件
（ADD/UPDATE/DELETE/REVERT）任何层都不清（revert缺省目标_pick_event/删除恢复old留档）。

单元测试tmp db直跑SkillLearner真实行为（可控行直接落表模拟账龄/容量）；
API测试走进程内隔离面（conftest gene_client）验证router接线。
"""

import json
import sqlite3
import time
import uuid

import pytest

from src.gene.skill_learner import SkillLearner

STATE_EVENTS = ("ADD", "UPDATE", "DELETE", "REVERT")


def _tool_calls(*names, **arg_overrides):
    out = []
    for n in names:
        args = {"command": "echo hi", "content": "x" * 20}
        args.update(arg_overrides)
        out.append({"function": {"name": n, "arguments": args}})
    return out


def _insert_audit(db_path, skill_id, event, created_at=None):
    """直落一行审计（retention单元测需要精确控制账龄/顺序/事件类型）。"""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO skill_audit
               (skill_id, event, old_json, new_json, actor, reason, created_at)
               VALUES (?, ?, '', '', 'test', 'seed', ?)""",
            (skill_id, event, created_at if created_at is not None else time.time()),
        )
        return cur.lastrowid


def _audit_ids(db_path):
    with sqlite3.connect(db_path) as conn:
        return [r[0] for r in conn.execute("SELECT audit_id FROM skill_audit ORDER BY audit_id")]


@pytest.fixture
def learner(tmp_path):
    return SkillLearner(db_path=str(tmp_path / "skills.db"))


class TestPruneLayers:
    def test_per_skill_cap_keeps_newest_rows(self, learner):
        ids = [_insert_audit(learner.db_path, "skill-A", "UPDATE") for _ in range(30)]
        result = learner.prune_audit(max_per_skill=10)
        kept = _audit_ids(learner.db_path)
        assert kept == ids[-10:]  # 保最新10行（全为UPDATE，锚点行在其中）
        assert result["deleted_per_skill_cap"] == 20
        assert result["before"] == 30 and result["after"] == 10

    def test_aged_fail_pruned_but_old_state_event_kept(self, learner):
        old = time.time() - 40 * 86400  # 40天前
        aged_fail = _insert_audit(learner.db_path, "skill-A", "FAIL", created_at=old)
        old_add = _insert_audit(learner.db_path, "skill-A", "ADD", created_at=old)
        recent_fail = _insert_audit(learner.db_path, "skill-A", "FAIL")

        result = learner.prune_audit(fail_max_age_days=30)
        kept = _audit_ids(learner.db_path)
        # 过龄FAIL清掉；同样40天前的ADD是状态事件+锚点，绝不因账龄被清
        assert aged_fail not in kept
        assert old_add in kept
        assert recent_fail in kept
        assert result["deleted_aged_fail"] == 1

    def test_total_cap_keeps_newest_and_redline_anchors(self, learner):
        a_ids = [_insert_audit(learner.db_path, "skill-A", "UPDATE") for _ in range(5)]
        b_ids = [_insert_audit(learner.db_path, "skill-B", "UPDATE") for _ in range(5)]
        # 全局最新4行都在B（b_ids[-4:]），A的锚点(a_ids[-1])按红线必须活
        result = learner.prune_audit(max_per_skill=100, max_total=4)
        kept = _audit_ids(learner.db_path)
        assert a_ids[-1] in kept  # A的最新状态变更事件=红线锚点
        assert set(b_ids[-4:]).issubset(set(kept))
        assert len(kept) == 5  # 4行上限+1行红线锚点（正确性>瘦身）
        assert result["before"] == 10

    def test_redline_anchor_survives_per_skill_cap(self, learner):
        add_id = _insert_audit(learner.db_path, "skill-A", "ADD")
        fail_ids = [_insert_audit(learner.db_path, "skill-A", "FAIL") for _ in range(20)]
        learner.prune_audit(max_per_skill=3)
        kept = _audit_ids(learner.db_path)
        # per-skill保最新3行FAIL + 红线锚点ADD（revert缺省/删除恢复依据）
        assert add_id in kept
        assert set(fail_ids[-3:]).issubset(set(kept))
        assert len(kept) == 4

    def test_invalid_params_raise(self, learner):
        with pytest.raises(ValueError):
            learner.prune_audit(max_per_skill=0)
        with pytest.raises(ValueError):
            learner.prune_audit(fail_max_age_days=-1)


class TestRevertIntegrity:
    def test_revert_still_works_after_prune(self, learner):
        """裁剪不得断回滚：默认revert目标（最近状态变更事件）必须活过裁剪。"""
        calls = _tool_calls("read_file", "write_file")
        s1 = learner.extract_from_execution("sess-1", "retention任务", calls, success=True)
        s2 = learner.extract_from_execution("sess-2", "retention任务", calls, success=True)
        assert s2.audit_event == "UPDATE"
        # 20条比UPDATE更新的FAIL噪声把UPDATE挤出"最新N行"
        for _ in range(20):
            _insert_audit(learner.db_path, s1.skill_id, "FAIL")
        learner.prune_audit(max_per_skill=5)

        preview = learner.revert_preview(s1.skill_id)  # 缺省=回滚上一次改动
        assert preview is not None
        assert preview["event"]["event"] == "UPDATE"  # 锚点仍在
        result = learner.revert_skill(s1.skill_id)
        assert result is not None
        assert result["reverted_to_before"] == preview["event"]["audit_id"]
        assert "changes" in result


class TestAutoPrune:
    def test_auto_prune_triggers_on_audit_write(self, learner):
        """写路径自动retention：_audit落账后超max_total自动裁剪（不留人工依赖）。"""
        learner.audit_retention["max_total"] = 5
        for i in range(8):
            learner.extract_from_execution(
                "sess",
                "retention自动任务",
                _tool_calls("read_file", "write_file"),
                success=True,
            )
        with sqlite3.connect(learner.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM skill_audit").fetchone()[0]
        assert total == 5  # ADD+7UPDATE→每次超5即裁，终态≤5

    def test_prune_never_touches_learned_skills(self, learner):
        learner.extract_from_execution(
            "s1", "任务甲", _tool_calls("read_file", "write_file"), success=True
        )
        learner.extract_from_execution(
            "s2", "任务乙", _tool_calls("terminal", "read_file"), success=True
        )
        learner.prune_audit(max_per_skill=1, max_total=1)
        with sqlite3.connect(learner.db_path) as conn:
            skills = conn.execute("SELECT COUNT(*) FROM learned_skills").fetchone()[0]
        assert skills == 2  # 只裁审计账本，学习成果本体不动


class TestPruneApi:
    MARKER = f"retention测试-{uuid.uuid4().hex[:8]}"

    def _payload(self):
        return {
            "task_description": self.MARKER,
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
            "success": True,
        }

    def test_prune_endpoint_prunes_and_reports(self, gene_client):
        for _ in range(3):
            r = gene_client.post("/api/gene/skill/extract", json=self._payload())
            assert r.status_code == 200, r.text

        rp = gene_client.post("/api/gene/skill/audit/prune?max_per_skill=1")
        assert rp.status_code == 200, rp.text
        d = rp.json()
        # 各层删除计数可观测（调用方立刻看到裁了多少）
        for key in (
            "before",
            "after",
            "deleted_aged_fail",
            "deleted_per_skill_cap",
            "deleted_total_cap",
        ):
            assert key in d
        assert d["before"] == 3 and d["after"] == 1
        assert d["deleted_per_skill_cap"] == 2

        ra = gene_client.get("/api/gene/skill/audit?limit=10")
        events = ra.json()["events"]
        assert len(events) == 1
        assert events[0]["event"] == "UPDATE"  # 最新状态变更事件=红线锚点活到最后

    def test_prune_endpoint_invalid_params_422(self, gene_client):
        r = gene_client.post("/api/gene/skill/audit/prune?max_per_skill=0")
        assert r.status_code == 422  # FastAPI Query(ge=1)拒绝
