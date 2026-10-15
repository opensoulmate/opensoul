"""Trajectory tracking API — records and replays agent execution events."""

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from src.database.postgres import db_pool
from src.services.tenant_binding import bind_identity

router = APIRouter()

# DEC-②-④ 行租户键单一判法（自带键缺失/空白→'default' 兜底，DEC-②-① _event_row_tenant / ② message_row_tenant 同款语义）。
# 写读同源：写点落行归一、读面加性 tenant_id 键共用本函数，禁消费点另发明判法
# （F4b-⑧⑧ 纪律）；raw=字典行 / 带 tenant_id 属性对象 / 原始租户值三形同收。
DEFAULT_TENANT = "default"


def v2_event_row_tenant(raw: object) -> str:
    if isinstance(raw, dict):
        raw = raw.get("tenant_id")
    elif hasattr(raw, "tenant_id"):
        raw = getattr(raw, "tenant_id")
    return str(raw or "").strip() or DEFAULT_TENANT


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
    timestamp TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'default'
)
"""

_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_traj_api_session
ON trajectory_v2_events(session_id, timestamp)
"""

# DEC-②-④ 行租户索引：补列之后建（③ʜ-2① 坑延续——老库形态先建索引=no such column）
_TENANT_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_traj_v2_tenant
ON trajectory_v2_events(tenant_id, session_id, timestamp)
"""


async def _ensure_table():
    await db_pool.execute(_TABLE_DDL)
    # C1 双轨幂等（DEC-②-④）：老表缺 tenant_id 列→PRAGMA 探测缺失才 ALTER 补列，
    # 存量行随 NOT NULL DEFAULT 'default' 回填（与 F3 实测生产分布一致）；幂等可重复
    # 执行（新表 DDL 已带列=探测命中不动）。
    cols = await db_pool.fetch("PRAGMA table_info(trajectory_v2_events)")
    names = {r["name"] for r in cols}
    if names and "tenant_id" not in names:
        await db_pool.execute(
            "ALTER TABLE trajectory_v2_events ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
        )
    await db_pool.execute(_INDEX_DDL)
    await db_pool.execute(_TENANT_INDEX_DDL)


def _row_to_dict(row) -> dict:
    d = dict(row)
    meta = d.get("metadata", "{}")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}
    d["metadata"] = meta
    # DEC-②-④ 加性键：行归属可观测（归一值，v2_event_row_tenant 单一判法）
    d["tenant_id"] = v2_event_row_tenant(d)
    return d


def _session_row_to_dict(row) -> dict:
    """DEC-②-④ /sessions 聚合行：加性 tenant_id=聚合作用域可观测（归一值）。

    API 面恒按派生租户过滤，组内行键均匀一（MIN(tenant_id)=真值）。
    """
    d = dict(row)
    d["tenant_id"] = v2_event_row_tenant(d)
    return d


# ── POST /api/trajectory/events — 记录事件 ──────────────────


@router.post("/events")
async def record_event(req: EventCreateRequest, authorization: str | None = Header(None)):
    """Record a single trajectory event.

    DEC-②-④ 写点行归属=bind_identity 派生租户流入（匿名 'default' 契约不变；
    EventCreateRequest 无 tenant_id 自报字段=派生轴纯，请求体自报被 pydantic 忽略不进落行）；
    坏 token 401 先于写入零副作用（F3b-③ 纪律）。
    """
    identity = bind_identity(authorization)
    await _ensure_table()
    ts = datetime.now(UTC).isoformat()
    row_tenant = v2_event_row_tenant(identity.tenant_id)
    await db_pool.execute(
        """INSERT INTO trajectory_v2_events
           (session_id, agent_id, event_type, content, metadata, timestamp, tenant_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        req.session_id,
        req.agent_id,
        req.event_type,
        req.content,
        json.dumps(req.metadata),
        ts,
        row_tenant,
    )
    # Extract the autoincremented id from the result string
    # db_pool.execute returns "INSERT 0 {rowcount}"
    row = await db_pool.fetchrow(
        "SELECT * FROM trajectory_v2_events WHERE session_id = ? AND timestamp = ? AND tenant_id = ? ORDER BY id DESC LIMIT 1",
        req.session_id,
        ts,
        row_tenant,
    )
    return (
        _row_to_dict(row)
        if row
        else {"session_id": req.session_id, "timestamp": ts, "tenant_id": row_tenant}
    )


# ── GET /api/trajectory/sessions — 列出所有有轨迹的会话 ─────


@router.get("/sessions")
async def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(None),
):
    """List all distinct sessions that have trajectory events.

    DEC-②-④ 读面：恒按行租户键过滤（tenant=bind_identity 派生禁自报，匿名
    'default' 契约不变，无 tenant_id 自报参数）；total 只计可见会话（过滤先于
    计数，禁泄露全局分布——②-③ count 同款）。
    """
    identity = bind_identity(authorization)
    await _ensure_table()
    tenant = v2_event_row_tenant(identity.tenant_id)
    rows = await db_pool.fetch(
        """SELECT session_id,
                  COUNT(*) as event_count,
                  MIN(timestamp) as first_event,
                  MAX(timestamp) as last_event,
                  GROUP_CONCAT(DISTINCT agent_id) as agents,
                  MIN(tenant_id) as tenant_id
           FROM trajectory_v2_events
           WHERE tenant_id = ?
           GROUP BY session_id
           ORDER BY last_event DESC
           LIMIT ? OFFSET ?""",
        tenant,
        limit,
        offset,
    )
    total_row = await db_pool.fetchrow(
        "SELECT COUNT(DISTINCT session_id) as cnt FROM trajectory_v2_events WHERE tenant_id = ?",
        tenant,
    )
    return {
        "sessions": [_session_row_to_dict(r) for r in rows],
        "total": total_row["cnt"] if total_row else 0,
        "limit": limit,
        "offset": offset,
    }


# ── GET /api/trajectory/sessions/{session_id} — 获取会话完整轨迹 ─


@router.get("/sessions/{session_id}")
async def get_session_trajectory(
    session_id: str,
    limit: int = Query(default=1000, ge=1, le=5000),
    authorization: str | None = Header(None),
):
    """Get the full trajectory for a session.

    DEC-②-④ by-ID 读面归属闸：恒按 (行租户键, session_id) 读取，跨租户
    回 404 与 not-found 同契约不差分泄露行存在性（③ʜ-1 纪律、②-⑨ 同款）。
    """
    identity = bind_identity(authorization)
    await _ensure_table()
    rows = await db_pool.fetch(
        """SELECT * FROM trajectory_v2_events
           WHERE session_id = ? AND tenant_id = ?
           ORDER BY timestamp ASC
           LIMIT ?""",
        session_id,
        v2_event_row_tenant(identity.tenant_id),
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
    authorization: str | None = Header(None),
):
    """Replay trajectory events in chronological order.
    Use `from` to start replay from a specific event id.

    DEC-②-④ by-ID 读面归属闸：与 /sessions/{session_id} 同契约（跨租户 404
    与 not-found 同文案不差分泄露）。
    """
    identity = bind_identity(authorization)
    tenant = v2_event_row_tenant(identity.tenant_id)
    await _ensure_table()
    if from_event > 0:
        rows = await db_pool.fetch(
            """SELECT * FROM trajectory_v2_events
               WHERE session_id = ? AND tenant_id = ? AND id >= ?
               ORDER BY timestamp ASC""",
            session_id,
            tenant,
            from_event,
        )
    else:
        rows = await db_pool.fetch(
            """SELECT * FROM trajectory_v2_events
               WHERE session_id = ? AND tenant_id = ?
               ORDER BY timestamp ASC""",
            session_id,
            tenant,
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
                "tenant_id": v2_event_row_tenant(r["tenant_id"]),
            }
            for i, r in enumerate(rows)
        ],
        "total_steps": len(rows),
    }
