"""G1-④c calibration→confidence 派生纠偏——失准观测（预测偏差）修正派生先验。

修前（G1-④a 定性③ 纠错面半缺 + F4b-㉕ 留档）：derive_agent_confidence 的 prior
固定 0.8「信心充足」，get_confidence_calibration 测出的系统性预测偏差（自评偏高/
偏低）只进观测口与 prompt 软建议（G1-④a/④b），不修正派生本身——派生值带着同一
偏差继续喂 F4b-㉒ 门禁/㉕ 落账（校准能观测不能纠错在派生轴上的残留）。

纠偏语义（详见 derive_agent_confidence docstring）：失准（'miscalibrated'）时按
预测误差方向（实际成功率−预测均值）修正 prior=根因修（高样本区 outcome 项主导
构造上已校准，失准来源就是固定 prior）；good/no_data/缺参 → 纠偏量 0.0=F4b-㉕
输出字节一致（既有契约零变化）。

测的是 src/mirror/metacognition.py::calibration_prior_correction（纠偏量纯函数）
+ derive_agent_confidence（校准纠偏派生）+ Metacognition 账本写读闭环 +
src/api/brain.py::think（真源读→纠偏→生效信心全链）。数据面零依赖（tmp
SQLitePool 全隔离，绝不写生产 data/opensoul.db）。
"""

import asyncio

import pytest


def _miscalibrated(avg, actual):
    """compute_confidence_calibration 真源形状的失准观测（声明从实现派生）。"""
    return {
        "avg_confidence": avg,
        "high_confidence_count": 0,
        "high_confidence_success_rate": 0,
        "calibration": "miscalibrated",
        "actual_success_rate": actual,
        "paired": 2,
        "unpaired_decisions": 0,
        "unpaired_outcomes": 0,
    }


# ── ① 纠偏量纯函数语义 ────────────────────────────────────────────


class TestCalibrationPriorCorrection:
    def test_overconfident_negative_correction(self):
        """预测偏高（avg 0.9 vs 实际 0.2）→ 纠偏量=实际−预测=−0.7（向下纠）。"""
        from src.mirror.metacognition import calibration_prior_correction

        assert calibration_prior_correction(_miscalibrated(0.9, 0.2)) == -0.7

    def test_underconfident_positive_correction(self):
        """预测偏低（avg 0.2 vs 实际 0.9）→ 纠偏量 +0.7（向上纠）。"""
        from src.mirror.metacognition import calibration_prior_correction

        assert calibration_prior_correction(_miscalibrated(0.2, 0.9)) == 0.7

    def test_good_calibration_no_correction(self):
        """'good' 无失准 → 0.0（无纠偏可言，G1-④a 校准行仅 miscalibrated 出行同语义）。"""
        from src.mirror.metacognition import calibration_prior_correction

        cal = _miscalibrated(0.5, 0.55)
        cal["calibration"] = "good"
        assert calibration_prior_correction(cal) == 0.0

    def test_no_data_no_correction(self):
        """'no_data'（配不出对）→ 0.0（禁拿无配对数据硬算纠偏）。"""
        from src.mirror.metacognition import calibration_prior_correction

        cal = _miscalibrated(0, 0)
        cal["calibration"] = "no_data"
        assert calibration_prior_correction(cal) == 0.0

    def test_missing_or_non_numeric_no_fabrication(self):
        """缺键/非数值/bool/非 dict → 0.0 如实不猜数字（G1-④a 字段缺失如实略纪律）。"""
        from src.mirror.metacognition import calibration_prior_correction

        cases = [
            None,
            "miscalibrated",
            {"calibration": "miscalibrated"},
            {"calibration": "miscalibrated", "avg_confidence": 0.9},
            {"calibration": "miscalibrated", "actual_success_rate": 0.2},
            {"calibration": "miscalibrated", "avg_confidence": "0.9", "actual_success_rate": 0.2},
            {"calibration": "miscalibrated", "avg_confidence": 0.9, "actual_success_rate": None},
            {"calibration": "miscalibrated", "avg_confidence": True, "actual_success_rate": 0.2},
            {"calibration": "miscalibrated", "avg_confidence": 0.9, "actual_success_rate": False},
        ]
        for cal in cases:
            assert calibration_prior_correction(cal) == 0.0, cal


# ── ② 校准纠偏派生语义 ────────────────────────────────────────────


class TestDeriveWithCalibrationCorrection:
    def test_contract_unchanged_without_miscalibration(self):
        """无 calibration/None/good/no_data → 与 F4b-㉕ 输出逐值一致（既有契约零变化）。"""
        from src.mirror.metacognition import derive_agent_confidence

        good = _miscalibrated(0.5, 0.55)
        good["calibration"] = "good"
        nodata = _miscalibrated(0, 0)
        nodata["calibration"] = "no_data"
        for cal in (None, good, nodata):
            assert derive_agent_confidence({}, calibration=cal) == 0.8
            assert (
                derive_agent_confidence({"outcomes": 1, "successes": 0}, calibration=cal) == 0.533
            )
            assert derive_agent_confidence({"outcomes": 3, "successes": 1}, calibration=cal) == 0.52

    def test_overconfident_lowers_derived(self):
        """预测偏高 → 派生下修：raw 0.4 → 纠偏后 0.0（prior 0.8−0.9 触底 0.0）。"""
        from src.mirror.metacognition import derive_agent_confidence

        stats = {"outcomes": 2, "successes": 0}
        raw = derive_agent_confidence(stats)
        corrected = derive_agent_confidence(stats, calibration=_miscalibrated(0.9, 0.0))
        assert raw == 0.4
        assert corrected == 0.0
        assert corrected < raw

    def test_underconfident_raises_derived(self):
        """预测偏低 → 派生上修：raw 0.4 → 纠偏后 0.5（prior 封顶 1.0，k/(n+k) 衰减可见）。"""
        from src.mirror.metacognition import derive_agent_confidence

        stats = {"outcomes": 2, "successes": 0}
        raw = derive_agent_confidence(stats)
        corrected = derive_agent_confidence(stats, calibration=_miscalibrated(0.1, 0.9))
        assert raw == 0.4
        assert corrected == 0.5
        assert corrected > raw

    def test_prior_clamp_floor_and_ceiling(self):
        """effective prior 封顶 [0,1]：偏低超上限 → 1.0（(1+2)/3=1.0）；偏高穿底 → 0.0。"""
        from src.mirror.metacognition import derive_agent_confidence

        hi = derive_agent_confidence(
            {"outcomes": 1, "successes": 1}, calibration=_miscalibrated(0.1, 0.9)
        )
        assert hi == 1.0  # prior 0.8+0.8=1.6 封顶 1.0 → (1+2)/3
        lo = derive_agent_confidence(
            {"outcomes": 1, "successes": 0}, prior=0.1, calibration=_miscalibrated(0.9, 0.0)
        )
        assert lo == 0.0  # prior 0.1−0.9 穿底 0.0 → (0+0)/3

    def test_zero_outcomes_path_uses_effective_prior(self):
        """无成败观测路径同样走生效 prior（纠偏一致应用）；无 calibration 时字节不变。"""
        from src.mirror.metacognition import derive_agent_confidence

        assert (
            derive_agent_confidence({}, prior=0.5, calibration=_miscalibrated(0.5, 0.8)) == 0.8
        )  # 0.5+0.3
        assert derive_agent_confidence({}) == 0.8
        assert derive_agent_confidence({}, prior=0.123456) == 0.123456  # 不四舍五入不改值


# ── ③ 账本写读闭环（tmp SQLitePool 全隔离）────────────────────────


class TestLedgerCorrectionLoop:
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

    def test_overconfident_ledger_corrects_derivation(self, pool):
        """写点→校准→纠偏真闭环：高自评+连败账本 → miscalibrated → 派生真被下修。"""
        from src.mirror.metacognition import (
            Metacognition,
            calibration_prior_correction,
            derive_agent_confidence,
        )

        async def _run():
            await pool.connect()
            meta = Metacognition(pool, "default", "oc")
            await meta.log_decision("auto", "r", 0.9, ref="t1")
            await meta.log_outcome("act", "failure", ref="t1")
            await meta.log_decision("auto", "r", 0.9, ref="t2")
            await meta.log_outcome("act", "failure", ref="t2")
            cal = await meta.get_confidence_calibration()
            stats = await meta.confidence_stats()
            return cal, stats

        cal, stats = asyncio.run(_run())
        assert cal["calibration"] == "miscalibrated"
        assert cal["avg_confidence"] == 0.9
        assert cal["actual_success_rate"] == 0.0
        assert calibration_prior_correction(cal) == -0.9
        raw = derive_agent_confidence(stats)
        corrected = derive_agent_confidence(stats, calibration=cal)
        assert raw == 0.4
        assert corrected == 0.0
        assert corrected < raw

    def test_underconfident_ledger_corrects_derivation(self, pool):
        """反向：低自评+连胜账本 → miscalibrated 偏低 → 派生真被上修。"""
        from src.mirror.metacognition import (
            Metacognition,
            calibration_prior_correction,
            derive_agent_confidence,
        )

        async def _run():
            await pool.connect()
            meta = Metacognition(pool, "default", "uc")
            await meta.log_decision("auto", "r", 0.1, ref="t1")
            await meta.log_outcome("act", "success", ref="t1")
            await meta.log_decision("auto", "r", 0.1, ref="t2")
            await meta.log_outcome("act", "success", ref="t2")
            cal = await meta.get_confidence_calibration()
            stats = await meta.confidence_stats()
            return cal, stats

        cal, stats = asyncio.run(_run())
        assert cal["calibration"] == "miscalibrated"
        assert calibration_prior_correction(cal) == 0.9
        raw = derive_agent_confidence(stats)
        corrected = derive_agent_confidence(stats, calibration=cal)
        assert raw == 0.9
        assert corrected == 1.0
        assert corrected > raw

    def test_unpaired_ledger_zero_correction(self, pool):
        """异 ref 不配对 → no_data → 零纠偏（禁拿无配对数据硬纠）。"""
        from src.mirror.metacognition import (
            Metacognition,
            calibration_prior_correction,
            derive_agent_confidence,
        )

        async def _run():
            await pool.connect()
            meta = Metacognition(pool, "default", "np")
            await meta.log_decision("auto", "r", 0.9, ref="t-dec")
            await meta.log_outcome("act", "failure", ref="t-out")
            cal = await meta.get_confidence_calibration()
            stats = await meta.confidence_stats()
            return cal, stats

        cal, stats = asyncio.run(_run())
        assert cal["calibration"] == "no_data"
        assert calibration_prior_correction(cal) == 0.0
        assert derive_agent_confidence(stats, calibration=cal) == derive_agent_confidence(stats)


# ── ④ think 全链（真 router 真读写点）────────────────────────────


@pytest.fixture()
def conf_client(tmp_path, monkeypatch):
    """brain router + tmp 元认知账本（opensoul.db）+ tmp SQLitePool（lifespan 真连接）。

    test_agent_confidence_source 同款路线：测试面=真实 router/think/verify 代码，
    数据面=tmp（metacognition_log 表随 _ensure_table 真建于 tmp 库）。
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


def _seed_pair(client, agent, ref, declared, ok):
    """一轮真双写点：think（声明信心+session_id=ref）→ verify（成败+task_id=ref）。"""
    r = client.post(
        "/api/brain/think",
        json={
            "user_input": "帮我修复一个 bug",
            "agent_id": agent,
            "session_id": ref,
            "agent_confidence": declared,
        },
    )
    assert r.status_code == 200
    r = client.post(
        "/api/brain/verify",
        json={
            "agent_id": agent,
            "task_id": ref,
            "action": "执行动作",
            "result": {"success": ok},
        },
    )
    assert r.status_code == 200


class TestThinkCalibrationCorrection:
    def test_overconfident_history_corrects_think_confidence(self, conf_client):
        """真双写点全链：高自评+连败（2 对）→ 再 think 未声明 → 生效信心被下修 0.0
        + 纠偏量 −0.9 可观测 + 决策落账自评=纠偏后生效值。"""
        _seed_pair(conf_client, "agent-oc", "t1", 0.9, False)
        _seed_pair(conf_client, "agent-oc", "t2", 0.9, False)
        resp = conf_client.post(
            "/api/brain/think", json={"user_input": "帮我修复一个 bug", "agent_id": "agent-oc"}
        )
        data = resp.json()
        assert data["confidence_correction"] == -0.9
        assert data["agent_confidence"] == 0.0  # raw 0.4 → prior 0.8−0.9 触底后派生 0.0
        meta = conf_client.get("/api/brain/metacognition", params={"agent_id": "agent-oc"}).json()
        assert meta["calibration"]["calibration"] == "miscalibrated"
        assert meta["recent_decisions"][0]["confidence"] == 0.0  # 纠偏值真进 ㉕ 落账

    def test_underconfident_history_corrects_think_confidence(self, conf_client):
        """反向全链：低自评+连胜 → 再 think 未声明 → 生效信心被上修 1.0 + 纠偏量 +0.9。"""
        _seed_pair(conf_client, "agent-uc", "t1", 0.1, True)
        _seed_pair(conf_client, "agent-uc", "t2", 0.1, True)
        resp = conf_client.post(
            "/api/brain/think", json={"user_input": "帮我修复一个 bug", "agent_id": "agent-uc"}
        )
        data = resp.json()
        assert data["confidence_correction"] == 0.9
        assert data["agent_confidence"] == 1.0

    def test_declared_confidence_never_corrected(self, conf_client):
        """声明优先不被纠偏顶替：失准账本下显式声明 0.9 → 生效 0.9、纠偏量恒 0.0。"""
        _seed_pair(conf_client, "agent-dc", "t1", 0.9, False)
        _seed_pair(conf_client, "agent-dc", "t2", 0.9, False)
        resp = conf_client.post(
            "/api/brain/think",
            json={
                "user_input": "帮我修复一个 bug",
                "agent_id": "agent-dc",
                "agent_confidence": 0.9,
            },
        )
        data = resp.json()
        assert data["agent_confidence"] == 0.9
        assert data["confidence_correction"] == 0.0

    def test_no_calibration_observations_contract_unchanged(self, conf_client):
        """无校准观测（no_data）→ 零纠偏、F4b-㉕ 缺省行为逐值不变（修前修后皆过不变面）。"""
        resp = conf_client.post(
            "/api/brain/think", json={"user_input": "帮我修复一个 bug", "agent_id": "agent-nc"}
        )
        data = resp.json()
        assert data["agent_confidence"] == 0.8
        assert data["confidence_correction"] == 0.0

    def test_ledger_failure_fallback_unchanged(self, monkeypatch):
        """账本不可读不阻断思考：回落 0.8 + 纠偏量 0.0（旁路语义零变化）。"""
        import src.database.postgres as pg_mod
        from src.api.brain import ThinkRequest, think

        async def _broken_stats(self):
            raise RuntimeError("ledger down")

        import src.mirror.metacognition as mc

        monkeypatch.setattr(pg_mod, "db_pool", object())
        monkeypatch.setattr(mc.Metacognition, "confidence_stats", _broken_stats)
        req = ThinkRequest(user_input="帮我修复一个 bug", agent_id="agent-bypass")
        data = asyncio.run(think(req))
        assert data["agent_confidence"] == 0.8
        assert data["confidence_correction"] == 0.0
