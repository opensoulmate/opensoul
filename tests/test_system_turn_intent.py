"""系统注入回合标注治理测试（2026-10-05轮遗留#1销账）。

生产实证：learned_patterns.strategy 被 "schedulewakeup 到点唤醒 系统自动回合
非用户消息"（confidence=1.0，样本8）占据——hermes唤醒注解不是用户话术。

范式（jobs.source provenance同款"从产生侧标注，不靠字符串猜"）：
1. 产生侧标记：agent_messages.metadata.turn_origin（wakeup_executor窗口打标）
   → collector落结构化provenance key "system_turn:{origin}"，原文保全intent_raw
2. 契约文本回退：turn_origin之前的存量行按WAKEUP_PROMPT_MARKER契约自述识别
3. gatekeeper单一真源：admit_intent新增原因码system_turn（提取/存量自愈/查询
   三面自动接线）——提取skipped计数、旧pattern行删、查询返回空
"""

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.learn.experience_collector import ExperienceCollector
from src.learn.long_term import (
    LongTermLearning,
    admit_intent,
    is_system_turn_intent,
    normalize_intent_key,
)

MARKER = "⏰ [ScheduleWakeup 到点唤醒｜系统自动回合，非用户消息]"
NORMAL_INTENT = "查看当前目录"


class _AsyncDB:
    """生产适配器注入（test_learn_pattern_admission.py同款：测试即生产路径）。"""

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
    def _mk_messages_db(self, tmp_path, messages, with_metadata=True):
        """建库：experiences（collector生产schema）+ agent_messages（ws落库schema）。

        messages=[(role, content, metadata_json|None)]，id自增=落库顺序。
        """
        path = tmp_path / "sys.db"
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        c = ExperienceCollector(db_path=str(path), job_db_path="", eval_db_path="")
        c.ensure_schema(conn)
        cols = "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, timestamp REAL"
        if with_metadata:
            cols += ", metadata TEXT"
        conn.execute(f"CREATE TABLE agent_messages ({cols})")
        now = time.time()
        for i, (role, content, meta) in enumerate(messages):
            if with_metadata:
                conn.execute(
                    "INSERT INTO agent_messages (session_id, role, content, timestamp, metadata) "
                    "VALUES ('s1', ?, ?, ?, ?)",
                    (role, content, now - i, meta),
                )
            else:
                conn.execute(
                    "INSERT INTO agent_messages (session_id, role, content, timestamp) "
                    "VALUES ('s1', ?, ?, ?)",
                    (role, content, now - i),
                )
        conn.commit()
        return path, c, conn

    def _collect(self, path, c, conn):
        stats = c._collect_from_messages(conn)
        conn.commit()
        return stats

    def _exp_rows(self, conn):
        rows = conn.execute(
            "SELECT intent_summary, outcome, error, metadata, source_ref FROM experiences ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def _mk_learn_db(self, tmp_path, experiences=None):
        """experiences带可选经验行（intent, outcome, metadata_json）→ LongTermLearning用。"""
        path = tmp_path / "learn.db"
        conn = sqlite3.connect(path)
        c = ExperienceCollector(db_path=str(path), job_db_path="", eval_db_path="")
        c.ensure_schema(conn)
        now = time.time()
        for i, item in enumerate(experiences or []):
            intent, outcome = item[0], item[1]
            meta = item[2] if len(item) > 2 else "{}"
            conn.execute(
                "INSERT INTO experiences (tenant_id, agent_id, action, "
                "intent_summary, outcome, error, created_at, metadata, source_ref) "
                "VALUES ('default','default','a',?,?,'',?, ?, ?)",
                (intent, outcome, now - i, meta, f"sys:{i}"),
            )
        conn.commit()
        conn.close()
        return path

    def _seed_pattern(self, path, pattern_type, key, cnt=3):
        db = _AsyncDB(path)
        asyncio.run(LongTermLearning(db, "default", "default")._ensure_table())
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO learned_patterns (tenant_id, agent_id, pattern_type, "
            "pattern_key, pattern_data, confidence, sample_count, created_at, updated_at) "
            "VALUES ('default','default',?,?,?,0.9,?,?,?)",
            (pattern_type, key, json.dumps({"legacy": True}), cnt, time.time(), time.time()),
        )
        conn.commit()
        conn.close()

    def _pattern_keys(self, path):
        conn = sqlite3.connect(path)
        rows = conn.execute(
            "SELECT pattern_type, pattern_key FROM learned_patterns ORDER BY pattern_type, pattern_key"
        ).fetchall()
        conn.close()
        return [(r[0], r[1]) for r in rows]

    def _run(self, path, fn, *args):
        db = _AsyncDB(path)
        learning = LongTermLearning(db, "default", "default")
        return asyncio.run(fn(learning, *args))


class TestIsSystemTurnIntent(_Base):
    def test_contract_marker_and_key_variants(self):
        # 契约原文 / 归一key变体 / 短式（测试里出现过的"⏰ [ScheduleWakeup 到点唤醒]"）
        assert is_system_turn_intent(MARKER)
        assert is_system_turn_intent("schedulewakeup 到点唤醒 系统自动回合 非用户消息")
        assert is_system_turn_intent("⏰ [ScheduleWakeup 到点唤醒]")
        assert is_system_turn_intent("System_Turn:wakeup")
        assert is_system_turn_intent("system_turn:cron")

    def test_normal_intents_not_flagged(self):
        assert not is_system_turn_intent(NORMAL_INTENT)
        assert not is_system_turn_intent("帮我查看当前目录")
        assert not is_system_turn_intent("background_job:hippo.dream")
        assert not is_system_turn_intent("用Python写一个快速排序算法")
        assert not is_system_turn_intent("请回复OK两个字即可")
        assert not is_system_turn_intent("")

    def test_no_misfire_on_partial_phrases(self):
        # 单个短语不命中（防误杀用户话术）：只有契约双短语/双短语组合才算
        assert not is_system_turn_intent("用户消息很多的会话统计")
        assert not is_system_turn_intent("分析schedulewakeup工具的用法")


class TestAdmitSystemTurn(_Base):
    def test_reason_code_system_turn(self):
        ok, reason = admit_intent(MARKER)
        assert (ok, reason) == (False, "system_turn")
        ok, reason = admit_intent("system_turn:wakeup")
        assert (ok, reason) == (False, "system_turn")

    def test_normal_intent_still_admitted(self):
        ok, reason = admit_intent(NORMAL_INTENT)
        assert (ok, reason) == (True, "ok")

    def test_query_side_rejects_system_intent(self, tmp_path):
        path = self._mk_learn_db(tmp_path, [(NORMAL_INTENT, "success")] * 3)
        self._seed_pattern(path, "strategy", normalize_intent_key(NORMAL_INTENT))
        res = self._run(path, LongTermLearning.get_recommendations, MARKER)
        assert res == []
        res = self._run(path, LongTermLearning.get_recommendations, "system_turn:wakeup")
        assert res == []


class TestCollectorProvenance(_Base):
    def test_turn_origin_metadata_primary(self, tmp_path):
        # 产生侧标记优先：无契约文本的未来系统回合同样落结构化key
        path, c, conn = self._mk_messages_db(
            tmp_path,
            [
                ("user", "检查天气计划", '{"turn_origin": "wakeup"}'),
                ("assistant", "检查完毕", None),
            ],
        )
        stats = self._collect(path, c, conn)
        rows = self._exp_rows(conn)
        conn.close()
        assert stats["system_turns"] == 1
        assert rows[0]["intent_summary"] == "system_turn:wakeup"
        meta = json.loads(rows[0]["metadata"])
        assert meta["turn_origin"] == "wakeup"
        assert meta["intent_raw"] == "检查天气计划"

    def test_legacy_marker_text_fallback(self, tmp_path):
        # 存量行（无turn_origin标记）：契约自述文本回退识别
        path, c, conn = self._mk_messages_db(
            tmp_path,
            [
                ("user", MARKER + "\n你之前自报的检查计划：x", None),
                ("assistant", "回复", None),
            ],
        )
        stats = self._collect(path, c, conn)
        rows = self._exp_rows(conn)
        conn.close()
        assert stats["system_turns"] == 1
        assert rows[0]["intent_summary"] == "system_turn:wakeup"
        meta = json.loads(rows[0]["metadata"])
        assert meta["intent_raw"].startswith("⏰ [ScheduleWakeup")

    def test_normal_turn_unchanged(self, tmp_path):
        path, c, conn = self._mk_messages_db(
            tmp_path,
            [
                ("user", NORMAL_INTENT, None),
                ("assistant", "好的", None),
            ],
        )
        stats = self._collect(path, c, conn)
        rows = self._exp_rows(conn)
        conn.close()
        assert stats["system_turns"] == 0
        assert rows[0]["intent_summary"] == NORMAL_INTENT
        meta = json.loads(rows[0]["metadata"])
        assert "turn_origin" not in meta and "intent_raw" not in meta

    def test_old_db_without_metadata_column(self, tmp_path):
        # 老库无metadata列：查询回退不中断，契约文本仍识别
        path, c, conn = self._mk_messages_db(
            tmp_path,
            [
                ("user", MARKER, None),
                ("assistant", "回复", None),
            ],
            with_metadata=False,
        )
        stats = self._collect(path, c, conn)
        rows = self._exp_rows(conn)
        conn.close()
        assert stats["system_turns"] == 1
        assert rows[0]["intent_summary"] == "system_turn:wakeup"

    def test_system_turn_outcome_still_real(self, tmp_path):
        # 系统回合的outcome照常采集（真实失败信号，进化分析要看见）
        path, c, conn = self._mk_messages_db(
            tmp_path,
            [
                ("user", MARKER, '{"turn_origin": "wakeup"}'),
                ("assistant", "推理错误: boom", None),
            ],
        )
        stats = self._collect(path, c, conn)
        rows = self._exp_rows(conn)
        conn.close()
        assert stats["failures"] == 1
        assert rows[0]["outcome"] == "failure"
        assert rows[0]["error"] == "推理错误: boom"


class TestPatternSideWiring(_Base):
    def test_extract_skips_system_intents(self, tmp_path):
        path = self._mk_learn_db(
            tmp_path,
            [(MARKER, "success")] * 3
            + [("system_turn:wakeup", "success")] * 3
            + [(NORMAL_INTENT, "success")] * 3,
        )
        res = self._run(path, LongTermLearning.extract_patterns)
        keys = self._pattern_keys(path)
        assert ("strategy", "system_turn:wakeup") not in keys
        assert not any("schedulewakeup" in k for _, k in keys)
        assert ("strategy", NORMAL_INTENT) in keys
        assert res["strategy"]["skipped"].get("system_turn") == 6

    def test_prune_removes_legacy_system_pattern_rows(self, tmp_path):
        # 存量自愈：治理前落下的唤醒pattern行（原始key+归一key两种形态）删除
        path = self._mk_learn_db(tmp_path, [(NORMAL_INTENT, "success")] * 3)
        self._seed_pattern(path, "strategy", "schedulewakeup 到点唤醒 系统自动回合 非用户消息")
        self._seed_pattern(path, "strategy", "system_turn:wakeup")
        self._seed_pattern(path, "strategy", normalize_intent_key(NORMAL_INTENT))
        self._seed_pattern(path, "failure", "other")
        res = self._run(path, LongTermLearning.extract_patterns)
        keys = self._pattern_keys(path)
        assert not any("schedulewakeup" in k for _, k in keys)
        assert ("strategy", "system_turn:wakeup") not in keys
        assert ("strategy", normalize_intent_key(NORMAL_INTENT)) in keys
        assert ("failure", "other") in keys  # failure行不误伤
        assert res["pruned"]["removed_inadmissible"] == 2

    def test_admit_wired_in_all_three_surfaces(self):
        # 接线静态：admit_intent必须在提取/prune/查询三面被调用（缺一即断）
        src = (Path(__file__).resolve().parent.parent / "src/learn/long_term.py").read_text()
        for surface in (
            "_extract_strategy_patterns",
            "_prune_inadmissible_patterns",
            "get_recommendations",
        ):
            idx = src.index(f"async def {surface}")
            body = src[
                idx : src.index("\n    async def ", idx + 10)
                if "\n    async def " in src[idx + 10 :]
                else len(src)
            ]
            assert "admit_intent(" in body, f"{surface} 未接admit_intent"
