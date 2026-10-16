import json
import logging
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from src.api._errors import stats_error
from src.database.postgres import db_pool
from src.middleware.auth import get_current_user, require_agent, require_role
from src.models.knowledge import KnowledgeCreate
from src.nest.isolation import IsolationEngine, ResourceType
from src.nest.tenant import tenant_namespace
from src.services import knowledge as knowledge_service
from src.services.rag import rag_query
from src.services.tenant_binding import bind_identity

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
# G1-⑤c-② 管理面租户轴收口：修前协调键钉 _MGMT_COORD_TENANT='default'（管理面
# 无租户轴时与数据真态一致的如实妥协），本拍 agents.tenant_id 行租户键落地后
# 协调键随行归属迁移——注册桥键=身份派生租户（bind_identity 禁自报）、心跳/
# 注销桥键=行归属租户单一源（_node_row_tenant）。
#
# 纪律（G1-⑤ 同族）：①旁路纪律——桥写失败 logger.warning 可见，绝不吃掉
# 管理面主路径（注册/心跳/删除照常成功）②心跳 UPDATE-only：未注册 agent_id
# 绝不 fabricate 协调行（heartbeat 方法既有语义）③协调键=行归属租户单一源
# 禁散落字面量④claim 持证（F3b-⑤ 同哲学）：X-Agent-Token 已证明 agent 身份，
# body agent_id 是 claim——必须与 token 行一致，否则任意持证 agent 可冒写他
# agent 行（跨租户写路直穿租户闸），矛盾=403 零副作用。


def _row_tenant(value) -> str:
    """行归属租户键：缺失/空白→'default' 兜底（键派生故障绝不吃掉落库，③b-2 旁路纪律）。"""
    return str(value or "").strip() or "default"


async def _node_row_tenant(node_id) -> str | None:
    """agents 行归属租户单一权威（心跳/注销桥键、删除双键共用，禁手抄）。
    缺行→None（调用方按各自语义 404/跳过）。"""
    row = await db_pool.fetchrow("SELECT tenant_id FROM agents WHERE id = $1", str(node_id))
    return _row_tenant(row["tenant_id"]) if row else None


def _require_agent_claim(agent: dict, claimed) -> None:
    """claim 持证：token 行 id 是 agent 真身份，body agent_id 是 claim——必须一致。
    矛盾=403 零副作用（身份证明先于一切写入，F3b-⑤ 纪律）。"""
    token_id = str(agent.get("id") or "")
    if str(claimed) != token_id:
        raise HTTPException(
            status_code=403,
            detail=f"agent claim mismatch: token identifies '{token_id}', claimed '{claimed}'",
        )


class AgentNodeAccessDenied(Exception):
    """跨租户访问被 IsolationEngine 拒绝（API 层映射 403 带真因，禁静默 not_found/假成功）。"""

    def __init__(self, reason: str, verdict: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.verdict = verdict or {}


_isolation_cache = None


def _iso() -> IsolationEngine:
    """隔离引擎（check_access 真执行点；审计落 NestStore）。按 NEST_DB env 值缓存，
    测试 env 切换自动换引擎（enterprise/ai_groups/agent_collaboration _iso 同款）。"""
    global _isolation_cache
    key = os.environ.get("NEST_DB") or ""
    if _isolation_cache is None or _isolation_cache[0] != key:
        _isolation_cache = (key, IsolationEngine())
    return _isolation_cache[1]


def _gate(tenant_id: str, row_tenant: str, resource_id: str, action: str) -> None:
    """by-ID 访问闸门（G1-⑤c-②）：IsolationEngine.check_access 真执行（F3b 家族）。
    资源 ID=namespaced_id(行归属租户 ns, id)——namespace 前缀是行归属租户，比较基准
    是访问者派生租户；namespace 不匹配=跨租户冒读/冒写，引擎真拒绝→403 带真因。
    复用 ResourceType.AGENT（③c-2②d 既有资源型，namespace_scoped=True 零策略改动）。"""
    iso = _iso()
    verdict = iso.check_access(
        tenant_namespace=tenant_namespace(tenant_id),
        resource_type=ResourceType.AGENT,
        resource_id=iso.namespaced_id(tenant_namespace(row_tenant), resource_id),
        action=action,
    )
    if not verdict.get("allowed"):
        raise AgentNodeAccessDenied(verdict.get("reason", "access denied"), verdict)


def _gate_bulk(tenant_id: str, action: str) -> None:
    """bulk 操作（/nodes /stats）审计留痕：执行隔离由 WHERE 租户过滤承担，
    引擎侧记一次 namespace 自匹配访问（audit_access="Log all access attempts"）。"""
    iso = _iso()
    iso.check_access(
        tenant_namespace=tenant_namespace(tenant_id),
        resource_type=ResourceType.AGENT,
        resource_id=iso.namespaced_id(tenant_namespace(tenant_id), "*"),
        action=action,
    )


def _coord(tenant_id: str):
    """协调器构造（call-time 读模块级 db_pool：与管理面同一数据面，
    测试 monkeypatch src.api.agent.db_pool 同时覆盖主路径与桥）。键=行归属租户。"""
    from src.cortex.multi_agent_coord import MultiAgentCoordinator

    return MultiAgentCoordinator(db_pool, tenant_id)


async def _coord_register(agent_id: str, agent_type: str, tenant_id: str) -> None:
    """注册桥（register_agent 方法生产消费点）：失败旁路不阻断注册。"""
    try:
        await _coord(tenant_id).register_agent(agent_id, agent_type)
    except Exception as exc:
        logger.warning("coord register bridge failed for %s: %s", agent_id, exc)


async def _coord_heartbeat(
    agent_id: str,
    tenant_id: str,
    status: str = "active",
    current_task: str | None = None,
    current_files: list[str] | None = None,
) -> None:
    """心跳桥（heartbeat 方法生产消费点）：失败旁路不阻断心跳主路径。

    G1-⑤c-④ 工作集声明透传（G1-⑤b 声明语义同款单一语义）：current_task/
    current_files 缺省 None=保留现值（heartbeat COALESCE——keep-alive 节拍不再
    洗掉已声明工作集），显式传值（含 ''/[]）=更新/清空声明。"""
    try:
        await _coord(tenant_id).heartbeat(
            agent_id,
            status=status,
            current_task=current_task,
            current_files=current_files,
        )
    except Exception as exc:
        logger.warning("coord heartbeat bridge failed for %s: %s", agent_id, exc)


async def _coord_unregister(agent_id: str, tenant_id: str) -> None:
    """注销桥（unregister_agent 方法首个生产消费点）：真源行已删，派生
    协调行随删防幽灵行；失败旁路不阻断删除主路径。"""
    try:
        await _coord(tenant_id).unregister_agent(agent_id)
    except Exception as exc:
        logger.warning("coord unregister bridge failed for %s: %s", agent_id, exc)


@router.get("/health")
async def agent_health():
    """Agent system health check."""
    return {"status": "ok", "component": "AgentSystem"}


@router.get("/stats")
async def agent_stats(authorization: str | None = Header(None)):
    """Get agent system statistics.

    G1-⑤c-②：恒按身份派生租户聚合（修前全局计数=跨租户分布泄露，③c-2②b
    stats 纪律）；匿名 'default' 契约不变（本端点无鉴权轴既有语义）。"""
    identity = bind_identity(authorization)
    try:
        _gate_bulk(identity.tenant_id, "read")
        total = (
            await db_pool.fetchval(
                "SELECT COUNT(*) FROM agents WHERE tenant_id = $1", identity.tenant_id
            )
            or 0
        )
        by_type = await db_pool.fetch(
            "SELECT agent_type, COUNT(*) as cnt FROM agents WHERE tenant_id = $1 "
            "GROUP BY agent_type ORDER BY cnt DESC",
            identity.tenant_id,
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
    # G1-⑤c-② 加性键：行归属租户可观测（register 身份派生落行）。
    tenant_id: str = "default"


class AgentHeartbeatRequest(BaseModel):
    agent_id: UUID
    status: str = "active"
    # G1-⑤c-④ 工作集声明（G1-⑤b 声明语义同款）：缺省 None=保留现值（COALESCE，
    # keep-alive 不洗声明）、显式值=更新（含空串/空列表=清空）。非法载荷
    # （非字符串/非字符串列表）由 pydantic 422 先拒零副作用，禁静默吞声明。
    current_task: str | None = None
    current_files: list[str] | None = None


class AgentReportRequest(BaseModel):
    agent_id: UUID
    report_type: str
    data: dict


# ── Agent node management ──────────────────────────────────────────────


@router.post("/register", response_model=AgentRegisterResponse)
async def register_agent(
    req: AgentRegisterRequest,
    current_user: dict = Depends(require_role("admin")),
    authorization: str | None = Header(None),
):
    """Register a new agent node — admin only. Returns agent token."""
    # G1-⑤c-② 行归属=身份派生（bind_identity 禁自报，F3b-②）：匿名 'default'
    # 契约不变（register 本就 admin-only=实际恒认证身份）。
    identity = bind_identity(authorization)
    agent_id = uuid4()
    token = uuid4().hex

    row = await db_pool.fetchrow(
        "INSERT INTO agents (id, name, agent_type, capabilities, metadata, token, status, tenant_id) "
        "VALUES ($1, $2, $3, $4, $5, $6, 'active', $7) "
        # C2：回包键名对齐AgentRegisterResponse（agent_id/registered_at），
        # 列表/字典必须json.dumps（SQLite绑定层不支持list/dict，铁律）
        "RETURNING id AS agent_id, name, token, created_at AS registered_at, tenant_id",
        str(agent_id),
        req.name,
        req.agent_type,
        json.dumps(req.capabilities, ensure_ascii=False),
        json.dumps(req.metadata, ensure_ascii=False),
        token,
        identity.tenant_id,
    )
    if not row:
        raise HTTPException(status_code=500, detail="Failed to register agent")
    # G1-⑤c 注册桥：管理面节点进协调注册表（+active 同步——管理面注册即
    # 'active'，两注册表状态一致禁两套脸；桥失败旁路不吃主路径）。协调键=
    # 行归属租户（G1-⑤c-② 随行归属迁移）。
    await _coord_register(str(agent_id), req.agent_type, identity.tenant_id)
    await _coord_heartbeat(str(agent_id), identity.tenant_id, status="active")
    return dict(row)


@router.post("/heartbeat")
async def agent_heartbeat(
    req: AgentHeartbeatRequest,
    agent: dict = Depends(require_agent),
):
    """Update agent heartbeat — requires valid X-Agent-Token header."""
    # claim 持证（G1-⑤c-②）：token 行=agent 真身份，body agent_id 必须一致，
    # 否则任意持证 agent 可冒写他 agent 行（跨租户写路直穿租户闸）——403 零副作用。
    _require_agent_claim(agent, req.agent_id)
    # 协调桥键=行归属租户单一源（_node_row_tenant）。
    row_tenant = await _node_row_tenant(req.agent_id)
    if row_tenant is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    result = await db_pool.execute(
        "UPDATE agents SET status = $1, last_heartbeat = NOW() WHERE id = $2",
        req.status,
        str(req.agent_id),
    )
    if "UPDATE 0" in result:
        raise HTTPException(status_code=404, detail="Agent not found")
    # G1-⑤c 心跳桥：管理面心跳保活协调活跃窗（UPDATE-only——协调行由注册桥
    # 建，此处绝不 fabricate；桥失败旁路不吃主路径）。
    # G1-⑤c-④ 工作集声明（G1-⑤b 同款单一语义）：显式声明=更新（含空=清空），
    # 缺省=None 保留现值；status='offline' 且未显式声明=清空工作集（WS 断开
    # 收尾 heartbeat_sync 绑 ''/'[]' 同语义——节点下线不留陈旧声明在冲突视图
    # 里造幽灵冲突）。显式声明恒优先（禁静默吞声明）。
    declared_task = req.current_task
    declared_files = req.current_files
    if declared_task is None and declared_files is None and req.status == "offline":
        declared_task = ""
        declared_files = []
    await _coord_heartbeat(
        str(req.agent_id),
        row_tenant,
        status=req.status,
        current_task=declared_task,
        current_files=declared_files,
    )
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
    # claim 持证（G1-⑤c-②）：上报行归属=token 行 agent（禁冒写他 agent 报告）。
    _require_agent_claim(agent, req.agent_id)
    # G1-⑤c-③ 行租户键：行归属=agents 行归属租户单一权威（_node_row_tenant，
    # 心跳/注销桥键同源禁手抄）；键派生故障绝不吃掉落库（③b-2 纪律：缺失/空白
    # →'default' 兜底）。
    row_tenant = _row_tenant(await _node_row_tenant(req.agent_id))
    await db_pool.execute(
        # C2：dict必须json.dumps、UUID手动str()（SQLite绑定层不支持list/dict，铁律）
        "INSERT INTO agent_reports (agent_id, report_type, data, tenant_id) "
        "VALUES ($1, $2, $3, $4)",
        str(req.agent_id),
        req.report_type,
        json.dumps(req.data, ensure_ascii=False),
        row_tenant,
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
    authorization: str | None = Header(None),
):
    """List all registered agent nodes with online/offline status.

    G1-⑤c-②：恒按身份派生租户过滤（修前全局列表=跨租户身份泄露，③c-2②d
    status 列表同款）；响应加性 tenant_id 键（行归属可观测）。"""
    identity = bind_identity(authorization)
    _gate_bulk(identity.tenant_id, "read")
    if status:
        rows = await db_pool.fetch(
            "SELECT id, name, agent_type, capabilities, status, last_heartbeat, created_at, tenant_id "
            "FROM agents WHERE status = $1 AND tenant_id = $2 ORDER BY created_at DESC",
            status,
            identity.tenant_id,
        )
    else:
        rows = await db_pool.fetch(
            "SELECT id, name, agent_type, capabilities, status, last_heartbeat, created_at, tenant_id "
            "FROM agents WHERE tenant_id = $1 ORDER BY created_at DESC",
            identity.tenant_id,
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
    authorization: str | None = Header(None),
):
    """Delete an agent node — admin only.

    G1-⑤c-②：删除走 by-ID 闸门（跨租户 403 带真因零破坏）+ 行租户双键
    （禁跨租户删除，F3b-③b-3 delete_group 同纪律）。"""
    identity = bind_identity(authorization)
    row_tenant = await _node_row_tenant(node_id)
    if row_tenant is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    try:
        _gate(identity.tenant_id, row_tenant, str(node_id), "delete")
    except AgentNodeAccessDenied as exc:
        raise HTTPException(status_code=403, detail=f"agent node access denied: {exc.reason}")
    result = await db_pool.execute(
        "DELETE FROM agents WHERE id = $1 AND tenant_id = $2",
        str(node_id),
        row_tenant,
    )
    if "DELETE 0" in result:
        raise HTTPException(status_code=404, detail="Agent not found")
    # G1-⑤c 注销桥：真源行删除→派生协调行随删（禁幽灵行留在活跃窗）；
    # 桥失败旁路不吃主路径（warn 可见，行删不回滚）。
    await _coord_unregister(str(node_id), row_tenant)
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
