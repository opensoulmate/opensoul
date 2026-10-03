"""gene技能库一键回滚（Agent Zero _time_travel travel/revert + preview先行）。

单元测试用tmp db直跑SkillLearner真实行为；API测试走进程内隔离面（conftest
gene_client，tmp独立库）验证router接线（GET /skill/{id}/revert-preview、POST /skill/{id}/revert）。
核心契约：
- 缺省回滚=最近一次有状态变更的事件（UPDATE/DELETE/REVERT），只有ADD史不自动选（防误删）
- 显式audit_id=回到任意事件之前（含ADD→删除、REVERT→再翻回去=travel链不断）
- 回滚记REVERT审计且审计表只追加不改写（回滚点本身不可被进化改写）
"""

import json
import uuid

import pytest

from src.gene.skill_learner import SkillLearner


def _tool_calls(*names, content="x" * 20, **arg_overrides):
    out = []
    for n in names:
        args = {"command": "echo hi", "content": content}
        args.update(arg_overrides)
        out.append({"function": {"name": n, "arguments": args}})
    return out


@pytest.fixture
def learner(tmp_path):
    return SkillLearner(db_path=str(tmp_path / "skills.db"))


def _make_v1_v2(learner, task="回滚测试任务"):
    """ADD(v1)→UPDATE(v2)，返回(skill_id, v1_created_at)。"""
    s1 = learner.extract_from_execution(
        "sess-1", task, _tool_calls("read_file", "write_file", content="v1"), True
    )
    s2 = learner.extract_from_execution(
        "sess-2", task, _tool_calls("read_file", "write_file", content="v2"), True
    )
    assert s1.skill_id == s2.skill_id and s2.audit_event == "UPDATE"
    return s1.skill_id


class TestRevertUpdate:
    def test_one_click_revert_restores_previous_state(self, learner):
        """一键回滚：UPDATE后缺省revert=回到v1——内容+计数都回退，created_at保留。"""
        sid = _make_v1_v2(learner)
        before_created = learner.current_state(sid)["created_at"]

        result = learner.revert_skill(sid, actor="tester", reason="回滚验证")
        assert result["action"] == "update"
        st = learner.current_state(sid)
        assert "v1" in st["code_template"] and "v2" not in st["code_template"]
        assert st["usage_count"] == 1 and st["success_count"] == 1  # 计数随快照回退
        assert st["created_at"] == before_created  # 创建时间不被回滚改写

        events = learner.history(sid)
        assert [e["event"] for e in events] == ["REVERT", "UPDATE", "ADD"]
        rev = events[0]
        assert rev["actor"] == "tester"
        assert "revert_to_before_audit_id" in rev["reason"]
        assert "v2" in rev["old"]["code_template"]  # 回滚前状态留档
        assert "v1" in rev["new"]["code_template"]  # 恢复后状态留档

    def test_auto_revert_picks_latest_change(self, learner):
        """缺省回滚=最近一次改动（v3→v2），不是一路回到v1。"""
        sid = _make_v1_v2(learner, task="回滚测试任务L")
        learner.extract_from_execution(
            "sess-3", "回滚测试任务L", _tool_calls("read_file", "write_file", content="v3"), True
        )

        result = learner.revert_skill(sid)
        st = learner.current_state(sid)
        assert "v2" in st["code_template"] and "v3" not in st["code_template"]
        # 目标=最近一次UPDATE（v3那次）——不是最早的ADD
        latest_upd = [e for e in learner.history(sid) if e["event"] == "UPDATE"][0]
        assert result["reverted_to_before"] == latest_upd["audit_id"]

    def test_revert_of_revert_is_redo(self, learner):
        """travel链：回滚可再被回滚（第二次缺省revert=翻回v2，Agent Zero travel语义）。"""
        sid = _make_v1_v2(learner)
        learner.revert_skill(sid)  # →v1
        assert "v1" in learner.current_state(sid)["code_template"]

        result = learner.revert_skill(sid)  # 选中REVERT事件→翻回v2
        st = learner.current_state(sid)
        assert "v2" in st["code_template"]
        assert st["usage_count"] == 2
        assert result["action"] == "update"

    def test_audit_trail_append_only(self, learner):
        """保护区：回滚不改写历史审计行（旧UPDATE的old留档原样），只追加REVERT。"""
        sid = _make_v1_v2(learner)
        upd = [e for e in learner.history(sid) if e["event"] == "UPDATE"][0]
        old_snapshot = json.dumps(upd["old"], sort_keys=True)

        learner.revert_skill(sid)
        upd_after = [e for e in learner.history(sid) if e["event"] == "UPDATE"][0]
        assert json.dumps(upd_after["old"], sort_keys=True) == old_snapshot
        assert len(learner.history(sid)) == 3  # ADD+UPDATE+REVERT，原两行原样


class TestRevertDelete:
    def test_revert_delete_undeletes_with_full_fidelity(self, learner):
        """DELETE回滚=undelete：整行无损恢复（含parameters/tags/计数/created_at）。"""
        s = learner.extract_from_execution(
            "sess-1", "回滚测试任务D", _tool_calls("terminal", "read_file"), True
        )
        sid = s.skill_id
        before = learner.current_state(sid)
        assert learner.delete_skill(sid, reason="先删") is True
        assert learner.current_state(sid) is None

        result = learner.revert_skill(sid)  # 缺省选中DELETE事件
        assert result["action"] == "restore"
        st = learner.current_state(sid)
        for f in (
            "name",
            "description",
            "category",
            "code_template",
            "source_session",
            "usage_count",
            "success_count",
            "created_at",
            "last_used_at",
            "avg_duration_ms",
        ):
            assert st[f] == before[f], f
        assert st["parameters"] == before["parameters"]
        assert st["tags"] == before["tags"]


class TestRevertAdd:
    def test_add_only_skill_needs_explicit_audit_id(self, learner):
        """只有ADD史：缺省不自动选（防误删）→None；显式ADD事件=删除技能。"""
        s = learner.extract_from_execution(
            "sess-1", "回滚测试任务A", _tool_calls("read_file", "write_file"), True
        )
        sid = s.skill_id
        assert learner.revert_preview(sid) is None
        assert learner.revert_skill(sid) is None

        add_id = learner.history(sid)[0]["audit_id"]
        result = learner.revert_skill(sid, audit_id=add_id, reason="撤销新增")
        assert result["action"] == "delete"
        assert learner.current_state(sid) is None
        events = learner.history(sid)
        assert events[0]["event"] == "REVERT"
        assert events[0]["new"] == {"deleted": True}

    def test_revert_the_revert_undeletes(self, learner):
        """travel链：把"删除回滚"再回滚=技能复活（REVERT的old=删除前整行）。"""
        s = learner.extract_from_execution(
            "sess-1", "回滚测试任务A2", _tool_calls("read_file", "write_file"), True
        )
        sid = s.skill_id
        add_id = learner.history(sid)[0]["audit_id"]
        learner.revert_skill(sid, audit_id=add_id)

        rev_id = learner.history(sid)[0]["audit_id"]
        result = learner.revert_skill(sid, audit_id=rev_id)
        assert result["action"] == "restore"
        st = learner.current_state(sid)
        assert st is not None and st["name"] == "回滚测试任务A2"


class TestPreviewAndErrors:
    def test_preview_is_zero_write_and_reports_diff(self, learner):
        """Agent Zero契约：破坏性动作前preview看清diff——零写入。"""
        sid = _make_v1_v2(learner)
        audit_n_before = len(learner.history(sid))
        row_before = learner.current_state(sid)

        p = learner.revert_preview(sid)
        assert p["action"] == "update"
        assert p["changes"]["code_template"]["from"] != p["changes"]["code_template"]["to"]
        assert p["changes"]["usage_count"] == {"from": 2, "to": 1}
        assert "v2" in p["current"]["code_template"]
        assert "v1" in p["target"]["code_template"]
        assert len(learner.history(sid)) == audit_n_before  # 零写入
        assert learner.current_state(sid) == row_before

    def test_fail_event_not_revertible(self, learner):
        """FAIL事件无状态变更→ValueError（API层映射400）。"""
        learner.extract_from_execution(
            "sess-1", "回滚测试任务F", _tool_calls("read_file", "write_file"), True
        )
        learner.extract_from_execution(
            "sess-2", "回滚测试任务F_fail", _tool_calls("read_file", "write_file"), False
        )
        # 直接从审计面拿FAIL事件的skill_id族谱（FAIL不建行）
        tail = learner.audit_tail()
        fail = [e for e in tail if e["event"] == "FAIL"][0]
        with pytest.raises(ValueError):
            learner.revert_preview(fail["skill_id"], audit_id=fail["audit_id"])
        with pytest.raises(ValueError):
            learner.revert_skill(fail["skill_id"], audit_id=fail["audit_id"])

    def test_unknown_skill_or_event_returns_none(self, learner):
        assert learner.revert_preview("skill_nonexistent") is None
        assert learner.revert_skill("skill_nonexistent") is None
        sid = _make_v1_v2(learner, task="回滚测试任务U")
        assert learner.revert_preview(sid, audit_id=999999) is None
        assert learner.revert_skill(sid, audit_id=999999) is None

    def test_cross_skill_event_isolation(self, learner):
        """别的技能的audit_id不能作用到本技能（skill_id过滤）。"""
        sid_a = _make_v1_v2(learner, task="回滚测试任务X")
        sid_b = _make_v1_v2(learner, task="回滚测试任务Y")
        ev_b = learner.history(sid_b)[0]["audit_id"]
        assert learner.revert_skill(sid_a, audit_id=ev_b) is None

    def test_legacy_old_snapshot_without_time_fields(self, learner):
        """旧审计行（扩展前形状：old无created_at等）回滚：记录到的字段回退，没记录的保留当前值。"""
        import sqlite3
        import time

        sid = _make_v1_v2(learner)
        legacy_old = {
            "name": "旧版任务",
            "description": "旧描述",
            "category": "general",
            "code_template": "legacy_call()",
            "parameters": [],
            "tags": [],
            "source_session": "sess-0",
            "usage_count": 7,
            "success_count": 5,
        }
        with sqlite3.connect(learner.db_path) as conn:
            conn.execute(
                """INSERT INTO skill_audit (skill_id, event, old_json, new_json, actor, reason, created_at)
                   VALUES (?, 'UPDATE', ?, '', 'legacy', 'legacy-shape', ?)""",
                (sid, json.dumps(legacy_old, ensure_ascii=False), time.time()),
            )
            conn.commit()
        legacy_id = learner.history(sid)[0]["audit_id"]
        cur_created = learner.current_state(sid)["created_at"]

        result = learner.revert_skill(sid, audit_id=legacy_id)
        assert result["action"] == "update"
        st = learner.current_state(sid)
        assert st["name"] == "旧版任务" and st["code_template"] == "legacy_call()"
        assert st["usage_count"] == 7 and st["success_count"] == 5
        assert st["created_at"] == cur_created  # 旧快照没记录→保留当前值


class TestApiWiring:
    """live服务（:8090）上的router接线验证。"""

    MARKER = f"回滚接线测试-{uuid.uuid4().hex[:8]}"

    def _payload(self, content="hello"):
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
            "success": True,
        }

    def test_revert_flow_via_api(self, gene_client):
        r1 = gene_client.post("/api/gene/skill/extract", json=self._payload(content="v1"))
        assert r1.status_code == 200, r1.text
        sid = r1.json()["skill_id"]
        r2 = gene_client.post("/api/gene/skill/extract", json=self._payload(content="v2"))
        assert r2.status_code == 200 and r2.json()["event"] == "UPDATE"

        rp = gene_client.get(f"/api/gene/skill/{sid}/revert-preview")
        assert rp.status_code == 200, rp.text
        p = rp.json()
        assert p["action"] == "update"
        assert "code_template" in p["changes"]

        rv = gene_client.post(f"/api/gene/skill/{sid}/revert?reason=api-revert-test")
        assert rv.status_code == 200, rv.text
        d = rv.json()
        assert d["action"] == "update" and d["reverted_to_before"] == p["event"]["audit_id"]

        rh = gene_client.get(f"/api/gene/skill/{sid}/history")
        assert rh.status_code == 200
        assert rh.json()["events"][0]["event"] == "REVERT"

        # travel链：再revert一次=翻回v2
        rv2 = gene_client.post(f"/api/gene/skill/{sid}/revert")
        assert rv2.status_code == 200

        rd = gene_client.delete(f"/api/gene/skill/{sid}?reason=api-revert-cleanup")
        assert rd.status_code == 200

    def test_revert_preview_errors_via_api(self, gene_client):
        r = gene_client.get("/api/gene/skill/skill_nonexistent/revert-preview")
        assert r.status_code == 404
        r2 = gene_client.post("/api/gene/skill/skill_nonexistent/revert")
        assert r2.status_code == 404

        # FAIL事件→400
        rf = gene_client.post(
            "/api/gene/skill/extract",
            json={**self._payload(), "task_description": f"{self.MARKER}-fail", "success": False},
        )
        assert rf.status_code == 400
        ra = gene_client.get("/api/gene/skill/audit?limit=50")
        fail = [
            e
            for e in ra.json()["events"]
            if e["event"] == "FAIL"
            and self.MARKER in (e.get("new") or {}).get("task_description", "")
        ][0]
        r3 = gene_client.get(
            f"/api/gene/skill/{fail['skill_id']}/revert-preview?audit_id={fail['audit_id']}"
        )
        assert r3.status_code == 400
        r4 = gene_client.post(
            f"/api/gene/skill/{fail['skill_id']}/revert?audit_id={fail['audit_id']}"
        )
        assert r4.status_code == 400
