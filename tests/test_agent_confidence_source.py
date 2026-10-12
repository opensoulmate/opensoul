"""F4b-㉕ agent_confidence 生产源派生——元认知账本真源 + 未声明信心派生。

测的是 src/mirror/metacognition.py::derive_agent_confidence（纯函数派生）+
src/mirror/metacognition.py::Metacognition.confidence_stats（真源统计）+
src/api/brain.py::think|verify（两写点落账 + 声明优先/派生兜底）。

真源=think 落决策行（自评信心轴）+ verify 落成败行（成败观测轴）；派生只消费
成败轴（自评进派生=自我循环）。数据面零依赖（tmp SQLitePool 全隔离，绝不写
生产 data/opensoul.db）。
"""

import asyncio

import pytest


@pytest.fixture()
def conf_client(tmp_path, monkeypatch):
    """brain router + tmp 元认知账本（opensoul.db）+ tmp SQLitePool（lifespan 真连接）。

    user_client/heredity_client 同款路线：测试面=真实 router/think/verify 代码，
    数据面=tmp（metacognition_log 表随 _ensure_schema 真建于 tmp 库）。
    """
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
        yield c


# ── ① 纯函数派生语义 ──────────────────────────────────────────────


class TestDeriveAgentConfidence:
    def test_no_observations_prior_default(self):
        """无成败观测 → 0.8 信心充足（F4b-㉒ 缺省行为契约零变化）。"""
        from src.mirror import metacognition

        assert metacognition.derive_agent_confidence({}) == 0.8
        assert (
            metacognition.derive_agent_confidence(
                {"decisions": 0, "outcomes": 0, "successes": 0, "avg_confidence": None}
            )
            == 0.8
        )

    def test_self_assessed_axis_does_not_feed_derivation(self):
        """自评信心（decision 行）刻意不进派生：5 条 0.1 自评但零成败观测仍 0.8
        （信心派生自评=自我循环）。"""
        from src.mirror import metacognition

        stats = {"decisions": 5, "outcomes": 0, "successes": 0, "avg_confidence": 0.1}
        assert metacognition.derive_agent_confidence(stats) == 0.8

    @pytest.mark.parametrize(
        "successes,outcomes,expected",
        [
            (0, 3, 0.32),  # (0 + 2*0.8) / (3 + 2) = 0.32
            (3, 3, 0.92),  # (3 + 1.6) / 5 = 0.92
            (1, 3, 0.52),  # (1 + 1.6) / 5 = 0.52
        ],
    )
    def test_laplace_shrinkage_exact_values(self, successes, outcomes, expected):
        """Laplace 经验收缩逐值精确：(successes + 2*0.8) / (outcomes + 2)。"""
        from src.mirror import metacognition

        stats = {"outcomes": outcomes, "successes": successes}
        assert metacognition.derive_agent_confidence(stats) == expected

    def test_clamp_normalizes_contract(self):
        """0~1 契约归一：畸形统计（successes>outcomes）不产出越界信心
        （越界值进 adjust_decision 会语义失真）。"""
        from src.mirror import metacognition

        assert metacognition.derive_agent_confidence({"outcomes": 1, "successes": 5}) == 1.0


# ── ② think 派生 + 决策落账（HTTP 全链）──────────────────────────────


class TestThinkConfidenceDerivation:
    def test_undeclared_empty_ledger_defaults_08(self, conf_client):
        """未声明 + 空账本 → 派生回落 0.8（行为契约零变化）：中性情绪 + 低风险 → auto。"""
        resp = conf_client.post(
            "/api/brain/think", json={"user_input": "帮我修复一个 bug", "agent_id": "agent-a"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_confidence"] == 0.8
        assert data["decision"]["execute_mode"] == "auto"
        assert data["decision"]["confirm_prompt"] is None

    def test_declared_takes_priority_over_derivation(self, conf_client):
        """声明优先不被派生顶替：账本连败（派生 0.32）但显式声明 0.9 → 生效 0.9。"""
        for _ in range(3):
            r = conf_client.post(
                "/api/brain/verify",
                json={
                    "agent_id": "agent-b",
                    "task_id": "t-b",
                    "action": "执行动作",
                    "result": {"success": False},
                },
            )
            assert r.status_code == 200
        resp = conf_client.post(
            "/api/brain/think",
            json={
                "user_input": "帮我修复一个 bug",
                "agent_id": "agent-b",
                "agent_confidence": 0.9,
            },
        )
        assert resp.json()["agent_confidence"] == 0.9

    def test_low_confidence_gate_reachable_from_track_record(self, conf_client):
        """F4b-㉒ 修前生产不可达的低信心收紧，经真实成败账本可达：连续 3 败 →
        派生 0.32 + 负面情绪 → auto 收紧成 confirm_required（修前未声明恒 0.8
        恒 auto=收紧面死路）。"""
        for _ in range(3):
            r = conf_client.post(
                "/api/brain/verify",
                json={
                    "agent_id": "agent-tr",
                    "task_id": "t-tr",
                    "action": "执行动作",
                    "result": {"success": False},
                },
            )
            assert r.status_code == 200
        resp = conf_client.post(
            "/api/brain/think",
            json={
                "user_input": "担心这个改动有危险，帮我修复 foo.py 的 bug",
                "agent_id": "agent-tr",
            },
        )
        data = resp.json()
        assert data["agent_confidence"] == 0.32
        assert data["decision"]["execute_mode"] == "confirm_required"
        assert "信心不足" in data["decision"]["confirm_prompt"]

    def test_think_logs_decision_row(self, conf_client):
        """think 决策落账（log_decision 修前全仓零调用点）：decision 行恰 1 条、
        自评信心=生效信心、决策值=execute_mode。"""
        resp = conf_client.post(
            "/api/brain/think",
            json={"user_input": "帮我修复一个 bug", "agent_id": "agent-c", "agent_confidence": 0.3},
        )
        assert resp.status_code == 200
        meta = conf_client.get("/api/brain/metacognition", params={"agent_id": "agent-c"}).json()
        assert meta["confidence_source"]["decisions"] == 1
        assert meta["confidence_source"]["avg_confidence"] == 0.3
        assert meta["recent_decisions"][0]["decision"] == resp.json()["decision"]["execute_mode"]

    def test_agent_confidence_out_of_range_422_unchanged(self, conf_client):
        """越界 422 契约不变（Optional 化后校验不放松）——修前修后皆过不变面。"""
        for bad in (1.5, -0.1):
            resp = conf_client.post(
                "/api/brain/think",
                json={"user_input": "hi", "agent_confidence": bad},
            )
            assert resp.status_code == 422, bad

    def test_decision_contract_keys_unchanged(self, conf_client):
        """decision 响应契约键集不变（A1 消费面锁死）——修前修后皆过不变面。"""
        resp = conf_client.post("/api/brain/think", json={"user_input": "帮我修复一个 bug"})
        assert set(resp.json()["decision"]) == {"edit_mode", "execute_mode", "confirm_prompt"}


# ── ③ verify 成败落账（HTTP 全链）────────────────────────────────────


class TestVerifyOutcomeLedger:
    def test_outcome_rows_land_with_success_axis(self, conf_client):
        """verify 成败落账（log_outcome 修前全仓零调用点）：2 成 1 败 →
        outcomes=3 / successes=2（'failure' 不被 '%success%' 误计）。"""
        for ok in (True, True, False):
            r = conf_client.post(
                "/api/brain/verify",
                json={
                    "agent_id": "agent-d",
                    "task_id": "t-d",
                    "action": "执行动作",
                    "result": {"success": ok},
                },
            )
            assert r.status_code == 200
        meta = conf_client.get("/api/brain/metacognition", params={"agent_id": "agent-d"}).json()
        src = meta["confidence_source"]
        assert (src["outcomes"], src["successes"]) == (3, 2)
        assert src["decisions"] == 0  # 未 think 过=决策轴为零，成败轴照常入账

    def test_outcome_ledger_feeds_derivation_end_to_end(self, conf_client):
        """两写点→派生消费真闭环：verify 入账 → think 未声明真读账本派生。"""
        conf_client.post(
            "/api/brain/verify",
            json={
                "agent_id": "agent-e",
                "task_id": "t-e",
                "action": "执行动作",
                "result": {"success": False},
            },
        )
        resp = conf_client.post(
            "/api/brain/think", json={"user_input": "帮我修复一个 bug", "agent_id": "agent-e"}
        )
        # 1 败 0 成 → (0 + 1.6) / 3 = 0.533
        assert resp.json()["agent_confidence"] == 0.533


# ── ④ 账本不可读旁路（不阻断思考）──────────────────────────────────


class TestLedgerBypass:
    def test_unconnected_pool_think_still_works(self, monkeypatch):
        """账本不可读（池未连接）不阻断思考：回落缺省 0.8 + 决策照常返回
        （A1 进程内直调语义延续：think 自身故障绝不吃掉决策面）。"""
        import src.database.postgres as pg_mod
        from src.api.brain import ThinkRequest, think
        from src.database.postgres import SQLitePool

        monkeypatch.setattr(pg_mod, "db_pool", SQLitePool())  # 从未 connect
        req = ThinkRequest(user_input="帮我修复一个 bug", session_id="t-bypass")
        assert req.agent_confidence is None  # 未声明哨兵（F4b-㉕ 契约）
        result = asyncio.run(think(req))
        assert result["agent_confidence"] == 0.8
        assert result["decision"]["execute_mode"] == "auto"
