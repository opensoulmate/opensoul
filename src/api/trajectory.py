"""OpenTrajectory API — Agent execution trace, replay, and fork endpoints."""

import json
import os

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.trajectory.session_fsm import SessionEvent, SessionStateMachine
from src.trajectory.store import (
    EventType,
    TrajectoryEvent,
    TrajectoryScore,
    trajectory_store,
)

router = APIRouter()
session_fsm = SessionStateMachine()


# ── session活跃度收口（04:20轮遗留#2销账）────────────────────────
# session.status不再恒running。三层收口合起来给session活跃度生命周期：
# ①显式收口（实时）：acp-proxy ws_acp连接teardown调POST /sessions/{id}/end；
# ②闲置清扫（兜底，本文件）：读列表/单session时顺带清扫——running但超过
#   TRAJ_SESSION_IDLE_S（默认1800s）无事件的session翻completed（崩溃没走到
#   收尾的session也不会永远running）；
# ③自愈重开：store.add_event把completed翻回running（长生命周期chat session
#   跨连接/跨天继续不卡死）。


def _session_idle_s() -> float:
    try:
        return max(float(os.getenv("TRAJ_SESSION_IDLE_S", "1800")), 0.0)
    except (TypeError, ValueError):
        return 1800.0


async def _sweep_idle_sessions() -> None:
    """读路径顺带闲置清扫。清扫失败绝不影响读（fail-safe）。"""
    try:
        await trajectory_store.sweep_idle_sessions(idle_seconds=_session_idle_s())
    except Exception:
        pass


# ── Request Schemas ──────────────────────────────────────────


class SessionCreateRequest(BaseModel):
    agent_id: str = ""
    task_description: str = ""
    tags: list[str] = []
    # 客户端稳定id（幂等ensure语义）：长生命周期chat session跨进程重启
    # 累积turn的单一真源标识（acp-proxy TrajectoryRecorder按chat session id
    # ensure）；空=服务端生成uuid（既有行为不变）。
    session_id: str = ""


class EventRecordRequest(BaseModel):
    event_type: str = EventType.CUSTOM.value
    agent_id: str = ""
    content: str = ""
    metadata: dict = {}
    parent_event_id: str | None = None
    token_usage: int = 0
    duration_ms: float = 0.0
    status: str = "ok"


class ForkRequest(BaseModel):
    fork_point_event_id: str
    new_agent_id: str = ""


class BatchEventRequest(BaseModel):
    events: list[EventRecordRequest]


# ── Health ───────────────────────────────────────────────────


@router.get("/health")
async def health():
    return {"status": "ok", "component": "trajectory"}


# ── Stats ────────────────────────────────────────────────────


@router.get("/stats")
async def stats():
    """Get trajectory system statistics."""
    return await trajectory_store.get_stats()


# ── Sessions ─────────────────────────────────────────────────


@router.post("/sessions")
async def create_session(req: SessionCreateRequest):
    """Create a new trajectory session.

    When ``session_id`` is provided the call is an idempotent *ensure*:
    an existing session with that id is returned unchanged (HTTP 200)
    instead of creating a duplicate row — repeated ensures across turns
    and process restarts must converge on one session per chat session.
    """
    if req.session_id:
        existing = await trajectory_store.get_session(req.session_id)
        if existing:
            return existing.to_dict()
    session = await trajectory_store.create_session(
        id=req.session_id,
        agent_id=req.agent_id,
        task_description=req.task_description,
        tags=req.tags,
    )
    return session.to_dict()


@router.get("/sessions")
async def list_sessions(
    agent_id: str = Query(default=""),
    status: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List trajectory sessions with optional filters."""
    await _sweep_idle_sessions()
    sessions = await trajectory_store.list_sessions(
        agent_id=agent_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    total = await trajectory_store.count_sessions()
    return {
        "sessions": [s.to_dict() for s in sessions],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get a single trajectory session."""
    await _sweep_idle_sessions()
    session = await trajectory_store.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session.to_dict()


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session and all its events."""
    session = await trajectory_store.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    await trajectory_store.delete_session(session_id)
    return {"deleted": True, "session_id": session_id}


@router.post("/sessions/{session_id}/end")
async def end_session(session_id: str, status: str = Query(default="completed")):
    """End a running session."""
    session = await trajectory_store.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    await trajectory_store.end_session(session_id, status=status)
    return {"ended": True, "session_id": session_id, "status": status}


# ── Events ───────────────────────────────────────────────────


@router.post("/sessions/{session_id}/events")
async def record_event(session_id: str, req: EventRecordRequest):
    """Record a single event in a session."""
    session = await trajectory_store.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    event = TrajectoryEvent(
        session_id=session_id,
        parent_event_id=req.parent_event_id,
        event_type=req.event_type,
        agent_id=req.agent_id,
        content=req.content,
        metadata_json=json.dumps(req.metadata),
        token_usage=req.token_usage,
        duration_ms=req.duration_ms,
        status=req.status,
    )
    await trajectory_store.add_event(event)
    return event.to_dict()


@router.post("/sessions/{session_id}/events/batch")
async def record_events_batch(session_id: str, req: BatchEventRequest):
    """Record multiple events in a session at once."""
    session = await trajectory_store.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    results = []
    for er in req.events:
        event = TrajectoryEvent(
            session_id=session_id,
            parent_event_id=er.parent_event_id,
            event_type=er.event_type,
            agent_id=er.agent_id,
            content=er.content,
            metadata_json=json.dumps(er.metadata),
            token_usage=er.token_usage,
            duration_ms=er.duration_ms,
            status=er.status,
        )
        await trajectory_store.add_event(event)
        results.append(event.to_dict())
    return {"recorded": len(results), "events": results}


@router.get("/sessions/{session_id}/events")
async def get_events(session_id: str, limit: int = Query(default=500, ge=1, le=2000)):
    """Get all events for a session (ordered by time)."""
    session = await trajectory_store.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    events = await trajectory_store.get_events(session_id, limit=limit)
    return {
        "session_id": session_id,
        "events": [e.to_dict() for e in events],
        "count": len(events),
    }


@router.get("/events/{event_id}")
async def get_event(event_id: str):
    """Get a single event by ID."""
    event = await trajectory_store.get_event(event_id)
    if not event:
        raise HTTPException(404, "Event not found")
    return event.to_dict()


# ── Search ───────────────────────────────────────────────────


@router.get("/search")
async def search_events(
    session_id: str = Query(default=""),
    event_type: str = Query(default=""),
    keyword: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Search across trajectory events."""
    events = await trajectory_store.search_events(
        session_id=session_id,
        event_type=event_type,
        keyword=keyword,
        limit=limit,
    )
    return {
        "events": [e.to_dict() for e in events],
        "count": len(events),
        "filters": {"session_id": session_id, "event_type": event_type, "keyword": keyword},
    }


# ── Fork (Branch) ────────────────────────────────────────────


@router.post("/sessions/{session_id}/fork")
async def fork_session(session_id: str, req: ForkRequest):
    """Fork a session at a specific event — creates a new session
    with events copied up to the fork point for branching execution."""
    try:
        new_session = await trajectory_store.fork_session(
            source_session_id=session_id,
            fork_point_event_id=req.fork_point_event_id,
            new_agent_id=req.new_agent_id,
        )
        return new_session.to_dict()
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Replay ───────────────────────────────────────────────────


@router.get("/sessions/{session_id}/replay")
async def replay_session(session_id: str, from_event: str = Query(default="")):
    """Get events for replay — optionally starting from a specific event.
    Returns events in chronological order with step numbers."""
    session = await trajectory_store.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    events = await trajectory_store.get_events(session_id)

    if from_event:
        # Find the starting point
        start_idx = 0
        for i, ev in enumerate(events):
            if ev.id == from_event:
                start_idx = i
                break
        events = events[start_idx:]

    return {
        "session_id": session_id,
        "session": session.to_dict(),
        "steps": [
            {
                "step": i + 1,
                "event_id": ev.id,
                "type": ev.event_type,
                "agent_id": ev.agent_id,
                "content": ev.content,
                "metadata": ev.metadata,
                "token_usage": ev.token_usage,
                "duration_ms": ev.duration_ms,
                "status": ev.status,
                "timestamp": ev.created_at,
            }
            for i, ev in enumerate(events)
        ],
        "total_steps": len(events),
    }


# ── Spans (langfuse Observation layer) ───────────────────────


class EventEndRequest(BaseModel):
    status: str = "ok"
    token_usage: int | None = None


@router.post("/events/{event_id}/end")
async def end_event(event_id: str, req: EventEndRequest | None = None):
    """Close a span-style event — stamps end_at and derives duration_ms."""
    status = req.status if req else "ok"
    token_usage = req.token_usage if req else None
    event = await trajectory_store.end_event(event_id, status=status, token_usage=token_usage)
    if not event:
        raise HTTPException(404, "Event not found")
    return event.to_dict()


@router.get("/sessions/{session_id}/trace")
async def get_trace_tree(session_id: str, limit: int = Query(default=500, ge=1, le=2000)):
    """Get the nested observation tree (Trace→Observation model) with
    per-subtree event/token/duration rollups."""
    session = await trajectory_store.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return await trajectory_store.get_trace_tree(session_id, limit=limit)


# ── Scores (langfuse Score layer) ────────────────────────────


class ScoreCreateRequest(BaseModel):
    name: str
    value: float = 0.0
    string_value: str | None = None
    data_type: str = "numeric"
    comment: str = ""
    source: str = "api"
    trace_id: str | None = None


@router.post("/sessions/{session_id}/scores")
async def add_score(session_id: str, req: ScoreCreateRequest):
    """Attach a quality score to a session (LLM judge / code eval / human)."""
    session = await trajectory_store.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    score = TrajectoryScore(
        session_id=session_id,
        trace_id=req.trace_id,
        name=req.name,
        value=req.value,
        string_value=req.string_value,
        data_type=req.data_type,
        comment=req.comment,
        source=req.source,
    )
    try:
        await trajectory_store.add_score(score)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return score.to_dict()


@router.get("/sessions/{session_id}/scores")
async def list_scores(
    session_id: str,
    name: str = Query(default=""),
    source: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
):
    """List scores attached to a session."""
    session = await trajectory_store.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    scores = await trajectory_store.get_scores(
        session_id=session_id, name=name, source=source, limit=limit
    )
    return {"session_id": session_id, "scores": [s.to_dict() for s in scores]}


@router.get("/sessions/{session_id}/scores/summary")
async def score_summary(session_id: str):
    """Aggregate scores per name — the 'is it getting better' query."""
    session = await trajectory_store.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return await trajectory_store.get_score_summary(session_id=session_id)


# ── Event Types ──────────────────────────────────────────────


@router.get("/event-types")
async def list_event_types():
    """List all supported event types."""
    return {"types": [{"value": et.value, "name": et.name} for et in EventType]}


# ── Analytics ──────────────────────────────────────────────


@router.get("/analytics/tools")
async def tool_analytics(limit: int = Query(default=50, ge=1, le=200)):
    """Get tool usage frequency and success rate analytics."""
    return await trajectory_store.get_tool_analytics(limit=limit)


@router.get("/analytics/agents")
async def agent_analytics(limit: int = Query(default=50, ge=1, le=200)):
    """Get per-agent performance analytics."""
    return await trajectory_store.get_agent_analytics(limit=limit)


@router.get("/analytics/event-types")
async def event_type_analytics():
    """Get event type distribution analytics."""
    return await trajectory_store.get_event_type_analytics()


@router.get("/analytics/tokens")
async def token_analytics(days: int = Query(default=30, ge=1, le=365)):
    """Get token usage over time (daily breakdown)."""
    return await trajectory_store.get_token_analytics(days=days)


# ── Session State Machine ────────────────────────────────────


class FSMTransitionRequest(BaseModel):
    session_id: str
    event: str


class FSMCreateRequest(BaseModel):
    session_id: str


@router.post("/fsm/create")
async def create_fsm(req: FSMCreateRequest):
    """Create a new session state machine."""
    session_fsm.create_session(req.session_id)
    return {"session_id": req.session_id, "state": "idle"}


@router.post("/fsm/transition")
async def fsm_transition(req: FSMTransitionRequest):
    """Transition session state."""
    try:
        event = SessionEvent(req.event)
    except ValueError:
        raise HTTPException(400, f"Unknown event: {req.event}")
    success, new_state = session_fsm.transition(req.session_id, event)
    if not success:
        raise HTTPException(400, f"Invalid transition: {req.event}")
    return {"session_id": req.session_id, "state": new_state.value, "success": True}


@router.get("/fsm/{session_id}/state")
async def fsm_state(session_id: str):
    """Get current session state."""
    return session_fsm.get_state(session_id)


@router.get("/fsm/{session_id}/history")
async def fsm_history(session_id: str):
    """Get session state transition history."""
    return {"history": session_fsm.get_history(session_id)}
