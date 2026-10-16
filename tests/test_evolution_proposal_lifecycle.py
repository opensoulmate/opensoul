"""提案生命周期测试（2026-10-05轮：重复立案刷屏治理）。

生产实证：evolution_log积压190行同reason复读——同一对已修复bug各95行
"高频失败: 推理错误: ...(5次)"，另有"成功率下降: 32% vs 90%"×8等同快照复读。
根因：SelfEvolution.analyze_and_evolve每次运行对同一发现无条件INSERT一行。

本文件钉住提案生命周期语义（LobeChat declareSelfFeedbackIntent去重范式）：
1. 无新证据不重复立案（N次运行=1行）
2. 新证据驱动再立新案（立案后又有新失败→新行）
3. 陈旧模式（>7天无新失败）不提案
4. 存量行（meta空）按reason形态兼容匹配不复读
5. 趋势/反馈同测量快照不复读
6. compact_duplicate_logs存量归档：同签名保最新1条active，get_stats只数active
7. 新行持久化结构化签名meta
"""

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.heredity.self_evolution import SelfEvolution
from src.learn.experience_collector import ExperienceCollector


class _AsyncDB:
    """生产适配器注入（test_error_normalization.py同款：测试即生产路径）。"""

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


class _Base:
    def _mk_experiences(self, path: Path, errors, created_at=None):
        conn = sqlite3.connect(path)
        c = ExperienceCollector(db_path=str(path), job_db_path="", eval_db_path="")
        c.ensure_schema(conn)
        now = time.time()
        for i, err in enumerate(errors):
            ts = created_at[i] if created_at else now - i
            conn.execute(
                "INSERT INTO experiences (tenant_id, agent_id, action, "
                "intent_summary, outcome, error, created_at, metadata, source_ref) "
                "VALUES ('default','default','a','i','failure',?,?,?,?)",
                (err, ts, "{}", f"life:{err}:{i}:{ts}"),
            )
        conn.commit()
        conn.close()

    def _log_rows(self, path: Path, ev_type: str = ""):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        sql = "SELECT * FROM evolution_log"
        if ev_type:
            sql += f" WHERE evolution_type = '{ev_type}'"
        rows = [dict(r) for r in conn.execute(sql)]
        conn.close()
        return rows

    def _evo(self, path: Path) -> SelfEvolution:
        return SelfEvolution(_AsyncDB(path))


class TestDedupNoNewEvidence(_Base):
    def test_same_signature_logged_once_across_runs(self, tmp_path):
        """核心断言：同一发现跑N次analyze_and_evolve=1行（治理前=N行复读）。"""
        self._mk_experiences(tmp_path / "main.db", ["errA"] * 3)
        evo = self._evo(tmp_path / "main.db")
        for _ in range(5):
            asyncio.run(evo.analyze_and_evolve())
        rows = self._log_rows(tmp_path / "main.db", "failure_avoidance")
        assert len(rows) == 1
        assert rows[0]["reason"] == "高频失败: errA (3次)"

    def test_suppressed_finding_not_in_return(self, tmp_path):
        """被抑制的发现不再进返回值（brain /evolve不再重复declare提案）。"""
        self._mk_experiences(tmp_path / "main.db", ["errA"] * 3)
        evo = self._evo(tmp_path / "main.db")
        first = asyncio.run(evo.analyze_and_evolve())
        assert [e["type"] for e in first if e["type"] == "failure_avoidance"] == [
            "failure_avoidance"
        ]
        second = asyncio.run(evo.analyze_and_evolve())
        assert [e for e in second if e["type"] == "failure_avoidance"] == []

    def test_new_failures_retrigger_proposal(self, tmp_path):
        """新证据驱动再立案：立案后又有该签名的新失败→第2行。"""
        now = time.time()
        self._mk_experiences(
            tmp_path / "main.db", ["errA"] * 3, created_at=[now - 100, now - 99, now - 98]
        )
        evo = self._evo(tmp_path / "main.db")
        asyncio.run(evo.analyze_and_evolve())
        assert len(self._log_rows(tmp_path / "main.db", "failure_avoidance")) == 1
        # 立案之后又失败3次（时间戳晚于第一行的created_at）
        self._mk_experiences(
            tmp_path / "main.db", ["errA"] * 3, created_at=[now + 100, now + 101, now + 102]
        )
        asyncio.run(evo.analyze_and_evolve())
        assert len(self._log_rows(tmp_path / "main.db", "failure_avoidance")) == 2

    def test_different_signatures_each_logged(self, tmp_path):
        """不同根因互不误合并——各立各案。"""
        self._mk_experiences(tmp_path / "main.db", ["errA"] * 3 + ["errB"] * 3)
        evo = self._evo(tmp_path / "main.db")
        asyncio.run(evo.analyze_and_evolve())
        rows = self._log_rows(tmp_path / "main.db", "failure_avoidance")
        assert len(rows) == 2


class TestFreshnessGate(_Base):
    def test_stale_pattern_not_proposed(self, tmp_path):
        """陈旧模式（last_seen>7天）不提案——190行复读的两个bug均系3周前已修的历史错误。"""
        now = time.time()
        self._mk_experiences(
            tmp_path / "main.db",
            ["errStale"] * 3,
            created_at=[now - 10 * 86400, now - 11 * 86400, now - 12 * 86400],
        )
        evo = self._evo(tmp_path / "main.db")
        asyncio.run(evo.analyze_and_evolve())
        assert self._log_rows(tmp_path / "main.db", "failure_avoidance") == []

    def test_fresh_pattern_still_proposed(self, tmp_path):
        """新鲜度门不误伤活跃模式。"""
        self._mk_experiences(tmp_path / "main.db", ["errFresh"] * 3)
        evo = self._evo(tmp_path / "main.db")
        asyncio.run(evo.analyze_and_evolve())
        assert len(self._log_rows(tmp_path / "main.db", "failure_avoidance")) == 1

    def test_mixed_fresh_and_stale_only_fresh_proposed(self, tmp_path):
        now = time.time()
        self._mk_experiences(
            tmp_path / "main.db",
            ["errStale"] * 3 + ["errFresh"] * 3,
            created_at=[now - 10 * 86400] * 3 + [now - 1, now - 2, now - 3],
        )
        evo = self._evo(tmp_path / "main.db")
        asyncio.run(evo.analyze_and_evolve())
        rows = self._log_rows(tmp_path / "main.db", "failure_avoidance")
        assert len(rows) == 1
        assert "errFresh" in rows[0]["reason"]


class TestLegacyRowCompat(_Base):
    def test_legacy_row_without_meta_suppresses_repeat(self, tmp_path):
        """存量行（meta/status列缺失时代写入）按reason形态回退匹配——升级后不复读。"""
        now = time.time()
        self._mk_experiences(
            tmp_path / "main.db", ["errA"] * 3, created_at=[now - 50, now - 49, now - 48]
        )
        evo = self._evo(tmp_path / "main.db")
        asyncio.run(evo._ensure_table())  # 迁移先行
        conn = sqlite3.connect(tmp_path / "main.db")
        conn.execute(
            "INSERT INTO evolution_log (tenant_id, agent_id, evolution_type, reason, "
            "impact_score, created_at) VALUES ('default','default','failure_avoidance',"
            "'高频失败: errA (3次)', 0.5, ?)",
            (now - 10,),
        )
        conn.commit()
        conn.close()
        asyncio.run(evo.analyze_and_evolve())
        rows = self._log_rows(tmp_path / "main.db", "failure_avoidance")
        assert len(rows) == 1  # 只剩存量行，无新增
        assert rows[0]["id"] == 1

    def test_legacy_count_variants_same_signature(self, tmp_path):
        """存量reason的"(N次)"计数快照差异不拆签——同发现归并。"""
        now = time.time()
        evo = self._evo(tmp_path / "main.db")
        asyncio.run(evo._ensure_table())
        conn = sqlite3.connect(tmp_path / "main.db")
        for i, reason in enumerate(
            ["高频失败: errA (5次)", "高频失败: errA (3次)", "高频失败: errA (5次)"]
        ):
            conn.execute(
                "INSERT INTO evolution_log (tenant_id, agent_id, evolution_type, reason, "
                "impact_score, created_at) VALUES ('default','default','failure_avoidance',"
                "?, 0.5, ?)",
                (reason, now - 100 + i),
            )
        conn.commit()
        conn.close()
        assert asyncio.run(evo.compact_duplicate_logs()) == {"superseded": 2}
        rows = self._log_rows(tmp_path / "main.db", "failure_avoidance")
        actives = [r for r in rows if r["status"] == "active"]
        assert len(actives) == 1
        assert actives[0]["reason"] == "高频失败: errA (5次)"  # 保最新（created_at最大）


class TestTrendDedup(_Base):
    def test_identical_feedback_snapshot_logged_once(self, tmp_path):
        """趋势/反馈同测量快照不复读（'成功率下降: 32% vs 90%'×8同款刷屏治理）。"""
        self._mk_experiences(tmp_path / "main.db", [])
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
        evo = self._evo(tmp_path / "main.db")
        for _ in range(4):
            asyncio.run(evo.analyze_and_evolve())
        rows = self._log_rows(tmp_path / "main.db", "style_adjustment")
        assert len(rows) == 1
        assert "2.0/5" in rows[0]["reason"]

    def test_changed_measurement_logs_new_row(self, tmp_path):
        """测量值变化=新发现→新行（签名含快照文本）。"""
        self._mk_experiences(tmp_path / "main.db", [])
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
        evo = self._evo(tmp_path / "main.db")
        asyncio.run(evo.analyze_and_evolve())
        conn = sqlite3.connect(tmp_path / "main.db")
        conn.execute(
            "INSERT INTO user_feedback (tenant_id, user_id, action, feedback, "
            "rating, created_at) VALUES ('default','default','a','f',1,?)",
            (now + 10,),
        )
        conn.commit()
        conn.close()
        asyncio.run(evo.analyze_and_evolve())
        rows = self._log_rows(tmp_path / "main.db", "style_adjustment")
        assert len(rows) == 2  # 均值变化→新快照


class TestCompaction(_Base):
    def test_compact_marks_older_duplicates_superseded(self, tmp_path):
        now = time.time()
        evo = self._evo(tmp_path / "main.db")
        asyncio.run(evo._ensure_table())
        conn = sqlite3.connect(tmp_path / "main.db")
        for i in range(5):
            conn.execute(
                "INSERT INTO evolution_log (tenant_id, agent_id, evolution_type, reason, "
                "impact_score, created_at) VALUES ('default','default','failure_avoidance',"
                "'高频失败: errA (5次)', 0.5, ?)",
                (now - 100 + i,),
            )
        conn.commit()
        conn.close()
        result = asyncio.run(evo.compact_duplicate_logs())
        assert result == {"superseded": 4}
        rows = self._log_rows(tmp_path / "main.db", "failure_avoidance")
        assert sorted(r["status"] for r in rows) == ["active"] + ["superseded"] * 4
        actives = [r for r in rows if r["status"] == "active"]
        assert actives[0]["created_at"] == now - 96  # 保最新
        # 幂等：再跑一次零变化
        assert asyncio.run(evo.compact_duplicate_logs()) == {"superseded": 0}

    def test_get_stats_counts_active_only(self, tmp_path):
        now = time.time()
        evo = self._evo(tmp_path / "main.db")
        asyncio.run(evo._ensure_table())
        conn = sqlite3.connect(tmp_path / "main.db")
        for i in range(3):
            conn.execute(
                "INSERT INTO evolution_log (tenant_id, agent_id, evolution_type, reason, "
                "impact_score, created_at) VALUES ('default','default','failure_avoidance',"
                "'高频失败: errA (3次)', 0.5, ?)",
                (now - 10 + i,),
            )
        conn.commit()
        conn.close()
        asyncio.run(evo.compact_duplicate_logs())
        stats = asyncio.run(evo.get_stats())
        assert stats == {"evolutions": 1}

    def test_compact_is_wired_into_analyze(self, tmp_path):
        """归档在analyze_and_evolve内自动跑（不靠手动调用=自动化优先）。"""
        now = time.time()
        self._mk_experiences(tmp_path / "main.db", ["errOther"] * 3)
        evo = self._evo(tmp_path / "main.db")
        asyncio.run(evo._ensure_table())
        conn = sqlite3.connect(tmp_path / "main.db")
        for i in range(3):
            conn.execute(
                "INSERT INTO evolution_log (tenant_id, agent_id, evolution_type, reason, "
                "impact_score, created_at) VALUES ('default','default','failure_avoidance',"
                "'高频失败: errA (3次)', 0.5, ?)",
                (now - 10 + i,),
            )
        conn.commit()
        conn.close()
        asyncio.run(evo.analyze_and_evolve())
        rows = self._log_rows(tmp_path / "main.db", "failure_avoidance")
        superseded = [r for r in rows if r["status"] == "superseded"]
        assert len(superseded) == 2  # 存量3行复读→保1归档2，且全程零人工


class TestStructuredMeta(_Base):
    def test_new_row_stores_signature_meta(self, tmp_path):
        self._mk_experiences(tmp_path / "main.db", ["errA"] * 3)
        evo = self._evo(tmp_path / "main.db")
        asyncio.run(evo.analyze_and_evolve())
        rows = self._log_rows(tmp_path / "main.db", "failure_avoidance")
        meta = json.loads(rows[0]["meta"])
        assert meta["signature"] == "failure_avoidance:errA"
        assert meta["pattern_key"] == "errA"
        assert meta["count"] == 3
        assert rows[0]["status"] == "active"

    def test_migration_adds_columns_to_legacy_table(self, tmp_path):
        """老库迁移：meta/status列PRAGMA探测+ALTER自愈，存量行status回填active。"""
        conn = sqlite3.connect(tmp_path / "main.db")
        conn.execute(
            """CREATE TABLE evolution_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL, agent_id TEXT NOT NULL,
                evolution_type TEXT NOT NULL, before_state TEXT, after_state TEXT,
                reason TEXT, impact_score REAL DEFAULT 0.0, created_at REAL NOT NULL
            )"""
        )
        conn.execute(
            "INSERT INTO evolution_log (tenant_id, agent_id, evolution_type, reason, "
            "impact_score, created_at) VALUES ('default','default','failure_avoidance',"
            "'高频失败: errA (3次)', 0.5, 1.0)"
        )
        conn.commit()
        conn.close()
        evo = self._evo(tmp_path / "main.db")
        asyncio.run(evo._ensure_table())
        rows = self._log_rows(tmp_path / "main.db")
        assert rows[0]["status"] == "active"  # DEFAULT回填
        assert rows[0]["meta"] is None


class TestWiring:
    def test_analyze_calls_dedup_gate(self):
        text = Path("src/heredity/self_evolution.py").read_text()
        assert "await self._last_proposal(evolution)" in text
        assert "await self._new_failures_since(" in text
        assert "await self.compact_duplicate_logs()" in text

    def test_brain_evolve_consumes_analyze(self):
        """单一代码路径契约（G1-②）：/api/brain/evolve 委托 run_evolution_cycle，
        其编排体真消费 analyze_and_evolve（禁第二套编排漂移）。"""
        brain = Path("src/api/brain.py").read_text()
        assert "run_evolution_cycle" in brain
        se = Path("src/heredity/self_evolution.py").read_text()
        assert "await SelfEvolution(db_pool, tenant_id, agent_id).analyze_and_evolve()" in se

    def test_log_evolution_writes_meta(self):
        text = Path("src/heredity/self_evolution.py").read_text()
        assert 'meta["signature"] = evolution["signature"]' in text
        assert "status" in text
