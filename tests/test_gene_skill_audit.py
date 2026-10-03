"""gene技能库进化审计（mem0 §1.2）+ 失败记忆分支（mem0 §1.1）+ 静默覆盖计数重置修复。

单元测试用tmp db直跑SkillLearner真实行为；API测试走进程内隔离面（conftest
gene_client，tmp独立库）验证router接线（GET /skill/audit、GET /skill/{id}/history、DELETE /skill/{id}）。
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


class TestAuditAddUpdate:
    def test_first_extract_writes_add_audit(self, learner):
        skill = learner.extract_from_execution(
            session_id="sess-1",
            task_description="测试任务A",
            tool_calls=_tool_calls("read_file", "write_file"),
            success=True,
        )
        assert skill is not None
        assert skill.audit_event == "ADD"

        events = learner.history(skill.skill_id)
        assert [e["event"] for e in events] == ["ADD"]
        add = events[0]
        assert add["actor"] == "sess-1"
        assert add["old"] is None
        assert add["new"]["name"] == "测试任务A"
        assert add["created_at"] > 0

    def test_reextract_updates_and_preserves_counts(self, learner):
        """P0修复钉：再见到同一skill不得把usage/success计数静默重置为1。"""
        calls = _tool_calls("read_file", "write_file")
        s1 = learner.extract_from_execution("sess-1", "测试任务B", calls, success=True)
        s2 = learner.extract_from_execution("sess-2", "测试任务B", calls, success=True)
        assert s1.skill_id == s2.skill_id
        assert s2.audit_event == "UPDATE"
        # 返回对象的计数也回填（API响应success_rate不再恒0）
        assert s2.usage_count == 2 and s2.success_count == 2
        assert s2.success_rate == 1.0

        rows = learner.find_relevant("测试任务B", limit=5)
        row = next(r for r in rows if r["skill_id"] == s1.skill_id)
        # 两次成功执行 → 累计2/2，而非INSERT OR REPLACE式的1/1重置
        assert row["usage_count"] == 2
        assert row["success_count"] == 2

        events = learner.history(s1.skill_id)
        assert [e["event"] for e in events] == ["UPDATE", "ADD"]  # 新→旧

    def test_reextract_preserves_created_at(self, learner):
        calls = _tool_calls("terminal", "read_file")
        s1 = learner.extract_from_execution("sess-1", "测试任务C", calls, success=True)
        created1 = learner.history(s1.skill_id)[0]["created_at"]
        learner.extract_from_execution("sess-2", "测试任务C", calls, success=True)
        with sqlite3.connect(learner.db_path) as conn:
            created_row = conn.execute(
                "SELECT created_at FROM learned_skills WHERE skill_id = ?", (s1.skill_id,)
            ).fetchone()[0]
        # created_at来自learned_skills行首建时刻；审计ADD时间≈首建，UPDATE不改created_at
        assert created_row > 0
        assert created1 > 0

    def test_update_records_old_new_content_diff(self, learner):
        """同task+tool序列但参数不同=同一skill的内容演进，old/new都要留档。"""
        s1 = learner.extract_from_execution(
            "sess-1",
            "测试任务D",
            _tool_calls("read_file", "write_file", content="alpha"),
            success=True,
        )
        s2 = learner.extract_from_execution(
            "sess-2",
            "测试任务D",
            _tool_calls("read_file", "write_file", content="beta"),
            success=True,
        )
        assert s1.skill_id == s2.skill_id
        upd = learner.history(s1.skill_id)[0]
        assert upd["event"] == "UPDATE"
        assert upd["old"]["code_template"] != upd["new"]["code_template"]
        assert upd["old"]["usage_count"] == 1
        assert upd["new"]["source_session"] == "sess-2"


class TestFailureMemory:
    def test_failure_recorded_in_fail_branch(self, learner):
        """mem0 §1.1：失败必须可见——进FAIL记忆分支，而非静默丢弃。"""
        result = learner.extract_from_execution(
            "sess-fail",
            "失败任务E",
            _tool_calls("terminal", "read_file"),
            success=False,
        )
        assert result is None
        # 技能行不落（没提炼出来），但失败留痕在审计
        assert learner.find_relevant("失败任务E") == []
        with sqlite3.connect(learner.db_path) as conn:
            n_skills = conn.execute("SELECT COUNT(*) FROM learned_skills").fetchone()[0]
        assert n_skills == 0

        events = learner.audit_tail()
        assert [e["event"] for e in events] == ["FAIL"]
        fail = events[0]
        assert fail["new"]["task_description"] == "失败任务E"
        assert "terminal" in fail["new"]["tool_sequence"]
        assert fail["reason"] == "execution failed"

    def test_failure_hits_same_skill_family_as_success(self, learner):
        """失败与成功同族谱（同skill_id）：能按skill_id看到'这个流程成功过也失败过'。"""
        calls = _tool_calls("read_file", "write_file")
        s1 = learner.extract_from_execution("sess-1", "测试任务F", calls, success=True)
        learner.extract_from_execution("sess-2", "测试任务F", calls, success=False)
        events = learner.history(s1.skill_id)
        assert [e["event"] for e in events] == ["FAIL", "ADD"]

    def test_failure_without_tool_calls_no_audit(self, learner):
        assert learner.extract_from_execution("s", "任务G", [], success=False) is None
        assert learner.audit_tail() == []


class TestDelete:
    def test_delete_skill_writes_delete_audit(self, learner):
        s1 = learner.extract_from_execution(
            "sess-1", "测试任务H", _tool_calls("terminal", "read_file"), success=True
        )
        assert learner.delete_skill(s1.skill_id, reason="测试清理") is True
        assert learner.find_relevant("测试任务H") == []

        events = learner.history(s1.skill_id)
        assert [e["event"] for e in events] == ["DELETE", "ADD"]
        assert events[0]["old"]["name"] == "测试任务H"
        assert events[0]["reason"] == "测试清理"

    def test_delete_missing_skill_returns_false(self, learner):
        assert learner.delete_skill("skill_nonexistent") is False
        assert learner.audit_tail() == []


class TestQueriesAndStats:
    def test_history_limit_and_order(self, learner):
        calls = _tool_calls("read_file", "write_file")
        s1 = learner.extract_from_execution("s1", "测试任务I", calls, success=True)
        learner.extract_from_execution("s2", "测试任务I", calls, success=True)
        learner.extract_from_execution("s3", "测试任务I", calls, success=True)
        events = learner.history(s1.skill_id, limit=2)
        assert len(events) == 2
        assert [e["event"] for e in events] == ["UPDATE", "UPDATE"]

    def test_audit_tail_across_skills(self, learner):
        learner.extract_from_execution("s1", "任务J1", _tool_calls("terminal", "read_file"), True)
        learner.extract_from_execution("s2", "任务J2", _tool_calls("terminal", "read_file"), True)
        tail = learner.audit_tail()
        assert [e["event"] for e in tail] == ["ADD", "ADD"]
        assert len({e["skill_id"] for e in tail}) == 2

    def test_stats_include_audit_counts(self, learner):
        calls = _tool_calls("read_file", "write_file")
        learner.extract_from_execution("s1", "任务K", calls, True)
        learner.extract_from_execution("s2", "任务K", calls, True)
        learner.extract_from_execution("s3", "任务K_fail", calls, False)
        stats = learner.get_stats()
        assert stats["audit_events"] == 3  # ADD+UPDATE+FAIL
        assert stats["failed_extractions"] == 1

    def test_audit_write_failure_does_not_block_extraction(self, learner, monkeypatch, caplog):
        """CowAgent记账哲学：记账失败绝不阻塞主流程，但要大声记日志。"""
        with sqlite3.connect(learner.db_path) as conn:
            conn.execute("DROP TABLE skill_audit")
            conn.commit()
        skill = learner.extract_from_execution(
            "s1", "任务L", _tool_calls("read_file", "write_file"), True
        )
        assert skill is not None  # 提炼本身不受记账失败影响
        assert skill.audit_event == "ADD"


class TestApiWiring:
    """live服务（:8090）上的router接线验证。"""

    MARKER = f"审计接线测试-{uuid.uuid4().hex[:8]}"

    def _payload(self, success=True, content="hello"):
        return {
            "task_description": self.MARKER,
            "execution_log": json.dumps(
                [
                    {
                        "function": {
                            "name": "read_file",
                            "arguments": {"path": "a.txt", "content": content},
                        }
                    },
                    {
                        "function": {
                            "name": "write_file",
                            "arguments": {"path": "b.txt", "content": content},
                        }
                    },
                ]
            ),
            "success": success,
        }

    def test_extract_history_delete_via_api(self, gene_client):
        r1 = gene_client.post("/api/gene/skill/extract", json=self._payload(content="v1"))
        assert r1.status_code == 200, r1.text
        d1 = r1.json()
        assert d1["event"] == "ADD"
        skill_id = d1["skill_id"]

        r2 = gene_client.post("/api/gene/skill/extract", json=self._payload(content="v2"))
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["skill_id"] == skill_id
        assert d2["event"] == "UPDATE"
        assert d2["success_rate"] == 1.0  # 计数回填，不再恒0

        rh = gene_client.get(f"/api/gene/skill/{skill_id}/history")
        assert rh.status_code == 200
        events = rh.json()["events"]
        assert [e["event"] for e in events] == ["UPDATE", "ADD"]
        # v1→v2的模板演进留档（old/new可diff=回滚依据）
        assert events[0]["old"]["code_template"] != events[0]["new"]["code_template"]

        rd = gene_client.delete(f"/api/gene/skill/{skill_id}?reason=api-test-cleanup")
        assert rd.status_code == 200
        rd2 = gene_client.delete(f"/api/gene/skill/{skill_id}")
        assert rd2.status_code == 404

    def test_failure_and_audit_tail_via_api(self, gene_client):
        rf = gene_client.post("/api/gene/skill/extract", json=self._payload(success=False))
        assert rf.status_code == 400  # 没提炼出技能
        assert "skill_audit" in rf.text  # 但失败已留痕的声明在错误契约里

        ra = gene_client.get("/api/gene/skill/audit?limit=20")
        assert ra.status_code == 200
        events = ra.json()["events"]
        fails = [
            e
            for e in events
            if e["event"] == "FAIL"
            and self.MARKER in (e.get("new") or {}).get("task_description", "")
        ]
        assert fails, f"FAIL event for {self.MARKER} not found in audit tail"
