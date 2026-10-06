"""会话级provenance（jobs.source同款）测试：测试会话不再污染生产experiences。

背景（2026-10-05 17:30轮遗留#2）："请回复ok两个字即可"×3 以 confidence=1.0
占据 learned_patterns.strategy——它是 e2e_ws_calibration.py live探针会话的用户
消息（om-5a0c56faa274等），本质=测试会话污染生产experiences。根治方向=
会话级provenance标记（类似jobs.source），不是字符串猜测。

产生侧（acp-proxy SoulMateAgent）：new_session经 _meta.source 绑定会话出身，
会话内所有落库行metadata带session_source="test"。
采集侧（本文件钉住）：
1. metadata.session_source='test' 的会话不产经验（skipped_test计数，只认
   schema标记，名字/内容不参与判定）；
2. 会话级语义：任一行标记即整会话生效（与行序无关）；
3. 非test标记（real/live等）不误杀；老库无metadata列fail-safe照常采集；
4. 派生pattern缓存视图自愈：证据行被清账后零背书strategy行自动驱逐
   （_prune_inadmissible_patterns规则3），有背书（哪怕<3不重建）不误删。
"""

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.learn.experience_collector import ExperienceCollector
from src.learn.long_term import LongTermLearning


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


def _mk_msg_db(tmp_path, transcripts):
    """建agent_messages库（生产schema子集）。

    transcripts: [(session_id, [(role, content, metadata_dict_or_None), ...]), ...]
    """
    path = tmp_path / "msg.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE agent_messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT,"
        " timestamp REAL, attachments TEXT, parent_message_id INTEGER, metadata TEXT)"
    )
    conn.execute(
        "CREATE TABLE agent_sessions (id TEXT PRIMARY KEY,"
        " message_count INTEGER DEFAULT 0, last_activity_at REAL)"
    )
    now = time.time()
    for session_id, rows in transcripts:
        for role, content, meta in rows:
            conn.execute(
                "INSERT INTO agent_messages (session_id, role, content, timestamp, metadata)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    session_id,
                    role,
                    content,
                    now,
                    json.dumps(meta, ensure_ascii=False) if meta else None,
                ),
            )
    conn.commit()
    conn.close()
    return path


def _collect(path):
    # job/eval数据源指向不存在的路径（隔离采集面；空串现=显式禁用，见
    # experience_collector._resolve_source_path三级解析）
    c = ExperienceCollector(
        db_path=str(path),
        job_db_path=str(Path(path).parent / "no_such_jobs.db"),
        eval_db_path=str(Path(path).parent / "no_such_eval.db"),
    )
    return c.collect()["sources"]["messages"]


def _intents(path):
    conn = sqlite3.connect(path)
    rows = conn.execute("SELECT intent_summary, source_ref FROM experiences ORDER BY id").fetchall()
    conn.close()
    return rows


# ════════════════════════════════════════════════════════════════
# ① 采集侧provenance门
# ════════════════════════════════════════════════════════════════


class TestCollectorProvenanceGate:
    def test_test_session_produces_no_experience(self, tmp_path):
        """session_source='test'会话整会话不产经验，skipped_test计数可观测。"""
        path = _mk_msg_db(
            tmp_path,
            [
                (
                    "om-probe-1",
                    [
                        ("user", "请回复OK两个字即可", {"session_source": "test"}),
                        ("assistant", "OK", {"session_source": "test"}),
                    ],
                )
            ],
        )
        stats = _collect(path)
        assert stats["written"] == 0
        assert stats["skipped_test"] == 2
        assert _intents(path) == []

    def test_session_level_any_row_marks_whole_session(self, tmp_path):
        """会话级语义：只有首行带标记，后续无标记行同样跳过（与行序无关）。"""
        path = _mk_msg_db(
            tmp_path,
            [
                (
                    "om-probe-2",
                    [
                        ("user", "请回复OK两个字即可", {"session_source": "test"}),
                        ("assistant", "OK", None),  # 落库路径漏标也整会话生效
                    ],
                )
            ],
        )
        stats = _collect(path)
        assert stats["written"] == 0
        assert stats["skipped_test"] == 2

    def test_marked_row_after_unmarked_row_also_blocks(self, tmp_path):
        """标记行在后（存量回填形态）：全表预扫建映射，前面的行同样跳过。"""
        path = _mk_msg_db(
            tmp_path,
            [
                (
                    "om-probe-3",
                    [
                        ("user", "请回复OK两个字即可", None),
                        ("assistant", "OK", {"session_source": "test"}),
                    ],
                )
            ],
        )
        stats = _collect(path)
        assert stats["written"] == 0
        assert stats["skipped_test"] == 2

    def test_unmarked_session_collected_unchanged(self, tmp_path):
        """回归面：无标记普通会话行为零变化（照常产经验）。"""
        path = _mk_msg_db(
            tmp_path,
            [
                (
                    "om-real-1",
                    [
                        ("user", "帮我查看当前目录", None),
                        ("assistant", "当前目录是/home/climbing", None),
                    ],
                )
            ],
        )
        stats = _collect(path)
        assert stats["written"] == 1
        assert stats["skipped_test"] == 0
        rows = _intents(path)
        assert rows[0][0] == "帮我查看当前目录"

    def test_non_test_source_not_skipped(self, tmp_path):
        """只认'test'精确标记（jobs.source同款）：source='real'不误杀。"""
        path = _mk_msg_db(
            tmp_path,
            [
                (
                    "om-real-2",
                    [
                        ("user", "帮我查看当前目录", {"session_source": "real"}),
                        ("assistant", "ok", {"session_source": "real"}),
                    ],
                )
            ],
        )
        stats = _collect(path)
        assert stats["written"] == 1
        assert stats["skipped_test"] == 0

    def test_mixed_sessions_isolated(self, tmp_path):
        """同库混跑：测试会话跳过不影响真实会话采集。"""
        path = _mk_msg_db(
            tmp_path,
            [
                (
                    "om-probe-x",
                    [
                        ("user", "请回复OK两个字即可", {"session_source": "test"}),
                        ("assistant", "OK", {"session_source": "test"}),
                    ],
                ),
                (
                    "om-real-x",
                    [
                        ("user", "帮我查看当前目录", None),
                        ("assistant", "done", None),
                    ],
                ),
            ],
        )
        stats = _collect(path)
        assert stats["written"] == 1
        assert stats["skipped_test"] == 2
        rows = _intents(path)
        assert [r[0] for r in rows] == ["帮我查看当前目录"]

    def test_old_db_without_metadata_column_fails_safe(self, tmp_path):
        """老库无metadata列：照常采集（fail-safe不误杀存量真实会话）。"""
        path = tmp_path / "old.db"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE agent_messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,"
            " content TEXT, timestamp REAL)"
        )
        conn.execute(
            "INSERT INTO agent_messages (session_id, role, content, timestamp)"
            " VALUES ('om-old', 'user', '帮我查看当前目录', ?)",
            (time.time(),),
        )
        conn.execute(
            "INSERT INTO agent_messages (session_id, role, content, timestamp)"
            " VALUES ('om-old', 'assistant', 'done', ?)",
            (time.time(),),
        )
        conn.commit()
        conn.close()
        stats = _collect(path)
        assert stats["written"] == 1
        assert stats["skipped_test"] == 0

    def test_broken_metadata_json_not_skipped(self, tmp_path):
        """坏JSON metadata：不猜、照常采集（fail-safe）。"""
        path = tmp_path / "badmeta.db"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE agent_messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,"
            " content TEXT, timestamp REAL, metadata TEXT)"
        )
        conn.execute(
            "INSERT INTO agent_messages (session_id, role, content, timestamp, metadata)"
            " VALUES ('om-x', 'user', '帮我查看当前目录', ?, 'not-json{{')",
            (time.time(),),
        )
        conn.execute(
            "INSERT INTO agent_messages (session_id, role, content, timestamp, metadata)"
            " VALUES ('om-x', 'assistant', 'done', ?, 'not-json{{')",
            (time.time(),),
        )
        conn.commit()
        conn.close()
        stats = _collect(path)
        assert stats["written"] == 1
        assert stats["skipped_test"] == 0


# ════════════════════════════════════════════════════════════════
# ② 派生pattern缓存视图自愈（规则3：零背书驱逐）
# ════════════════════════════════════════════════════════════════


class _PatternBase:
    def _mk_db(self, tmp_path, experiences):
        path = tmp_path / "learn.db"
        conn = sqlite3.connect(path)
        c = ExperienceCollector(db_path=str(path), job_db_path="", eval_db_path="")
        c.ensure_schema(conn)
        now = time.time()
        for i, (intent, outcome) in enumerate(experiences):
            conn.execute(
                "INSERT INTO experiences (tenant_id, agent_id, action, "
                "intent_summary, outcome, error, created_at, metadata, source_ref) "
                "VALUES ('default','default','a',?,?,'',?, '{}', ?)",
                (intent, outcome, now - i, f"prov:{intent}:{outcome}:{i}"),
            )
        conn.commit()
        conn.close()
        return path

    def _seed_pattern(self, path, key, cnt=3):
        db = _AsyncDB(path)
        asyncio.run(LongTermLearning(db, "default", "default")._ensure_table())
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO learned_patterns (tenant_id, agent_id, pattern_type, "
            "pattern_key, pattern_data, confidence, sample_count, created_at, updated_at) "
            "VALUES ('default','default','strategy',?,?,0.9,?,?,?)",
            (key, json.dumps({"legacy": True}), cnt, time.time(), time.time()),
        )
        conn.commit()
        conn.close()

    def _keys(self, path):
        conn = sqlite3.connect(path)
        rows = conn.execute(
            "SELECT pattern_key FROM learned_patterns WHERE pattern_type='strategy'"
        ).fetchall()
        conn.close()
        return {r[0] for r in rows}

    def _run(self, path, fn, *args):
        db = _AsyncDB(path)
        learning = LongTermLearning(db, "default", "default")
        return asyncio.run(fn(learning, *args))


class TestUnbackedPatternEviction(_PatternBase):
    def test_zero_backing_evidence_evicted(self, tmp_path):
        """零背书strategy行驱逐（探针话术pattern的出清机制）。"""
        path = self._mk_db(tmp_path, [("帮我查看当前目录", "success")] * 4)
        self._seed_pattern(path, "请回复ok两个字即可", 3)  # 探针话术：无证据背书

        stats = self._run(path, lambda l: l.extract_patterns())
        assert stats["pruned"]["removed_unbacked"] == 1
        assert "请回复ok两个字即可" not in self._keys(path)

    def test_evidence_deleted_then_evicted(self, tmp_path):
        """生命周期：证据在→pattern在；证据清账→下一轮extract自动驱逐。

        "请回复ok两个字即可"治理的完整故事：experiences删掉探针行后，
        派生pattern不需要人工删，规则3自动出清。
        """
        path = self._mk_db(tmp_path, [("请回复OK两个字即可", "success")] * 3)
        self._run(path, lambda l: l.extract_patterns())
        assert "请回复ok两个字即可" in self._keys(path)  # 有证据：正常派生

        conn = sqlite3.connect(path)
        conn.execute("DELETE FROM experiences")
        conn.commit()
        conn.close()

        stats = self._run(path, lambda l: l.extract_patterns())
        assert stats["pruned"]["removed_unbacked"] == 1
        assert self._keys(path) == set()

    def test_backed_row_survives_below_threshold(self, tmp_path):
        """保守边界：背书>0即保留（哪怕cnt<3不触发重建），防证据分裂误删。"""
        path = self._mk_db(tmp_path, [("部署到生产环境", "success")] * 2)  # <3不重建
        self._seed_pattern(path, "部署到生产环境", 5)

        stats = self._run(path, lambda l: l.extract_patterns())
        assert stats["pruned"]["removed_unbacked"] == 0
        assert "部署到生产环境" in self._keys(path)

    def test_missing_experiences_table_fail_safe(self, tmp_path):
        """experiences表缺失：规则3跳过，不知道证据不乱删（fail-safe）。

        直接测_prune_inadmissible_patterns（extract_patterns后续提取步骤
        需要experiences表存在，缺表场景只在prune层可到达）。
        """
        path = tmp_path / "noexp.db"
        conn = sqlite3.connect(path)
        conn.commit()
        conn.close()
        self._seed_pattern(path, "查看当前目录", 4)  # 唯一DDL来源是seed的_ensure_table

        stats = self._run(path, lambda l: l._prune_inadmissible_patterns())
        assert stats["removed_unbacked"] == 0
        assert "查看当前目录" in self._keys(path)

    def test_non_strategy_types_not_evicted(self, tmp_path):
        """规则3只管strategy：failure/file_combo不误伤。"""
        path = self._mk_db(tmp_path, [("帮我查看当前目录", "success")] * 4)
        db = _AsyncDB(path)
        asyncio.run(LongTermLearning(db, "default", "default")._ensure_table())
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO learned_patterns (tenant_id, agent_id, pattern_type, "
            "pattern_key, pattern_data, confidence, sample_count, created_at, updated_at) "
            "VALUES ('default','default','failure','other','{}',0.9,5,?,?)",
            (time.time(), time.time()),
        )
        conn.execute(
            "INSERT INTO learned_patterns (tenant_id, agent_id, pattern_type, "
            "pattern_key, pattern_data, confidence, sample_count, created_at, updated_at) "
            "VALUES ('default','default','file_combo','a.py+b.py','{}',0.9,4,?,?)",
            (time.time(), time.time()),
        )
        conn.commit()
        conn.close()

        self._run(path, lambda l: l.extract_patterns())
        rows = self._keys(path)
        conn = sqlite3.connect(path)
        types = {r[0] for r in conn.execute("SELECT pattern_type FROM learned_patterns").fetchall()}
        conn.close()
        assert "failure" in types
        assert "file_combo" in types
        assert rows == {"查看当前目录"}  # strategy侧只留有背书的重建行

    def test_idempotent_second_run_zero_removals(self, tmp_path):
        """幂等：驱逐后二跑removed_unbacked=0（不重复删）。"""
        path = self._mk_db(tmp_path, [("帮我查看当前目录", "success")] * 4)
        self._seed_pattern(path, "请回复ok两个字即可", 3)
        self._run(path, lambda l: l.extract_patterns())
        stats2 = self._run(path, lambda l: l.extract_patterns())
        assert stats2["pruned"]["removed_unbacked"] == 0
