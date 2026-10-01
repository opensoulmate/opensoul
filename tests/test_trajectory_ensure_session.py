"""Idempotent ensure semantics for POST /api/trajectory/sessions.

dev-reports 09-17 15:37轮langfuse三层模型缺"生产写入方"——acp-proxy侧
TrajectoryRecorder按chat session id ensure trajectory session（跨turn/跨进程
重启收敛到一行），本测试钉store与API handler两侧契约：

1. store.create_session(id=...) 使用客户端稳定id（空=uuid既有行为不变）；
2. handler带session_id=幂等ensure：已存在→原样返回，不建重复行；
3. handler不带session_id=既有行为：服务端生成uuid，两次调用两行。
"""

from contextlib import asynccontextmanager

import pytest

from src.api.trajectory import SessionCreateRequest, create_session
from src.config import settings
from src.database.postgres import db_pool
from src.trajectory.store import TrajectoryStore


@asynccontextmanager
async def temp_store(tmp_path):
    old_url = settings.database_url
    settings.database_url = f"sqlite:///{tmp_path}/traj_ensure_test.db"
    await db_pool.connect()
    try:
        yield TrajectoryStore()
    finally:
        await db_pool.disconnect()
        settings.database_url = old_url


@pytest.mark.asyncio
async def test_create_session_with_explicit_id(tmp_path):
    async with temp_store(tmp_path) as store:
        s = await store.create_session(id="chat-abc", agent_id="soulmate", task_description="t")
        assert s.id == "chat-abc"
        got = await store.get_session("chat-abc")
        assert got is not None and got.agent_id == "soulmate"


@pytest.mark.asyncio
async def test_create_session_without_id_generates_uuid(tmp_path):
    async with temp_store(tmp_path) as store:
        s1 = await store.create_session()
        s2 = await store.create_session()
        assert s1.id and s2.id and s1.id != s2.id


@pytest.mark.asyncio
async def test_handler_ensure_idempotent(tmp_path):
    async with temp_store(tmp_path):
        req = SessionCreateRequest(
            session_id="chat-ens", agent_id="soulmate", task_description="hello"
        )
        r1 = await create_session(req)
        r2 = await create_session(req)
        assert r1["id"] == "chat-ens" and r2["id"] == "chat-ens"
        # 不建重复行：list只有一行（显式实参：直呼handler时FastAPI Query默认值未解析）
        from src.api.trajectory import list_sessions

        listing = await list_sessions(agent_id="", status="", limit=50, offset=0)
        assert listing["total"] == 1
        assert listing["sessions"][0]["task_description"] == "hello"
        # 第二次ensure不覆盖已有task_description（原样返回语义）
        assert r2["task_description"] == "hello"


@pytest.mark.asyncio
async def test_handler_without_session_id_keeps_legacy_behavior(tmp_path):
    async with temp_store(tmp_path):
        r1 = await create_session(SessionCreateRequest(agent_id="a"))
        r2 = await create_session(SessionCreateRequest(agent_id="a"))
        assert r1["id"] and r2["id"] and r1["id"] != r2["id"]


@pytest.mark.asyncio
async def test_handler_ensure_then_events_share_session(tmp_path):
    """ensure之后事件落同一session（recorder的turn root→llm_call→tool链前提）。"""
    from src.api.trajectory import EventRecordRequest, record_event

    async with temp_store(tmp_path):
        s = await create_session(SessionCreateRequest(session_id="chat-flow", agent_id="soulmate"))
        root = await record_event(
            "chat-flow", EventRecordRequest(event_type="user_input", content="hi")
        )
        child = await record_event(
            "chat-flow",
            EventRecordRequest(event_type="llm_call", parent_event_id=root["id"], content="round"),
        )
        assert child["parent_event_id"] == root["id"]
        assert s["id"] == "chat-flow"
