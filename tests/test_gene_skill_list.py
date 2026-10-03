"""gene技能库库存列表：GET /api/gene/skill + SkillLearner.list_skills（回滚UI/审计视图枚举端）。

缺口背景（多轮dev-reports挂账"前端无回滚UI/审计视图"的第一块地基）：gene API此前
只有"按任务检索"的/skill/recommend，没有"无任务的库存盘点"端点——learned_skills
整库对用户不可枚举，前端无从渲染技能列表/审计历史/回滚入口，回滚API（revert-preview/
revert）成了只有curl能用的能力。本文件钉住：
1. list_skills枚举口径（success_rate单一真源回填、tags/parameters解JSON、最近活动序）；
2. category过滤 + limit/offset分页；
3. API面真实走router（conftest gene_client隔离库，测试绝不写生产skills.db）。
"""

import sqlite3

import pytest

from src.gene.skill_learner import SkillLearner


def _tool_calls(*names):
    return [
        {"function": {"name": n, "arguments": {"command": "echo hi", "content": "x" * 20}}}
        for n in names
    ]


@pytest.fixture
def learner(tmp_path):
    return SkillLearner(db_path=str(tmp_path / "skills.db"))


class TestListSkillsEnumeration:
    def test_empty_db_returns_empty_list(self, learner):
        assert learner.list_skills() == []

    def test_success_rate_single_source_and_json_fields(self, learner):
        """success_rate与find_relevant同口径回填；tags/parameters解成list。"""
        s = learner.extract_from_execution(
            "sess-a", "库存任务甲", _tool_calls("read_file", "write_file"), success=True
        )
        assert s is not None
        learner.record_usage(s.skill_id, success=False)  # 2用1成 → 0.5

        rows = learner.list_skills()
        assert len(rows) == 1
        row = rows[0]
        assert row["skill_id"] == s.skill_id
        assert row["usage_count"] == 2 and row["success_count"] == 1
        assert row["success_rate"] == 0.5
        assert isinstance(row["tags"], list) and isinstance(row["parameters"], list)
        # find_relevant同口径（_success_rate单一真源）——两面数字不许各说各话
        fr = next(
            r for r in learner.find_relevant("库存任务甲", limit=5) if r["skill_id"] == s.skill_id
        )
        assert fr["success_rate"] == row["success_rate"]

    def test_usage_zero_success_rate_zero_not_faked(self, learner):
        """usage=0的行success_rate=0.0（没证据不冒充全胜/全败）。"""
        with sqlite3.connect(learner.db_path) as conn:
            conn.execute(
                "INSERT INTO learned_skills (skill_id, name, usage_count, success_count,"
                " created_at) VALUES ('raw-0', '零使用行', 0, 0, 1.0)"
            )
        row = learner.list_skills()[0]
        assert row["usage_count"] == 0 and row["success_rate"] == 0.0

    def test_ordering_most_recent_activity_first(self, learner):
        """最近活动（last_used_at，未用过按created_at）新→旧。"""
        a = learner.extract_from_execution(
            "sess-a", "库存任务A", _tool_calls("read_file", "write_file"), success=True
        )
        b = learner.extract_from_execution(
            "sess-b", "库存任务B", _tool_calls("terminal", "read_file"), success=True
        )
        assert [r["skill_id"] for r in learner.list_skills()] == [b.skill_id, a.skill_id]

        learner.record_usage(a.skill_id, success=True)  # A被重新激活→排最前
        assert [r["skill_id"] for r in learner.list_skills()] == [a.skill_id, b.skill_id]

    def test_category_filter_and_pagination(self, learner):
        learner.extract_from_execution(
            "sess-1", "读取文件并写入文件", _tool_calls("read_file", "write_file"), success=True
        )
        learner.extract_from_execution(
            "sess-2", "搜索网页信息", _tool_calls("web_search", "read_file"), success=True
        )
        files = learner.list_skills(category="file_operations")
        assert files and all(r["category"] == "file_operations" for r in files)
        web = learner.list_skills(category="web_operations")
        assert web and all(r["category"] == "web_operations" for r in web)
        assert learner.list_skills(category="nonexistent") == []

        all_rows = learner.list_skills(limit=1)
        assert len(all_rows) == 1
        rest = learner.list_skills(limit=10, offset=1)
        assert len(rest) == 1 and rest[0]["skill_id"] != all_rows[0]["skill_id"]


class TestSkillListApi:
    _EXTRACT_PAYLOAD = {
        "task_description": "API库存列表测试",
        "execution_log": (
            '[{"function": {"name": "read_file", "arguments": {"path": "a.txt"}}},'
            ' {"function": {"name": "write_file", "arguments": {"path": "b.txt"}}}]'
        ),
        "success": True,
    }

    def test_list_endpoint_empty_then_after_extract(self, gene_client):
        r = gene_client.get("/api/gene/skill")
        assert r.status_code == 200, r.text
        assert r.json() == {"skills": [], "count": 0}

        r = gene_client.post("/api/gene/skill/extract", json=self._EXTRACT_PAYLOAD)
        assert r.status_code == 200, r.text
        skill_id = r.json()["skill_id"]

        r = gene_client.get("/api/gene/skill")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == 1
        assert body["skills"][0]["skill_id"] == skill_id
        assert body["skills"][0]["success_rate"] == 1.0
        assert isinstance(body["skills"][0]["tags"], list)

    def test_usage_receipt_reflected_in_listing(self, gene_client):
        """使用遥测漂移在列表面可见（record_usage→GET /skill数字如实变）。"""
        skill_id = gene_client.post("/api/gene/skill/extract", json=self._EXTRACT_PAYLOAD).json()[
            "skill_id"
        ]
        r = gene_client.post(f"/api/gene/skill/{skill_id}/usage", json={"success": False})
        assert r.status_code == 200, r.text

        row = next(
            s
            for s in gene_client.get("/api/gene/skill").json()["skills"]
            if s["skill_id"] == skill_id
        )
        assert row["usage_count"] == 2 and row["success_count"] == 1
        assert row["success_rate"] == 0.5

    def test_category_query_param_filters(self, gene_client):
        payload = {
            "task_description": "读取文件并写入文件",
            "execution_log": self._EXTRACT_PAYLOAD["execution_log"],
            "success": True,
        }
        gene_client.post("/api/gene/skill/extract", json=payload)
        r = gene_client.get("/api/gene/skill", params={"category": "file_operations"})
        assert r.status_code == 200
        assert r.json()["count"] >= 1
        assert all(s["category"] == "file_operations" for s in r.json()["skills"])
        r = gene_client.get("/api/gene/skill", params={"category": "nope"})
        assert r.json() == {"skills": [], "count": 0}

    def test_list_rows_land_in_isolated_db_not_prod(self, gene_client):
        """列表面只读tmp隔离库（隔离护栏同门：绝不碰生产skills.db）。"""
        tmp_db = gene_client.app.state.gene_skills_db
        assert "opensoul/gene/skills.db" not in tmp_db.replace(
            str(__import__("pathlib").Path.home()), "~"
        )
        with sqlite3.connect(tmp_db) as conn:
            conn.execute(
                "INSERT INTO learned_skills (skill_id, name, usage_count, success_count,"
                " created_at) VALUES ('iso-list-1', '隔离列表行', 0, 0, 1.0)"
            )
        r = gene_client.get("/api/gene/skill")
        assert any(s["skill_id"] == "iso-list-1" for s in r.json()["skills"])
