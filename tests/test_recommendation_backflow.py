"""F2b-② 推荐回流执行流程测试：learned_patterns → 执行 prompt（行为优化真闭环）。

F2 审计定性真断点：get_recommendations 全仓消费=仅 GET /api/brain/recommendations
API 出口，仓内执行流程消费=0。本文件钉住：
1. 格式化纯函数（空推荐零注入、三类型如实渲染、top-k 截断、免责块头）
2. 检索语义（精确命中优先、字面相关度兜底、无关零注入、寒暄/空拒、top-k 限制）
3. 真实执行路径接线（_execute_task_impl 真调用取数口 → 执行 prompt 真注入
   → learned-context 事件可观测；无推荐=零注入控制组；学习层故障不阻断执行）
数据面全 tmp（learned_patterns 行直插），db_pool 换 tmp 走生产适配器契约。
"""

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.learn.long_term import (
    LongTermLearning,
    format_advisory_for_prompt,
    normalize_intent_key,
)


class _AsyncDB:
    """生产适配器注入（test_proactive_pattern_extract.py 同款：测试即生产路径）。"""

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


def _mk_db(tmp_path, patterns=None) -> Path:
    """建库：learned_patterns（LongTermLearning 生产 schema）+ 可选模式行。"""
    path = tmp_path / "learn.db"
    learning = LongTermLearning(_AsyncDB(path), "default", "default")
    asyncio.run(learning._ensure_table())
    conn = sqlite3.connect(path)
    now = time.time()
    for rtype, key, data, conf, samples in patterns or []:
        conn.execute(
            "INSERT INTO learned_patterns (tenant_id, agent_id, pattern_type, "
            "pattern_key, pattern_data, confidence, sample_count, created_at, updated_at) "
            "VALUES ('default','default',?,?,?,?,?,?,?)",
            (rtype, key, json.dumps(data), conf, samples, now, now),
        )
    conn.commit()
    conn.close()
    return path


def _query(path: Path, intent: str, top_k: int = 3) -> list:
    learning = LongTermLearning(_AsyncDB(path), "default", "default")
    return asyncio.run(learning.get_recommendations_for_prompt(intent, top_k=top_k))


# ── 1. 格式化纯函数 ──────────────────────────────────────────────────────


class TestFormatAdvisory:
    def test_empty_recs_zero_injection(self):
        assert format_advisory_for_prompt([]) == ""
        assert format_advisory_for_prompt(None) == ""

    def test_strategy_patch_and_stepwise(self):
        recs = [
            {
                "type": "strategy",
                "key": "查看当前目录",
                "data": {"success_rate": 0.8, "total": 4, "recommendation": "patch", "variants": 1},
                "confidence": 0.8,
                "samples": 4,
            },
            {
                "type": "strategy",
                "key": "用python写一个快速排序算法",
                "data": {
                    "success_rate": 0.0,
                    "total": 4,
                    "recommendation": "stepwise",
                    "variants": 1,
                },
                "confidence": 0.0,
                "samples": 4,
            },
        ]
        text = format_advisory_for_prompt(recs)
        assert "历史经验建议" in text and "仅供参考" in text and "以任务要求为准" in text
        assert "1. 任务「查看当前目录」历史样本4次、成功率80%" in text
        assert "小步补丁式修改" in text
        assert "2. 任务「用python写一个快速排序算法」" in text
        assert "分步执行、逐步验证" in text

    def test_all_types_rendered_honestly(self):
        recs = [
            {
                "type": "file_combo",
                "key": "a.py+b.py",
                "data": {"files": ["a.py", "b.py"], "co_occurrence": 5},
                "confidence": 0.5,
                "samples": 5,
            },
            {
                "type": "failure",
                "key": "timeout",
                "data": {"count": 3, "common_fixes": ["加超时重试", "改异步"]},
                "confidence": 0.6,
                "samples": 3,
            },
            {
                "type": "unknown_type",
                "key": "神秘模式",
                "data": {},
                "confidence": 0.1,
                "samples": 1,
            },
        ]
        text = format_advisory_for_prompt(recs)
        assert "1. 文件组合 a.py+b.py 历史上常一起修改（共现5次）" in text
        assert "2. 易错点「timeout」（历史3次）：加超时重试；改异步" in text
        assert "3. 「神秘模式」（unknown_type，样本1）" in text  # 未知类型如实标注不硬造

    def test_top_k_truncation(self):
        recs = [
            {"type": "strategy", "key": f"k{i}", "data": {"recommendation": "patch"}, "samples": 1}
            for i in range(5)
        ]
        text = format_advisory_for_prompt(recs, top_k=2)
        assert "1. " in text and "2. " in text and "3. " not in text


# ── 2. 检索语义（get_recommendations_for_prompt）─────────────────────────


class TestRecommendationsForPrompt:
    def test_precise_match_preferred(self, tmp_path):
        path = _mk_db(
            tmp_path,
            [
                (
                    "strategy",
                    "查看当前目录",
                    {"success_rate": 1.0, "recommendation": "patch"},
                    1.0,
                    4,
                ),
                (
                    "strategy",
                    "查看当前目录内容",
                    {"success_rate": 0.5, "recommendation": "stepwise"},
                    0.5,
                    3,
                ),
            ],
        )
        recs = _query(path, "帮我查看当前目录")  # normalize 后精确命中"查看当前目录"
        assert [r["key"] for r in recs] == ["查看当前目录"]  # 精确档不带近似 key
        assert recs[0]["samples"] == 4

    def test_fallback_overlap_relevance(self, tmp_path):
        path = _mk_db(
            tmp_path,
            [
                (
                    "strategy",
                    "用 write_file 创建文件 /tmp/demo/y.py 内容为 print 2",
                    {"success_rate": 0.9, "recommendation": "patch"},
                    0.9,
                    3,
                ),
                (
                    "strategy",
                    "background_job:hippo.dream",
                    {"success_rate": 1.0, "recommendation": "patch"},
                    1.0,
                    111,
                ),
            ],
        )
        recs = _query(path, "用 write_file 创建文件 /tmp/demo/x.py 内容为 print 1")
        keys = [r["key"] for r in recs]
        assert keys == ["用 write_file 创建文件 /tmp/demo/y.py 内容为 print 2"], keys
        assert "background_job:hippo.dream" not in keys  # 无关高置信模式不许混入

    def test_no_relevance_zero_injection(self, tmp_path):
        path = _mk_db(
            tmp_path,
            [
                ("strategy", "background_job:hippo.dream", {"recommendation": "patch"}, 1.0, 111),
                ("strategy", "你是谁", {"recommendation": "patch"}, 1.0, 3),
            ],
        )
        recs = _query(path, "修复超时bug")
        assert recs == []
        assert format_advisory_for_prompt(recs) == ""  # 无关=零注入（宁缺勿滥）

    def test_phatic_and_empty_rejected(self, tmp_path):
        path = _mk_db(
            tmp_path,
            [("strategy", "你好", {"recommendation": "patch"}, 1.0, 3)],
        )
        assert _query(path, "你好") == []
        assert _query(path, "") == []

    def test_top_k_limit(self, tmp_path):
        rows = [
            (
                "strategy",
                f"用 write_file 创建文件 /tmp/demo/x{i}.py 内容为 print {i}",
                {"recommendation": "patch"},
                1.0 - i * 0.1,
                3,
            )
            for i in range(5)
        ]
        path = _mk_db(tmp_path, rows)
        # 意图自身不是任何 key（精确档空）→ 相关度兜底取 top-k
        recs = _query(path, "用 write_file 创建文件 /tmp/demo/x9.py 内容为 print 9", top_k=2)
        assert len(recs) == 2


# ── 3. 真实执行路径接线（_execute_task_impl 真调用真注入）─────────────────


@pytest.fixture()
def backflow_env(tmp_path, monkeypatch):
    """真实 _execute_task_impl + 真取数口（db_pool 换 tmp 生产适配器）+ agent 执行桩。"""
    import src.api.ai_groups as ai_groups
    import src.database.postgres as pg
    from src.api import brain as brain_api
    from src.hippo.memory_store import MemoryStore

    monkeypatch.setattr(ai_groups, "DB_PATH", tmp_path / "ai_groups.db")
    ai_groups.init_db()
    monkeypatch.setattr(brain_api, "store", MemoryStore())

    learn_path = tmp_path / "learn.db"
    monkeypatch.setattr(pg, "db_pool", _AsyncDB(learn_path))

    calls = {"n": 0}

    async def fake_run_agent_proxy(agent_id, text, on_event=None, **kwargs):
        calls["n"] += 1
        calls["goal"] = text
        return ("桩执行结果：任务完成", "stub-agent", True)

    monkeypatch.setattr(ai_groups, "run_agent_proxy", fake_run_agent_proxy)

    def seed(patterns):
        conn = sqlite3.connect(learn_path)
        conn.close()
        # 重建（fixture 期 learn.db 可能已被 _ensure_table 建好，直接插行）
        learning = LongTermLearning(_AsyncDB(learn_path), "default", "default")
        asyncio.run(learning._ensure_table())
        conn = sqlite3.connect(learn_path)
        now = time.time()
        for rtype, key, data, conf, samples in patterns:
            conn.execute(
                "INSERT INTO learned_patterns (tenant_id, agent_id, pattern_type, "
                "pattern_key, pattern_data, confidence, sample_count, created_at, updated_at) "
                "VALUES ('default','default',?,?,?,?,?,?,?)",
                (rtype, key, json.dumps(data), conf, samples, now, now),
            )
        conn.commit()
        conn.close()

    def make_task(goal):
        conn = ai_groups.get_db()
        try:
            gid, tid = "g-backflow", "t-backflow"
            now = "2026-10-11T12:00:00"
            conn.execute(
                "INSERT OR REPLACE INTO ai_groups (id,name,description,status,created_at) VALUES (?,?,?,?,?)",
                (gid, "backflow", "", "active", now),
            )
            conn.execute(
                "INSERT OR REPLACE INTO ai_group_tasks (id,group_id,goal,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (tid, gid, goal, "pending", now, now),
            )
            conn.commit()
        finally:
            conn.close()
        return gid, tid

    def task_events(tid):
        conn = ai_groups.get_db()
        try:
            rows = conn.execute(
                "SELECT event_type, content FROM task_execution_events WHERE task_id=? ORDER BY id",
                (tid,),
            ).fetchall()
            return [(r["event_type"], r["content"]) for r in rows]
        finally:
            conn.close()

    yield {
        "ai_groups": ai_groups,
        "calls": calls,
        "seed": seed,
        "make_task": make_task,
        "task_events": task_events,
        "learn_path": learn_path,
    }


class TestBackflowWiredIntoExecution:
    def test_advisory_injected_into_real_execution_prompt(self, backflow_env):
        """真取数口+真注入：learned_patterns → 执行prompt → agent 拿到建议块。"""
        env = backflow_env
        ai_groups = env["ai_groups"]
        goal = "帮我查看当前目录"
        env["seed"](
            [("strategy", "查看当前目录", {"success_rate": 1.0, "recommendation": "patch"}, 1.0, 4)]
        )
        gid, tid = env["make_task"](goal)

        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal=goal)
        result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req))

        assert result["status"] == "scored"
        assert env["calls"]["n"] == 1
        prompt = env["calls"]["goal"]
        assert prompt.startswith(goal)  # 任务本体要求在前，建议块只追加
        assert "历史经验建议" in prompt and "以任务要求为准" in prompt
        assert "查看当前目录" in prompt
        types = [t for t, _ in env["task_events"](tid)]
        assert "learned-context" in types, f"learned-context事件缺失: {types}"

    def test_no_match_zero_injection_control(self, backflow_env):
        """控制组：无相关学习产出 → prompt 与 goal 逐字一致、零学习事件。"""
        env = backflow_env
        ai_groups = env["ai_groups"]
        goal = "修复超时bug"
        env["seed"](
            [("strategy", "background_job:hippo.dream", {"recommendation": "patch"}, 1.0, 111)]
        )
        gid, tid = env["make_task"](goal)

        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal=goal)
        result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req))

        assert result["status"] == "scored"
        assert env["calls"]["goal"] == goal  # 零注入=逐字一致（禁半注入噪声）
        types = [t for t, _ in env["task_events"](tid)]
        assert "learned-context" not in types

    def test_learning_failure_does_not_block_execution(self, backflow_env, monkeypatch):
        """学习层自身故障不阻断执行，但 learned-context-error 必须可见（禁静默）。"""
        env = backflow_env
        ai_groups = env["ai_groups"]
        goal = "写个测试"

        async def broken_advisory(run_goal):
            raise RuntimeError("学习库炸了")

        monkeypatch.setattr(ai_groups, "_learned_advisory_for", broken_advisory)
        gid, tid = env["make_task"](goal)
        req = ai_groups.ExecuteTaskRequest(agent_id="mimo", goal=goal)
        result = asyncio.run(ai_groups._execute_task_impl(gid, tid, req))

        assert result["status"] == "scored"
        assert env["calls"]["n"] == 1
        assert env["calls"]["goal"] == goal
        events = env["task_events"](tid)
        types = [t for t, _ in events]
        assert "learned-context-error" in types, f"故障必须可见: {types}"
        err = next(c for t, c in events if t == "learned-context-error")
        assert "学习库炸了" in err
