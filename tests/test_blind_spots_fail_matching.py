"""F4b-㉗ get_blind_spots '%fail%' 子串匹配收口——成败轴单一源精确串判法。

修前缺陷（HEAD 源码实证非推断）：get_blind_spots 用 outcome LIKE '%fail%' 子串
匹配，与同模块其余成败分类（compute_confidence_calibration 的 == 'success'、
confidence_stats 的 outcome = 'success'，F4b-㉕/㉖ 精确串纪律）相悖=同一 outcome
轴三种判法语义漂移；子串假阳（'failproof'/'no-fail'/'fail-safe' 含 'fail' 被计
失败）；LIKE 大小写不敏感（'FAILURE' 被计）与 'success' 轴 BINARY 精确不对称。

测的是 src/mirror/metacognition.py::Metacognition.get_blind_spots（真源读面）+
成败轴单一源常量（SUCCESS_OUTCOME/FAILURE_OUTCOME 写点与读面同源）+
src/api/brain.py::verify（写点常量接线）。数据面零依赖（tmp SQLitePool 全隔离，
绝不写生产 data/opensoul.db）。
"""

import asyncio

import pytest

# ── ① 盲区失败判法：精确串，禁子串误计 ─────────────────────────────


class TestBlindSpotFailMatching:
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

    def test_canonical_failure_counted(self, pool):
        """规范串 'failure' ×2 同一决策 → 盲区真现身（生产数据分类语义零变化面）。"""
        from src.mirror.metacognition import Metacognition

        async def _run():
            await pool.connect()
            meta = Metacognition(pool, "default", "a1")
            await meta.log_outcome("部署流水线", "failure")
            await meta.log_outcome("部署流水线", "failure")
            return await meta.get_blind_spots()

        spots = asyncio.run(_run())
        assert spots == [{"area": "部署流水线", "fail_count": 2}]

    def test_substring_false_positive_not_counted(self, pool):
        """修前反证核心①：含 'fail' 子串的非失败串禁计失败。

        'failproof'/'no-fail'/'fail-safe'/'unfailing' 在旧 LIKE '%fail%' 下全被
        误计失败（各自 ×2 即浮出盲区）；精确串判法下如实零盲区。
        """
        from src.mirror.metacognition import Metacognition

        async def _run():
            await pool.connect()
            meta = Metacognition(pool, "default", "a2")
            for area in ("failproof", "no-fail", "fail-safe", "unfailing"):
                await meta.log_outcome(area, area)
                await meta.log_outcome(area, area)
            return await meta.get_blind_spots()

        spots = asyncio.run(_run())
        assert spots == []  # 修前 4 个盲区全浮出（子串假阳）

    def test_case_exact_upper_failure_not_counted(self, pool):
        """修前反证核心②：'FAILURE' 大写禁计失败（LIKE 不分大小写与 'success' 轴
        BINARY 精确不对称；精确串判法两轴对称，宁窄勿滥）。"""
        from src.mirror.metacognition import Metacognition

        async def _run():
            await pool.connect()
            meta = Metacognition(pool, "default", "a3")
            await meta.log_outcome("大写探针", "FAILURE")
            await meta.log_outcome("大写探针", "FAILURE")
            return await meta.get_blind_spots()

        spots = asyncio.run(_run())
        assert spots == []  # 修前 LIKE 计入（不分大小写）

    def test_non_canonical_failure_words_not_counted(self, pool):
        """诚实边界锁死：非规范串如实不计失败（宁窄勿滥，F4b-㉔ 闭集精确匹配纪律）。

        'failed'/'fail' 修前被 LIKE '%fail%' 计入；'error'/'timeout' 修前修后均
        不计（生产写点只写 'success'/'failure' 两规范串，此面生产零影响）。若未来
        要扩失败闭集须先改本契约。
        """
        from src.mirror.metacognition import Metacognition

        async def _run():
            await pool.connect()
            meta = Metacognition(pool, "default", "a4")
            for area, outcome in (
                ("failed-领域", "failed"),
                ("fail-领域", "fail"),
                ("error-领域", "error"),
                ("timeout-领域", "timeout"),
            ):
                await meta.log_outcome(area, outcome)
                await meta.log_outcome(area, outcome)
            return await meta.get_blind_spots()

        spots = asyncio.run(_run())
        assert spots == []  # 修前 'failed'/'fail' 两组浮出

    def test_success_never_counted(self, pool):
        """'success' 不计失败（与其余两面 'success' 精确判法同源一致）。"""
        from src.mirror.metacognition import Metacognition

        async def _run():
            await pool.connect()
            meta = Metacognition(pool, "default", "a5")
            for _ in range(3):
                await meta.log_outcome("顺利领域", "success")
            return await meta.get_blind_spots()

        assert asyncio.run(_run()) == []

    def test_threshold_requires_two_failures(self, pool):
        """fail_count >= 2 门槛：1 次失败不浮出（单次≠盲区），2 次浮出。"""
        from src.mirror.metacognition import Metacognition

        async def _run():
            await pool.connect()
            meta = Metacognition(pool, "default", "a6")
            await meta.log_outcome("偶发领域", "failure")
            first = await meta.get_blind_spots()
            await meta.log_outcome("偶发领域", "failure")
            second = await meta.get_blind_spots()
            return first, second

        first, second = asyncio.run(_run())
        assert first == []
        assert second == [{"area": "偶发领域", "fail_count": 2}]

    def test_grouping_ordering_and_limit(self, pool):
        """按决策聚合、fail_count 降序、LIMIT 5（契约延续面，新旧同过）。"""
        from src.mirror.metacognition import Metacognition

        async def _run():
            await pool.connect()
            meta = Metacognition(pool, "default", "a7")
            for i in range(6):  # 6 个领域，失败数 7,6,5,4,3,2
                for _ in range(7 - i):
                    await meta.log_outcome(f"area-{i}", "failure")
            return await meta.get_blind_spots()

        spots = asyncio.run(_run())
        assert len(spots) == 5  # 第 6 个（fail_count=2）被 LIMIT 5 裁掉
        assert [s["fail_count"] for s in spots] == [7, 6, 5, 4, 3]
        assert spots[0]["area"] == "area-0"

    def test_namespace_isolation(self, pool):
        """盲区限 (tenant_id, agent_id) 命名空间：他 agent 的失败不串面。"""
        from src.mirror.metacognition import Metacognition

        async def _run():
            await pool.connect()
            a = Metacognition(pool, "default", "agent-x")
            b = Metacognition(pool, "default", "agent-y")
            for _ in range(2):
                await b.log_outcome("他人的坑", "failure")
            return await a.get_blind_spots(), await b.get_blind_spots()

        ra, rb = asyncio.run(_run())
        assert ra == []
        assert rb == [{"area": "他人的坑", "fail_count": 2}]


# ── ② 成败轴单一源常量：写点与读面同源 ────────────────────────────


class TestOutcomeAxisSingleSource:
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

    def test_canonical_strings_locked(self):
        """规范串本体锁死：改串=改契约，须先过本断言（写点/读面/测试三方同源）。"""
        from src.mirror.metacognition import FAILURE_OUTCOME, SUCCESS_OUTCOME

        assert SUCCESS_OUTCOME == "success"
        assert FAILURE_OUTCOME == "failure"

    def test_read_classification_matches_write_axis(self, pool):
        """读面分类消费的就是写点落账的串：写 failure → 盲区计、写 success → 不计
        （声明从实现派生：两面同一常量真源，禁字面抄写漂移）。"""
        from src.mirror.metacognition import FAILURE_OUTCOME, SUCCESS_OUTCOME, Metacognition

        async def _run():
            await pool.connect()
            meta = Metacognition(pool, "default", "a8")
            await meta.log_outcome("写读同源", FAILURE_OUTCOME)
            await meta.log_outcome("写读同源", FAILURE_OUTCOME)
            await meta.log_outcome("成功面", SUCCESS_OUTCOME)
            await meta.log_outcome("成功面", SUCCESS_OUTCOME)
            return await meta.get_blind_spots()

        spots = asyncio.run(_run())
        assert spots == [{"area": "写读同源", "fail_count": 2}]


# ── ③ HTTP 全链（verify 写点常量接线 + metacognition 读面）──────────


@pytest.fixture()
def blind_client(tmp_path, monkeypatch):
    """test_confidence_calibration_pairing.py 同款路线：测试面=真实 router/
    verify 代码，数据面=tmp SQLitePool 全隔离。"""
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


class TestHttpBlindSpots:
    def test_failed_verifies_surface_blind_spot(self, blind_client):
        """生产写点全链：verify success=false ×2 同 action → 盲区真现身。"""
        for _ in range(2):
            blind_client.post(
                "/api/brain/verify",
                json={
                    "agent_id": "agent-b1",
                    "task_id": "t-b1",
                    "action": "部署流水线",
                    "result": {"success": False},
                },
            )
        meta = blind_client.get("/api/brain/metacognition", params={"agent_id": "agent-b1"}).json()
        assert meta["blind_spots"] == [{"area": "部署流水线", "fail_count": 2}]

    def test_successful_verifies_not_blind_spot(self, blind_client):
        """verify success=true ×2 → 零盲区（'success' 判法与读面同源）。"""
        for _ in range(2):
            blind_client.post(
                "/api/brain/verify",
                json={
                    "agent_id": "agent-b2",
                    "task_id": "t-b2",
                    "action": "顺利部署",
                    "result": {"success": True},
                },
            )
        meta = blind_client.get("/api/brain/metacognition", params={"agent_id": "agent-b2"}).json()
        assert meta["blind_spots"] == []

    def test_write_point_emits_axis_constants(self, monkeypatch, tmp_path):
        """写点常量接线直证：verify 落账的 outcome 串==成败轴单一源常量
        （brain.py 写点与 metacognition.py 读面分类同一真源）。"""
        from contextlib import asynccontextmanager

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import src.api.brain as brain_api
        import src.database.postgres as pg_mod
        import src.mirror.metacognition as meta_mod
        from src.config import settings
        from src.database.postgres import SQLitePool

        recorded = []

        async def _spy(self, decision, outcome, lesson="", ref=""):
            recorded.append(outcome)

        monkeypatch.setattr(meta_mod.Metacognition, "log_outcome", _spy)
        monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path}/opensoul.db")
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
            c.post(
                "/api/brain/verify",
                json={"agent_id": "a", "task_id": "t", "action": "x", "result": {"success": True}},
            )
            c.post(
                "/api/brain/verify",
                json={"agent_id": "a", "task_id": "t", "action": "x", "result": {"success": False}},
            )
        assert recorded == [meta_mod.SUCCESS_OUTCOME, meta_mod.FAILURE_OUTCOME]

    def test_metacognition_response_shape_unchanged(self, blind_client):
        """零回归：metacognition 读面既有键集逐键不变（加性键另拍才许动）。"""
        blind_client.post(
            "/api/brain/think",
            json={"user_input": "hi", "agent_id": "agent-b3", "agent_confidence": 0.8},
        )
        meta = blind_client.get("/api/brain/metacognition", params={"agent_id": "agent-b3"}).json()
        assert set(meta.keys()) == {
            "recent_decisions",
            "blind_spots",
            "calibration",
            "confidence_source",
        }
        assert meta["blind_spots"] == []
