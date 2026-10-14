"""P0-7进化数据层：ExperienceCollector测试。

覆盖：schema幂等+legacy ALTER、三数据源outcome判定、显式失败签名矩阵、
source_ref去重（agno idempotency_key）、缺表fail-safe、provenance、
SelfEvolution端到端（采集→分析→真实提案）、非sqlite skip、handler注册。
"""

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.learn.experience_collector import ExperienceCollector, _message_failure
from src.will.job_handlers import HANDLER_SPECS

MSG_SCHEMA = """
CREATE TABLE agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp REAL NOT NULL
)
"""

JOB_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    params TEXT DEFAULT '{}',
    result TEXT,
    error TEXT DEFAULT '',
    created_at REAL,
    started_at REAL DEFAULT 0,
    finished_at REAL DEFAULT 0,
    timeout_s INTEGER DEFAULT 300,
    retries INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 2,
    idempotency_key TEXT DEFAULT ''
)
"""

EVAL_SCHEMA = """
CREATE TABLE eval_experiments (
    experiment_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    baseline_id TEXT DEFAULT '',
    policy_fingerprint TEXT NOT NULL,
    env_fingerprint TEXT NOT NULL,
    policy_meta TEXT DEFAULT '{}',
    k INTEGER DEFAULT 1,
    results TEXT DEFAULT '{}',
    circuit_broken INTEGER DEFAULT 0,
    created_at REAL NOT NULL
)
"""


def _mk_messages_db(path: Path, rows: list[tuple]):
    conn = sqlite3.connect(path)
    conn.execute(MSG_SCHEMA)
    for sid, role, content, ts in rows:
        conn.execute(
            "INSERT INTO agent_messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
            (sid, role, content, ts),
        )
    conn.commit()
    conn.close()


def _mk_jobs_db(path: Path, rows: list[tuple]):
    conn = sqlite3.connect(path)
    conn.execute(JOB_SCHEMA)
    for jid, name, status, error, ts in rows:
        conn.execute(
            "INSERT INTO jobs (id, name, status, error, created_at) VALUES (?,?,?,?,?)",
            (jid, name, status, error, ts),
        )
    conn.commit()
    conn.close()


def _mk_eval_db(path: Path, rows: list[tuple]):
    conn = sqlite3.connect(path)
    conn.execute(EVAL_SCHEMA)
    for exp_id, ds_id, results, ts in rows:
        conn.execute(
            "INSERT INTO eval_experiments (experiment_id, dataset_id, policy_fingerprint, "
            "env_fingerprint, results, created_at) VALUES (?,?,?,?,?,?)",
            (exp_id, ds_id, "pol_hash", "env_hash", json.dumps(results), ts),
        )
    conn.commit()
    conn.close()


def _collector(tmp_path: Path, **kw) -> ExperienceCollector:
    return ExperienceCollector(
        db_path=str(tmp_path / "main.db"),
        job_db_path=str(tmp_path / "jobs.db"),
        eval_db_path=str(tmp_path / "eval.db"),
        **kw,
    )


def _count_exp(db_path: Path, where: str = "") -> int:
    conn = sqlite3.connect(db_path)
    try:
        q = "SELECT COUNT(*) FROM experiences"
        if where:
            q += f" WHERE {where}"
        return conn.execute(q).fetchone()[0]
    finally:
        conn.close()


class _AsyncDB:
    """注入生产适配器src.database.postgres._SQLiteConnection（真实契约，
    含单tuple参数兼容层）——测试即生产路径，非仿制品。每操作独立aiosqlite连接。"""

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
# 失败签名判定
# ----------------------------------------------------------------------
class TestMessageFailure:
    @pytest.mark.parametrize(
        "content",
        [
            "推理错误: name 'session' is not defined",
            "前面有点上下文\n[被拦截] 权限拒绝: sudo rm -rf /",
            "[BLOCKED] 输出含API key已被脱敏拦截",
            "执行结果如下\nTraceback (most recent call last):\n  File x",
            "[tool_call terminal id=t1]\n[tool_result id=t1 error=1] command failed",
        ],
    )
    def test_failure_signatures(self, content):
        outcome, error = _message_failure(content)
        assert outcome == "failure"
        assert error  # 失败必须携带error原文（mem0 §1.1）

    @pytest.mark.parametrize(
        "content",
        [
            "OK",
            "这是一段正常回复，包含文件读取结果。",
            "[tool_call read_file id=t1]\n[tool_result id=t1] 文件内容正常",  # 成功工具结果
        ],
    )
    def test_clean_content_is_success(self, content):
        outcome, error = _message_failure(content)
        assert outcome == "success"
        assert error == ""

    def test_error_text_is_signature_line(self):
        outcome, error = _message_failure("推理错误: 'str' object has no attribute 'value'")
        assert outcome == "failure"
        assert error.startswith("推理错误")
        assert len(error) <= 200

    def test_empty_content_success(self):
        assert _message_failure("") == ("success", "")


# ----------------------------------------------------------------------
# schema
# ----------------------------------------------------------------------
class TestSchema:
    def test_ensure_schema_creates_tables_idempotent(self, tmp_path):
        c = _collector(tmp_path)
        conn = sqlite3.connect(c.db_path)
        c.ensure_schema(conn)
        c.ensure_schema(conn)  # 幂等连跑两次
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "experiences" in tables
        assert "user_feedback" in tables
        cols = {r[1] for r in conn.execute("PRAGMA table_info(experiences)").fetchall()}
        assert "source_ref" in cols
        conn.close()

    def test_ensure_schema_alters_legacy_table(self, tmp_path):
        """experience.py先建表（无source_ref）→ collector探测+ALTER补齐。"""
        c = _collector(tmp_path)
        conn = sqlite3.connect(c.db_path)
        conn.execute(
            """CREATE TABLE experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL, agent_id TEXT NOT NULL,
                action TEXT NOT NULL, intent_summary TEXT NOT NULL,
                outcome TEXT NOT NULL, error TEXT, fix TEXT,
                relevance_score REAL DEFAULT 1.0, created_at REAL NOT NULL,
                metadata TEXT DEFAULT '{}')"""
        )
        conn.commit()
        c.ensure_schema(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(experiences)").fetchall()}
        assert "source_ref" in cols
        conn.close()

    def test_user_feedback_schema_matches_consumer_contract(self, tmp_path):
        """SelfEvolution._analyze_feedback查询rating列——空表不报错返回默认。"""
        c = _collector(tmp_path)
        conn = sqlite3.connect(c.db_path)
        c.ensure_schema(conn)
        conn.close()

        async def _run():
            from src.heredity.self_evolution import SelfEvolution

            evo = SelfEvolution(_AsyncDB(tmp_path / "main.db"))
            return await evo._analyze_feedback()

        result = asyncio.run(_run())
        assert result == {"avg_rating": 3.0, "negative_trend": False}


# ----------------------------------------------------------------------
# 数据源采集
# ----------------------------------------------------------------------
class TestCollectMessages:
    def test_outcomes_and_intent(self, tmp_path):
        _mk_messages_db(
            tmp_path / "main.db",
            [
                ("s1", "user", "帮我修复解析器", 1000.0),
                ("s1", "assistant", "推理错误: name 'session' is not defined", 1001.0),
                ("s1", "user", "再试一次", 1002.0),
                ("s1", "assistant", "已修复，解析器现在正常工作", 1003.0),
            ],
        )
        c = _collector(tmp_path)
        stats = c.collect()
        msg = stats["sources"]["messages"]
        assert msg["scanned"] == 2
        assert msg["failures"] == 1
        assert msg["successes"] == 1
        assert msg["written"] == 2
        conn = sqlite3.connect(c.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM experiences ORDER BY id").fetchall()
        conn.close()
        assert rows[0]["outcome"] == "failure"
        assert rows[0]["intent_summary"] == "帮我修复解析器"
        assert rows[0]["error"].startswith("推理错误")
        assert rows[0]["source_ref"] == "msg:2"
        assert rows[1]["outcome"] == "success"
        assert rows[1]["intent_summary"] == "再试一次"
        meta = json.loads(rows[0]["metadata"])
        assert meta["source"] == "agent_message"
        assert meta["message_id"] == 2

    def test_timestamp_from_message(self, tmp_path):
        _mk_messages_db(
            tmp_path / "main.db",
            [("s1", "user", "任务", 1234.5), ("s1", "assistant", "OK", 1235.5)],
        )
        c = _collector(tmp_path)
        c.collect()
        conn = sqlite3.connect(c.db_path)
        ts = conn.execute("SELECT created_at FROM experiences WHERE source_ref='msg:2'").fetchone()[
            0
        ]
        conn.close()
        assert ts == 1235.5  # 真实事件时间（趋势分析按发生时间统计）

    def test_sessions_isolated_intent(self, tmp_path):
        """intent_summary按会话隔离——s2的回合不用s1的用户消息。"""
        _mk_messages_db(
            tmp_path / "main.db",
            [
                ("s1", "user", "会话一任务", 1000.0),
                ("s2", "user", "会话二任务", 1001.0),
                ("s2", "assistant", "推理错误: x", 1002.0),
            ],
        )
        c = _collector(tmp_path)
        c.collect()
        conn = sqlite3.connect(c.db_path)
        intent = conn.execute("SELECT intent_summary FROM experiences").fetchone()[0]
        conn.close()
        assert intent == "会话二任务"


class TestCollectJobs:
    def test_job_outcomes(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "main.db")
        conn.execute(
            "CREATE TABLE agent_messages (id INTEGER, session_id TEXT, role TEXT, content TEXT, timestamp REAL)"
        )
        conn.commit()
        conn.close()
        _mk_jobs_db(
            tmp_path / "jobs.db",
            [
                ("a", "hippo.dream", "completed", "", 2000.0),
                ("b", "test_job", "failed", "No handler for job type 'test_job'", 2001.0),
                ("c", "hippo.dream", "timeout", "Job timed out after 300s", 2002.0),
                ("d", "x", "pending", "", 2003.0),
                ("e", "x", "running", "", 2004.0),
                ("f", "stress", "orphaned", "脚本进程提交,内存队列随进程退出丢失", 2005.0),
            ],
        )
        c = _collector(tmp_path)
        stats = c.collect()
        job = stats["sources"]["jobs"]
        assert job["scanned"] == 4  # pending/running跳过
        assert job["successes"] == 1
        assert job["failures"] == 3
        conn = sqlite3.connect(c.db_path)
        conn.row_factory = sqlite3.Row
        rows = {
            r["source_ref"]: r
            for r in conn.execute("SELECT * FROM experiences WHERE source_ref LIKE 'job:%'")
        }
        conn.close()
        assert rows["job:a"]["outcome"] == "success"
        assert rows["job:b"]["outcome"] == "failure"
        assert rows["job:b"]["error"] == "No handler for job type 'test_job'"
        assert rows["job:c"]["error"] == "Job timed out after 300s"
        assert "job:d" not in rows and "job:e" not in rows
        meta = json.loads(rows["job:b"]["metadata"])
        assert meta == {"source": "job_queue", "job_id": "b", "status": "failed"}

    def test_jobs_db_missing_fail_safe(self, tmp_path):
        c = _collector(tmp_path)  # jobs.db不存在
        stats = c.collect()
        assert stats["sources"]["jobs"]["note"] == "job_queue.db missing"
        assert stats["status"] == "ok"  # 其余源照常


class TestJobSourceProvenance:
    """jobs.source provenance门（本轮）：source='test'的测试作业不产经验。

    背景：systemic_test往生产 /api/will/jobs/submit 提交test_job/test_cancel/
    stress_test（无标记）→ job_queue.db 1092行垃圾 → experiences 1092行假经验
    → evolution_log 279行假"高频失败"进化。修复=source列+本门。
    名字不参与判定（test_job_outcomes钉住：名字叫test_job的作业照常采集）。
    """

    @staticmethod
    def _mk_jobs_db_with_source(path, rows):
        conn = sqlite3.connect(path)
        conn.execute(
            JOB_SCHEMA.replace(
                "idempotency_key TEXT DEFAULT ''",
                "idempotency_key TEXT DEFAULT '', source TEXT DEFAULT 'real'",
            )
        )
        for jid, name, status, error, ts, source in rows:
            conn.execute(
                "INSERT INTO jobs (id, name, status, error, created_at, source) VALUES (?,?,?,?,?,?)",
                (jid, name, status, error, ts, source),
            )
        conn.commit()
        conn.close()

    def test_test_source_skipped_real_collected(self, tmp_path):
        self._mk_jobs_db_with_source(
            tmp_path / "jobs.db",
            [
                ("t1", "test_job", "failed", "No handler for job type 'test_job'", 2000.0, "test"),
                ("r1", "hippo.dream", "completed", "", 2001.0, "real"),
                ("r2", "test_job", "failed", "No handler for job type 'test_job'", 2002.0, "real"),
            ],
        )
        c = _collector(tmp_path)
        stats = c.collect()
        job = stats["sources"]["jobs"]
        assert job["skipped_test"] == 1  # t1（source=test）不产经验
        assert job["scanned"] == 2 and job["failures"] == 1
        conn = sqlite3.connect(c.db_path)
        refs = {
            r[0]
            for r in conn.execute(
                "SELECT source_ref FROM experiences WHERE source_ref LIKE 'job:%'"
            )
        }
        conn.close()
        # r2名字同为test_job但source=real照常采集（名字不参与判定）
        assert refs == {"job:r1", "job:r2"}

    def test_legacy_jobs_db_without_source_column(self, tmp_path):
        # 老库无source列（迁移前）：fallback按real采集，不误杀存量真实作业
        _mk_jobs_db(
            tmp_path / "jobs.db",
            [("b", "test_job", "failed", "No handler for job type 'test_job'", 2001.0)],
        )
        c = _collector(tmp_path)
        stats = c.collect()
        job = stats["sources"]["jobs"]
        assert job["skipped_test"] == 0 and job["scanned"] == 1
        conn = sqlite3.connect(c.db_path)
        n = conn.execute("SELECT COUNT(*) FROM experiences WHERE source_ref='job:b'").fetchone()[0]
        conn.close()
        assert n == 1

    def test_test_rows_not_counted_as_failures(self, tmp_path):
        # 测试垃圾不再进失败计数——"高频失败"提案不再被test作业刷屏
        self._mk_jobs_db_with_source(
            tmp_path / "jobs.db",
            [
                ("t1", "test_job", "failed", "No handler for job type 'test_job'", 2000.0, "test"),
                (
                    "t2",
                    "test_cancel",
                    "failed",
                    "No handler for job type 'test_cancel'",
                    2001.0,
                    "test",
                ),
                (
                    "t3",
                    "stress_test",
                    "failed",
                    "No handler for job type 'stress_test'",
                    2002.0,
                    "test",
                ),
            ],
        )
        c = _collector(tmp_path)
        stats = c.collect()
        job = stats["sources"]["jobs"]
        assert job["skipped_test"] == 3
        assert job["failures"] == 0 and job["scanned"] == 0


class TestCollectEval:
    def test_eval_case_outcomes(self, tmp_path):
        results_pass = {
            "cases": {
                "cap": {
                    "scored_count": 1,
                    "pass_count": 1,
                    "attempts": [{"error": "", "passed": True}],
                }
            }
        }
        results_fail = {
            "cases": {
                "arith": {
                    "scored_count": 2,
                    "pass_count": 1,
                    "attempts": [
                        {"error": "", "passed": True},
                        {"error": "timeout waiting model", "passed": False},
                    ],
                },
                "unscored_case": {
                    "scored_count": 0,
                    "pass_count": 0,
                    "attempts": [{"error": "boom", "scored": False}],
                },
            }
        }
        _mk_eval_db(
            tmp_path / "eval.db",
            [("exp1", "ds1", results_pass, 3000.0), ("exp2", "ds1", results_fail, 3001.0)],
        )
        c = _collector(tmp_path)
        stats = c.collect()
        ev = stats["sources"]["eval_experiments"]
        assert ev["scanned"] == 2  # unscored_case跳过（agno超时≠答错）
        assert ev["successes"] == 1
        assert ev["failures"] == 1
        conn = sqlite3.connect(c.db_path)
        conn.row_factory = sqlite3.Row
        rows = {
            r["source_ref"]: r
            for r in conn.execute("SELECT * FROM experiences WHERE source_ref LIKE 'eval:%'")
        }
        conn.close()
        assert rows["eval:exp1:cap"]["outcome"] == "success"
        assert rows["eval:exp2:arith"]["outcome"] == "failure"
        assert rows["eval:exp2:arith"]["error"] == "timeout waiting model"  # attempt错误原文
        assert "eval:exp2:unscored_case" not in rows

    def test_eval_fallback_error_key(self, tmp_path):
        results = {
            "cases": {
                "c1": {
                    "scored_count": 1,
                    "pass_count": 0,
                    "attempts": [{"error": "", "passed": False}],
                }
            }
        }
        _mk_eval_db(tmp_path / "eval.db", [("exp1", "ds9", results, 3000.0)])
        c = _collector(tmp_path)
        c.collect()
        conn = sqlite3.connect(c.db_path)
        err = conn.execute(
            "SELECT error FROM experiences WHERE source_ref='eval:exp1:c1'"
        ).fetchone()[0]
        conn.close()
        assert err == "eval_case_failed:ds9:c1"  # 无原文→确定性分组键

    def test_eval_db_missing_fail_safe(self, tmp_path):
        _mk_messages_db(tmp_path / "main.db", [("s1", "assistant", "OK", 1.0)])
        c = _collector(tmp_path)
        stats = c.collect()
        assert stats["sources"]["eval_experiments"]["note"] == "eval_loop.db missing"


# ----------------------------------------------------------------------
# 去重/统计/边界
# ----------------------------------------------------------------------
class TestDedupeAndStats:
    def test_second_collect_zero_new(self, tmp_path):
        _mk_messages_db(
            tmp_path / "main.db",
            [("s1", "user", "任务", 1000.0), ("s1", "assistant", "推理错误: dup", 1001.0)],
        )
        _mk_jobs_db(tmp_path / "jobs.db", [("job_a", "n", "failed", "err happened", 2000.0)])
        c = _collector(tmp_path)
        first = c.collect()
        assert first["written"] == 2
        second = c.collect()
        assert second["written"] == 0  # agno idempotency_key：重复采集零写入
        assert second["deduped"] == 2
        assert _count_exp(tmp_path / "main.db") == 2

    def test_get_stats(self, tmp_path):
        _mk_messages_db(
            tmp_path / "main.db",
            [
                ("s1", "user", "任务", 1000.0),
                ("s1", "assistant", "推理错误: e", 1001.0),
                ("s1", "assistant", "好的完成了", 1002.0),
            ],
        )
        c = _collector(tmp_path)
        c.collect()
        stats = c.get_stats()
        assert stats["total"] == 2
        assert stats["failure_count"] == 1
        assert stats["success_count"] == 1
        assert stats["user_feedback_count"] == 0
        assert len(stats["recent"]) == 2

    def test_empty_dbs_no_crash(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "main.db")
        conn.close()  # 空库无任何表
        c = _collector(tmp_path)
        stats = c.collect()
        assert stats["sources"]["messages"]["note"] == "agent_messages table missing"
        assert stats["written"] == 0

    def test_non_sqlite_database_url_skipped(self, tmp_path, monkeypatch):
        from src.config import settings

        # PROACT-③ 契约随更新：OPENSOUL_DB env 现优先于 settings.database_url
        # （行为执行面数据面隔离），本例验 settings 回退面须显式去 env。
        monkeypatch.delenv("OPENSOUL_DB", raising=False)
        monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://user:pw@db/x")
        c = ExperienceCollector(job_db_path="x", eval_db_path="x")
        stats = c.collect()
        assert stats["status"] == "skipped"
        assert "sqlite" in stats["reason"]

    def test_disabled_source_no_production_fallback(self, tmp_path):
        """空串=显式禁用钉（_resolve_source_path三级解析）——历史陷阱修复：
        空串曾fallback到生产job_queue.db（test_session_provenance._collect老注释）。"""
        c = ExperienceCollector(db_path=str(tmp_path / "main.db"), job_db_path="", eval_db_path="")
        assert c.job_db_path == ""
        assert c.eval_db_path == ""
        stats = c.collect()
        assert stats["sources"]["jobs"]["note"] == "source disabled"
        assert stats["sources"]["eval_experiments"]["note"] == "source disabled"

    def test_unique_index_blocks_ref_collision(self, tmp_path):
        """source_ref UNIQUE索引是去重的backstop（并发写入防线）。"""
        c = _collector(tmp_path)
        conn = sqlite3.connect(c.db_path)
        c.ensure_schema(conn)
        c._insert_exp(
            conn,
            action="a",
            intent_summary="i",
            outcome="success",
            error="",
            created_at=1.0,
            source_ref="msg:1",
            metadata={},
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO experiences (tenant_id, agent_id, action, intent_summary, "
                "outcome, created_at, source_ref) VALUES ('d','d','a','i','success',1.0,'msg:1')"
            )
        conn.close()


# ----------------------------------------------------------------------
# 端到端：采集 → SelfEvolution分析 → 真实提案
# ----------------------------------------------------------------------
class TestEvolutionEndToEnd:
    def test_collector_feeds_self_evolution(self, tmp_path):
        """生产故障形态复刻：≥3条相同作业错误 + 聊天推理错误 → failure_avoidance提案。"""
        import time as _time

        now = _time.time()
        rows = []
        for i in range(4):
            rows.append(
                (
                    f"job_{i}",
                    "test_job",
                    "failed",
                    "No handler for job type 'test_job'",
                    now - 100 + i,
                )
            )
        rows.append(("job_ok", "hippo.dream", "completed", "", now - 90))
        _mk_jobs_db(tmp_path / "jobs.db", rows)
        _mk_messages_db(
            tmp_path / "main.db",
            [
                ("s1", "user", "任务A", now - 80),
                ("s1", "assistant", "推理错误: name 'session' is not defined", now - 79),
                ("s1", "assistant", "推理错误: name 'session' is not defined", now - 78),
                ("s1", "assistant", "推理错误: name 'session' is not defined", now - 77),
                ("s1", "assistant", "好的，已完成", now - 76),
            ],
        )
        c = _collector(tmp_path)
        c.collect()
        db_path = tmp_path / "main.db"
        assert _count_exp(db_path, "outcome='failure'") == 7  # 4作业失败 + 3聊天推理错误
        assert _count_exp(db_path, "outcome='success'") == 2

        from src.heredity.self_evolution import SelfEvolution

        evo = SelfEvolution(_AsyncDB(db_path))

        evolutions = asyncio.run(evo.analyze_and_evolve())
        types = {e["type"] for e in evolutions}
        assert "failure_avoidance" in types  # 高频失败模式被识别
        reasons = " | ".join(e["reason"] for e in evolutions)
        assert "No handler for job type 'test_job'" in reasons  # 真实错误原文进提案
        assert "推理错误" in reasons
        # analyze_and_evolve全程无异常=experiences与user_feedback两表契约成立

    def test_stats_experiences_total_in_collect(self, tmp_path):
        _mk_messages_db(
            tmp_path / "main.db", [("s1", "user", "x", 1.0), ("s1", "assistant", "OK", 2.0)]
        )
        c = _collector(tmp_path)
        stats = c.collect()
        assert stats["experiences_total"] == 1
        assert stats["sources"]["messages"]["written"] == 1


# ----------------------------------------------------------------------
# 接线静态校验 + handler注册
# ----------------------------------------------------------------------
class TestWiring:
    def test_handler_registered(self):
        assert "learn.collect_experiences" in HANDLER_SPECS

    def test_brain_evolve_calls_collector(self):
        text = Path("src/api/brain.py").read_text()
        assert "from src.learn.experience_collector import ExperienceCollector" in text
        assert "ExperienceCollector(tenant_id=tenant_id, agent_id=agent_id).collect()" in text
        assert '"experience_collection": collection' in text

    def test_job_handler_calls_collector(self):
        text = Path("src/will/job_handlers.py").read_text()
        assert '"learn.collect_experiences": _learn_collect_experiences' in text
        assert "asyncio.to_thread(collector.collect)" in text

    def test_brain_title_carries_reason_for_distinct_digest(self):
        """去重键=_digest(kind,title)：title必须携带reason，否则不同失败模式
        （相同action文本）互相误判duplicate（live实证4条发现被折叠成1条）。"""
        text = Path("src/api/brain.py").read_text()
        assert 'f"{_action}: {_reason}"' in text
        assert "title=_title[:200]" in text


# ----------------------------------------------------------------------
# db_pool适配器调用约定兼容层（本轮P0修复的回归锚点）
# 6个器官模块/43处调用点按单tuple风格传参，修复前全部ProgrammingError
# ----------------------------------------------------------------------
class TestDbPoolAdapter:
    def _setup_db(self, tmp_path):
        path = tmp_path / "adapter.db"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE t (a TEXT, b TEXT, n REAL)")
        conn.execute("INSERT INTO t VALUES ('x','y',3.0)")
        conn.commit()
        conn.close()
        return path

    def test_tuple_style_args_flattened(self, tmp_path):
        import aiosqlite

        from src.database.postgres import _SQLiteConnection

        path = self._setup_db(tmp_path)

        async def run():
            conn = await aiosqlite.connect(path)
            conn.row_factory = sqlite3.Row
            try:
                wrapper = _SQLiteConnection(conn)
                # 器官模块风格：args=单个tuple（self_evolution/long_term同款）
                return await wrapper.fetch("SELECT a FROM t WHERE a = ? AND b = ?", ("x", "y"))
            finally:
                await conn.close()

        rows = asyncio.run(run())
        assert len(rows) == 1
        assert rows[0]["a"] == "x"

    def test_unpacked_style_still_works(self, tmp_path):
        import aiosqlite

        from src.database.postgres import _SQLiteConnection

        path = self._setup_db(tmp_path)

        async def run():
            conn = await aiosqlite.connect(path)
            conn.row_factory = sqlite3.Row
            try:
                wrapper = _SQLiteConnection(conn)
                return await wrapper.fetch("SELECT a FROM t WHERE a = ? AND b = ?", "x", "y")
            finally:
                await conn.close()

        rows = asyncio.run(run())
        assert len(rows) == 1

    def test_single_placeholder_string_not_flattened(self, tmp_path):
        import aiosqlite

        from src.database.postgres import _SQLiteConnection

        path = self._setup_db(tmp_path)

        async def run():
            conn = await aiosqlite.connect(path)
            conn.row_factory = sqlite3.Row
            try:
                wrapper = _SQLiteConnection(conn)
                return await wrapper.fetchval("SELECT COUNT(*) FROM t WHERE a = ?", ("x",))
            finally:
                await conn.close()

        assert asyncio.run(run()) == 1

    def test_placeholder_mismatch_still_errors(self, tmp_path):
        """数量不一致不静默吞——保持ProgrammingError可见（mem0 §1.1）。"""
        import aiosqlite

        from src.database.postgres import _SQLiteConnection

        path = self._setup_db(tmp_path)

        async def run():
            conn = await aiosqlite.connect(path)
            conn.row_factory = sqlite3.Row
            try:
                wrapper = _SQLiteConnection(conn)
                return await wrapper.fetch("SELECT a FROM t WHERE a = ? AND b = ?", ("x",))
            finally:
                await conn.close()

        with pytest.raises(sqlite3.ProgrammingError):
            asyncio.run(run())

    def test_execute_tuple_style(self, tmp_path):
        import aiosqlite

        from src.database.postgres import _SQLiteConnection

        path = self._setup_db(tmp_path)

        async def run():
            conn = await aiosqlite.connect(path)
            conn.row_factory = sqlite3.Row
            try:
                wrapper = _SQLiteConnection(conn)
                # self_evolution._log_evolution同款INSERT风格
                await wrapper.execute("INSERT INTO t (a, b, n) VALUES (?, ?, ?)", ("p", "q", 0.5))
                return await wrapper.fetchval("SELECT COUNT(*) FROM t WHERE a = ?", ("p",))
            finally:
                await conn.close()

        assert asyncio.run(run()) == 1
