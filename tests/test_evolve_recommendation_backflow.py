"""F2b-②c-1 推荐回流 evolve 面 —— SelfEvolution 进化提案真消费 learned_patterns。

覆盖：
1. classify_error_type 分类法单一源（提取/消费同键，禁字面抄写漂移）
2. 取数口 learned_failure_fixes_for / LongTermLearning.get_failure_fixes
   （同一读真源、ADVISORY_SCOPE 口径、空=零推荐禁硬凑、异常如实抛禁静默吞）
3. SelfEvolution._analyze_failure_patterns 建议回流（learned 修复优先、
   无学习数据=规则文案契约零变化、学习故障可见不阻断、签名去重零影响）

数据面全 tmp（experiences+learned_patterns 同库=生产同库形态），绝不写生产库。
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
from src.learn import long_term as lt_mod
from src.learn.experience_collector import ExperienceCollector
from src.learn.long_term import LongTermLearning


# 容缺取符号（控制组旧代码无此符号时按 AttributeError 炸在行为断言上，而非
# 收集期 ImportError——控制组语义内建，test_a2a_cancel_semantics.py 同款）。
def _missing(name: str):
    def _f(*args, **kwargs):
        raise AttributeError(f"old code has no {name}")

    return _f


classify_error_type = getattr(lt_mod, "classify_error_type", _missing("classify_error_type"))
learned_failure_fixes_for = getattr(
    lt_mod, "learned_failure_fixes_for", _missing("learned_failure_fixes_for")
)
ADVISORY_SCOPE = getattr(lt_mod, "ADVISORY_SCOPE", ("<missing>", "<missing>"))


class _AsyncDB:
    """生产适配器注入（test_recommendation_backflow.py 同款：测试即生产路径）。"""

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


class _BrokenDB:
    """学习读取故障桩：任何查询如实抛（禁静默吞造"无推荐"假象）。"""

    async def fetch(self, *a, **k):
        raise RuntimeError("learned read boom")

    async def execute(self, *a, **k):
        raise RuntimeError("learned read boom")

    async def fetchval(self, *a, **k):
        raise RuntimeError("learned read boom")


def _mk_db(tmp_path, errors=None, learned=None) -> Path:
    """建库：experiences（ExperienceCollector 生产 schema）+ learned_patterns
    （LongTermLearning 生产 schema），同一 tmp 库=生产同库形态（opensoul.db）。"""
    path = tmp_path / "main.db"
    conn = sqlite3.connect(path)
    ExperienceCollector(db_path=str(path), job_db_path="", eval_db_path="").ensure_schema(conn)
    now = time.time()
    for i, err in enumerate(errors or []):
        conn.execute(
            "INSERT INTO experiences (tenant_id, agent_id, action, "
            "intent_summary, outcome, error, created_at, metadata, source_ref) "
            "VALUES ('default','default','a','i','failure',?,?,?,?)",
            (err, now - i, "{}", f"evolve-backflow:{i}"),
        )
    conn.commit()
    conn.close()
    learning = LongTermLearning(_AsyncDB(path), "default", "default")
    asyncio.run(learning._ensure_table())
    for key, data in (learned or {}).items():
        asyncio.run(learning._upsert_pattern("failure", key, data, 0.8, data.get("count", 3)))
    return path


# ----------------------------------------------------------------------
# 1. 分类法单一源
# ----------------------------------------------------------------------
class TestClassifyErrorType:
    @pytest.mark.parametrize(
        "error,expected",
        [
            ("SyntaxError: bad indent", "syntax_error"),
            ("TimeoutError: provider x", "timeout"),
            ("Permission denied: /etc", "permission"),
            ("file not found: a.txt", "not_found"),
            ("something else entirely", "other"),
            ("", "other"),
        ],
    )
    def test_taxonomy_matrix(self, error, expected):
        assert classify_error_type(error) == expected

    def test_case_insensitive(self):
        assert classify_error_type("TIMEOUT after 30s") == "timeout"
        assert classify_error_type("SYNTAX err") == "syntax_error"

    def test_precedence_matches_legacy_chain(self):
        """判断序=修前内联链（syntax→timeout→permission→not found→other）。"""
        assert classify_error_type("timeout: file not found") == "timeout"
        assert classify_error_type("syntax permission") == "syntax_error"


# ----------------------------------------------------------------------
# 2. 取数口（同一读真源 + ADVISORY_SCOPE 口径）
# ----------------------------------------------------------------------
class TestLearnedFailureFixes:
    def test_learned_fixes_returned(self, tmp_path):
        path = _mk_db(
            tmp_path,
            learned={
                "timeout": {
                    "count": 5,
                    "common_fixes": ["加大超时", "分步执行", "加重试", "第四条截断"],
                }
            },
        )
        fixes = asyncio.run(learned_failure_fixes_for("TimeoutError: provider x", _AsyncDB(path)))
        assert fixes == ["加大超时", "分步执行", "加重试"]  # 上限3如实截断

    def test_no_row_empty(self, tmp_path):
        path = _mk_db(tmp_path)
        assert asyncio.run(learned_failure_fixes_for("timeout", _AsyncDB(path))) == []

    def test_empty_fixes_empty(self, tmp_path):
        path = _mk_db(tmp_path, learned={"timeout": {"count": 2, "common_fixes": []}})
        assert asyncio.run(learned_failure_fixes_for("timeout", _AsyncDB(path))) == []

    def test_scope_default_only(self, tmp_path):
        """口径=ADVISORY_SCOPE（学习产出在哪写就去哪读）：他租户行不外溢。"""
        path = _mk_db(tmp_path)
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO learned_patterns (tenant_id, agent_id, pattern_type, "
            "pattern_key, pattern_data, confidence, sample_count, created_at, updated_at) "
            "VALUES ('tenant-b','default','failure','timeout',?,0.8,3,?,?)",
            (json.dumps({"common_fixes": ["他租户建议"]}), time.time(), time.time()),
        )
        conn.commit()
        conn.close()
        assert asyncio.run(learned_failure_fixes_for("timeout", _AsyncDB(path))) == []
        assert ADVISORY_SCOPE == ("default", "default")  # 口径单一源锁死

    def test_broken_db_raises_not_silent(self):
        """异常如实抛禁静默吞造"无推荐"假象（learned_advisory_for 同契约）。"""
        with pytest.raises(RuntimeError):
            asyncio.run(learned_failure_fixes_for("timeout", _BrokenDB()))


# ----------------------------------------------------------------------
# 3. SelfEvolution 建议回流
# ----------------------------------------------------------------------
class TestEvolveRecommendationBackflow:
    def test_learned_fixes_win(self, tmp_path):
        path = _mk_db(
            tmp_path,
            errors=["TimeoutError: provider x"] * 3,
            learned={
                "timeout": {"count": 5, "common_fixes": ["加大超时预算", "分步执行", "第三条"]}
            },
        )
        evo = SelfEvolution(_AsyncDB(path))
        patterns = asyncio.run(evo._analyze_failure_patterns())
        assert len(patterns) == 1
        assert patterns[0]["recommendation"] == "加大超时预算；分步执行"  # 取前2
        assert patterns[0]["recommendation_source"] == "learned_patterns"
        assert patterns[0]["error_class"] == "timeout"
        assert patterns[0]["learned_error"] == ""

    def test_meta_carries_provenance(self, tmp_path):
        """analyze_and_evolve 全链：meta 带来源/类别，落库回读真源一致。"""
        path = _mk_db(
            tmp_path,
            errors=["TimeoutError: provider y"] * 3,
            learned={"timeout": {"count": 4, "common_fixes": ["真实学习修复"]}},
        )
        evo = SelfEvolution(_AsyncDB(path))
        evolutions = asyncio.run(evo.analyze_and_evolve())
        failure_evos = [e for e in evolutions if e["type"] == "failure_avoidance"]
        assert len(failure_evos) == 1
        assert failure_evos[0]["action"] == "自动规避: 真实学习修复"
        meta = failure_evos[0]["meta"]
        assert meta["recommendation_source"] == "learned_patterns"
        assert meta["error_class"] == "timeout"
        conn = sqlite3.connect(path)
        row = conn.execute(
            "SELECT meta FROM evolution_log WHERE evolution_type='failure_avoidance'"
        ).fetchone()
        conn.close()
        stored = json.loads(row[0])
        assert stored["recommendation_source"] == "learned_patterns"
        assert stored["error_class"] == "timeout"

    def test_rule_fallback_contract_unchanged(self, tmp_path):
        """契约零变化控制组：无学习数据时建议文案与修前内联链逐字一致。"""
        expected = {
            "syntax error in script": "执行前做语法检查",
            "TimeoutError: x": "增加超时时间或分步执行",
            "whatever weird": "增加前置检查",
            "Permission denied": "增加前置检查",
            "file not found": "增加前置检查",
        }
        errors = [e for e in expected for _ in range(3)]
        path = _mk_db(tmp_path, errors=errors)
        evo = SelfEvolution(_AsyncDB(path))
        patterns = asyncio.run(evo._analyze_failure_patterns())
        got = {p["error_type"]: p for p in patterns}
        assert set(got) == set(expected)
        # 契约零变化（不变面）：文案与修前内联链逐字一致
        for err, rec in expected.items():
            assert got[err]["recommendation"] == rec

    def test_rule_fallback_source_labeled(self, tmp_path):
        """新契约：无学习数据时建议来源如实标注 rule。"""
        path = _mk_db(tmp_path, errors=["TimeoutError: x"] * 3)
        evo = SelfEvolution(_AsyncDB(path))
        patterns = asyncio.run(evo._analyze_failure_patterns())
        assert patterns[0]["recommendation_source"] == "rule"

    def test_empty_fixes_fallback_rule(self, tmp_path):
        """学习行无有效修复建议→回落规则文案（禁硬凑"注意规避"）。"""
        path = _mk_db(
            tmp_path,
            errors=["TimeoutError: z"] * 3,
            learned={"timeout": {"count": 2, "common_fixes": ["", None]}},
        )
        evo = SelfEvolution(_AsyncDB(path))
        patterns = asyncio.run(evo._analyze_failure_patterns())
        assert patterns[0]["recommendation"] == "增加超时时间或分步执行"

    def test_learned_failure_visible_not_blocking(self, tmp_path, monkeypatch):
        """学习读取故障：不阻断进化分析、回落规则、learned_error 带真因可见。"""
        import src.heredity.self_evolution as se

        def _boom(error, db_pool=None):
            raise RuntimeError("learned read boom")

        monkeypatch.setattr(se, "learned_failure_fixes_for", _boom)
        path = _mk_db(tmp_path, errors=["TimeoutError: w"] * 3)
        evo = SelfEvolution(_AsyncDB(path))
        evolutions = asyncio.run(evo.analyze_and_evolve())
        failure_evos = [e for e in evolutions if e["type"] == "failure_avoidance"]
        assert len(failure_evos) == 1  # 分析照常产出提案
        assert failure_evos[0]["action"] == "自动规避: 增加超时时间或分步执行"
        assert failure_evos[0]["meta"]["recommendation_source"] == "rule"
        assert "learned read boom" in failure_evos[0]["meta"]["learned_error"]

    def test_signature_dedup_unaffected(self, tmp_path):
        """meta 加性键不进签名：候选与存量行同签名（去重语义零变化）。"""
        evo = SelfEvolution(_AsyncDB(tmp_path / "sig.db"))
        candidate = {
            "evolution_type": "failure_avoidance",
            "reason": "高频失败: E (3次)",
            "meta": None,
        }
        stored = {
            "evolution_type": "failure_avoidance",
            "reason": "高频失败: E (5次)",
            "meta": json.dumps(
                {
                    "pattern_key": "E",
                    "count": 5,
                    "error_class": "other",
                    "recommendation_source": "learned_patterns",
                    "learned_error": "",
                }
            ),
        }
        assert evo._row_signature(candidate) == evo._row_signature(stored)

    def test_second_run_no_duplicate_proposal(self, tmp_path):
        """同一发现不复读：学习建议改变 action 文本也不产生第二案（签名去重）。"""
        path = _mk_db(
            tmp_path,
            errors=["TimeoutError: dup"] * 3,
            learned={"timeout": {"count": 4, "common_fixes": ["学习建议"]}},
        )
        evo = SelfEvolution(_AsyncDB(path))
        first = asyncio.run(evo.analyze_and_evolve())
        second = asyncio.run(evo.analyze_and_evolve())
        assert len([e for e in first if e["type"] == "failure_avoidance"]) == 1
        assert [e for e in second if e["type"] == "failure_avoidance"] == []


# ----------------------------------------------------------------------
# 4. 提取侧与消费侧同键（单一源）
# ----------------------------------------------------------------------
class TestExtractSharedClassifier:
    def test_failure_rows_keyed_by_shared_classifier(self, tmp_path):
        """提取侧 failure 行 pattern_key=classify_error_type（消费侧回查才对得上）。"""
        errors = ["SyntaxError: bad"] * 2 + ["Permission denied"] * 2 + ["nope"] * 2
        path = _mk_db(tmp_path, errors=errors)
        learning = LongTermLearning(_AsyncDB(path), "default", "default")
        asyncio.run(learning.extract_patterns())
        conn = sqlite3.connect(path)
        keys = {
            r[0]
            for r in conn.execute(
                "SELECT pattern_key FROM learned_patterns WHERE pattern_type='failure'"
            )
        }
        conn.close()
        assert keys == {"syntax_error", "permission", "other"}

    def test_classifier_single_source(self):
        """静态契约：消费侧真调用 classify_error_type，修前内联判断链不再手抄。"""
        lt = Path("src/learn/long_term.py").read_text()
        se_text = Path("src/heredity/self_evolution.py").read_text()
        assert "def classify_error_type" in lt
        assert "classify_error_type(error)" in se_text
        assert 'error_type = "syntax_error"' not in se_text
