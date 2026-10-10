"""Trajectory tracking API — records and replays agent execution events."""

import json
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.database.postgres import db_pool

router = APIRouter()
# ── Health ────────────────────────────────────────────────────


@router.get("/health")
async def trajectory_v2_health():
    """Trajectory-v2 health check."""
    return {"status": "ok", "component": "trajectory-v2"}


# ── Request Schemas ──────────────────────────────────────────


class EventCreateRequest(BaseModel):
    session_id: str
    agent_id: str = ""
    event_type: str
    content: str = ""
    metadata: dict = {}


# ── Table Init ───────────────────────────────────────────────
# C1（2026-10-11）拆表：原表名trajectory_events与trajectory/store.py的同名表
# 同名不同形（那边TEXT uuid主键/metadata_json/created_at，这边自增id/metadata/
# timestamp），共享一张表必互相"no such column/NOT NULL冲突"（live实测GET
# /api/trajectory-v2/sessions恒500）。各归各表：v2事件落自己的表，store的
# trajectory_events及其存量数据零改动。

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS trajectory_v2_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}',
    timestamp TEXT NOT NULL
)
"""

_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_traj_api_session
ON trajectory_v2_events(session_id, timestamp)
"""


async def _ensure_table():
    await db_pool.execute(_TABLE_DDL)
    await db_pool.execute(_INDEX_DDL)


def _row_to_dict(row) -> dict:
    d = dict(row)
    meta = d.get("metadata", "{}")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}
    d["metadata"] = meta
    return d


# ── POST /api/trajectory/events — 记录事件 ──────────────────


@router.post("/events")
async def record_event(req: EventCreateRequest):
    """Record a single trajectory event."""
    await _ensure_table()
    ts = datetime.now(UTC).isoformat()
    await db_pool.execute(
        """INSERT INTO trajectory_v2_events
           (session_id, agent_id, event_type, content, metadata, timestamp)
           VALUES (?, ?, ?, ?, ?, ?)""",
        req.session_id,
        req.agent_id,
        req.event_type,
        req.content,
        json.dumps(req.metadata),
        ts,
    )
    # Extract the autoincremented id from the result string
    # db_pool.execute returns "INSERT 0 {rowcount}"
    row = await db_pool.fetchrow(
        "SELECT * FROM trajectory_v2_events WHERE session_id = ? AND timestamp = ? ORDER BY id DESC LIMIT 1",
        req.session_id,
        ts,
    )
    return _row_to_dict(row) if row else {"session_id": req.session_id, "timestamp": ts}


# ── GET /api/trajectory/sessions — 列出所有有轨迹的会话 ─────


@router.get("/sessions")
async def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List all distinct sessions that have trajectory events."""
    await _ensure_table()
    rows = await db_pool.fetch(
        """SELECT session_id,
                  COUNT(*) as event_count,
                  MIN(timestamp) as first_event,
                  MAX(timestamp) as last_event,
                  GROUP_CONCAT(DISTINCT agent_id) as agents
           FROM trajectory_v2_events
           GROUP BY session_id
           ORDER BY last_event DESC
           LIMIT ? OFFSET ?""",
        limit,
        offset,
    )
    total_row = await db_pool.fetchrow(
        "SELECT COUNT(DISTINCT session_id) as cnt FROM trajectory_v2_events"
    )
    return {
        "sessions": [dict(r) for r in rows],
        "total": total_row["cnt"] if total_row else 0,
        "limit": limit,
        "offset": offset,
    }


# ── GET /api/trajectory/sessions/{session_id} — 获取会话完整轨迹 ─


@router.get("/sessions/{session_id}")
async def get_session_trajectory(
    session_id: str,
    limit: int = Query(default=1000, ge=1, le=5000),
):
    """Get the full trajectory for a session."""
    await _ensure_table()
    rows = await db_pool.fetch(
        """SELECT * FROM trajectory_v2_events
           WHERE session_id = ?
           ORDER BY timestamp ASC
           LIMIT ?""",
        session_id,
        limit,
    )
    if not rows:
        raise HTTPException(404, "Session not found or has no events")
    return {
        "session_id": session_id,
        "events": [_row_to_dict(r) for r in rows],
        "count": len(rows),
    }


# ── GET /api/trajectory/sessions/{session_id}/replay — 回放轨迹 ─


@router.get("/sessions/{session_id}/replay")
async def replay_session(
    session_id: str,
    from_event: int = Query(default=0, ge=0, alias="from"),
):
    """Replay trajectory events in chronological order.
    Use `from` to start replay from a specific event id."""
    await _ensure_table()
    if from_event > 0:
        rows = await db_pool.fetch(
            """SELECT * FROM trajectory_v2_events
               WHERE session_id = ? AND id >= ?
               ORDER BY timestamp ASC""",
            session_id,
            from_event,
        )
    else:
        rows = await db_pool.fetch(
            """SELECT * FROM trajectory_v2_events
               WHERE session_id = ?
               ORDER BY timestamp ASC""",
            session_id,
        )
    if not rows:
        raise HTTPException(404, "Session not found or no events from that point")
    return {
        "session_id": session_id,
        "from_event": from_event,
        "steps": [
            {
                "step": i + 1,
                "id": r["id"],
                "agent_id": r["agent_id"],
                "event_type": r["event_type"],
                "content": r["content"],
                "metadata": json.loads(r["metadata"])
                if isinstance(r["metadata"], str)
                else r["metadata"],
                "timestamp": r["timestamp"],
            }
            for i, r in enumerate(rows)
        ],
        "total_steps": len(rows),
    }
