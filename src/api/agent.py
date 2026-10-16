import json
import logging
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

logger = logging.getLogger(__name__)

router = APIRouter()

# ── G1-⑤c 管理面注册接线（MultiAgentCoordinator 协调面） ────────────────
#
# 修前（G1-⑤ 诚实边界④留档）：agent.py 管理面注册（admin agent 节点，
# opensoul.db agents 表）与协调注册表零接线——admin 注册的节点不进
# /api/brain/agents 协作视图、不参与冲突检查/协调收件箱（G1-⑤b 面）。
# 本节把管理面生命周期接进协调面：注册→协调行（+active 同步两注册表状态）、
# 心跳→活跃窗保活、删除→注销（派生协调行随真源删除）。
#
# 纪律（G1-⑤ 同族）：①旁路纪律——桥写失败 logger.warning 可见，绝不吃掉
# 管理面主路径（注册/心跳/删除照常成功）②心跳 UPDATE-only：未注册 agent_id
# 绝不 fabricate 协调行（heartbeat 方法既有语义）③协调键=_MGMT_COORD_TENANT
# 单一源禁散落字面量。
#
# 诚实边界：管理面无租户轴（agents 表无 tenant 列、/nodes 全局列表、删除
# 全局可见），协调行钉 'default' 与数据真态一致（F3 实测分布同款：无租户轴
# 行归属 'default'）；管理面租户轴收口（agents.tenant_id+身份派生）是另一拍
# （F3b-② 同型），届时协调键随行归属迁移。

_MGMT_COORD_TENANT = "default"


def _coord():
    """协调器构造（call-time 读模块级 db_pool：与管理面同一数据面，
    测试 monkeypatch src.api.agent.db_pool 同时覆盖主路径与桥）。"""
    from src.cortex.multi_agent_coord import MultiAgentCoordinator

    return MultiAgentCoordinator(db_pool, _MGMT_COORD_TENANT)


async def _coord_register(agent_id: str, agent_type: str) -> None:
    """注册桥（register_agent 方法生产消费点）：失败旁路不阻断注册。"""
    try:
        await _coord().register_agent(agent_id, agent_type)
    except Exception as exc:
        logger.warning("coord register bridge failed for %s: %s", agent_id, exc)


async def _coord_heartbeat(agent_id: str, status: str = "active") -> None:
    """心跳桥（heartbeat 方法生产消费点）：失败旁路不阻断心跳主路径。
    current_task/current_files 缺省 None=保留现值（heartbeat COALESCE 语义）。"""
    try:
        await _coord().heartbeat(agent_id, status=status)
    except Exception as exc:
        logger.warning("coord heartbeat bridge failed for %s: %s", agent_id, exc)


async def _coord_unregister(agent_id: str) -> None:
    """注销桥（unregister_agent 方法首个生产消费点）：真源行已删，派生
    协调行随删防幽灵行；失败旁路不阻断删除主路径。"""
    try:
        await _coord().unregister_agent(agent_id)
    except Exception as exc:
        logger.warning("coord unregister bridge failed for %s: %s", agent_id, exc)


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
    # G1-⑤c 注册桥：管理面节点进协调注册表（+active 同步——管理面注册即
    # 'active'，两注册表状态一致禁两套脸；桥失败旁路不吃主路径）。
    await _coord_register(str(agent_id), req.agent_type)
    await _coord_heartbeat(str(agent_id), status="active")
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
    # G1-⑤c 心跳桥：管理面心跳保活协调活跃窗（UPDATE-only——协调行由注册桥
    # 建，此处绝不 fabricate；桥失败旁路不吃主路径）。
    await _coord_heartbeat(str(req.agent_id), status=req.status)
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
    # G1-⑤c 注销桥：真源行删除→派生协调行随删（禁幽灵行留在活跃窗）；
    # 桥失败旁路不吃主路径（warn 可见，行删不回滚）。
    await _coord_unregister(str(node_id))
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
