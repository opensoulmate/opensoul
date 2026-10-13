"""F4b-㉜ get_relevant_experience 零消费复核+接线——think 响应经验观测键
+ 近期窗口跨类截断收口（F4b-㉚ 边界⑥ 留档件）。

测的是 src/learn/experience.py::ExperienceMemory.get_relevant_experience
（扫描面：按 outcome 类分窗，跨类不再互相挤占——修前单条混合 top-100 窗口
被成功行量大挤掉失败行=结构性静默丢）+ src/api/brain.py::think 5d 相关经验
观测键（修前 get_relevant_experience 全仓零消费=wired-but-not-functional）。

数据面全 tmp（SQLitePool/tmp experiences 表，绝不写生产 data/opensoul.db）。
"""

import asyncio
import sqlite3
import time

import pytest

from src.learn.experience import ExperienceMemory
from src.models.cognitive import Intent

_PROBE = "帮我写一个Python二分查找"
_UNRELATED = "生成一份季度销售报表模板xyzabc9527"


def _intent(user_prompt=_PROBE):
    return Intent(
        user_prompt=user_prompt,
        target_files=[],
        modify_scope="single_file",
        goal="fix_bug",
        change_size="small",
    )


def _seed(tmp_db, rows):
    """tmp experiences 表播种。rows=(action,intent_summary,outcome,age_days)。"""
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
            " VALUES ('default','default',?,?,?,'err-probe',NULL,1.0,?)",
            (action, summary, outcome, time.time() - age_days * 86400),
        )
    conn.commit()
    conn.close()


# ── ① 扫描面：跨类不挤占（控制组可证的修前缺陷）────────────────────────


class TestScanWindowCrossClass:
    def _run(self, tmp_path, monkeypatch, rows, query_intent=None):
        from src.config import settings
        from src.database.postgres import SQLitePool

        tmp_db = tmp_path / "opensoul.db"
        _seed(tmp_db, rows)
        monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_db}")
        pool = SQLitePool()

        async def _go():
            await pool.connect()
            try:
                mem = ExperienceMemory(pool, "default", "default")
                return await mem.get_relevant_experience(query_intent or _intent())
            finally:
                await pool.disconnect()

        return asyncio.run(_go())

    def test_failure_not_starved_by_success_volume(self, tmp_path, monkeypatch):
        """修前缺陷硬反证：120 条更新的成功行把 25 天前的强匹配失败挤出混合
        top-100 窗口（失败行只有失败补贴才可能过门槛，查不到=结构性静默丢）；
        分窗后失败类独立扫描=可见。"""
        rows = [
            ("chat_turn:om-s%d" % i, "新鲜无关成功任务%d" % i, "success", 0.01) for i in range(120)
        ]
        rows.append(("chat_turn:om-old-fail", _PROBE, "failure", 25.0))
        got = self._run(tmp_path, monkeypatch, rows)
        assert [e.action for e in got] == ["chat_turn:om-old-fail"]
        assert got[0].outcome == "failure"

    def test_success_not_starved_by_failure_volume(self, tmp_path, monkeypatch):
        """对称缺陷：120 条更新的失败行把 1 天前的强匹配成功挤出混合窗口；
        分窗后非失败类独立扫描=可见。"""
        rows = [
            ("chat_turn:om-f%d" % i, "新鲜无关失败任务%d" % i, "failure", 0.01) for i in range(120)
        ]
        rows.append(("chat_turn:om-day1-succ", _PROBE, "success", 1.0))
        got = self._run(tmp_path, monkeypatch, rows)
        assert [e.action for e in got] == ["chat_turn:om-day1-succ"]
        assert got[0].outcome == "success"

    def test_unrelated_zero_injection(self, tmp_path, monkeypatch):
        """宁缺勿滥不变面：无关行恒 0 注入（门槛语义零变化）。"""
        rows = [("chat_turn:om-u%d" % i, _UNRELATED, "success", 0.5) for i in range(10)]
        rows.append(("chat_turn:om-uf", _UNRELATED, "failure", 0.5))
        assert self._run(tmp_path, monkeypatch, rows) == []

    def test_top_cap_and_order(self, tmp_path, monkeypatch):
        """top-10 上限 + 相关度降序（评分语义零变化）：5 条强匹配按龄区分
        分数，返回全 5 条且降序；再加到 12 条只回 10 条。"""
        rows = [("chat_turn:om-o%d" % i, _PROBE, "success", 1.0 + i) for i in range(5)]
        got = self._run(tmp_path, monkeypatch, rows)
        assert [e.action for e in got] == ["chat_turn:om-o%d" % i for i in range(5)]
        scores = [e.relevance_score for e in got]
        assert scores == sorted(scores, reverse=True)

        rows12 = [("chat_turn:om-p%d" % i, _PROBE, "success", 1.0 + i * 0.1) for i in range(12)]
        got12 = self._run(tmp_path, monkeypatch, rows12)
        assert len(got12) == 10

    def test_db_none_returns_empty(self):
        """不变面：无 db 连接恒空表不炸。"""
        mem = ExperienceMemory()
        assert asyncio.run(mem.get_relevant_experience(_intent())) == []

    def test_scan_bound_truncates_beyond_window(self, tmp_path, monkeypatch):
        """扫描上界如实锁死 A 半（文档化边界非静默语义）：100 条无关新行占满
        失败类 _QUERY_SCAN_LIMIT=100 窗口后，第 101 的强匹配老失败被截断
        （got==[]）。未来改此边界须先改本断言=契约锁死。"""
        rows = [("chat_turn:om-fill%d" % i, _UNRELATED, "failure", 0.01) for i in range(100)]
        rows.append(("chat_turn:om-beyond-bound", _PROBE, "failure", 25.0))
        assert self._run(tmp_path, monkeypatch, rows) == []

    def test_scan_bound_within_window_visible(self, tmp_path, monkeypatch):
        """扫描上界如实锁死 B 半：99 条占位时同一条强匹配老失败恰在窗口内
        =可见（与 A 半成对直接锁边界恰 100/类）。"""
        rows = [("chat_turn:om-fill%d" % i, _UNRELATED, "failure", 0.01) for i in range(99)]
        rows.append(("chat_turn:om-beyond-bound", _PROBE, "failure", 25.0))
        got = self._run(tmp_path, monkeypatch, rows)
        assert [e.action for e in got] == ["chat_turn:om-beyond-bound"]


# ── ② think 接线（真 HTTP 路径：经验观测键加性契约）────────────────────


@pytest.fixture()
def exp_client(tmp_path, monkeypatch):
    """brain router + tmp 库全隔离（conf_client 同款路线）。"""
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
        c.tmp_db = tmp_db  # type: ignore[attr-defined]
        yield c


class TestThinkExperienceWiring:
    def test_think_surfaces_old_failure_despite_recency_pressure(self, exp_client, tmp_path):
        """真执行路径全链：120 条新成功 + 1 条 25 天前强匹配失败——修前 think
        的 experience 面不存在且混合窗口查不到失败行；现失败经验（含错误真因）
        真进 think 响应观测键。"""
        _seed(
            exp_client.tmp_db,
            [
                ("chat_turn:om-s%d" % i, "新鲜无关成功任务%d" % i, "success", 0.01)
                for i in range(120)
            ]
            + [("chat_turn:om-old-fail", _PROBE, "failure", 25.0)],
        )
        r = exp_client.post("/api/brain/think", json={"user_input": _PROBE})
        assert r.status_code == 200
        exp = r.json()["experience"]
        assert exp["count"] == 1
        assert [it["outcome"] for it in exp["items"]] == ["failure"]
        assert exp["items"][0]["intent_summary"] == _PROBE
        assert exp["items"][0]["error"] == "err-probe"
        assert exp["items"][0]["score"] > 0.2

    def test_think_experience_shape_no_match(self, exp_client):
        """空真源恒稳定形状：count=0/items=[] 且无 error 键（禁 fabricate）。"""
        r = exp_client.post("/api/brain/think", json={"user_input": _PROBE})
        assert r.status_code == 200
        exp = r.json()["experience"]
        assert exp == {"count": 0, "items": []}

    def test_think_items_cap_count_total(self, exp_client):
        """items=top-3、count=相关命中总数（加性契约形状）。"""
        _seed(
            exp_client.tmp_db,
            [("chat_turn:om-c%d" % i, _PROBE, "success", 1.0 + i * 0.1) for i in range(5)],
        )
        r = exp_client.post("/api/brain/think", json={"user_input": _PROBE})
        exp = r.json()["experience"]
        assert exp["count"] == 5
        assert len(exp["items"]) == 3

    def test_think_decision_contract_unchanged(self, exp_client):
        """只观测不决策（零变化面）：成功行在场（不进风险轴）decision 照旧
        auto，既有键集全在场——观测键零门禁影响。"""
        _seed(
            exp_client.tmp_db,
            [("chat_turn:om-ok", _PROBE, "success", 1.0)],
        )
        body = exp_client.post("/api/brain/think", json={"user_input": _PROBE}).json()
        for key in ("intent", "risk", "decision", "emotion", "agent_confidence", "memory_count"):
            assert key in body
        assert body["decision"]["execute_mode"] == "auto"
        assert body["experience"]["count"] == 1

    def test_think_experience_fault_visible_not_blocking(self, exp_client, monkeypatch):
        """读取故障可见不阻断（F4b-㉚ 纪律）：experience.error 带真因，思考/
        决策照常。"""
        from src.learn.experience import ExperienceMemory

        async def _boom(self, intent):
            raise RuntimeError("exp db exploded")

        monkeypatch.setattr(ExperienceMemory, "get_relevant_experience", _boom)
        body = exp_client.post("/api/brain/think", json={"user_input": _PROBE}).json()
        assert body["experience"]["count"] == 0
        assert body["experience"]["items"] == []
        assert "exp db exploded" in body["experience"]["error"]
        assert body["decision"]["execute_mode"] in ("auto", "confirm_required", "deny")
