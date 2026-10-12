"""F4b-㉚ 失败经验风险轴接线——brain think 风险评估「历史失败经验」缝真消费 experiences 真源。

测的是 src/learn/experience.py::ExperienceMemory._compute_relevance（写读语义对齐
的纯函数相关度）+ get_similar_failures（数据面）+ src/cortex/risk_assessor.py 经验
缝（含故障降级）+ src/api/brain.py::think 全链（RiskAssessor 第二参真喂
ExperienceMemory——修前硬编码 None=消费缝构造上不可达）。

数据面全 tmp（SQLitePool/tmp experiences 表，绝不写生产 data/opensoul.db）。
"""

import asyncio
import sqlite3
import time
from datetime import datetime, timedelta

import pytest

from src.learn.experience import ExperienceMemory
from src.learn.long_term import LITERAL_OVERLAP_THRESHOLD, literal_overlap
from src.models.cognitive import Experience, Intent, TaskContext

_PROBE = "帮我写一个Python二分查找"
_UNRELATED = "生成一份季度销售报表模板xyzabc9527"


def _exp(
    intent_summary=_PROBE,
    action="act",
    outcome: str = "failure",
    age_days=1.0,
):
    return Experience(
        action=action,
        intent_summary=intent_summary,
        outcome=outcome,  # type: ignore[arg-type]
        timestamp=datetime.now() - timedelta(days=age_days),
    )


def _intent(user_prompt=_PROBE, target_files=None, goal="fix_bug"):
    return Intent(
        user_prompt=user_prompt,
        target_files=list(target_files or []),
        modify_scope="single_file",
        goal=goal,
        change_size="small",
    )


# ── ① 相关度纯函数（写读语义对齐，接线前定形）────────────────────────


class TestComputeRelevance:
    def test_goal_axis_match(self):
        """goal 枚举精确轴保留（修前既有语义）：匹配→0.5×衰减。"""
        mem = ExperienceMemory()
        score = mem._compute_relevance(
            _exp(intent_summary="fix_bug", outcome="success"), _intent(user_prompt=_UNRELATED)
        )
        assert 0.4 < score < 0.5

    def test_file_axis_checks_action_and_intent_summary(self):
        """文件轴同时查 action 与 intent_summary（生产 action 常是不透明 ID）。"""
        mem = ExperienceMemory()
        from_action = mem._compute_relevance(
            _exp(intent_summary="改点东西", action="edit conf.ini", outcome="success"),
            _intent(user_prompt=_UNRELATED, target_files=["conf.ini"]),
        )
        from_summary = mem._compute_relevance(
            _exp(
                intent_summary="修改 conf.ini 配置文件", action="chat_turn:om-x", outcome="success"
            ),
            _intent(user_prompt=_UNRELATED, target_files=["conf.ini"]),
        )
        assert from_action > 0.25
        assert from_summary > 0.25

    def test_literal_axis_exact_match(self):
        """字面轴：任务原文==intent_summary → 重合率 1.0（写读对齐，修前恒 0 命中）。"""
        mem = ExperienceMemory()
        score = mem._compute_relevance(_exp(outcome="success", age_days=1.0), _intent())
        assert score > 0.9  # 1.0 匹配分 × ~0.96 衰减

    def test_unrelated_is_exactly_zero(self):
        """无关恒 0 分（宁缺勿滥硬断言）——修前 failure 补贴会给出 0.15 伪分。"""
        mem = ExperienceMemory()
        assert mem._compute_relevance(_exp(intent_summary=_UNRELATED), _intent()) == 0.0

    def test_literal_below_threshold_not_counted(self):
        """字面重合率低于门槛=零分（与 F2b-② 同门槛单一源）。"""
        weak = "海洋天气预报"
        assert literal_overlap(_PROBE, weak) < LITERAL_OVERLAP_THRESHOLD
        mem = ExperienceMemory()
        assert mem._compute_relevance(_exp(intent_summary=weak), _intent()) == 0.0

    def test_failure_bonus_after_decay_and_age_ordering(self):
        """失败补贴在衰减之后（差恒 0.15，弱匹配不触顶）；老行分数低于新行。"""
        mem = ExperienceMemory()
        fail = mem._compute_relevance(
            _exp(intent_summary="fix_bug", outcome="failure", age_days=1.0), _intent()
        )
        ok = mem._compute_relevance(
            _exp(intent_summary="fix_bug", outcome="success", age_days=1.0), _intent()
        )
        assert abs((fail - ok) - 0.15) < 1e-9
        old = mem._compute_relevance(
            _exp(intent_summary="fix_bug", outcome="failure", age_days=29.0), _intent()
        )
        assert old < fail
        # 衰减下限 0.1 生效：29 天前 goal 匹配 failure=0.5×0.1+0.15=0.20
        assert old == pytest.approx(0.2)


# ── ② 数据面（tmp 真 SQLitePool）────────────────────────────────────


async def _seed_and_query(tmp_db, rows, query_intent):
    """tmp experiences 表播种 + ExperienceMemory 真查询。rows=(action,intent_summary,outcome,age_days)。"""
    from src.database.postgres import SQLitePool

    conn = sqlite3.connect(tmp_db)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS experiences (
               id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL,
               agent_id TEXT NOT NULL, action TEXT NOT NULL,
               intent_summary TEXT NOT NULL, outcome TEXT NOT NULL, error TEXT,
               fix TEXT, relevance_score REAL DEFAULT 1.0, created_at REAL NOT NULL,
               metadata TEXT DEFAULT '{}', source_ref TEXT)"""
    )
    for action, summary, outcome, age_days in rows:
        conn.execute(
            "INSERT INTO experiences (tenant_id, agent_id, action, intent_summary,"
            " outcome, error, fix, relevance_score, created_at)"
            " VALUES ('default','default',?,?,?,'err',NULL,1.0,?)",
            (action, summary, outcome, time.time() - age_days * 86400),
        )
    conn.commit()
    conn.close()

    pool = SQLitePool()
    await pool.connect()
    try:
        mem = ExperienceMemory(pool, "default", "default")
        failures = await mem.get_similar_failures(query_intent)
        relevant = await mem.get_relevant_experience(query_intent)
        return failures, relevant
    finally:
        await pool.disconnect()


class TestExperienceMemoryQuery:
    def _run(self, tmp_path, monkeypatch, rows, query_intent):
        from src.config import settings

        tmp_db = tmp_path / "opensoul.db"
        monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_db}")
        return asyncio.run(_seed_and_query(tmp_db, rows, query_intent))

    def test_similar_failures_only_failure_rows(self, tmp_path, monkeypatch):
        rows = [
            ("chat_turn:om-a", _PROBE, "failure", 1.0),
            ("chat_turn:om-b", _PROBE, "failure", 1.0),
            ("chat_turn:om-c", _PROBE, "success", 1.0),
            ("chat_turn:om-d", _PROBE, "success", 1.0),
        ]
        failures, relevant = self._run(tmp_path, monkeypatch, rows, _intent())
        assert len(failures) == 2
        assert all(e.outcome == "failure" for e in failures)
        assert len(relevant) == 4

    def test_production_shape_rows_match(self, tmp_path, monkeypatch):
        """生产行形态（action=chat_turn:ID、intent_summary=任务原文自由文本）真命中——
        修前 goal 枚举精确轴对生产数据恒 0 命中（写读语义错配）。"""
        rows = [("chat_turn:om-0822", _PROBE, "failure", 1.0)]
        failures, _ = self._run(tmp_path, monkeypatch, rows, _intent())
        assert len(failures) == 1

    def test_old_strong_match_survives_weak_ages_out(self, tmp_path, monkeypatch):
        """30 天前：强匹配（重合 1.0）仍算数，弱相关（重合<0.5）随时间淡出。"""
        rows = [
            ("chat_turn:om-old-strong", _PROBE, "failure", 30.0),
            ("chat_turn:om-old-weak", "帮我写一个Python快速排序算法演示", "failure", 30.0),
        ]
        failures, _ = self._run(tmp_path, monkeypatch, rows, _intent())
        assert [e.action for e in failures] == ["chat_turn:om-old-strong"]

    def test_failures_not_shadowed_by_recency_window(self, tmp_path, monkeypatch):
        """回归锁死：top-100 近期窗口不得挡住窗口外的相似失败（live 修前实证：
        963 行里 22 条 failure 全在窗口外，get_similar_failures 恒空集）。"""
        rows = [("chat_turn:om-new", f"新鲜成功任务{i}", "success", 0.01) for i in range(120)]
        rows.append(("chat_turn:om-old-fail", _PROBE, "failure", 25.0))
        failures, _ = self._run(tmp_path, monkeypatch, rows, _intent())
        assert [e.action for e in failures] == ["chat_turn:om-old-fail"]


# ── ③ RiskAssessor 经验缝 ───────────────────────────────────────────


class _FakeMemory:
    def __init__(self, items=None, boom=False):
        self.items = items or []
        self.boom = boom

    async def get_similar_failures(self, intent):
        if self.boom:
            raise RuntimeError("db exploded")
        return self.items


class TestRiskAssessorAxis:
    def _assess(self, memory, count=1):
        from src.cortex.risk_assessor import RiskAssessor

        assessor = RiskAssessor(None, memory)  # type: ignore[arg-type]
        return asyncio.run(
            assessor.assess(_intent(), TaskContext(user_input=_PROBE, intent=_intent()))
        )

    def test_history_risk_levels(self):
        one = self._assess(_FakeMemory([_exp()] * 1))
        three = self._assess(_FakeMemory([_exp()] * 3))
        assert [r for r in one.risks if r.title == "历史失败经验"][0].level == "medium"
        assert [r for r in three.risks if r.title == "历史失败经验"][0].level == "high"

    def test_no_experience_memory_no_risk_control(self):
        """修前接线（第二参 None）反证：有数据也永远不出「历史失败经验」。"""
        result = self._assess(None)
        assert [r for r in result.risks if r.title == "历史失败经验"] == []

    def test_read_failure_degrades_visible_not_blocking(self):
        """读取故障→跳过该轴但风险评估照常（F2b-②c-1 故障可见不阻断纪律）。"""
        result = self._assess(_FakeMemory(boom=True))
        assert [r for r in result.risks if r.title == "历史失败经验"] == []
        assert result.overall_level in ("low", "medium", "high", "critical")


# ── ④ think HTTP 全链（修前硬反证：None 接线下恒不可达）──────────────


@pytest.fixture()
def risk_client(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.brain as brain_api
    import src.database.postgres as pg_mod
    from src.config import settings
    from src.database.postgres import SQLitePool

    tmp_db = tmp_path / "opensoul.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_db}")
    tmp_pool = SQLitePool()
    monkeypatch.setattr(pg_mod, "db_pool", tmp_pool)

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()
        yield
        await tmp_pool.disconnect()

    app = FastAPI(lifespan=_lifespan)
    app.include_router(brain_api.router, prefix="/api/brain")
    with TestClient(app) as c:
        yield c, tmp_db


def _seed(db_file, summaries, outcome="failure", action="chat_turn:om-seed"):
    conn = sqlite3.connect(db_file)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS experiences (
               id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL,
               agent_id TEXT NOT NULL, action TEXT NOT NULL,
               intent_summary TEXT NOT NULL, outcome TEXT NOT NULL, error TEXT,
               fix TEXT, relevance_score REAL DEFAULT 1.0, created_at REAL NOT NULL,
               metadata TEXT DEFAULT '{}', source_ref TEXT)"""
    )
    for i, summary in enumerate(summaries):
        conn.execute(
            "INSERT INTO experiences (tenant_id, agent_id, action, intent_summary,"
            " outcome, error, fix, relevance_score, created_at)"
            " VALUES ('default','default',?,?,?,'err',NULL,1.0,?)",
            (f"{action}-{i}", summary, outcome, time.time() - 86400),
        )
    conn.commit()
    conn.close()


class TestThinkHTTP:
    def test_axis_live_end_to_end(self, risk_client):
        """3 条相似失败→「历史失败经验」high→决策真受影响（confirm_required）。"""
        client, tmp_db = risk_client
        _seed(tmp_db, [_PROBE, _PROBE, _PROBE])
        resp = client.post("/api/brain/think", json={"user_input": _PROBE})
        assert resp.status_code == 200
        body = resp.json()
        hist = [r for r in body["risk"]["risks"] if r["title"] == "历史失败经验"]
        assert hist and hist[0]["level"] == "high"
        assert "3" in hist[0]["desc"]
        assert body["decision"]["execute_mode"] == "confirm_required"

    def test_unrelated_zero_inject(self, risk_client):
        """无关播种→零注入（宁缺勿滥），决策面不变（auto）。"""
        client, tmp_db = risk_client
        _seed(tmp_db, [_PROBE, _PROBE, _PROBE])
        resp = client.post("/api/brain/think", json={"user_input": _UNRELATED})
        body = resp.json()
        assert [r for r in body["risk"]["risks"] if r["title"] == "历史失败经验"] == []
        assert body["decision"]["execute_mode"] == "auto"

    def test_a1_in_process_direct_call_regression(self, risk_client, monkeypatch):
        """A1 进程内直调（不带 authorization）契约不变，同一 tmp 数据面真连接。"""
        import src.database.postgres as pg_mod
        from src.api.brain import ThinkRequest
        from src.api.brain import think as brain_think
        from src.database.postgres import SQLitePool

        client, tmp_db = risk_client
        _seed(tmp_db, [_PROBE])

        async def _call():
            pool = SQLitePool()
            await pool.connect()
            monkeypatch.setattr(pg_mod, "db_pool", pool)
            try:
                return await brain_think(ThinkRequest(user_input=_PROBE))
            finally:
                await pool.disconnect()

        result = asyncio.run(_call())
        assert set(result["decision"].keys()) == {"edit_mode", "execute_mode", "confirm_prompt"}
        # 进程内直调同样吃到失败经验轴（1 条相似失败→medium→confirm_required）
        assert result["decision"]["execute_mode"] == "confirm_required"
        assert [r for r in result["risk"]["risks"] if r["title"] == "历史失败经验"]
