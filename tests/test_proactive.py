"""A3主动行为能力测试：心跳驱动的定期任务真调度+真留痕+真行为（不只被动响应）。

测的三层：
① ProactiveEngine 心跳调度（interval到期执行/失败隔离/手动触发同路径/事件留痕回读）
② 默认行为本体（memory_decay真衰减、experience_collect真写experiences、heredity_triggers真评估）
③ /api/proactive 面板路由（status/events/run 真读写引擎状态）
数据面全tmp（proactive.db/ExperienceCollector/EvolutionEngine/MemoryStore换tmp实例）。
"""

import asyncio
import sqlite3
import time

import pytest

# ── ① 引擎心跳调度 ──────────────────────────────────────────


def test_heartbeat_runs_due_watchers():
    """心跳tick只跑到期watcher：短间隔反复跑，长间隔只跑首轮。"""
    from src.will.proactive import ProactiveEngine

    async def main():
        eng = ProactiveEngine(db_path="")  # 内存留痕，零生产接触
        counts = {"fast": 0, "slow": 0}
        eng.register_watcher(
            "fast", lambda: counts.__setitem__("fast", counts["fast"] + 1), interval_seconds=0.05
        )
        eng.register_watcher(
            "slow", lambda: counts.__setitem__("slow", counts["slow"] + 1), interval_seconds=3600
        )
        await eng.start(interval_seconds=0.02)
        await asyncio.sleep(0.25)
        await eng.stop()
        return counts, await eng.get_status()

    counts, status = asyncio.run(main())
    assert counts["fast"] >= 2, f"短间隔watcher应反复执行，实际{counts}"
    assert counts["slow"] == 1, f"长间隔watcher首轮后不应再跑，实际{counts}"
    by_name = {w["name"]: w for w in status["watchers"]}
    assert by_name["fast"]["run_count"] == counts["fast"]
    assert by_name["fast"]["last_ok"] is True


def test_watcher_failure_isolated_and_recorded():
    """单watcher抛异常绝不掀翻心跳循环，且失败留痕可回读（禁静默）。"""
    from src.will.proactive import ProactiveEngine

    async def main():
        eng = ProactiveEngine(db_path="")
        ok_count = {"n": 0}

        def boom():
            raise RuntimeError("故意炸")

        eng.register_watcher("boom", boom, interval_seconds=3600)
        eng.register_watcher(
            "ok", lambda: ok_count.__setitem__("n", ok_count["n"] + 1), interval_seconds=0.05
        )
        await eng.start(interval_seconds=0.02)
        await asyncio.sleep(0.15)
        await eng.stop()
        return ok_count["n"], eng.recent_events(), await eng.get_status()

    ok_n, events, status = asyncio.run(main())
    assert ok_n >= 1, "失败watcher不应阻断其他watcher执行"
    boom_events = [e for e in events if e["watcher"] == "boom"]
    assert boom_events and boom_events[0]["ok"] is False
    assert "RuntimeError" in boom_events[0]["error"]
    by_name = {w["name"]: w for w in status["watchers"]}
    assert by_name["boom"]["error_count"] == 1


def test_run_watcher_manual_and_unknown():
    """手动触发与心跳同路径（留痕同表）；未知watcher抛KeyError（API层转404）。"""
    from src.will.proactive import ProactiveEngine

    async def main():
        eng = ProactiveEngine(db_path="")
        eng.register_watcher("hello", lambda: {"greet": "hi"}, interval_seconds=3600)
        res = await eng.run_watcher("hello")
        with pytest.raises(KeyError):
            await eng.run_watcher("nope")
        return res, eng.recent_events()

    res, events = asyncio.run(main())
    assert res["ok"] is True and res["result"] == {"greet": "hi"}
    assert events and events[0]["watcher"] == "hello" and events[0]["ok"] is True


def test_events_persisted_sqlite_roundtrip(tmp_path):
    """事件落SQLite：result走json.dumps往返一致（metadata json铁律），ok 0/1→bool。"""
    from src.will.proactive import ProactiveEngine

    db = tmp_path / "proactive.db"

    async def main():
        eng = ProactiveEngine(db_path=str(db))
        eng.register_watcher("w", lambda: {"count": 3, "标签": "中文"}, interval_seconds=3600)
        await eng.run_watcher("w")
        return eng.recent_events()

    events = asyncio.run(main())
    assert db.exists(), "事件库文件应真实落盘"
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT watcher, ok, result FROM proactive_events").fetchall()
    assert rows == [("w", 1, '{"count": 3, "标签": "中文"}')]
    assert events[0]["result"] == {"count": 3, "标签": "中文"}
    assert events[0]["ok"] is True


# ── ② 默认行为本体（真执行，tmp数据面） ──────────────────────────


def test_default_watchers_registered():
    from src.will.proactive import ProactiveEngine
    from src.will.proactive_behaviors import register_default_watchers

    eng = ProactiveEngine(db_path="")
    names = register_default_watchers(eng)
    assert set(names) == {
        "memory_decay",
        "experience_collect",
        "heredity_triggers",
        "pattern_extract",
        "advisory_review",
    }
    intervals = {
        w["name"]: w["interval_seconds"] for w in asyncio.run(eng.get_status())["watchers"]
    }
    assert (
        intervals["memory_decay"]
        > intervals["advisory_review"]
        > intervals["pattern_extract"]
        > intervals["experience_collect"]
        > intervals["heredity_triggers"]
        > 0
    )


def test_memory_decay_real_effect():
    """memory_decay对真实MemoryStore跑衰减周期：过期记忆真被遗忘。"""
    from src.hippo.memory_store import MemoryStore
    from src.will.proactive_behaviors import memory_decay

    store = MemoryStore()
    old = store.add(session_id="s1", content="很旧的记忆", memory_id="m-old")
    old.created_at = old.last_accessed_at = time.time() - 24 * 3600 * 40  # 远超遗忘线
    store.add(session_id="s1", content="新记忆", memory_id="m-new")

    stats = asyncio.run(memory_decay(store=store))
    assert stats["forgotten"] >= 1, f"过期记忆应被遗忘，实际stats={stats}"
    assert store.get("m-old") is None, "get应确认过期记忆已真删除"
    assert "m-new" in store._memories
    assert stats["updated"] == 2


def test_experience_collect_real_writes_tmp(tmp_path):
    """experience_collect走真实ExperienceCollector：tmp库建experiences表并回读（幂等）。"""
    from src.will.proactive_behaviors import experience_collect

    db = tmp_path / "opensoul.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE agent_messages (id INTEGER PRIMARY KEY, session_id TEXT DEFAULT '',"
            " role TEXT, content TEXT, timestamp REAL, metadata TEXT DEFAULT '{}')"
        )
        conn.execute(
            "INSERT INTO agent_messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
            ("s1", "user", "任务：修复超时bug", time.time()),
        )
        conn.execute(
            "INSERT INTO agent_messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
            ("s1", "assistant", "已完成修复", time.time()),
        )
    stats1 = asyncio.run(experience_collect(db_path=str(db), job_db_path="", eval_db_path=""))
    stats2 = asyncio.run(experience_collect(db_path=str(db), job_db_path="", eval_db_path=""))
    with sqlite3.connect(db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM experiences").fetchone()[0]
    assert n >= 1, f"应采集出experiences行，实际stats={stats1}"
    # source_ref幂等：第二轮written=0/deduped命中，experiences行数不增
    assert stats1["sources"]["messages"]["written"] == 1
    assert stats2["sources"]["messages"]["written"] == 0
    assert stats2["sources"]["messages"]["deduped"] == 1
    with sqlite3.connect(db) as conn:
        n2 = conn.execute("SELECT COUNT(*) FROM experiences").fetchone()[0]
    assert n2 == n, "重复采集零副作用：experiences行数不变"


def test_heredity_triggers_real_eval(tmp_path):
    """heredity_triggers走真实EvolutionEngine.evaluate_triggers（tmp库），返回触发评估dict。"""
    from src.heredity.evolution_loop import EvolutionEngine
    from src.will.proactive_behaviors import heredity_triggers

    eng = EvolutionEngine(db_path=str(tmp_path / "evo.db"))
    res = asyncio.run(heredity_triggers(engine=eng))
    assert isinstance(res, dict) and "fired" in res
    # 错误风暴熔断真生效：3连同错≥storm_window → breaker打开且只记一条
    res2 = asyncio.run(
        heredity_triggers(engine=eng, recent_errors=["boom"] * 5, auto_propose=False)
    )
    assert res2.get("breaker_open") is True
    assert res2["fired"] == ["error_storm_breaker"]


# ── ③ /api/proactive 面板路由 ──────────────────────────────────


@pytest.fixture()
def proactive_client(tmp_path, monkeypatch):
    """真实proactive router + 引擎tmp数据面（monkeypatch模块单例属性）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api import proactive as proactive_api
    from src.will.proactive import ProactiveEngine

    tmp_engine = ProactiveEngine(db_path=str(tmp_path / "proactive.db"))
    tmp_engine.register_watcher("hello", lambda: {"greet": "hi"}, interval_seconds=3600)
    monkeypatch.setattr(proactive_api.proactive_mod, "proactive_engine", tmp_engine)

    app = FastAPI()
    app.include_router(proactive_api.router, prefix="/api/proactive")
    with TestClient(app) as c:
        yield c


def test_router_status_events_run(proactive_client):
    r = proactive_client.get("/api/proactive/status")
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is False and len(body["watchers"]) == 1
    assert body["watchers"][0]["name"] == "hello"

    r = proactive_client.post("/api/proactive/run/hello")
    assert r.status_code == 200
    assert r.json() == {
        "watcher": "hello",
        "ok": True,
        "duration_ms": r.json()["duration_ms"],
        "result": {"greet": "hi"},
        "error": "",
    }

    r = proactive_client.get("/api/proactive/events")
    assert r.status_code == 200
    events = r.json()["events"]
    assert events and events[0]["watcher"] == "hello" and events[0]["ok"] is True

    r = proactive_client.post("/api/proactive/run/nope")
    assert r.status_code == 404, "未知watcher必须404而非500/谎报"
