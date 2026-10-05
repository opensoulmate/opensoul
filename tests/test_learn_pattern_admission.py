"""intent准入 gatekeeper 测试（2026-10-05轮：learned_patterns用户话术噪声治理）。

生产实证：learned_patterns.strategy 被四类噪声占据——prompt模板片段
（"## 角色"×65/"## 任务"×57，样本数全表前二）、寒暄（"你好"×15）、
占位符（"unknown"）、原样用户话术直提key（"帮我查看当前目录"——措辞
不归并不泛化）。get_recommendations top-10被confidence=1.0的噪声占满。

本文件钉住 LobeChat gatekeeper 范式（feature-matrix/26-lobe-chat-source.md：
"不加过滤的记忆=垃圾堆积"）：
1. normalize_intent_key：填充词剥离/措辞变体归并/结构化key原样保留
2. admit_intent：template/placeholder/phatic/too_short/empty 五类拒绝+原因码
3. 提取侧：噪声不进pattern_key、skipped按原因码计数、变体cnt求和
4. 存量自愈：旧行过不了门删除、未归一陈旧行删除重建、幂等、不误删failure行
5. 查询侧：入参与key同归一（查得到写得进）、寒暄入参不配推荐
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
    normalize_intent_key,
)


class _AsyncDB:
    """生产适配器注入（test_evolution_proposal_lifecycle.py同款：测试即生产路径）。"""

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
    def _mk_db(self, tmp_path, experiences=None):
        """建库：experiences（collector生产schema）+ 可选经验行。"""
        path = tmp_path / "learn.db"
        conn = sqlite3.connect(path)
        c = ExperienceCollector(db_path=str(path), job_db_path="", eval_db_path="")
        c.ensure_schema(conn)
        now = time.time()
        for i, (intent, outcome) in enumerate(experiences or []):
            conn.execute(
                "INSERT INTO experiences (tenant_id, agent_id, action, "
                "intent_summary, outcome, error, created_at, metadata, source_ref) "
                "VALUES ('default','default','a',?,?,'',?, '{}', ?)",
                (intent, outcome, now - i, f"adm:{intent}:{outcome}:{i}"),
            )
        conn.commit()
        conn.close()
        return path

    def _seed_pattern(self, path, pattern_type, key, cnt=3):
        """直接种learned_patterns旧行（模拟治理前存量）。表DDL走生产_ensure_table。"""
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
            "SELECT pattern_type, pattern_key, sample_count FROM learned_patterns "
            "ORDER BY pattern_type, pattern_key"
        ).fetchall()
        conn.close()
        return rows

    def _run(self, path, fn, *args):
        db = _AsyncDB(path)
        learning = LongTermLearning(db, "default", "default")
        return asyncio.run(fn(learning, *args))


class TestNormalizeIntentKey(_Base):
    def test_politeness_variants_converge(self):
        """口语变体归并同一key（写/查同一真源的前提）。"""
        assert normalize_intent_key("帮我查看当前目录") == "查看当前目录"
        assert normalize_intent_key("请帮我查看当前目录") == "查看当前目录"
        assert normalize_intent_key("查看当前目录") == "查看当前目录"
        assert normalize_intent_key("帮我查看一下") == "查看"

    def test_structured_keys_untouched(self):
        """background_job:*/eval_case:* 结构化key不被填充词误伤。"""
        assert normalize_intent_key("background_job:hippo.dream") == "background_job:hippo.dream"
        assert (
            normalize_intent_key("eval_case:ds_911f0b5f02ab:arith")
            == "eval_case:ds_911f0b5f02ab:arith"
        )

    def test_no_bare_verb_stripping(self):
        """单字"帮"/"请"不剥——"帮助X"/"请求X"不能变"助X"/"求X"。"""
        assert normalize_intent_key("帮助修复解析器") == "帮助修复解析器"
        assert normalize_intent_key("请求删除临时文件") == "请求删除临时文件"

    def test_casefold_punct_collapse(self):
        """casefold+去标点emoji+折叠空白。"""
        assert normalize_intent_key("帮我修Bug！！") == "修bug"
        assert (
            normalize_intent_key("/home/a/b.html  把这个文件发给我")
            == "/home/a/b.html 把这个文件发给我"
        )

    def test_interior_yixia_not_stripped(self):
        """已知边界（保守取舍）：内嵌"一下"不剥——宁可分裂不误合并。"""
        assert normalize_intent_key("请帮我查看一下当前目录") == "查看一下当前目录"
        assert normalize_intent_key("查一下午日志") == "查一下午日志"


class TestAdmitIntent(_Base):
    def test_template_rejected(self):
        assert admit_intent("## 角色") == (False, "template")
        assert admit_intent("## 任务") == (False, "template")
        assert admit_intent("```python") == (False, "template")
        assert admit_intent("system: you are a helper") == (False, "template")
        assert admit_intent("<!-- comment -->") == (False, "template")

    def test_heading_like_but_not_template(self):
        """'#FF0000是什么颜色'是任务不是markdown标题——不误杀。"""
        assert admit_intent("#FF0000是什么颜色") == (True, "ok")

    def test_placeholder_rejected(self):
        assert admit_intent("unknown") == (False, "placeholder")
        assert admit_intent("None") == (False, "placeholder")
        assert admit_intent("null") == (False, "placeholder")

    def test_phatic_rejected(self):
        assert admit_intent("你好") == (False, "phatic")
        assert admit_intent("你好啊") == (False, "phatic")  # 尾部语气词剥后仍寒暄
        assert admit_intent("好的") == (False, "phatic")
        assert admit_intent("thanks") == (False, "phatic")
        assert admit_intent("谢谢！") == (False, "phatic")

    def test_empty_and_too_short_rejected(self):
        assert admit_intent("") == (False, "empty")
        assert admit_intent("   ") == (False, "empty")
        assert admit_intent("？？") == (False, "empty")  # 纯标点→归一后空
        assert admit_intent("看") == (False, "too_short")
        assert admit_intent("ab") == (False, "too_short")

    def test_task_intents_admitted(self):
        assert admit_intent("帮我查看当前目录") == (True, "ok")
        assert admit_intent("用Python写一个快速排序算法") == (True, "ok")
        assert admit_intent("background_job:hippo.dream") == (True, "ok")


class TestExtractGate(_Base):
    def test_noise_blocked_and_variants_merged(self, tmp_path):
        """噪声四类不进pattern_key；措辞变体归并cnt求和；skipped按原因码计数。"""
        exps = (
            [("## 角色", "success")] * 4  # template
            + [("你好", "success")] * 3  # phatic
            + [("unknown", "success")] * 3  # placeholder
            + [("帮我查看当前目录", "success")] * 3  # 变体1
            + [("请帮我查看当前目录", "success")] * 3  # 变体2（同key）
            + [("用Python写一个快速排序算法", "failure")] * 3
        )
        path = self._mk_db(tmp_path, exps)
        stats = self._run(path, lambda l: l.extract_patterns())
        keys = {k for t, k, _c in self._pattern_keys(path) if t == "strategy"}

        # 噪声四类不进
        assert "## 角色" not in keys
        assert "你好" not in keys
        assert "unknown" not in keys
        # 变体归并成单一归一key
        assert keys == {"查看当前目录", "用python写一个快速排序算法"}
        # skipped按原因码计数（观测面：被拒的不静默消失）
        assert stats["strategy"]["skipped"] == {
            "template": 4,
            "phatic": 3,
            "placeholder": 3,
        }
        assert stats["strategy"]["considered"] == 6  # GROUP BY intent,outcome组数
        assert stats["strategy"]["admitted"] == 2

        rows = {(k): c for _t, k, c in self._pattern_keys(path)}
        assert rows["查看当前目录"] == 6  # 3+3变体求和
        assert rows["用python写一个快速排序算法"] == 3

    def test_variants_count_recorded(self, tmp_path):
        exps = [("帮我查看当前目录", "success")] * 3 + [("请帮我查看当前目录", "success")] * 3
        path = self._mk_db(tmp_path, exps)
        self._run(path, lambda l: l.extract_patterns())
        conn = sqlite3.connect(path)
        data = conn.execute(
            "SELECT pattern_data FROM learned_patterns WHERE pattern_key='查看当前目录'"
        ).fetchone()[0]
        conn.close()
        assert json.loads(data)["variants"] == 2

    def test_failure_and_file_combo_types_kept(self, tmp_path):
        """准入门只管strategy key——failure/file_combo型不误伤。"""
        path = self._mk_db(tmp_path, [("帮我修解析器", "success")] * 3)
        self._seed_pattern(path, "failure", "other", 5)
        self._seed_pattern(path, "file_combo", "a.py+b.py", 4)
        self._run(path, lambda l: l.extract_patterns())
        rows = self._pattern_keys(path)
        assert ("failure", "other", 5) in rows
        assert ("file_combo", "a.py+b.py", 4) in rows


class TestPruneLegacy(_Base):
    def test_legacy_noise_pruned_and_stale_rebuilt(self, tmp_path):
        """存量自愈：噪声旧行删除、未归一旧行删后按归一key重建。

        background_job:hippo.dream 只给2条背书（<3不触发extract重建）——
        它的存活证明规则3的保守边界：有任意证据就不驱逐，只删零背书行。
        """
        exps = (
            [("帮我查看当前目录", "success")] * 4
            + [("background_job:hippo.dream", "success")] * 2  # 背书2条<3，不重建
        )
        path = self._mk_db(tmp_path, exps)
        self._seed_pattern(path, "strategy", "## 角色", 65)  # template
        self._seed_pattern(path, "strategy", "你好", 15)  # phatic
        self._seed_pattern(path, "strategy", "unknown", 3)  # placeholder
        self._seed_pattern(path, "strategy", "帮我查看当前目录", 4)  # 未归一陈旧key
        self._seed_pattern(path, "strategy", "background_job:hippo.dream", 50)  # 结构化合法+有背书

        stats = self._run(path, lambda l: l.extract_patterns())
        keys = {k for _t, k, _c in self._pattern_keys(path)}
        assert "## 角色" not in keys
        assert "你好" not in keys
        assert "unknown" not in keys
        assert "帮我查看当前目录" not in keys  # 陈旧key删后重建
        assert "查看当前目录" in keys  # 归一key重建自experiences
        assert "background_job:hippo.dream" in keys  # 合法结构化key+有背书不动
        assert stats["pruned"]["removed_inadmissible"] == 3
        assert stats["pruned"]["removed_stale_key"] == 1
        assert stats["pruned"]["removed_unbacked"] == 0  # 有背书不驱逐

    def test_prune_idempotent(self, tmp_path):
        exps = [("帮我查看当前目录", "success")] * 4
        path = self._mk_db(tmp_path, exps)
        self._seed_pattern(path, "strategy", "你好", 15)
        self._run(path, lambda l: l.extract_patterns())
        stats2 = self._run(path, lambda l: l.extract_patterns())
        assert stats2["pruned"] == {
            "scanned": 1,
            "removed_inadmissible": 0,
            "removed_stale_key": 0,
            "removed_unbacked": 0,
        }


class TestRecommendations(_Base):
    def test_query_normalized_matches_stored_key(self, tmp_path):
        """查得到写得进：入参措辞变体命中归一key的模式。"""
        exps = [("帮我查看当前目录", "success")] * 4
        path = self._mk_db(tmp_path, exps)
        self._run(path, lambda l: l.extract_patterns())
        recs = self._run(path, lambda l: l.get_recommendations("请帮我查看当前目录"))
        assert len(recs) == 1
        assert recs[0]["key"] == "查看当前目录"

    def test_phatic_query_gets_nothing(self, tmp_path):
        exps = [("帮我查看当前目录", "success")] * 4
        path = self._mk_db(tmp_path, exps)
        self._run(path, lambda l: l.extract_patterns())
        assert self._run(path, lambda l: l.get_recommendations("你好")) == []

    def test_unfiltered_listing_clean_of_noise(self, tmp_path):
        exps = (
            [("## 角色", "success")] * 4
            + [("你好", "success")] * 4
            + [("帮我查看当前目录", "success")] * 4
        )
        path = self._mk_db(tmp_path, exps)
        self._run(path, lambda l: l.extract_patterns())
        recs = self._run(path, lambda l: l.get_recommendations())
        assert [r["key"] for r in recs] == ["查看当前目录"]
