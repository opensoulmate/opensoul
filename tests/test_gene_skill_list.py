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
        body = r.json()
        assert body["skills"] == [] and body["count"] == 0
        assert body["total"] == 0 and body["has_more"] is False

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
        assert r.json()["skills"] == [] and r.json()["count"] == 0

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


class TestCategoryFacet:
    """分类facet（过滤控件真值源）：categories选项集+计数与count_skills同口径。

    前端过滤chip此前"写了≠接线了"（API支持category参数但无控件消费）；接线的
    前提是facet必须有真值源——不许从分页rows推断选项集（limit截断会漏分类）。
    钉住三条：①facet计数=各分类count_skills（两面数字不许各说各话）②facet不受
    category过滤影响（全局分布语义）而total受影响 ③NULL/空串category归一进
    "general"（facet/过滤/总数三面同源）。
    """

    def test_facet_counts_match_count_skills_and_sum(self, learner):
        learner.extract_from_execution(
            "sess-1", "读取文件并写入文件", _tool_calls("read_file", "write_file"), success=True
        )
        learner.extract_from_execution(
            "sess-2", "搜索网页信息", _tool_calls("web_search", "read_file"), success=True
        )
        learner.extract_from_execution(
            "sess-3", "调试代码测试", _tool_calls("terminal", "read_file"), success=True
        )
        facet = learner.category_counts()
        assert {f["category"] for f in facet} == {"file_operations", "web_operations", "coding"}
        # 每个facet计数与count_skills(过滤)两面一致
        for f in facet:
            assert f["count"] == learner.count_skills(category=f["category"])
        # facet计数之和=库存总数（无漏项无重复）
        assert sum(f["count"] for f in facet) == learner.count_skills() == 3
        # 排序：计数降序、同数分类名典序（稳定可断言）
        assert facet == sorted(facet, key=lambda f: (-f["count"], f["category"]))

    def test_null_and_empty_category_normalized_to_general(self, learner):
        """NULL/空串category按general计——facet、过滤、总数三面数字一致。"""
        with sqlite3.connect(learner.db_path) as conn:
            conn.execute(
                "INSERT INTO learned_skills (skill_id, name, category, usage_count,"
                " success_count, created_at) VALUES ('null-cat', '空分类行', '', 0, 0, 1.0)"
            )
            conn.execute(
                "INSERT INTO learned_skills (skill_id, name, category, usage_count,"
                " success_count, created_at) VALUES ('norm-cat', '正常行', 'coding', 0, 0, 1.0)"
            )
        facet = {f["category"]: f["count"] for f in learner.category_counts()}
        assert facet["general"] == 1 and facet["coding"] == 1
        assert learner.count_skills(category="general") == facet["general"]
        # 过滤面同口径：空串行可被category=general召回，且输出category已归一
        rows = learner.list_skills(category="general")
        assert [r["skill_id"] for r in rows] == ["null-cat"]
        assert rows[0]["category"] == "general"
        # 总数面：2行（facet之和=总数）
        assert sum(facet.values()) == learner.count_skills() == 2

    def test_api_facet_global_while_total_follows_filter(self, gene_client):
        """API面：categories是全局分布（过滤后chip计数不变），total随过滤变。"""
        for desc in ("读取文件并写入文件", "搜索网页信息"):
            gene_client.post(
                "/api/gene/skill/extract",
                json={
                    "task_description": desc,
                    "execution_log": (
                        '[{"function": {"name": "read_file", "arguments": {"path": "a.txt"}}},'
                        ' {"function": {"name": "write_file", "arguments": {"path": "b.txt"}}}]'
                    ),
                    "success": True,
                },
            )
        r_all = gene_client.get("/api/gene/skill")
        assert r_all.status_code == 200, r_all.text
        body_all = r_all.json()
        facet_all = {f["category"]: f["count"] for f in body_all["categories"]}
        assert facet_all == {"file_operations": 1, "web_operations": 1}

        r_f = gene_client.get("/api/gene/skill", params={"category": "file_operations"})
        assert r_f.status_code == 200, r_f.text
        body_f = r_f.json()
        # total/列表随过滤变……
        assert body_f["total"] == 1
        assert all(s["category"] == "file_operations" for s in body_f["skills"])
        # ……但facet不变（全局分布语义：过滤后web_operations chip仍显示1）
        assert {f["category"]: f["count"] for f in body_f["categories"]} == facet_all
        # 空过滤结果的分类：facet与total两面不许打架（facet里有=总数里该有）
        r_none = gene_client.get("/api/gene/skill", params={"category": "nope"})
        assert r_none.json()["total"] == 0
        assert {f["category"]: f["count"] for f in r_none.json()["categories"]} == facet_all
