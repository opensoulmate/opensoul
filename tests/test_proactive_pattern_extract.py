"""F2b-① 定期学习收口测试：extract_patterns 进 proactive 调度。

F2 审计定性半断点：extract_patterns 全仓唯一调用点=POST /api/brain/learn
手动 API，无任何调度触发 → learned_patterns 5 天停更而 experiences 持续
积累（生产实证：learned_patterns MAX updated_at 2026-10-06 vs experiences
滚动到 2026-10-11）。本文件钉住：
1. pattern_extract 行为真提取（experiences→learned_patterns 真落库、统计可观测）
2. 引擎路径真执行（run_watcher 留痕 + 提取结果进 result）
3. 调度接线（register_default_watchers 注册 pattern_extract，回调即行为本体）
4. 幂等（重复提取零重复行）+ 空经验诚实零产出不炸
"""

import asyncio
import sqlite3
import sys
import time
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.learn.experience_collector import ExperienceCollector
from src.will.proactive import ProactiveEngine
from src.will.proactive_behaviors import (
    DEFAULT_INTERVALS,
    pattern_extract,
    register_default_watchers,
)


class _AsyncDB:
    """生产适配器注入（test_learn_pattern_admission.py 同款：测试即生产路径）。"""

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


def _mk_db(tmp_path, experiences=None) -> Path:
    """建库：experiences（collector 生产 schema）+ 可选经验行。"""
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
            (intent, outcome, now - i, f"f2b:{intent}:{outcome}:{i}"),
        )
    conn.commit()
    conn.close()
    return path


def _pattern_rows(path: Path) -> list:
    conn = sqlite3.connect(path)
    rows = conn.execute(
        "SELECT pattern_type, pattern_key, sample_count FROM learned_patterns "
        "ORDER BY pattern_type, pattern_key"
    ).fetchall()
    conn.close()
    return rows


# ── ① 行为本体：真提取真落库 ──────────────────────────────────


def test_pattern_extract_real_effect(tmp_path):
    """3 条同意图经验 → 真提取出 strategy 模式行（extract_patterns 本体零改动）。"""
    path = _mk_db(
        tmp_path,
        experiences=[
            ("帮我查看当前目录", "success"),
            ("帮我查看当前目录", "success"),
            ("帮我查看当前目录", "success"),
        ],
    )
    res = asyncio.run(pattern_extract(db_pool=_AsyncDB(path)))
    assert res["extraction"]["strategy"]["admitted"] == 1
    assert res["patterns"] == 1  # get_stats 真回读 learned_patterns
    rows = _pattern_rows(path)
    assert rows == [("strategy", "查看当前目录", 3)]  # 归一 key + cnt 求和真落库


def test_pattern_extract_idempotent(tmp_path):
    """重复提取零重复行（upsert 同 key 更新不增行，可反复调度）。"""
    path = _mk_db(
        tmp_path,
        experiences=[("帮我查看当前目录", "success")] * 3,
    )
    asyncio.run(pattern_extract(db_pool=_AsyncDB(path)))
    res2 = asyncio.run(pattern_extract(db_pool=_AsyncDB(path)))
    assert res2["patterns"] == 1
    assert len(_pattern_rows(path)) == 1


def test_pattern_extract_empty_experiences(tmp_path):
    """空经验诚实零产出不炸（admitted=0/patterns=0，禁虚报提取）。"""
    path = _mk_db(tmp_path, experiences=[])
    res = asyncio.run(pattern_extract(db_pool=_AsyncDB(path)))
    assert res["extraction"]["strategy"]["admitted"] == 0
    assert res["patterns"] == 0
    assert _pattern_rows(path) == []


# ── ② 引擎路径：run_watcher 真执行留痕 ─────────────────────────


def test_pattern_extract_via_engine_watcher(tmp_path):
    """经 ProactiveEngine.run_watcher 执行：ok 留痕 + 提取结果进 result 可观测。"""
    path = _mk_db(tmp_path, experiences=[("帮我查看当前目录", "success")] * 3)
    eng = ProactiveEngine(db_path="")
    eng.register_watcher(
        "pattern_extract",
        partial(pattern_extract, db_pool=_AsyncDB(path)),
        interval_seconds=DEFAULT_INTERVALS["pattern_extract"],
    )
    res = asyncio.run(eng.run_watcher("pattern_extract"))
    assert res["ok"] is True
    assert res["result"]["patterns"] == 1
    assert res["result"]["extraction"]["strategy"]["admitted"] == 1
    events = eng.recent_events()
    assert events[0]["watcher"] == "pattern_extract"
    assert events[0]["ok"] is True


# ── ③ 调度接线：默认注册真含 pattern_extract ────────────────────


def test_pattern_extract_wired_into_default_watchers():
    """register_default_watchers 注册 pattern_extract，回调=行为本体（非空壳）。"""
    eng = ProactiveEngine(db_path="")
    names = register_default_watchers(eng)
    assert "pattern_extract" in names
    cb = eng._watchers["pattern_extract"]["callback"]
    assert getattr(cb, "func", None) is pattern_extract
    assert (
        eng._watchers["pattern_extract"]["interval_seconds"] == DEFAULT_INTERVALS["pattern_extract"]
    )
