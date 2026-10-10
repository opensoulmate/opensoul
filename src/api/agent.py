import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api._errors import stats_error
from src.database.postgres import db_pool
from src.middleware.auth import get_current_user, require_agent, require_role
from src.models.knowledge import KnowledgeCreate
from src.services import knowledge as knowledge_service
from src.services.rag import rag_query

router = APIRouter()


@router.get("/health")
async def agent_health():
    """Agent system health check."""
    return {"status": "ok", "component": "AgentSystem"}


@router.get("/stats")
async def agent_stats():
    """Get agent system statistics."""
    try:
        total = await db_pool.fetchval("SELECT COUNT(*) FROM agents") or 0
        by_type = await db_pool.fetch(
            "SELECT agent_type, COUNT(*) as cnt FROM agents GROUP BY agent_type ORDER BY cnt DESC"
        )
        return {
            "status": "ok",
            "component": "AgentSystem",
            "total_agents": total,
            "by_type": {r["agent_type"]: r["cnt"] for r in (by_type or [])},
        }
    except Exception as exc:
        # C2统一错误响应：查询失败说失败、说原因，禁谎报ok（假0冒充"没数据"）
        return stats_error("AgentSystem", exc, total_agents=0, by_type={})


# ── Request / Response models ──────────────────────────────────────────


class AgentRegisterRequest(BaseModel):
    name: str
    agent_type: str = "generic"
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class AgentRegisterResponse(BaseModel):
    agent_id: UUID
    name: str
    token: str
    registered_at: datetime


class AgentHeartbeatRequest(BaseModel):
    agent_id: UUID
    status: str = "active"


class AgentReportRequest(BaseModel):
    agent_id: UUID
    report_type: str
    data: dict


# ── Agent node management ──────────────────────────────────────────────


@router.post("/register", response_model=AgentRegisterResponse)
async def register_agent(
    req: AgentRegisterRequest,
    current_user: dict = Depends(require_role("admin")),
):
    """Register a new agent node — admin only. Returns agent token."""
    agent_id = uuid4()
    token = uuid4().hex

    row = await db_pool.fetchrow(
        "INSERT INTO agents (id, name, agent_type, capabilities, metadata, token, status) "
        "VALUES ($1, $2, $3, $4, $5, $6, 'active') "
        # C2：回包键名对齐AgentRegisterResponse（agent_id/registered_at），
        # 列表/字典必须json.dumps（SQLite绑定层不支持list/dict，铁律）
        "RETURNING id AS agent_id, name, token, created_at AS registered_at",
        str(agent_id),
        req.name,
        req.agent_type,
        json.dumps(req.capabilities, ensure_ascii=False),
        json.dumps(req.metadata, ensure_ascii=False),
        token,
    )
    if not row:
        raise HTTPException(status_code=500, detail="Failed to register agent")
    return dict(row)


@router.post("/heartbeat")
async def agent_heartbeat(
    req: AgentHeartbeatRequest,
    agent: dict = Depends(require_agent),
):
    """Update agent heartbeat — requires valid X-Agent-Token header."""
    result = await db_pool.execute(
        "UPDATE agents SET status = $1, last_heartbeat = NOW() WHERE id = $2",
        req.status,
        req.agent_id,
    )
    if "UPDATE 0" in result:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {
        "agent_id": req.agent_id,
        "status": req.status,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.post("/report")
async def agent_report(
    req: AgentReportRequest,
    agent: dict = Depends(require_agent),
):
    """Agent reports Soma-collected data — stored and queued for processing."""
    await db_pool.execute(
        # C2：dict必须json.dumps、UUID手动str()（SQLite绑定层不支持list/dict，铁律）
        "INSERT INTO agent_reports (agent_id, report_type, data) VALUES ($1, $2, $3)",
        str(req.agent_id),
        req.report_type,
        json.dumps(req.data, ensure_ascii=False),
    )
    return {"status": "received", "agent_id": str(req.agent_id)}


def _parse_ts(value) -> datetime | None:
    """last_heartbeat 列在 SQLite 是 TEXT（datetime('now')格式），解析失败返回 None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@router.get("/nodes")
async def list_agent_nodes(
    status: str | None = None,
    current_user: dict = Depends(get_current_user),
):
    """List all registered agent nodes with online/offline status."""
    if status:
        rows = await db_pool.fetch(
            "SELECT id, name, agent_type, capabilities, status, last_heartbeat, created_at "
            "FROM agents WHERE status = $1 ORDER BY created_at DESC",
            status,
        )
    else:
        rows = await db_pool.fetch(
            "SELECT id, name, agent_type, capabilities, status, last_heartbeat, created_at "
            "FROM agents ORDER BY created_at DESC"
        )
    now = datetime.now(UTC)
    result = []
    for r in rows:
        d = dict(r)
        # C2根因修复：last_heartbeat是TEXT直接做datetime算术会TypeError（有心跳行必炸），
        # 解析失败视为不在线，禁500。
        hb = _parse_ts(d.get("last_heartbeat"))
        d["online"] = hb is not None and (now - hb).total_seconds() < 120
        result.append(d)
    return result


@router.delete("/nodes/{node_id}")
async def delete_agent_node(
    node_id: UUID,
    current_user: dict = Depends(require_role("admin")),
):
    """Delete an agent node — admin only."""
    result = await db_pool.execute("DELETE FROM agents WHERE id = $1", node_id)
    if "DELETE 0" in result:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "deleted", "agent_id": str(node_id)}


# ── Agent memory operations ────────────────────────────────────────────


class AgentRememberRequest(BaseModel):
    title: str
    content: str
    tags: list[str] = []


class AgentRecallRequest(BaseModel):
    question: str
    top_k: int = 5


@router.post("/remember")
async def remember(req: AgentRememberRequest, current_user: dict = Depends(get_current_user)):
    """Agent stores a new memory."""
    data = KnowledgeCreate(title=req.title, content=req.content, tags=req.tags)
    row = await knowledge_service.create_knowledge(data, current_user["id"])
    return {"status": "remembered", "id": row["id"]}


@router.post("/recall")
async def recall(req: AgentRecallRequest, current_user: dict = Depends(get_current_user)):
    """Agent retrieves relevant memories."""
    result = await rag_query(req.question, current_user["id"], req.top_k)
    return result
