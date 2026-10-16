"""G1-② 自我进化自动调度测试。

G0 审计定性半断点：analyze_and_evolve→进化审批管线真链只经 POST /api/brain/evolve
手动触发（触发面全手动）。本拍=run_evolution_cycle 单一代码路径抽取 + proactive
self_evolve 行为挂进心跳调度（与端点共用同一编排，F2b-① pattern_extract 纪律）。

测的五层：
1. 方法本体不变面（SelfEvolution.analyze_and_evolve 既有语义零改动，控制组对照面）
2. run_evolution_cycle 真效果（种子失败经验→真分析→真声明进审批管线）
3. 单一代码路径（brain /evolve 端点真委托 run_evolution_cycle，禁第二套编排）
4. self_evolve 行为真执行（默认池归 env 数据面 PROACT-③ + run_watcher 留痕）
5. 注册缝（register_default_watchers 注册 self_evolve，回调=行为本体非空壳）

数据面全 tmp（OPENSOUL_DB env autouse / heredity evolution_engine autouse /
显式 db_pool 注入），绝不写生产 opensoul.db/evolution.db。
"""

import asyncio
import os
import sqlite3
import sys
import time
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _AsyncDB:
    """生产适配器注入（test_evolution_proposal_lifecycle.py 同款：测试即生产路径）。"""

    def __init__(self, path):
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
    """分析层故障注入：失败必须可见（analysis_error 带真因），禁静默吞。"""

    async def fetch(self, query, *args):
        raise RuntimeError("g1b2 analysis boom")

    async def execute(self, query, *args):
        raise RuntimeError("g1b2 analysis boom")

    async def fetchval(self, query, *args):
        raise RuntimeError("g1b2 analysis boom")


def _seed_failures(db_path, errors):
    """种子同根因失败经验（test_evolution_proposal_lifecycle 同款行形）。"""
    from src.learn.experience_collector import ExperienceCollector

    conn = sqlite3.connect(str(db_path))
    c = ExperienceCollector(db_path=str(db_path), job_db_path="", eval_db_path="")
    c.ensure_schema(conn)
    now = time.time()
    for i, err in enumerate(errors):
        conn.execute(
            "INSERT INTO experiences (tenant_id, agent_id, action, "
            "intent_summary, outcome, error, created_at, metadata, source_ref) "
            "VALUES ('default','default','a','i','failure',?,?,?,?)",
            (err, now - i, "{}", f"g1b2:{err}:{i}:{now}"),
        )
    conn.commit()
    conn.close()


# ── 1. 方法本体不变面（控制组对照：新旧代码皆过）─────────────────────


def test_analyze_and_evolve_unchanged_face(tmp_path):
    """analyze_and_evolve 既有语义零改动：3 条同根因失败→failure_avoidance 发现。"""
    from src.heredity.self_evolution import SelfEvolution

    db = tmp_path / "opensoul.db"
    _seed_failures(db, ["g1b2 same root cause"] * 3)
    evolutions = asyncio.run(SelfEvolution(_AsyncDB(db)).analyze_and_evolve())
    assert any(e.get("type") == "failure_avoidance" for e in evolutions), evolutions


# ── 2. run_evolution_cycle 真效果 ───────────────────────────────────


def test_run_evolution_cycle_declares_into_pipeline(tmp_path):
    """采集→分析→提案声明全链真跑：发现真进进化审批管线（声明≠执行）。"""
    from src.heredity.self_evolution import run_evolution_cycle

    db = tmp_path / "opensoul.db"
    _seed_failures(db, ["g1b2 cycle root cause"] * 3)
    res = asyncio.run(run_evolution_cycle(db_pool=_AsyncDB(db)))
    assert res["analysis_error"] == "", res
    assert len(res["evolutions"]) >= 1, res
    assert res["declared_proposals"], res
    assert res["declared_proposals"][0]["status"] == "pending", res
    assert "experience_collection" in res and "pipeline" in res, res


def test_run_evolution_cycle_analysis_failure_visible():
    """分析层故障→analysis_error 带真因可见，禁静默空转谎报 ok（C2）。"""
    from src.heredity.self_evolution import run_evolution_cycle

    res = asyncio.run(run_evolution_cycle(db_pool=_BrokenDB()))
    assert res["evolutions"] == [] and res["declared_proposals"] == [], res
    assert "g1b2 analysis boom" in res["analysis_error"], res
    assert "experience_collection" in res and "pipeline" in res, res


# ── 3. 单一代码路径（端点真委托，禁第二套编排）────────────────────────


def test_brain_evolve_endpoint_delegates_single_code_path(monkeypatch, heredity_client):
    """POST /api/brain/evolve 必须真调 run_evolution_cycle（写了≠接线了）。

    走 heredity_client 隔离面（test_heredity_test_isolation 静态钉契约：进化写
    用例必须隔离面；本例 monkeypatch 委托函数=零真实写，仍守同一契约）。
    """
    import src.heredity.self_evolution as se_mod

    calls = []

    async def _marker(tenant_id="default", agent_id="default", db_pool=None):
        calls.append({"tenant_id": tenant_id, "agent_id": agent_id, "db_pool": db_pool})
        return {"marker": "g1b2-delegate", "evolutions": []}

    monkeypatch.setattr(se_mod, "run_evolution_cycle", _marker)
    resp = heredity_client.post("/api/brain/evolve", params={"agent_id": "g1b2-agent"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"marker": "g1b2-delegate", "evolutions": []}, resp.json()
    assert len(calls) == 1, calls
    assert calls[0]["agent_id"] == "g1b2-agent", calls


# ── 4. self_evolve 行为真执行 ──────────────────────────────────────


def test_self_evolve_behavior_default_face_lands_in_env_db(tmp_path):
    """PROACT-③：行为缺省池归 OPENSOUL_DB env 数据面（种子 env 库真被分析）。"""
    from src.will.proactive_behaviors import self_evolve

    env_db = os.environ.get("OPENSOUL_DB")
    assert env_db, "autouse _isolate_behavior_execution_faces 应已设 OPENSOUL_DB"
    _seed_failures(env_db, ["g1b2 behavior root cause"] * 3)
    res = asyncio.run(self_evolve())
    assert res["analysis_error"] == "", res
    assert len(res["evolutions"]) >= 1, res
    assert res["declared_proposals"], res


def test_self_evolve_run_watcher_leaves_trace():
    """行为经 run_watcher 路径真执行+留痕（心跳/API 手动触发共用路径）。"""
    from src.will.proactive import ProactiveEngine
    from src.will.proactive_behaviors import register_default_watchers

    eng = ProactiveEngine(db_path="")
    register_default_watchers(eng)
    r = asyncio.run(eng.run_watcher("self_evolve"))
    assert r["ok"] is True, r
    assert "declared_proposals" in r["result"] and "analysis_error" in r["result"], r
    events = eng.recent_events()
    assert events and events[0]["watcher"] == "self_evolve" and events[0]["ok"] is True


# ── 5. 注册缝 ──────────────────────────────────────────────────────


def test_self_evolve_wired_into_default_watchers():
    """register_default_watchers 注册 self_evolve，回调=行为本体（非空壳）。"""
    from src.will.proactive import ProactiveEngine
    from src.will.proactive_behaviors import (
        DEFAULT_INTERVALS,
        register_default_watchers,
        self_evolve,
    )

    eng = ProactiveEngine(db_path="")
    names = register_default_watchers(eng)
    assert "self_evolve" in names, names
    w = eng._watchers["self_evolve"]
    assert w["interval_seconds"] == DEFAULT_INTERVALS["self_evolve"]
    cb = w["callback"]
    assert isinstance(cb, partial) and cb.func is self_evolve, cb
