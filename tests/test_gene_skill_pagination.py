"""gene技能库库存分页真值：total/has_more + 可回滚事件集单一真源。

缺口背景（2026-10-04 06:05轮遗留#2/#3销账）：GET /api/gene/skill返回count=len(本页)
被当总数展示——生产库122技能 vs 默认limit=100，22条技能对用户不可见且无从发现
"还有更多"（库存盘点自己说谎）；前端回滚按钮可见性硬编码"FAIL除外"，与后端
AUDIT_STATE_EVENTS单一真源各持一份（新增事件类型就两端漂移）。本文件钉住：
1. total=COUNT(*)真实库存（与len(本页)区分）、has_more/offset/limit分页真值；
2. count_skills与list_skills同口径category过滤，offset分页无重复无遗漏；
3. revertible_events=SkillLearner.AUDIT_STATE_EVENTS经API透出（前端按钮可见性真源）；
4. revert_preview拒绝面=非AUDIT_STATE_EVENTS事件（单一真源，不再点名FAIL）。
"""

import sqlite3

import pytest

from src.gene.skill_learner import SkillLearner


def _tool_calls(*names):
    return [
        {"function": {"name": n, "arguments": {"command": "echo hi", "content": "x" * 20}}}
        for n in names
    ]


def _add_skills(learner: SkillLearner, n: int) -> list[str]:
    """造n个互不相同的技能（任务描述带序号，避开近重复合并）。"""
    ids = []
    for i in range(n):
        s = learner.extract_from_execution(
            f"sess-page-{i}",
            f"库存分页测试任务第{i}号独特描述",
            _tool_calls("read_file", "write_file"),
            success=True,
        )
        assert s is not None
        ids.append(s.skill_id)
    assert len(set(ids)) == n
    return ids


@pytest.fixture
def learner(tmp_path):
    return SkillLearner(db_path=str(tmp_path / "skills.db"))


class TestCountSkills:
    def test_count_zero_on_empty(self, learner):
        assert learner.count_skills() == 0

    def test_count_is_total_not_page_len(self, learner):
        """total=库存真实总数，与受limit截断的len(本页)区分（122 vs 100的病根）。"""
        _add_skills(learner, 5)
        page = learner.list_skills(limit=2)
        assert len(page) == 2  # 本页被limit截断
        assert learner.count_skills() == 5  # 总数不跟着截断说谎

    def test_count_respects_category_filter(self, learner):
        """category过滤口径与list_skills一致（total与本页同一个过滤面）。"""
        _add_skills(learner, 3)
        # 全部为general类（_classify由描述推断），改一个到coding类
        with sqlite3.connect(learner.db_path) as conn:
            conn.execute("UPDATE learned_skills SET category = 'coding'")
            conn.execute(
                "UPDATE learned_skills SET category = 'general'"
                " WHERE skill_id = (SELECT MIN(skill_id) FROM learned_skills)"
            )
        assert learner.count_skills(category="coding") == 2
        assert learner.count_skills(category="general") == 1
        assert learner.count_skills(category="nope") == 0
        # 与list_skills同口径：过滤后条数=total
        assert len(learner.list_skills(category="coding", limit=100)) == learner.count_skills(
            category="coding"
        )


class TestOffsetPaging:
    def test_pages_cover_all_without_dup_or_gap(self, learner):
        """limit=2翻3页：5个技能每个恰好出现一次（无重复无遗漏）。"""
        ids = set(_add_skills(learner, 5))
        seen = []
        offset = 0
        while True:
            page = learner.list_skills(limit=2, offset=offset)
            if not page:
                break
            seen.extend(r["skill_id"] for r in page)
            offset += len(page)
        assert len(seen) == 5
        assert set(seen) == ids
        assert len(set(seen)) == len(seen)

    def test_offset_beyond_end_empty(self, learner):
        _add_skills(learner, 2)
        assert learner.list_skills(limit=10, offset=50) == []


class TestListEndpointPaginationShape:
    def test_api_total_has_more_revertible_events(self, gene_client):
        """API面：total/offset/limit/has_more真值 + revertible_events单一真源透出。"""
        for i in range(5):
            gene_client.post(
                "/api/gene/skill/extract",
                json={
                    "task_description": f"端点分页测试任务第{i}号独特描述",
                    "execution_log": (
                        '[{"function": {"name": "read_file", "arguments": {"path": "a.txt"}}},'
                        ' {"function": {"name": "write_file", "arguments": {"path": "b.txt"}}}]'
                    ),
                    "success": True,
                },
            )

        r = gene_client.get("/api/gene/skill", params={"limit": 2, "offset": 0})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == 2  # 本页2条（兼容字段=本页条数）
        assert body["total"] == 5  # 真实库存5条（分页真值，不许说谎）
        assert body["offset"] == 0 and body["limit"] == 2
        assert body["has_more"] is True
        # 可回滚事件集=后端AUDIT_STATE_EVENTS单一真源（前端按钮可见性据此判断）
        assert body["revertible_events"] == list(SkillLearner.AUDIT_STATE_EVENTS)
        assert "FAIL" not in body["revertible_events"]

        r2 = gene_client.get("/api/gene/skill", params={"limit": 2, "offset": 4})
        body2 = r2.json()
        assert body2["count"] == 1 and body2["total"] == 5
        assert body2["has_more"] is False  # 末页不再说"还有更多"

    def test_api_empty_db_shape(self, gene_client):
        r = gene_client.get("/api/gene/skill")
        body = r.json()
        assert body["skills"] == [] and body["count"] == 0
        assert body["total"] == 0 and body["has_more"] is False


class TestRevertibleEventsSingleSource:
    def test_revert_preview_rejects_non_state_events(self, learner):
        """拒绝面=非AUDIT_STATE_EVENTS（单一真源），不再点名FAIL——新增非状态事件自动被拒。"""
        (sid,) = _add_skills(learner, 1)
        with sqlite3.connect(learner.db_path) as conn:
            conn.execute(
                "INSERT INTO skill_audit (skill_id, event, actor, reason, created_at)"
                " VALUES (?, 'FAIL', 'test', 'x', 1.0)",
                (sid,),
            )
            conn.execute(
                "INSERT INTO skill_audit (skill_id, event, actor, reason, created_at)"
                " VALUES (?, 'NOTE', 'test', 'x', 2.0)",
                (sid,),
            )
        # FAIL与假想的非状态事件NOTE都被拒（membership判定，非硬编码名单）
        for event_name in ("FAIL", "NOTE"):
            with sqlite3.connect(learner.db_path) as conn:
                aid = conn.execute(
                    "SELECT audit_id FROM skill_audit WHERE skill_id = ? AND event = ?",
                    (sid, event_name),
                ).fetchone()[0]
            with pytest.raises(ValueError):
                learner.revert_preview(sid, audit_id=aid)

    def test_state_events_still_revertible(self, learner):
        """ADD等状态变更事件照常可回滚（拒绝面收窄不误伤既有回滚语义）。"""
        (sid,) = _add_skills(learner, 1)
        with sqlite3.connect(learner.db_path) as conn:
            aid = conn.execute(
                "SELECT audit_id FROM skill_audit WHERE skill_id = ? AND event = 'ADD'",
                (sid,),
            ).fetchone()[0]
        preview = learner.revert_preview(sid, audit_id=aid)
        assert preview is not None and preview["action"] == "delete"
