"""错误分组键归一化 + user_feedback进化闭环测试（20:36轮遗留#3 + 22:05轮遗留#3销账）。

覆盖：normalize_error_key矩阵（breaker/stale/identity/truncate/组合）、
采集侧归一化落库（error列=稳定键、metadata.error_raw=原文）、
SelfEvolution分析侧合并计数（历史存量分裂组修复——阈值作用于合并后计数）、
brain /api/brain/feedback 双写接线（静态+live）、SelfEvolution.feedback_stats。
"""

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.heredity.self_evolution import SelfEvolution
from src.learn.experience_collector import ExperienceCollector, normalize_error_key

JOB_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT DEFAULT 'pending',
    params TEXT DEFAULT '{}', result TEXT, error TEXT DEFAULT '',
    created_at REAL
)
"""


class _AsyncDB:
    """生产适配器注入（test_experience_collector.py同款：测试即生产路径）。"""

    def __init__(self, path: Path):
        self._path = str(path)

    async def _wrapper(self):
        import aiosqlite

        from src.database.postgres import _SQLiteConnection

        conn = await aiosqlite.connect(self._path)
        conn.row_factory = sqlite3.Row
        return _SQLiteConnection(conn), conn

    async def fetch(self, query, *args):
        wrapper, conn = await self._wrapper()
        try:
            return await wrapper.fetch(query, *args)
        finally:
            await conn.close()

    async def execute(self, query, *args):
        wrapper, conn = await self._wrapper()
        try:
            return await wrapper.execute(query, *args)
        finally:
            await conn.close()

    async def fetchval(self, query, *args):
        wrapper, conn = await self._wrapper()
        try:
            return await wrapper.fetchval(query, *args)
        finally:
            await conn.close()


# ----------------------------------------------------------------------
# normalize_error_key 矩阵
# ----------------------------------------------------------------------
class TestNormalizeErrorKey:
    def test_breaker_prefix_stripped(self):
        assert (
            normalize_error_key("[breaker_open] could not convert string to float: 'x'")
            == "could not convert string to float: 'x'"
        )

    def test_double_breaker_prefix_stripped(self):
        assert normalize_error_key("[breaker_open] [breaker_open] err") == "err"

    def test_stale_budget_exhausted_collapses_dynamic_parts(self):
        """(n/m)与job_id逐作业唯一——归一化后坍缩为同一稳定键。"""
        a = (
            "stale recovery: crashed mid-run, retry budget exhausted "
            "(2/2) — requeue via POST /api/will/jobs/job_11aaa/requeue"
        )
        b = (
            "stale recovery: crashed mid-run, retry budget exhausted "
            "(3/5) — requeue via POST /api/will/jobs/job_999zz/requeue"
        )
        assert normalize_error_key(a) == "stale recovery: retry budget exhausted"
        assert normalize_error_key(a) == normalize_error_key(b)

    def test_stale_previous_process_stable(self):
        msg = "stale recovery: previous process exited mid-run"
        assert normalize_error_key(msg) == msg

    def test_breaker_plus_stale_combined(self):
        """requeue后再崩+再熔断的叠加形态：先剥breaker再坍缩stale。"""
        combined = (
            "[breaker_open] stale recovery: crashed mid-run, retry budget "
            "exhausted (1/2) — requeue via POST /api/will/jobs/abc/requeue"
        )
        assert normalize_error_key(combined) == "stale recovery: retry budget exhausted"

    @pytest.mark.parametrize(
        "err",
        [
            "No handler for job type 'test_job'",
            "推理错误: name 'session' is not defined",
            "Job timed out after 300s",
            "eval_case_failed:ds9:c1",
            "",
            None,
        ],
    )
    def test_plain_errors_identity(self, err):
        """无运行时标记的error原样保留（绝不误合并不同根因）。"""
        expected = (err or "").strip()[:200]
        assert normalize_error_key(err) == expected

    def test_long_error_truncated_200(self):
        long_err = "x" * 500
        assert len(normalize_error_key(long_err)) == 200

    def test_stale_unknown_rest_kept_visible(self):
        """未知stale形态不瞎猜：保留rest可见（mem0失败可见）。"""
        out = normalize_error_key("stale recovery: something new happened")
        assert out == "stale recovery: something new happened"


# ----------------------------------------------------------------------
# 采集侧：归一化落库 + error_raw审计
# ----------------------------------------------------------------------
class TestCollectorNormalization:
    def _mk_jobs_db(self, path: Path, rows):
        conn = sqlite3.connect(path)
        conn.execute(JOB_SCHEMA)
        for jid, name, status, error, ts in rows:
            conn.execute(
                "INSERT INTO jobs (id, name, status, error, created_at) VALUES (?,?,?,?,?)",
                (jid, name, status, error, ts),
            )
        conn.commit()
        conn.close()

    def test_collector_writes_normalized_key_and_raw_provenance(self, tmp_path):
        now = time.time()
        self._mk_jobs_db(
            tmp_path / "jobs.db",
            [
                ("j1", "t", "failed", "[breaker_open] ValueError: boom", now),
                ("j2", "t", "failed", "ValueError: boom", now),
                (
                    "j3",
                    "t",
                    "failed",
                    "stale recovery: crashed mid-run, retry budget exhausted "
                    "(1/2) — requeue via POST /api/will/jobs/j3/requeue",
                    now,
                ),
                (
                    "j4",
                    "t",
                    "failed",
                    "stale recovery: crashed mid-run, retry budget exhausted "
                    "(2/2) — requeue via POST /api/will/jobs/j4/requeue",
                    now,
                ),
            ],
        )
        c = ExperienceCollector(
            db_path=str(tmp_path / "main.db"),
            job_db_path=str(tmp_path / "jobs.db"),
            eval_db_path=str(tmp_path / "none.db"),
        )
        c.collect()
        conn = sqlite3.connect(c.db_path)
        conn.row_factory = sqlite3.Row
        rows = {
            r["source_ref"]: dict(r)
            for r in conn.execute("SELECT source_ref, error, metadata FROM experiences")
        }
        conn.close()

        # error列=稳定分组键
        assert rows["job:j1"]["error"] == "ValueError: boom"
        assert rows["job:j2"]["error"] == "ValueError: boom"
        assert rows["job:j3"]["error"] == "stale recovery: retry budget exhausted"
        assert rows["job:j4"]["error"] == "stale recovery: retry budget exhausted"
        # metadata.error_raw=原文审计（mem0：分组键稳定≠丢原文）
        meta1 = json.loads(rows["job:j1"]["metadata"])
        assert meta1["error_raw"] == "[breaker_open] ValueError: boom"
        meta3 = json.loads(rows["job:j3"]["metadata"])
        assert "jobs/j3/requeue" in meta3["error_raw"]
        # 无前缀的行不携带error_raw（metadata不被无谓污染）
        meta2 = json.loads(rows["job:j2"]["metadata"])
        assert "error_raw" not in meta2
        assert meta2 == {"source": "job_queue", "job_id": "j2", "status": "failed"}


# ----------------------------------------------------------------------
# 分析侧：SelfEvolution合并计数（历史存量行无需迁移即正确聚合）
# ----------------------------------------------------------------------
class TestSelfEvolutionMerge:
    def _mk_experiences(self, path: Path, errors: list[str]):
        conn = sqlite3.connect(path)
        c = ExperienceCollector(db_path=str(path), job_db_path="", eval_db_path="")
        c.ensure_schema(conn)
        now = time.time()
        for i, err in enumerate(errors):
            conn.execute(
                "INSERT INTO experiences (tenant_id, agent_id, action, "
                "intent_summary, outcome, error, created_at, metadata, source_ref) "
                "VALUES ('default','default','a','i','failure',?,?,?,?)",
                (err, now - i, "{}", f"norm:{i}"),
            )
        conn.commit()
        conn.close()

    def test_fragmented_groups_merge_to_threshold(self, tmp_path):
        """核心修复断言：2条带前缀+1条裸原文——各自<3不达阈值，
        合并后=3 → 高频失败提案不再漏报。"""
        self._mk_experiences(
            tmp_path / "main.db",
            [
                "[breaker_open] TimeoutError: provider x",
                "[breaker_open] TimeoutError: provider x",
                "TimeoutError: provider x",
            ],
        )
        evo = SelfEvolution(_AsyncDB(tmp_path / "main.db"))
        patterns = asyncio.run(evo._analyze_failure_patterns())
        assert len(patterns) == 1
        assert patterns[0]["error_type"] == "TimeoutError: provider x"
        assert patterns[0]["count"] == 3

    def test_stale_dynamic_variants_merge(self, tmp_path):
        self._mk_experiences(
            tmp_path / "main.db",
            [
                "stale recovery: crashed mid-run, retry budget exhausted "
                "(1/2) — requeue via POST /api/will/jobs/a/requeue",
                "stale recovery: crashed mid-run, retry budget exhausted "
                "(2/2) — requeue via POST /api/will/jobs/b/requeue",
                "stale recovery: crashed mid-run, retry budget exhausted "
                "(3/3) — requeue via POST /api/will/jobs/c/requeue",
            ],
        )
        evo = SelfEvolution(_AsyncDB(tmp_path / "main.db"))
        patterns = asyncio.run(evo._analyze_failure_patterns())
        assert len(patterns) == 1
        assert patterns[0]["error_type"] == "stale recovery: retry budget exhausted"
        assert patterns[0]["count"] == 3

    def test_distinct_root_causes_not_merged(self, tmp_path):
        """归一化绝不误合并不同根因（宁可保守）。"""
        self._mk_experiences(
            tmp_path / "main.db",
            [
                "[breaker_open] ValueError: a",
                "[breaker_open] ValueError: b",
                "ValueError: c",
            ],
        )
        evo = SelfEvolution(_AsyncDB(tmp_path / "main.db"))
        patterns = asyncio.run(evo._analyze_failure_patterns())
        assert patterns == []  # 三种根因各1条，均<3

    def test_below_threshold_after_merge_excluded(self, tmp_path):
        self._mk_experiences(
            tmp_path / "main.db",
            ["[breaker_open] KeyError: k", "KeyError: k"],
        )
        evo = SelfEvolution(_AsyncDB(tmp_path / "main.db"))
        assert asyncio.run(evo._analyze_failure_patterns()) == []

    def test_recommendation_mapping_survives_merge(self, tmp_path):
        self._mk_experiences(
            tmp_path / "main.db",
            [
                "[breaker_open] SyntaxError: bad token",
                "[breaker_open] SyntaxError: bad token",
                "SyntaxError: bad token",
            ],
        )
        evo = SelfEvolution(_AsyncDB(tmp_path / "main.db"))
        patterns = asyncio.run(evo._analyze_failure_patterns())
        assert patterns[0]["recommendation"] == "执行前做语法检查"

    def test_analyze_and_evolve_end_to_end_with_feedback(self, tmp_path):
        """人类反馈→style_adjustment提案全链路（22:05遗留#3的机制验证）：
        user_feedback低分 + SelfEvolution.analyze_and_evolve → style_adjustment。"""
        self._mk_experiences(tmp_path / "main.db", ["errX", "errY"])  # 无高频失败
        conn = sqlite3.connect(tmp_path / "main.db")
        now = time.time()
        for i in range(3):
            conn.execute(
                "INSERT INTO user_feedback (tenant_id, user_id, action, feedback, "
                "rating, created_at) VALUES ('default','default',?,?,?,?)",
                (f"act{i}", "不好用", 2, now - i),
            )
        conn.commit()
        conn.close()
        evo = SelfEvolution(_AsyncDB(tmp_path / "main.db"))
        evolutions = asyncio.run(evo.analyze_and_evolve())
        styles = [e for e in evolutions if e["type"] == "style_adjustment"]
        assert len(styles) == 1
        assert "2.0/5" in styles[0]["reason"]

    def test_feedback_stats_shares_analysis_semantics(self, tmp_path):
        self._mk_experiences(tmp_path / "main.db", [])
        conn = sqlite3.connect(tmp_path / "main.db")
        conn.execute(
            "INSERT INTO user_feedback (tenant_id, user_id, action, feedback, "
            "rating, created_at) VALUES ('default','default','a','f',5,?)",
            (time.time(),),
        )
        conn.commit()
        conn.close()
        evo = SelfEvolution(_AsyncDB(tmp_path / "main.db"))
        stats = asyncio.run(evo.feedback_stats())
        assert stats["feedback_count"] == 1
        assert stats["avg_rating"] == 5.0
        assert stats["negative_trend"] is False

    def test_feedback_stats_empty_table_honest_default(self, tmp_path):
        self._mk_experiences(tmp_path / "main.db", [])
        evo = SelfEvolution(_AsyncDB(tmp_path / "main.db"))
        stats = asyncio.run(evo.feedback_stats())
        assert stats["feedback_count"] == 0
        assert stats["avg_rating"] == 3.0
        assert stats["negative_trend"] is False


# ----------------------------------------------------------------------
# 接线静态校验
# ----------------------------------------------------------------------
class TestWiring:
    def test_learn_init_exports_normalize(self):
        text = Path("src/learn/__init__.py").read_text()
        assert "normalize_error_key" in text

    def test_insert_exp_calls_normalize(self):
        text = Path("src/learn/experience_collector.py").read_text()
        assert "error_key = normalize_error_key(error)" in text
        assert 'meta["error_raw"] = error[:200]' in text

    def test_self_evolution_merges_by_normalized_key(self):
        text = Path("src/heredity/self_evolution.py").read_text()
        assert "from src.learn.experience_collector import normalize_error_key" in text
        # 合并载体从dict[int]升级为dict[{count,last_seen}]（提案生命周期新鲜度门），
        # "按归一化键合并计数"的接线语义不变（行为由TestSelfEvolutionMerge钉住）
        assert 'entry = merged.setdefault(key, {"count": 0, "last_seen": 0.0})' in text
        assert 'entry["count"] += int(row["cnt"])' in text

    def test_brain_feedback_writes_user_feedback_table(self):
        """写了≠接线了：/api/brain/feedback必须调用UserMemory.record_feedback。"""
        text = Path("src/api/brain.py").read_text()
        assert "from src.mind.user_memory import UserMemory" in text
        assert "await um.record_feedback(action" in text
        # F3b-②后契约字面量：tenant/user 为 bind_identity 派生值（自报被收口），
        # UserMemory 与 SelfEvolution 必须同源同 scope
        assert "identity = bind_identity(authorization, req.tenant_id, req.user_id)" in text
        assert "SelfEvolution(db_pool, tenant_id, user_id).feedback_stats()" in text

    def test_brain_get_feedback_endpoint_exists(self):
        text = Path("src/api/brain.py").read_text()
        assert '@router.get("/feedback")' in text
        assert "evolution_facing" in text

    def test_brain_feedback_validates_rating(self):
        text = Path("src/api/brain.py").read_text()
        assert "rating is required and must be an integer 1-5" in text
        assert "action is required" in text


# ----------------------------------------------------------------------
# live API（:8090重启后的真实服务；写生产库cron_smoke_*键，teardown清理）
# ----------------------------------------------------------------------
LIVE_USER = "cron_smoke_feedback"


def _prod_db() -> Path:
    return Path("data/opensoul.db").resolve()


@pytest.fixture
def cleanup_live_feedback():
    yield
    conn = sqlite3.connect(_prod_db())
    try:
        conn.execute("DELETE FROM user_feedback WHERE user_id=?", (LIVE_USER,))
        conn.commit()
    finally:
        conn.close()


class TestBrainFeedbackLive:
    def test_post_feedback_dual_write_and_evolution_facing(self, client, cleanup_live_feedback):
        resp = client.post(
            "/api/brain/feedback",
            json={
                "user_id": LIVE_USER,
                "action": "cron_smoke: 聊天质量",
                "feedback": "太啰嗦",
                "rating": 2,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "recorded"
        assert data["evolution_facing"]["avg_rating"] == 2.0
        assert data["evolution_facing"]["negative_trend"] is True
        assert data["evolution_facing"]["feedback_count"] == 1
        # 双写实证：user_feedback表真实落行
        conn = sqlite3.connect(_prod_db())
        try:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM user_feedback WHERE user_id=?", (LIVE_USER,)
            ).fetchone()[0]
        finally:
            conn.close()
        assert cnt == 1

    def test_post_accumulates_average(self, client, cleanup_live_feedback):
        client.post(
            "/api/brain/feedback",
            json={"user_id": LIVE_USER, "action": "a1", "rating": 1},
        )
        resp = client.post(
            "/api/brain/feedback",
            json={"user_id": LIVE_USER, "action": "a2", "rating": 2},
        )
        assert resp.status_code == 200
        assert resp.json()["evolution_facing"]["avg_rating"] == 1.5
        assert resp.json()["evolution_facing"]["feedback_count"] == 2

    @pytest.mark.parametrize(
        "payload",
        [
            {"user_id": LIVE_USER, "action": "x", "rating": 6},
            {"user_id": LIVE_USER, "action": "x", "rating": 0},
            {"user_id": LIVE_USER, "action": "x"},  # rating省略不再静默记3分
            {"user_id": LIVE_USER, "rating": 3},  # action为空
            {"user_id": LIVE_USER, "action": "   ", "rating": 3},
        ],
    )
    def test_post_validation_400(self, client, payload):
        resp = client.post("/api/brain/feedback", json=payload)
        assert resp.status_code == 400

    def test_get_feedback_lists_and_stats(self, client, cleanup_live_feedback):
        for i in range(2):
            client.post(
                "/api/brain/feedback",
                json={"user_id": LIVE_USER, "action": f"act_{i}", "feedback": "fb", "rating": 4},
            )
        resp = client.get("/api/brain/feedback", params={"user_id": LIVE_USER})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["feedback"]) == 2
        assert data["evolution_facing"]["feedback_count"] == 2
        assert data["evolution_facing"]["avg_rating"] == 4.0

    def test_get_feedback_unknown_user_honest_default(self, client):
        resp = client.get("/api/brain/feedback", params={"user_id": "no_such_user_xyz"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["feedback"] == []
        assert data["evolution_facing"]["feedback_count"] == 0

    def test_evolve_endpoint_still_healthy(self, heredity_client):
        """回归：/api/brain/evolve不因本轮改动破坏（响应契约字段齐全）。

        2026-10-06收编：迁heredity_client隔离面——此前live POST每次写穿生产
        evolution.db/experiences/evolution_log（TestEvolutionAPI同族污染源）。
        """
        resp = heredity_client.post("/api/brain/evolve")
        assert resp.status_code == 200
        data = resp.json()
        assert "evolutions" in data
        assert "experience_collection" in data
        assert "declared_proposals" in data
