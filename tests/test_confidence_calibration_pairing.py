"""F4b-㉖ get_confidence_calibration 配对缺陷收口——决策行/成败行按 ref 配对校准。

修前结构缺陷（F4b-㉕ 留档）：decision 行（confidence 有值/outcome NULL）与 outcome
行（outcome 有值/confidence NULL）两行无配对键，旧实现按单行同时读 confidence+outcome
（WHERE confidence IS NOT NULL 选出的行 outcome 恒 NULL）→ high_confidence_success_rate
恒 0 → calibration 恒 'miscalibrated'。

配对键真源：think.session_id ↔ verify.task_id（ai_groups 同一 task_id 双写点，
src/api/ai_groups.py think 调用 session_id=task_id / verify 调用 task_id=task_id）。

测的是 src/mirror/metacognition.py::compute_confidence_calibration（配对纯函数）+
Metacognition.get_confidence_calibration（真源读面）+ src/api/brain.py::think|verify
（ref 两写点接线）。数据面零依赖（tmp SQLitePool 全隔离，绝不写生产 data/opensoul.db）。
"""

import asyncio

import pytest


def _dec(id, ref, confidence, t):
    return {"id": id, "ref": ref, "confidence": confidence, "created_at": t}


def _out(id, ref, outcome, t):
    return {"id": id, "ref": ref, "outcome": outcome, "created_at": t}


# ── ① 配对纯函数语义 ──────────────────────────────────────────────


class TestComputeConfidenceCalibration:
    def test_paired_high_confidence_success_rate_not_structurally_zero(self):
        """修前反证核心：配对观测的 high_confidence_success_rate 真值非恒 0
        （旧实现单行读两轴，success 恒 0 → 恒 'miscalibrated'）。"""
        from src.mirror.metacognition import compute_confidence_calibration

        res = compute_confidence_calibration(
            [_dec(1, "t1", 0.9, 10.0)], [_out(2, "t1", "success", 20.0)]
        )
        assert res["paired"] == 1
        assert res["avg_confidence"] == 0.9
        assert res["actual_success_rate"] == 1.0
        assert res["high_confidence_count"] == 1
        assert res["high_confidence_success_rate"] == 1.0  # 修前恒 0
        assert res["calibration"] == "good"  # |0.9-1.0|=0.1 < 0.2
        assert res["unpaired_decisions"] == 0
        assert res["unpaired_outcomes"] == 0

    def test_most_recent_preceding_decision_pairs(self):
        """因果配对：成败行归属它之前最近的决策（多轮 think/verify 不乱串）——
        0.3@10 的旧决策不把均值拉偏，配 0.9@20 的决策。"""
        from src.mirror.metacognition import compute_confidence_calibration

        res = compute_confidence_calibration(
            [_dec(1, "t1", 0.3, 10.0), _dec(2, "t1", 0.9, 20.0)],
            [_out(3, "t1", "success", 30.0)],
        )
        assert res["paired"] == 1
        assert res["avg_confidence"] == 0.9
        assert res["unpaired_decisions"] == 1  # 0.3 的决策未被任何成败观测消费

    def test_outcome_before_any_decision_unpaired(self):
        """成败先于任何同 ref 决策 → 如实不配对（禁拿后来的决策倒配）。"""
        from src.mirror.metacognition import compute_confidence_calibration

        res = compute_confidence_calibration(
            [_dec(1, "t1", 0.9, 20.0)], [_out(2, "t1", "success", 10.0)]
        )
        assert res["paired"] == 0
        assert res["calibration"] == "no_data"
        assert res["avg_confidence"] == 0
        assert res["actual_success_rate"] == 0
        assert res["unpaired_outcomes"] == 1
        assert res["unpaired_decisions"] == 1

    def test_empty_ref_never_pairs(self):
        """空 ref=无配对键，如实不配对（两行无配对禁硬凑）。"""
        from src.mirror.metacognition import compute_confidence_calibration

        res = compute_confidence_calibration(
            [_dec(1, "", 0.9, 10.0), _dec(2, None, 0.8, 11.0)],
            [_out(3, "", "success", 20.0), _out(4, "t-x", "success", 21.0)],
        )
        assert res["paired"] == 0
        assert res["calibration"] == "no_data"
        assert res["unpaired_decisions"] == 2
        assert res["unpaired_outcomes"] == 2

    def test_cross_ref_never_pairs(self):
        """不同 ref（think session_id ≠ verify task_id）禁跨键配对。"""
        from src.mirror.metacognition import compute_confidence_calibration

        res = compute_confidence_calibration(
            [_dec(1, "s1", 0.9, 10.0)], [_out(2, "t1", "success", 20.0)]
        )
        assert res["paired"] == 0
        assert res["unpaired_decisions"] == 1
        assert res["unpaired_outcomes"] == 1

    def test_one_decision_many_outcomes_each_counts(self):
        """一决策可配多成败（同决策下多次 verify=同一预测的多次观测，如实各计）。"""
        from src.mirror.metacognition import compute_confidence_calibration

        res = compute_confidence_calibration(
            [_dec(1, "t1", 0.8, 10.0)],
            [_out(2, "t1", "success", 20.0), _out(3, "t1", "failure", 30.0)],
        )
        assert res["paired"] == 2
        assert res["avg_confidence"] == 0.8
        assert res["actual_success_rate"] == 0.5
        assert res["unpaired_decisions"] == 0

    def test_success_exact_string_only(self):
        """成败精确串 'success' 才计成功（F4b-㉕ 禁子串误计纪律延续）：
        'succeeded'/'partial_success' 只进分母不计成功。"""
        from src.mirror.metacognition import compute_confidence_calibration

        res = compute_confidence_calibration(
            [_dec(1, "t1", 0.5, 10.0), _dec(2, "t2", 0.5, 11.0), _dec(3, "t3", 0.5, 12.0)],
            [
                _out(4, "t1", "success", 20.0),
                _out(5, "t2", "partial_success", 21.0),
                _out(6, "t3", "succeeded", 22.0),
            ],
        )
        assert res["paired"] == 3
        assert res["actual_success_rate"] == 0.33  # 1/3，仅精确 'success'

    def test_miscalibrated_when_prediction_fails(self):
        """高信心全败 → 校准如实 'miscalibrated'（|0.95-0.0|>=0.2）。"""
        from src.mirror.metacognition import compute_confidence_calibration

        res = compute_confidence_calibration(
            [_dec(1, "t1", 0.95, 10.0)], [_out(2, "t1", "failure", 20.0)]
        )
        assert res["calibration"] == "miscalibrated"
        assert res["actual_success_rate"] == 0.0
        assert res["high_confidence_success_rate"] == 0.0
        assert res["high_confidence_count"] == 1

    def test_good_when_prediction_close_to_reality(self):
        """预测均值贴近实际成功率（|0.55-0.5|<0.2）→ 'good'。"""
        from src.mirror.metacognition import compute_confidence_calibration

        res = compute_confidence_calibration(
            [_dec(1, "t1", 0.6, 10.0), _dec(2, "t2", 0.5, 11.0)],
            [_out(3, "t1", "success", 20.0), _out(4, "t2", "failure", 21.0)],
        )
        assert res["paired"] == 2
        assert res["avg_confidence"] == 0.55
        assert res["actual_success_rate"] == 0.5
        assert res["calibration"] == "good"

    def test_no_pairs_no_data_shape(self):
        """配不出任何对 → 'no_data'（校准无从谈起，禁拿无配对数据硬算），
        既有键 avg_confidence=0 / calibration 保留 + 加性计数键。"""
        from src.mirror.metacognition import compute_confidence_calibration

        res = compute_confidence_calibration([], [])
        assert res["calibration"] == "no_data"
        assert res["avg_confidence"] == 0
        assert res["paired"] == 0
        assert res["unpaired_decisions"] == 0
        assert res["unpaired_outcomes"] == 0

    def test_high_threshold_boundary(self):
        """高信心阈值边界：恰 0.7 不算高信心（>0.7 语义延续）。"""
        from src.mirror.metacognition import compute_confidence_calibration

        res = compute_confidence_calibration(
            [_dec(1, "t1", 0.7, 10.0)], [_out(2, "t1", "success", 20.0)]
        )
        assert res["high_confidence_count"] == 0
        assert res["high_confidence_success_rate"] == 0
        assert res["paired"] == 1


# ── ② 账本真源：ref 列自愈 + 写点往返 ──────────────────────────────


class TestLedgerRefColumn:
    @pytest.fixture()
    def pool(self, tmp_path, monkeypatch):
        import src.database.postgres as pg_mod
        from src.config import settings
        from src.database.postgres import SQLitePool

        monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path}/opensoul.db")
        p = SQLitePool()
        monkeypatch.setattr(pg_mod, "db_pool", p)
        yield p
        asyncio.run(p.disconnect())

    def test_legacy_table_self_heal_adds_ref(self, pool):
        """老库（F4b-㉕ 前形态，无 ref 列）→ 幂等补列自愈 + 存量行如实不配对。"""
        from src.mirror.metacognition import Metacognition

        async def _run():
            await pool.connect()
            # 先用老形态 DDL 建表+存量行（decision/outcome 两行无配对键=修前数据）
            await pool.execute(
                """CREATE TABLE IF NOT EXISTS metacognition_log (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       tenant_id TEXT NOT NULL, agent_id TEXT NOT NULL,
                       event_type TEXT NOT NULL, decision TEXT, reasoning TEXT,
                       confidence REAL, outcome TEXT, lesson TEXT,
                       created_at REAL NOT NULL)"""
            )
            await pool.execute(
                "INSERT INTO metacognition_log (tenant_id, agent_id, event_type, decision, confidence, created_at) VALUES ('default', 'a1', 'decision', 'auto', 0.9, 10.0)"
            )
            await pool.execute(
                "INSERT INTO metacognition_log (tenant_id, agent_id, event_type, decision, outcome, created_at) VALUES ('default', 'a1', 'outcome', 'act', 'success', 20.0)"
            )
            meta = Metacognition(pool, "default", "a1")
            res = await meta.get_confidence_calibration()
            cols = await pool.fetch("PRAGMA table_info(metacognition_log)")
            return res, {row["name"] for row in cols}

        res, cols = asyncio.run(_run())
        assert "ref" in cols  # 自愈补列真落库
        # 存量行无配对键 → 如实不配对（禁对老数据虚构配对）
        assert res["paired"] == 0
        assert res["calibration"] == "no_data"
        assert res["unpaired_decisions"] == 1
        assert res["unpaired_outcomes"] == 1

    def test_log_roundtrip_ref_pairs(self, pool):
        """写点 ref 真落库并配对：log_decision/log_outcome 同 ref → 1 对。"""
        from src.mirror.metacognition import Metacognition

        async def _run():
            await pool.connect()
            meta = Metacognition(pool, "default", "a2")
            await meta.log_decision("auto", "reason", 0.9, ref="t-rt")
            await meta.log_outcome("act", "success", ref="t-rt")
            return await meta.get_confidence_calibration()

        res = asyncio.run(_run())
        assert res["paired"] == 1
        assert res["avg_confidence"] == 0.9
        assert res["actual_success_rate"] == 1.0
        assert res["high_confidence_success_rate"] == 1.0

    def test_ensure_table_idempotent(self, pool):
        """重复 _ensure_table 幂等（ALTER 不重复炸 duplicate column）。"""
        from src.mirror.metacognition import Metacognition

        async def _run():
            await pool.connect()
            meta = Metacognition(pool, "default", "a3")
            await meta._ensure_table()
            await meta._ensure_table()
            meta2 = Metacognition(pool, "default", "a3")
            await meta2.log_decision("auto", "r", 0.5, ref="t-idem")
            return await meta2.get_confidence_calibration()

        res = asyncio.run(_run())
        assert res["unpaired_decisions"] == 1  # 表健康、写入正常

    def test_pairing_never_crosses_agent_or_tenant(self, pool):
        """配对键限 (tenant_id, agent_id) 命名空间：他 agent 同 ref 不串对。"""
        from src.mirror.metacognition import Metacognition

        async def _run():
            await pool.connect()
            a = Metacognition(pool, "default", "agent-x")
            b = Metacognition(pool, "default", "agent-y")
            await a.log_decision("auto", "r", 0.9, ref="shared")
            await b.log_outcome("act", "success", ref="shared")
            return await a.get_confidence_calibration(), await b.get_confidence_calibration()

        ra, rb = asyncio.run(_run())
        assert ra["paired"] == 0 and ra["unpaired_decisions"] == 1
        assert rb["paired"] == 0 and rb["unpaired_outcomes"] == 1


# ── ③ HTTP 全链（think/verify 两写点接线 + metacognition 读面）────────


@pytest.fixture()
def conf_client(tmp_path, monkeypatch):
    """test_agent_confidence_source.py 同款路线：测试面=真实 router/think/verify
    代码，数据面=tmp SQLitePool 全隔离。"""
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


class TestHttpPairing:
    def test_production_key_shape_pairs_end_to_end(self, conf_client):
        """ai_groups 生产键形（think session_id=verify task_id=同一 task_id）
        全链配对：配对校准真值可读（修前 high_confidence_success_rate 恒 0）。"""
        conf_client.post(
            "/api/brain/think",
            json={
                "user_input": "帮我修复一个 bug",
                "agent_id": "agent-p",
                "session_id": "task-42",
                "agent_confidence": 0.9,
            },
        )
        conf_client.post(
            "/api/brain/verify",
            json={
                "agent_id": "agent-p",
                "task_id": "task-42",
                "action": "执行动作",
                "result": {"success": True},
            },
        )
        meta = conf_client.get("/api/brain/metacognition", params={"agent_id": "agent-p"}).json()
        cal = meta["calibration"]
        assert cal["paired"] == 1
        assert cal["avg_confidence"] == 0.9
        assert cal["actual_success_rate"] == 1.0
        assert cal["high_confidence_success_rate"] == 1.0  # 修前恒 0 反证
        assert cal["calibration"] == "good"

    def test_unpaired_refs_reported_not_fabricated(self, conf_client):
        """think session_id ≠ verify task_id → 如实 0 对+计数（禁虚构配对）。"""
        conf_client.post(
            "/api/brain/think",
            json={
                "user_input": "帮我修复一个 bug",
                "agent_id": "agent-q",
                "session_id": "s-different",
                "agent_confidence": 0.9,
            },
        )
        conf_client.post(
            "/api/brain/verify",
            json={
                "agent_id": "agent-q",
                "task_id": "t-different",
                "action": "执行动作",
                "result": {"success": True},
            },
        )
        cal = conf_client.get("/api/brain/metacognition", params={"agent_id": "agent-q"}).json()[
            "calibration"
        ]
        assert cal["paired"] == 0
        assert cal["calibration"] == "no_data"
        assert cal["unpaired_decisions"] == 1
        assert cal["unpaired_outcomes"] == 1

    def test_empty_ref_rows_unpaired(self, conf_client):
        """think 无 session_id（ref 空）+ verify 有 task_id → 不配对。"""
        conf_client.post(
            "/api/brain/think",
            json={"user_input": "帮我修复一个 bug", "agent_id": "agent-r", "agent_confidence": 0.9},
        )
        conf_client.post(
            "/api/brain/verify",
            json={
                "agent_id": "agent-r",
                "task_id": "t-r",
                "action": "执行动作",
                "result": {"success": True},
            },
        )
        cal = conf_client.get("/api/brain/metacognition", params={"agent_id": "agent-r"}).json()[
            "calibration"
        ]
        assert cal["paired"] == 0
        assert cal["calibration"] == "no_data"

    def test_two_rounds_pair_by_ref(self, conf_client):
        """两轮 think/verify 同 ref 各配各的：2 对、均值/成功率精确。"""
        for i, ok in enumerate((True, False)):
            conf_client.post(
                "/api/brain/think",
                json={
                    "user_input": "帮我修复一个 bug",
                    "agent_id": "agent-s",
                    "session_id": "task-rr",
                    "agent_confidence": 0.8 if i == 0 else 0.6,
                },
            )
            conf_client.post(
                "/api/brain/verify",
                json={
                    "agent_id": "agent-s",
                    "task_id": "task-rr",
                    "action": "执行动作",
                    "result": {"success": ok},
                },
            )
        cal = conf_client.get("/api/brain/metacognition", params={"agent_id": "agent-s"}).json()[
            "calibration"
        ]
        assert cal["paired"] == 2
        assert cal["avg_confidence"] == 0.7  # (0.8+0.6)/2
        assert cal["actual_success_rate"] == 0.5
        assert cal["unpaired_decisions"] == 0
        assert cal["unpaired_outcomes"] == 0
        # 逐对信心：0.8 对（success）算高信心（>0.7）、0.6 对不算 → 子集恰 1 条成功率 1.0
        # （均值 0.7 不是判定输入，判定逐对进行——首轮断言混淆均值与逐对信心=测试作者错）
        assert cal["high_confidence_count"] == 1
        assert cal["high_confidence_success_rate"] == 1.0
