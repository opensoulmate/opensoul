"""OpenTimeline API — Persistent cross-component event timeline.

Aggregates events from all organs into a searchable, filterable timeline
with persistent SQLite storage. Complements the in-memory event stream
with full history, statistics, and analytics.
"""

import time

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from src.services.tenant_binding import bind_identity
from src.timeline.store import TimelineEventAccessDenied, TimelineStore

router = APIRouter()

# ── Singleton ──────────────────────────────────────────────
store = TimelineStore()


# ── Request Schemas ────────────────────────────────────────


class RecordEventRequest(BaseModel):
    organ: str
    emoji: str = ""
    event_type: str
    summary: str
    detail: dict = {}


class ClearRequest(BaseModel):
    older_than_days: int | None = None


# ── Event Recording ────────────────────────────────────────


@router.post("/record")
async def record_event(req: RecordEventRequest, authorization: str | None = Header(None)):
    """Record an event to the persistent timeline.

    F3b-③c-2①：行归属租户从认证身份派生（bind_identity，匿名='default'），禁自报——
    RecordEventRequest 无 tenant 字段。
    """
    import uuid

    identity = bind_identity(authorization)
    event = {
        "id": f"tl_{uuid.uuid4().hex[:12]}",
        "organ": req.organ,
        "emoji": req.emoji,
        "type": req.event_type,
        "summary": req.summary,
        "detail": req.detail,
        "timestamp": time.time(),
        "collected_at": time.time(),
        "tenant_id": identity.tenant_id,
    }
    ok = store.record(event)
    return {"status": "ok" if ok else "duplicate", "event_id": event["id"]}


# ── Event Querying ─────────────────────────────────────────


@router.get("/events")
async def list_events(
    organ: str = Query(default=None, description="Filter by organ name"),
    event_type: str = Query(default=None, description="Filter by event type"),
    search: str = Query(default=None, description="Search in summary and detail"),
    since: float = Query(default=None, description="Events after this timestamp"),
    until: float = Query(default=None, description="Events before this timestamp"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(None),
):
    """Query timeline events with rich filtering.

    F3b-③c-2①：恒按派生租户过滤（匿名='default' 只见 default 行）。
    """
    identity = bind_identity(authorization)
    events = store.query(
        organ=organ,
        event_type=event_type,
        search=search,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
        tenant_id=identity.tenant_id,
    )
    return {
        "events": [e.to_dict() for e in events],
        "count": len(events),
        "limit": limit,
        "offset": offset,
    }


@router.get("/events/{event_id}")
async def get_event(event_id: str, authorization: str | None = Header(None)):
    """Get a single event by ID. F3b-③c-2①：by-ID 读走隔离闸门，跨租户 403。"""
    identity = bind_identity(authorization)
    try:
        event = store.get_event(event_id, tenant_id=identity.tenant_id)
    except TimelineEventAccessDenied as e:
        raise HTTPException(403, f"timeline event access denied: {e.reason}")
    if not event:
        raise HTTPException(404, "Event not found")
    return event.to_dict()


@router.delete("/events/{event_id}")
async def delete_event(event_id: str, authorization: str | None = Header(None)):
    """Delete a single event. F3b-③c-2①：写操作走隔离闸门，跨租户 403。"""
    identity = bind_identity(authorization)
    try:
        ok = store.delete_event(event_id, tenant_id=identity.tenant_id)
    except TimelineEventAccessDenied as e:
        raise HTTPException(403, f"timeline event access denied: {e.reason}")
    if not ok:
        raise HTTPException(404, "Event not found")
    return {"status": "ok", "event_id": event_id}


# ── Bulk Operations ────────────────────────────────────────


@router.post("/clear")
async def clear_events(req: ClearRequest | None = None, authorization: str | None = Header(None)):
    """Clear events. Optionally only clear events older than N days.

    F3b-③c-2①：清理恒按派生租户范围（防"只给租户"变相清全史）。
    """
    identity = bind_identity(authorization)
    older = req.older_than_days if req else None
    count = store.clear(older_than_days=older, tenant_id=identity.tenant_id)
    return {"status": "ok", "cleared": count}


# ── Statistics ─────────────────────────────────────────────


@router.get("/stats")
async def timeline_stats(authorization: str | None = Header(None)):
    """Get comprehensive timeline statistics. F3b-③c-2①：恒按派生租户范围。"""
    identity = bind_identity(authorization)
    return store.stats(tenant_id=identity.tenant_id)


@router.get("/organs")
async def list_organs(authorization: str | None = Header(None)):
    """List all organs with event counts. F3b-③c-2①：恒按派生租户范围。"""
    identity = bind_identity(authorization)
    return {"organs": store.organs(tenant_id=identity.tenant_id)}


@router.get("/types")
async def list_event_types(authorization: str | None = Header(None)):
    """List all event types with counts. F3b-③c-2①：恒按派生租户范围。"""
    identity = bind_identity(authorization)
    return {"types": store.event_types(tenant_id=identity.tenant_id)}


# ── Sync from Event Stream ─────────────────────────────────


@router.post("/sync")
async def sync_from_stream():
    """Sync events from the in-memory event stream buffer to persistent timeline.

    This captures events that were pushed via push_event() but not yet
    recorded in the timeline database.

    F3b-③c-2① 诚实边界：sync 是系统漏斗（buffer 事件来自器官内生 push_event，
    无身份上下文），行归属按事件自带键、缺失→'default' 兜底（record 同款），不归
    因到调用方——调用方只触发落盘，不获得他租户行的读写面。
    """
    try:
        from src.api.event_stream import _event_buffer

        synced = 0
        for event in _event_buffer:
            if store.record(event):
                synced += 1
        return {"status": "ok", "synced": synced, "buffer_size": len(_event_buffer)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Health ─────────────────────────────────────────────────


@router.get("/health")
async def timeline_health():
    """OpenTimeline health check.

    F3b-③c-2①：health stats 全局不闸不滤（③c-1 同款边界：体检面是系统视图）。
    """
    stats = store.stats()
    return {
        "status": "ok",
        "component": "OpenTimeline",
        "total_events": stats["total_events"],
        "recent_24h": stats["recent_24h"],
    }
