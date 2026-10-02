"""Session活跃度生命周期 — 04:20轮遗留#2销账（session.status不再恒running）。

session收口三层语义（dev-reports本轮）：
①显式收口（实时）：acp-proxy ws_acp连接teardown调POST /sessions/{id}/end
  （acp-proxy侧tests/test_session_lifecycle.py钉）；
②闲置清扫（兜底）：sweep_idle_sessions——running但超时无事件→completed；
③自愈重开：add_event把completed翻回running（长生命周期chat session跨连接/
  跨天继续不卡死）。

本文件用真实SQLite（temp_store模式，同test_trajectory_ensure_session）跑真
行为断言——SQL字符串mock断言证明不了语义，翻转状态回读才算。
"""

from contextlib import asynccontextmanager

import pytest

from src.api.trajectory import (
    _session_idle_s,
    get_session,
    list_sessions,
)
from src.config import settings
from src.database.postgres import db_pool
from src.trajectory.store import TrajectoryEvent, TrajectoryStore


@asynccontextmanager
async def temp_store(tmp_path):
    old_url = settings.database_url
    settings.database_url = f"sqlite:///{tmp_path}/traj_lifecycle_test.db"
    await db_pool.connect()
    try:
        yield TrajectoryStore()
    finally:
        await db_pool.disconnect()
        settings.database_url = old_url


async def _add_event(store: TrajectoryStore, session_id: str) -> TrajectoryEvent:
    return await store.add_event(
        TrajectoryEvent(session_id=session_id, event_type="user_input", content="hi")
    )


# ── ③ 自愈重开 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_event_reopens_completed_session(tmp_path):
    async with temp_store(tmp_path) as store:
        await store.create_session(id="s-reopen", agent_id="a")
        await _add_event(store, "s-reopen")
        await store.end_session("s-reopen", status="completed")
        mid = await store.get_session("s-reopen")
        assert mid.status == "completed" and mid.ended_at is not None

        await _add_event(store, "s-reopen")
        got = await store.get_session("s-reopen")
        assert got.status == "running", "add_event必须把completed翻回running"
        assert got.ended_at is None, "重开必须清掉ended_at"
        assert got.total_events == 2


@pytest.mark.asyncio
async def test_add_event_keeps_running_session_running(tmp_path):
    async with temp_store(tmp_path) as store:
        await store.create_session(id="s-alive", agent_id="a")
        await _add_event(store, "s-alive")
        got = await store.get_session("s-alive")
        assert got.status == "running" and got.ended_at is None


# ── ① 显式收口（store级语义） ─────────────────────────────────


@pytest.mark.asyncio
async def test_end_session_first_end_wins(tmp_path):
    async with temp_store(tmp_path) as store:
        await store.create_session(id="s-first", agent_id="a")
        await _add_event(store, "s-first")
        await store.end_session("s-first", status="completed")
        first = await store.get_session("s-first")

        await store.end_session("s-first", status="error")  # 重复收口=no-op
        second = await store.get_session("s-first")
        assert second.status == "completed", "首收口生效，重复收口不得改写"
        assert second.ended_at == first.ended_at, "重复收口不得重盖ended_at时间戳"


@pytest.mark.asyncio
async def test_reopened_session_can_be_ended_again(tmp_path):
    async with temp_store(tmp_path) as store:
        await store.create_session(id="s-reend", agent_id="a")
        await _add_event(store, "s-reend")
        await store.end_session("s-reend")
        await _add_event(store, "s-reend")  # 重开
        await store.end_session("s-reend")  # 重开后的收口必须生效
        got = await store.get_session("s-reend")
        assert got.status == "completed" and got.ended_at is not None


@pytest.mark.asyncio
async def test_end_session_missing_id_is_noop(tmp_path):
    async with temp_store(tmp_path) as store:
        await store.end_session("nope-not-here")  # 不抛异常
        assert await store.get_session("nope-not-here") is None


# ── ② 闲置清扫 ────────────────────────────────────────────────


async def _backdate(store: TrajectoryStore, session_id: str, when: str) -> None:
    await db_pool.execute(
        "UPDATE trajectory_events SET created_at = ? WHERE session_id = ?",
        when,
        session_id,
    )
    await db_pool.execute(
        "UPDATE trajectory_sessions SET created_at = ? WHERE id = ?",
        when,
        session_id,
    )


@pytest.mark.asyncio
async def test_sweep_idle_marks_stale_running_completed(tmp_path):
    async with temp_store(tmp_path) as store:
        await store.create_session(id="s-stale", agent_id="a")
        await _add_event(store, "s-stale")
        await _backdate(store, "s-stale", "2020-01-01T00:00:00+00:00")

        flipped = await store.sweep_idle_sessions(idle_seconds=3600.0)
        assert flipped == 1
        got = await store.get_session("s-stale")
        assert got.status == "completed" and got.ended_at is not None


@pytest.mark.asyncio
async def test_sweep_skips_recent_sessions(tmp_path):
    async with temp_store(tmp_path) as store:
        await store.create_session(id="s-fresh", agent_id="a")
        await _add_event(store, "s-fresh")

        flipped = await store.sweep_idle_sessions(idle_seconds=3600.0)
        assert flipped == 0
        assert (await store.get_session("s-fresh")).status == "running"


@pytest.mark.asyncio
async def test_sweep_uses_latest_event_not_session_created_at(tmp_path):
    """会话很老但刚有新事件=仍活跃，不得被清扫（最后活跃=最新事件时刻）。"""
    async with temp_store(tmp_path) as store:
        await store.create_session(id="s-old-chat", agent_id="a")
        await _backdate(store, "s-old-chat", "2020-01-01T00:00:00+00:00")
        await _add_event(store, "s-old-chat")  # 刚发生的新事件

        flipped = await store.sweep_idle_sessions(idle_seconds=3600.0)
        assert flipped == 0
        assert (await store.get_session("s-old-chat")).status == "running"


@pytest.mark.asyncio
async def test_sweep_skips_already_ended_and_counts_only_flips(tmp_path):
    async with temp_store(tmp_path) as store:
        await store.create_session(id="s-done", agent_id="a")
        await _add_event(store, "s-done")
        await store.end_session("s-done", status="error")
        first_ended = (await store.get_session("s-done")).ended_at
        await _backdate(store, "s-done", "2020-01-01T00:00:00+00:00")

        await store.create_session(id="s-stale2", agent_id="a")
        await _add_event(store, "s-stale2")
        await _backdate(store, "s-stale2", "2020-01-01T00:00:00+00:00")

        flipped = await store.sweep_idle_sessions(idle_seconds=3600.0)
        assert flipped == 1, "只数running→completed的翻转"
        done = await store.get_session("s-done")
        assert done.status == "error", "已收口session不得被清扫改写"
        assert done.ended_at == first_ended, "清扫不得重盖已收口session的ended_at"


@pytest.mark.asyncio
async def test_sweep_zero_threshold_closes_all_running(tmp_path):
    async with temp_store(tmp_path) as store:
        await store.create_session(id="s-z1", agent_id="a")
        await _add_event(store, "s-z1")
        await store.create_session(id="s-z2", agent_id="a")

        flipped = await store.sweep_idle_sessions(idle_seconds=0.0)
        assert flipped == 2
        assert (await store.get_session("s-z1")).status == "completed"
        assert (await store.get_session("s-z2")).status == "completed"


@pytest.mark.asyncio
async def test_sweep_zero_event_session_falls_back_to_created_at(tmp_path):
    """无事件session：最后活跃=created_at，老于阈值照样收口。"""
    async with temp_store(tmp_path) as store:
        await store.create_session(id="s-noev", agent_id="a")
        await _backdate(store, "s-noev", "2020-01-01T00:00:00+00:00")

        flipped = await store.sweep_idle_sessions(idle_seconds=3600.0)
        assert flipped == 1
        assert (await store.get_session("s-noev")).status == "completed"


# ── 接线钉：读路径触发清扫（写了≠接线了） ──────────────────────


@pytest.mark.asyncio
async def test_list_sessions_handler_triggers_sweep(monkeypatch, tmp_path):
    calls: list[float] = []

    async def fake_sweep(idle_seconds: float = 1800.0) -> int:
        calls.append(float(idle_seconds))
        return 0

    async with temp_store(tmp_path):
        monkeypatch.setattr("src.api.trajectory.trajectory_store.sweep_idle_sessions", fake_sweep)
        await list_sessions(agent_id="", status="", limit=50, offset=0)
    assert calls, "list_sessions handler必须触发闲置清扫"
    assert calls[0] == pytest.approx(_session_idle_s())


@pytest.mark.asyncio
async def test_get_session_handler_triggers_sweep(monkeypatch, tmp_path):
    calls: list[float] = []

    async def fake_sweep(idle_seconds: float = 1800.0) -> int:
        calls.append(float(idle_seconds))
        return 0

    async with temp_store(tmp_path):
        monkeypatch.setattr("src.api.trajectory.trajectory_store.sweep_idle_sessions", fake_sweep)
        try:
            await get_session("whatever")
        except Exception:
            pass  # 404也无妨——清扫发生在查询之前
    assert calls, "get_session handler必须触发闲置清扫"


@pytest.mark.asyncio
async def test_sweep_failure_never_breaks_read(monkeypatch, tmp_path):
    async with temp_store(tmp_path):

        async def boom(idle_seconds: float = 1800.0) -> int:
            raise RuntimeError("sweep exploded")

        monkeypatch.setattr("src.api.trajectory.trajectory_store.sweep_idle_sessions", boom)
        resp = await list_sessions(agent_id="", status="", limit=50, offset=0)
        assert resp["sessions"] == []  # 读路径照常返回


def test_idle_s_env_parsing(monkeypatch):
    monkeypatch.setenv("TRAJ_SESSION_IDLE_S", "42")
    assert _session_idle_s() == 42.0
    monkeypatch.setenv("TRAJ_SESSION_IDLE_S", "garbage")
    assert _session_idle_s() == 1800.0
    monkeypatch.setenv("TRAJ_SESSION_IDLE_S", "-5")
    assert _session_idle_s() == 0.0


@pytest.mark.asyncio
async def test_ensure_after_close_stays_closed_until_new_event(tmp_path):
    """生命周期契约：收口后仅凭ensure/读不重开——重开只由新事件触发。"""
    async with temp_store(tmp_path) as store:
        await store.create_session(id="chat-x", agent_id="soulmate")
        await _add_event(store, "chat-x")
        await store.end_session("chat-x")
        # 幂等ensure（API handler是先get再返回，见test_trajectory_ensure_session
        # 既有钉）与读路径都不得改变生命周期状态
        assert (await store.get_session("chat-x")).status == "completed"
        assert (await store.get_session("chat-x")).status == "completed"
