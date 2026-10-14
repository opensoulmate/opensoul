"""Unit tests for trajectory/store.py — trajectory data models."""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.trajectory.store import (
    EventType,
    TrajectoryEvent,
    TrajectorySession,
    TrajectoryStore,
)


def _mock_db_pool():
    """Create a mock db_pool with AsyncMock methods."""
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=0)
    return pool


class TestEventType:
    def test_all_values(self):
        assert EventType.SESSION_START == "session_start"
        assert EventType.SESSION_END == "session_end"
        assert EventType.USER_INPUT == "user_input"
        assert EventType.LLM_CALL == "llm_call"
        assert EventType.LLM_RESPONSE == "llm_response"
        assert EventType.TOOL_CALL == "tool_call"
        assert EventType.TOOL_RESULT == "tool_result"
        assert EventType.AGENT_DISPATCH == "agent_dispatch"
        assert EventType.AGENT_RESULT == "agent_result"
        assert EventType.ERROR == "error"
        assert EventType.CHECKPOINT == "checkpoint"
        assert EventType.BRANCH == "branch"
        assert EventType.CUSTOM == "custom"

    def test_count(self):
        assert len(EventType) == 14


class TestTrajectoryEvent:
    def test_auto_id(self):
        ev = TrajectoryEvent()
        assert ev.id != ""
        uuid.UUID(ev.id)

    def test_auto_created_at(self):
        ev = TrajectoryEvent()
        assert ev.created_at != ""
        assert "T" in ev.created_at

    def test_explicit_id(self):
        ev = TrajectoryEvent(id="custom-id")
        assert ev.id == "custom-id"

    def test_explicit_created_at(self):
        ev = TrajectoryEvent(created_at="2024-01-01T00:00:00Z")
        assert ev.created_at == "2024-01-01T00:00:00Z"

    def test_defaults(self):
        ev = TrajectoryEvent()
        assert ev.session_id == ""
        assert ev.parent_event_id is None
        assert ev.event_type == ""
        assert ev.agent_id == ""
        assert ev.content == ""
        assert ev.metadata_json == "{}"
        assert ev.token_usage == 0
        assert ev.duration_ms == 0.0
        assert ev.status == "ok"

    def test_metadata_property_valid(self):
        ev = TrajectoryEvent(metadata_json='{"key": "value"}')
        assert ev.metadata == {"key": "value"}

    def test_metadata_property_invalid(self):
        ev = TrajectoryEvent(metadata_json="not json")
        assert ev.metadata == {}

    def test_metadata_property_empty(self):
        ev = TrajectoryEvent(metadata_json="")
        assert ev.metadata == {}

    def test_to_dict(self):
        ev = TrajectoryEvent(
            id="e1",
            session_id="s1",
            event_type="user_input",
            content="Hello",
            metadata_json='{"k": "v"}',
        )
        d = ev.to_dict()
        assert d["id"] == "e1"
        assert d["session_id"] == "s1"
        assert d["event_type"] == "user_input"
        assert d["content"] == "Hello"
        assert d["metadata"] == {"k": "v"}

    def test_to_dict_includes_metadata_parsed(self):
        ev = TrajectoryEvent(metadata_json='{"a": 1}')
        d = ev.to_dict()
        assert "metadata" in d
        assert d["metadata"] == {"a": 1}


class TestTrajectorySession:
    def test_auto_id(self):
        s = TrajectorySession()
        assert s.id != ""
        uuid.UUID(s.id)

    def test_auto_created_at(self):
        s = TrajectorySession()
        assert s.created_at != ""

    def test_defaults(self):
        s = TrajectorySession()
        assert s.agent_id == ""
        assert s.task_description == ""
        assert s.status == "running"
        assert s.forked_from is None
        assert s.fork_point_event_id is None
        assert s.total_events == 0
        assert s.total_tokens == 0
        assert s.total_duration_ms == 0.0
        assert s.tags == []
        assert s.ended_at is None

    def test_explicit_fields(self):
        s = TrajectorySession(
            id="s1",
            agent_id="agent-1",
            task_description="Do something",
            status="completed",
            tags=["test", "unit"],
        )
        assert s.id == "s1"
        assert s.agent_id == "agent-1"
        assert s.task_description == "Do something"
        assert s.status == "completed"
        assert s.tags == ["test", "unit"]

    def test_to_dict(self):
        s = TrajectorySession(
            id="s1",
            agent_id="a1",
            task_description="task",
            total_events=5,
            total_tokens=1000,
        )
        d = s.to_dict()
        assert d["id"] == "s1"
        assert d["agent_id"] == "a1"
        assert d["total_events"] == 5
        assert d["total_tokens"] == 1000

    def test_fork_fields(self):
        s = TrajectorySession(
            forked_from="parent-session",
            fork_point_event_id="evt-123",
        )
        assert s.forked_from == "parent-session"
        assert s.fork_point_event_id == "evt-123"


class TestTrajectoryStore:
    def setup_method(self):
        self.store = TrajectoryStore()

    @pytest.mark.asyncio
    async def test_ensure_tables(self):
        pool = _mock_db_pool()
        with patch("src.trajectory.store.db_pool", pool):
            await self.store.ensure_tables()
            # 2 tables + 2 索引 + end_at migration ALTER + 2 租户 ALTER
            # + 2 租户索引 + scores table（DEC-⑦-⑥ 行租户键迁移随契约演进）
            # + scores 租户 ALTER + scores 租户索引（DEC-⑦-⑨ scores 加轴随契约演进）
            assert pool.execute.call_count == 12

    @pytest.mark.asyncio
    async def test_create_session(self):
        pool = _mock_db_pool()
        with patch("src.trajectory.store.db_pool", pool):
            session = await self.store.create_session(
                agent_id="agent-1",
                task_description="Test task",
                tags=["tag1"],
            )
            assert session.agent_id == "agent-1"
            assert session.task_description == "Test task"
            assert session.tags == ["tag1"]
            assert session.status == "running"
            assert session.tenant_id == "default"  # DEC-⑦-⑥ 缺省='default' 系统写方
            # create_session calls ensure_tables (12 calls) + INSERT (1 call)
            # （DEC-⑦-⑥/⑦-⑧b 迁移 DDL 随契约演进；hijack SELECT 是 fetchrow 不计）
            assert pool.execute.call_count == 13

    @pytest.mark.asyncio
    async def test_add_event(self):
        pool = _mock_db_pool()
        with patch("src.trajectory.store.db_pool", pool):
            event = TrajectoryEvent(
                session_id="s1",
                event_type="user_input",
                agent_id="agent-1",
                content="Hello",
                token_usage=10,
                duration_ms=50.0,
            )
            result = await self.store.add_event(event)
            assert result.session_id == "s1"
            assert result.event_type == "user_input"
            assert result.content == "Hello"
            assert result.token_usage == 10
            assert result.duration_ms == 50.0
            assert result.tenant_id == "default"  # DEC-⑦-⑥ 父行缺失→'default' 兜底
            # add_event calls ensure_tables (12) + INSERT + UPDATE
            # （DEC-⑦-⑥ 父行 SELECT 是 fetchrow 不计）
            assert pool.execute.call_count == 14

    @pytest.mark.asyncio
    async def test_list_sessions(self):
        pool = _mock_db_pool()
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "id": "s1",
                    "agent_id": "a1",
                    "task_description": "task1",
                    "status": "completed",
                    "forked_from": None,
                    "fork_point_event_id": None,
                    "total_events": 10,
                    "total_tokens": 500,
                    "total_duration_ms": 1000.0,
                    "tags_json": '["tag1"]',
                    "created_at": "2024-01-01T00:00:00Z",
                    "ended_at": "2024-01-01T01:00:00Z",
                }
            ]
        )
        with patch("src.trajectory.store.db_pool", pool):
            sessions = await self.store.list_sessions(limit=10)
            assert len(sessions) == 1
            assert sessions[0].id == "s1"
            assert sessions[0].agent_id == "a1"
            assert sessions[0].tags == ["tag1"]

    @pytest.mark.asyncio
    async def test_list_sessions_empty(self):
        pool = _mock_db_pool()
        with patch("src.trajectory.store.db_pool", pool):
            sessions = await self.store.list_sessions()
            assert sessions == []

    @pytest.mark.asyncio
    async def test_end_session(self):
        pool = _mock_db_pool()
        # DEC-⑦-⑥：end_session 先取行（劫持闸判据）——行存在才走到 UPDATE
        pool.fetchrow = AsyncMock(
            return_value={
                "id": "s1",
                "agent_id": "a1",
                "task_description": "task1",
                "status": "running",
                "forked_from": None,
                "fork_point_event_id": None,
                "total_events": 0,
                "total_tokens": 0,
                "total_duration_ms": 0,
                "tags_json": "[]",
                "created_at": "2024-01-01T00:00:00Z",
                "ended_at": None,
                "tenant_id": "default",
            }
        )
        with patch("src.trajectory.store.db_pool", pool):
            await self.store.end_session("s1", status="completed")
            # end_session: just 1 UPDATE call（ensure_tables的DDL不计；
            # 2026-10-02 session收口轮有意演进：end_session补ensure_tables
            # 与其余store方法对齐，fresh DB上调用不再炸no such table）
            updates = [
                c
                for c in pool.execute.call_args_list
                if "UPDATE trajectory_sessions" in str(c.args[0])
            ]
            assert len(updates) == 1
            # 首收口守卫：重复收口no-op（ended_at只记第一次）
            assert "status = 'running' OR ended_at IS NULL" in str(updates[0].args[0])

    @pytest.mark.asyncio
    async def test_get_events(self):
        pool = _mock_db_pool()
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "id": "e1",
                    "session_id": "s1",
                    "parent_event_id": None,
                    "event_type": "user_input",
                    "agent_id": "a1",
                    "content": "Hello",
                    "metadata_json": "{}",
                    "token_usage": 10,
                    "duration_ms": 5.0,
                    "status": "ok",
                    "created_at": "2024-01-01T00:00:00Z",
                }
            ]
        )
        with patch("src.trajectory.store.db_pool", pool):
            events = await self.store.get_events("s1")
            assert len(events) == 1
            assert events[0].id == "e1"
            assert events[0].content == "Hello"

    @pytest.mark.asyncio
    async def test_search_events(self):
        pool = _mock_db_pool()
        with patch("src.trajectory.store.db_pool", pool):
            events = await self.store.search_events(keyword="test", limit=10)
            assert events == []

    @pytest.mark.asyncio
    async def test_get_session(self):
        pool = _mock_db_pool()
        pool.fetchrow = AsyncMock(
            return_value={
                "id": "s1",
                "agent_id": "a1",
                "task_description": "task",
                "status": "running",
                "forked_from": None,
                "fork_point_event_id": None,
                "total_events": 0,
                "total_tokens": 0,
                "total_duration_ms": 0,
                "tags_json": "[]",
                "created_at": "2024-01-01T00:00:00Z",
                "ended_at": None,
            }
        )
        with patch("src.trajectory.store.db_pool", pool):
            session = await self.store.get_session("s1")
            assert session is not None
            assert session.id == "s1"

    @pytest.mark.asyncio
    async def test_get_session_not_found(self):
        pool = _mock_db_pool()
        pool.fetchrow = AsyncMock(return_value=None)
        with patch("src.trajectory.store.db_pool", pool):
            session = await self.store.get_session("nonexistent")
            assert session is None

    @pytest.mark.asyncio
    async def test_delete_session(self):
        pool = _mock_db_pool()
        # DEC-⑦-⑥：delete_session 先取行（归属闸判据）——行存在才走到 DELETE
        pool.fetchrow = AsyncMock(
            return_value={
                "id": "s1",
                "agent_id": "a1",
                "task_description": "task1",
                "status": "running",
                "forked_from": None,
                "fork_point_event_id": None,
                "total_events": 0,
                "total_tokens": 0,
                "total_duration_ms": 0,
                "tags_json": "[]",
                "created_at": "2024-01-01T00:00:00Z",
                "ended_at": None,
                "tenant_id": "default",
            }
        )
        with patch("src.trajectory.store.db_pool", pool):
            assert await self.store.delete_session("s1") is True
            # delete_session: ensure_tables (12) + DELETE scores/events/session
            assert pool.execute.call_count == 15
