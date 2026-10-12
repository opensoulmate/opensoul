"""P1-15 五终止条件组合入 trace——store 侧落点测试（真实临时 SQLite，无 mock）。

覆盖两个落点：
1. end_event(termination=...) 合入事件 metadata.termination（回合被哪个终止
   条件收口）；空串=正常完成，metadata 不动（向后兼容零变化）。
2. sweep_idle_sessions 闲置收口（user_idle 终止条件）先落终止标记事件再收口
   session（顺序纪律：add_event 会自愈翻回 running，标记必须在 end_session 前）。
"""

from contextlib import asynccontextmanager

import pytest

from src.config import settings
from src.database.postgres import db_pool
from src.trajectory.store import (
    EventType,
    TrajectoryEvent,
    TrajectoryStore,
)


@asynccontextmanager
async def temp_store(tmp_path):
    """TrajectoryStore backed by a fresh SQLite file."""
    old_url = settings.database_url
    settings.database_url = f"sqlite:///{tmp_path}/traj_termination_test.db"
    await db_pool.connect()
    try:
        yield TrajectoryStore()
    finally:
        await db_pool.disconnect()
        settings.database_url = old_url


@pytest.mark.asyncio
async def test_end_event_merges_termination_into_metadata(tmp_path):
    async with temp_store(tmp_path) as store:
        session = await store.create_session(agent_id="a1")
        ev = await store.add_event(
            TrajectoryEvent(
                session_id=session.id,
                event_type=EventType.USER_INPUT.value,
                content="turn root",
            )
        )
        closed = await store.end_event(ev.id, status="ok", termination="max_turns")
        assert closed is not None
        assert closed.metadata.get("termination") == "max_turns"
        # 服务端真源回读（不认调用方返回值自报）
        row = await store.get_event(ev.id)
        assert row is not None
        assert row.metadata.get("termination") == "max_turns"


@pytest.mark.asyncio
async def test_end_event_without_termination_keeps_metadata(tmp_path):
    async with temp_store(tmp_path) as store:
        session = await store.create_session(agent_id="a1")
        ev = await store.add_event(
            TrajectoryEvent(
                session_id=session.id,
                event_type=EventType.USER_INPUT.value,
                content="turn root",
                metadata_json='{"kind": "turn_root"}',
            )
        )
        closed = await store.end_event(ev.id, status="ok")
        assert closed is not None
        assert "termination" not in closed.metadata
        assert closed.metadata.get("kind") == "turn_root"


@pytest.mark.asyncio
async def test_sweep_idle_marks_user_idle_then_closes_session(tmp_path):
    async with temp_store(tmp_path) as store:
        session = await store.create_session(agent_id="a1")
        await store.add_event(
            TrajectoryEvent(
                session_id=session.id,
                event_type=EventType.USER_INPUT.value,
                content="hello",
            )
        )
        flipped = await store.sweep_idle_sessions(idle_seconds=0)
        assert flipped == 1
        s = await store.get_session(session.id)
        assert s is not None
        assert s.status == "completed"
        # user_idle 终止标记事件落在 trace 里（顺序：标记→收口，收口是终态）
        events = await store.get_events(session.id)
        markers = [e for e in events if e.metadata.get("termination") == "user_idle"]
        assert len(markers) == 1
        assert markers[0].metadata.get("kind") == "termination"
        # 标记事件即活动会自愈翻回 running——收口在其后，终态必须仍是 completed
        s2 = await store.get_session(session.id)
        assert s2 is not None
        assert s2.status == "completed"


@pytest.mark.asyncio
async def test_sweep_idle_skips_already_closed_sessions(tmp_path):
    """已收口 session 不重复落 user_idle 标记（清扫只翻 running 行）。"""
    async with temp_store(tmp_path) as store:
        session = await store.create_session(agent_id="a1")
        await store.add_event(
            TrajectoryEvent(
                session_id=session.id,
                event_type=EventType.USER_INPUT.value,
                content="hello",
            )
        )
        await store.end_session(session.id, status="completed")
        flipped = await store.sweep_idle_sessions(idle_seconds=0)
        assert flipped == 0
        events = await store.get_events(session.id)
        assert not [e for e in events if e.metadata.get("termination") == "user_idle"]
