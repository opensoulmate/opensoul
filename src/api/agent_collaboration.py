"""Agent Collaboration Protocol — registration, messaging, handoff, WebSocket channel."""

import asyncio
import hmac
import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from src.nerve.event_bridge import push_event
from src.nest.isolation import IsolationEngine, ResourceType
from src.nest.tenant import tenant_namespace
from src.services.tenant_binding import bind_identity

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "component": "OpenNerve"}


# AGENT_COLLAB_DB env 覆盖（NEST_DB/LEARN_DB 先例）：测试隔离用，缺省生产路径。
DB_PATH = Path(
    os.environ.get("AGENT_COLLAB_DB")
    or Path.home() / "opensoul" / "data" / "agent_collaboration.db"
)

DEFAULT_TENANT = "default"

# 连接期凭据周期重校验节拍（F3b-④b；link_gateway 1c-⑤ 同族语义）：
# 每拍重跑 _agent_token_verdict 单一判定口。
TOKEN_REVALIDATE_INTERVAL_S = 60.0

# In-memory WebSocket connection registry
_ws_connections: dict[str, WebSocket] = {}


# ── Database ───────────────────────────────────────────────────────────


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db():
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agents (
            agent_id   TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            model      TEXT DEFAULT '',
            role       TEXT DEFAULT '',
            endpoint   TEXT DEFAULT '',
            token      TEXT NOT NULL,
            status     TEXT DEFAULT 'online',
            last_seen  TEXT NOT NULL,
            created_at TEXT NOT NULL,
            tenant_id  TEXT NOT NULL DEFAULT 'default'
        );
        CREATE TABLE IF NOT EXISTS agent_capabilities (
            agent_id      TEXT NOT NULL,
            capability    TEXT NOT NULL,
            tenant_id     TEXT NOT NULL DEFAULT 'default',
            PRIMARY KEY (agent_id, capability),
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS agent_messages (
            id         TEXT PRIMARY KEY,
            from_agent TEXT NOT NULL,
            to_agent   TEXT NOT NULL,
            msg_type   TEXT NOT NULL,
            payload    TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            tenant_id  TEXT NOT NULL DEFAULT 'default'
        );
    """)
    # F3b-③c-2②d：老库补列自愈（DDL+ALTER 双轨幂等，F3b 先例）——存量行回填 'default'
    for table in ("agents", "agent_capabilities", "agent_messages"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if cols and "tenant_id" not in cols:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
            )
            logger.info("schema drift healed: %s.tenant_id added", table)
    # 租户索引必须在补列之后建（③c-2① 坑记档延续：老库形态先建索引=no such column）
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_collab_agents_tenant ON agents(tenant_id);
        CREATE INDEX IF NOT EXISTS idx_collab_caps_tenant ON agent_capabilities(tenant_id);
        CREATE INDEX IF NOT EXISTS idx_collab_msgs_tenant ON agent_messages(tenant_id);
    """)
    conn.commit()
    conn.close()


_init_db()


# ── 租户隔离（F3b-③c-2②d）───────────────────────────────────────────


def _row_tenant(value) -> str:
    """行归属租户键：缺失/空白→'default' 兜底（键派生故障绝不吃掉落库，③b-2 旁路纪律）。"""
    return str(value or "").strip() or DEFAULT_TENANT


def _agent_row_tenant(conn: sqlite3.Connection, agent_id: str) -> str | None:
    """agent 行归属租户单一权威（能力行继承/消息归属/跨租户投递判定共用，禁手抄）。
    未注册 agent→None（调用方按各自语义兜底）。"""
    row = conn.execute("SELECT tenant_id FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
    return _row_tenant(row["tenant_id"]) if row else None


class AgentCollabAccessDenied(Exception):
    """跨租户访问被 IsolationEngine 拒绝（API 层映射 403 带真因，禁静默 not_found/假成功）。"""

    def __init__(self, reason: str, verdict: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.verdict = verdict or {}


_isolation_cache = None


def _iso() -> IsolationEngine:
    """隔离引擎（check_access 真执行点；审计落 NestStore）。按 NEST_DB env 值缓存，
    测试 env 切换自动换引擎（enterprise/ai_groups _iso 同款）。"""
    global _isolation_cache
    key = os.environ.get("NEST_DB") or ""
    if _isolation_cache is None or _isolation_cache[0] != key:
        _isolation_cache = (key, IsolationEngine())
    return _isolation_cache[1]


def _gate(
    tenant_id: str,
    row_tenant: str,
    resource_id: str,
    action: str,
    resource_type: ResourceType = ResourceType.AGENT,
) -> None:
    """by-ID 访问闸门：IsolationEngine.check_access 真执行（F3b-③c-2②d）。

    资源 ID=namespaced_id(行归属租户 ns, id)——namespace 前缀是行归属租户，比较基准
    是访问者派生租户；namespace 不匹配=跨租户冒读/冒写，引擎真拒绝（allowed=False）→
    AgentCollabAccessDenied（API 层映射 403），禁静默 not_found/假成功。允许/拒绝都
    进引擎审计日志（audit_access=True）。"""
    iso = _iso()
    verdict = iso.check_access(
        tenant_namespace=tenant_namespace(tenant_id),
        resource_type=resource_type,
        resource_id=iso.namespaced_id(tenant_namespace(row_tenant), resource_id),
        action=action,
    )
    if not verdict.get("allowed"):
        raise AgentCollabAccessDenied(verdict.get("reason", "access denied"), verdict)


def _gate_bulk(
    tenant_id: str,
    action: str,
    resource_type: ResourceType = ResourceType.AGENT,
) -> None:
    """bulk 操作（status 列表/消息落库）审计留痕：执行隔离由 WHERE 租户过滤/闸门承担，
    引擎侧记一次 namespace 自匹配访问（audit_access=True「Log all access attempts」），
    使数据面访问在 /api/nest 隔离审计里可见。"""
    iso = _iso()
    iso.check_access(
        tenant_namespace=tenant_namespace(tenant_id),
        resource_type=resource_type,
        resource_id=iso.namespaced_id(tenant_namespace(tenant_id), "*"),
        action=action,
    )


# ── G1-⑤ MultiAgentCoordinator 注册接线 ─────────────────────────────────
#
# 修前（G0 审计⑤）：MultiAgentCoordinator（src/cortex/multi_agent_coord.py）
# 协调面有 API 零注册方——agent_registry 恒空、/api/brain/agents 恒 []，
# register_agent/heartbeat 两方法全仓零生产调用点（wired-but-not-functional）。
# 本节把真实注册面接到协调面：agent 注册即落协调注册表、WS 连接/心跳事件/
# 看门狗节拍维持活跃（连接=活跃代理）、断开标 offline。
#
# 纪律：①协调面是派生观测面（注册真源=collab agents 行，协调行只作协作视图/
# 冲突检查），桥写失败绝不吃掉注册/连接主路径（旁路纪律）——logger.warning
# 可见禁静默②键=行归属租户单一源（_agent_row_tenant/身份派生），禁自报③
# heartbeat 是 UPDATE-only：未注册 agent_id 绝不 fabricate 协调行。


def _coord(tenant_id: str):
    """协调器构造（call-time import：测试 monkeypatch
    src.database.postgres.db_pool 生效；生产=lifespan 已连接的 db_pool）。"""
    from src.cortex.multi_agent_coord import MultiAgentCoordinator
    from src.database.postgres import db_pool

    return MultiAgentCoordinator(db_pool, tenant_id)


async def _coord_register(tenant_id: str, agent_id: str, agent_type: str) -> None:
    """注册桥（register_agent 方法首个生产消费点）：失败旁路不阻断注册。"""
    try:
        await _coord(tenant_id).register_agent(agent_id, agent_type)
    except Exception as exc:
        logger.warning("coord register bridge failed for %s: %s", agent_id, exc)


async def _coord_heartbeat(
    tenant_id: str,
    agent_id: str,
    status: str = "active",
    current_task=None,
    current_files=None,
) -> None:
    """心跳桥（heartbeat 方法首个生产消费点）：失败旁路不阻断连接主路径。

    G1-⑤b：current_task/current_files 透传（缺省 None=保留现值——看门狗/连接
    keep-alive 节拍不再洗掉已声明工作集，heartbeat 方法 COALESCE 语义）。"""
    try:
        await _coord(tenant_id).heartbeat(
            agent_id,
            status=status,
            current_task=current_task,
            current_files=current_files,
        )
    except Exception as exc:
        logger.warning("coord heartbeat bridge failed for %s: %s", agent_id, exc)


# ── G1-⑤b check_file_conflict·send_message·get_messages 消费面 ─────────
#
# 修前（G1-⑤ 诚实边界留档）：check_file_conflict/send_message/get_messages 三
# 方法全仓零生产调用（wired-but-not-functional）。本节把三方法接进 collab WS
# 协议真实流程：heartbeat 事件声明工作集（数据真源）→ check_conflicts 冲突
# 检查（check_file_conflict 真消费）→ 冲突通知落协调收件箱（send_message 真
# 消费）→ inbox 拉取/连接接取消息（get_messages 真消费，拉取即送达标 read）。
#
# 纪律：①旁路纪律（桥失败 logger.warning 可见，绝不吃掉 WS 主循环）②冲突
# 视图=同租户活跃代理（check_file_conflict 自带租户过滤），零跨租户泄露③
# 检查/拉取故障如实 error 帧禁谎报 ok（C2）④投递=接取/拉取面（inbox 轮询/
# 重连接收），无实时推送（诚实边界，见 roadmap 记档）。


async def _coord_check_conflicts(tenant_id, agent_id, target_files):
    """check_file_conflict 方法首个生产消费点：返回 (conflicts, error)，
    故障如实带 error 禁当空冲突谎报 ok。"""
    try:
        conflicts = await _coord(tenant_id).check_file_conflict(agent_id, target_files)
        return conflicts, None
    except Exception as exc:
        logger.warning("coord conflict check failed for %s: %s", agent_id, exc)
        return [], str(exc)


async def _coord_notify_conflict(tenant_id, from_agent, conflict):
    """send_message 方法首个生产消费点：冲突通知落协调收件箱（收方经 inbox
    拉取/连接接取消费）。失败旁路可见，绝不吃掉冲突检查主路径。"""
    try:
        await _coord(tenant_id).send_message(
            from_agent,
            conflict.get("agent_id", ""),
            "conflict",
            json.dumps(
                {
                    "files": conflict.get("conflicting_files", []),
                    "their_task": conflict.get("their_task", ""),
                },
                ensure_ascii=False,
            ),
        )
    except Exception as exc:
        logger.warning("coord conflict notice failed for %s: %s", from_agent, exc)


async def _coord_fetch_inbox(tenant_id, agent_id, unread_only):
    """get_messages 方法首个生产消费点：返回 (messages, error)。"""
    try:
        messages = await _coord(tenant_id).get_messages(agent_id, unread_only=unread_only)
        return messages, None
    except Exception as exc:
        logger.warning("coord inbox fetch failed for %s: %s", agent_id, exc)
        return [], str(exc)


async def _coord_mark_read(tenant_id, agent_id):
    """收件箱送达标记（拉取即送达）。失败旁路可见禁静默。"""
    try:
        await _coord(tenant_id).mark_messages_read(agent_id)
    except Exception as exc:
        logger.warning("coord inbox mark-read failed for %s: %s", agent_id, exc)


async def _coord_flush_inbox(tenant_id, agent_id, websocket):
    """连接接取：离线期积压协调消息逐条 coord_message 帧投递+标 read（送达）。
    旁路纪律：失败 logger.warning 可见，绝不吃掉连接主路径。"""
    try:
        messages = await _coord(tenant_id).get_messages(agent_id, unread_only=True)
        for m in messages:
            await websocket.send_json(
                {
                    "event": "coord_message",
                    "from_agent": m.get("from_agent", ""),
                    "message_type": m.get("message_type", ""),
                    "content": m.get("content", ""),
                    "created_at": m.get("created_at", ""),
                }
            )
        if messages:
            await _coord(tenant_id).mark_messages_read(agent_id)
    except Exception as exc:
        logger.warning("coord inbox flush failed for %s: %s", agent_id, exc)


# ── Pydantic models ────────────────────────────────────────────────────


class AgentRegisterRequest(BaseModel):
    agent_id: str
    name: str
    capabilities: list[str] = Field(default_factory=list)
    model: str = ""
    role: str = ""
    endpoint: str = ""  # WebSocket address


class AgentRegisterResponse(BaseModel):
    agent_id: str
    token: str
    status: str
    tenant_id: str = "default"  # F3b-③c-2②d 加性契约：行归属可观测


class MessageRequest(BaseModel):
    from_agent: str
    to_agent: str
    type: str  # task | result | query
    payload: dict = Field(default_factory=dict)


class MessageResponse(BaseModel):
    message_id: str
    status: str


class HandoffRequest(BaseModel):
    source_agent: str
    target_agent: str
    full_context: str
    goal: str


class HandoffResponse(BaseModel):
    filtered_context: str
    removed_sections: list[str]


class AgentStatus(BaseModel):
    agent_id: str
    name: str
    model: str
    role: str
    capabilities: list[str]
    status: str
    last_seen: str
    tenant_id: str = "default"  # F3b-③c-2②d 加性契约：行归属可观测


# ── 1. POST /api/agents/register ──────────────────────────────────────


@router.post("/register", response_model=AgentRegisterResponse)
async def register_agent(req: AgentRegisterRequest, authorization: str | None = Header(None)):
    """Register an agent and receive a token for subsequent communication.

    F3b-③c-2②d：agent 行归属租户=身份派生（bind_identity 禁自报，匿名='default' 契约
    不变）；同 agent_id 已归他租户=跨租户劫持，闸门真拒绝 403 带真因（禁静默改写
    他租户 agent 行/token）。"""
    identity = bind_identity(authorization)
    row_tenant = _row_tenant(identity.tenant_id)
    now = datetime.now(UTC).isoformat()
    token = uuid.uuid4().hex

    conn = _get_db()
    try:
        existing = conn.execute(
            "SELECT tenant_id FROM agents WHERE agent_id = ?", (req.agent_id,)
        ).fetchone()
        if existing is not None:
            # 跨租户重注册=劫持他租户 agent 身份/token，闸门真拒绝（行不改写）
            try:
                _gate(row_tenant, _row_tenant(existing["tenant_id"]), req.agent_id, "write")
            except AgentCollabAccessDenied as e:
                raise HTTPException(
                    status_code=403, detail=f"cross-tenant access denied: {e.reason}"
                )
        # Upsert agent record（tenant_id 只进 INSERT=粘性，冲突更新不洗行键）
        conn.execute(
            "INSERT INTO agents (agent_id, name, model, role, endpoint, token, status, last_seen, created_at, tenant_id) "
            "VALUES (?, ?, ?, ?, ?, ?, 'online', ?, ?, ?) "
            "ON CONFLICT(agent_id) DO UPDATE SET "
            "  name=excluded.name, model=excluded.model, role=excluded.role, "
            "  endpoint=excluded.endpoint, token=excluded.token, status='online', last_seen=excluded.last_seen",
            (
                req.agent_id,
                req.name,
                req.model,
                req.role,
                req.endpoint,
                token,
                now,
                now,
                row_tenant,
            ),
        )
        # Refresh capabilities（行键=agent 行继承）
        conn.execute(
            "DELETE FROM agent_capabilities WHERE agent_id = ? AND tenant_id = ?",
            (req.agent_id, row_tenant),
        )
        for cap in req.capabilities:
            conn.execute(
                "INSERT OR IGNORE INTO agent_capabilities (agent_id, capability, tenant_id) VALUES (?, ?, ?)",
                (req.agent_id, cap, row_tenant),
            )
        conn.commit()
    finally:
        conn.close()

    logger.info("Agent registered: %s (%s)", req.agent_id, req.name)
    # G1-⑤ 注册桥：真实注册面→协调注册表（行归属租户单一源；agent_type=role
    # 优先回落 model/openmate）。失败旁路不阻断注册（上方纪律①）。
    await _coord_register(
        row_tenant,
        req.agent_id,
        (req.role or "").strip() or (req.model or "").strip() or "openmate",
    )
    return AgentRegisterResponse(
        agent_id=req.agent_id, token=token, status="online", tenant_id=row_tenant
    )


# ── 2. POST /api/agents/message ───────────────────────────────────────


@router.post("/message", response_model=MessageResponse)
async def send_message(
    req: MessageRequest,
    authorization: str | None = Header(None),
    x_agent_token: str | None = Header(None),
):
    """Send a message between agents; persisted to SQLite and forwarded via WebSocket if online.

    F3b-③c-2②d：消息行归属租户=身份派生（bind_identity 禁自报）；收发双方已注册
    agent 行须同租户（跨租户冒名/跨租户投递=403 带真因，禁静默写入/投递），未注册
    agent_id 保留既有语义（行归调用方租户）。
    F3b-⑤：from_agent 身份轴收口——from_agent 是身份 claim，claim 注册身份必须持
    register 签发 token（X-Agent-Token 头单一取参源——HTTP 客户端都能设头无需 query
    双源，且 query 会把 token 带进访问日志；与 Authorization 的 JWT 租户轴刻意分开
    两条身份轴互不顶替）。缺/错 token=403 零副作用（不落库不投递），修前 from_agent
    自报任意冒名落库+以其名义投递。未注册 from_agent 保留既有语义（无注册身份可顶）。
    """
    if req.type not in ("task", "result", "query"):
        raise HTTPException(status_code=400, detail="type must be task | result | query")

    identity = bind_identity(authorization)
    row_tenant = _row_tenant(identity.tenant_id)

    msg_id = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()

    conn = _get_db()
    try:
        # F3b-⑤：身份证明先于一切授权检查（先证身份再谈租户）
        verdict = _agent_token_verdict(conn, req.from_agent, (x_agent_token or "").strip())
        if verdict is False:
            logger.warning(
                "Agent message rejected: invalid/missing token for registered agent %s",
                req.from_agent,
            )
            raise HTTPException(
                status_code=403,
                detail=f"invalid agent token for registered agent {req.from_agent}",
            )
        for party in (req.from_agent, req.to_agent):
            party_tenant = _agent_row_tenant(conn, party)
            if party_tenant is None:
                continue  # 未注册 agent_id：保留既有语义（无行可闸）
            try:
                _gate(row_tenant, party_tenant, party, "write")
            except AgentCollabAccessDenied as e:
                raise HTTPException(
                    status_code=403, detail=f"cross-tenant access denied: {e.reason}"
                )
        _gate_bulk(row_tenant, "write", ResourceType.AGENT_MESSAGE)
        conn.execute(
            "INSERT INTO agent_messages (id, from_agent, to_agent, msg_type, payload, created_at, tenant_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                msg_id,
                req.from_agent,
                req.to_agent,
                req.type,
                json.dumps(req.payload),
                now,
                row_tenant,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Attempt real-time delivery via WebSocket
    ws = _ws_connections.get(req.to_agent)
    if ws:
        try:
            await ws.send_json(
                {
                    "event": "message",
                    "message_id": msg_id,
                    "from_agent": req.from_agent,
                    "type": req.type,
                    "payload": req.payload,
                    "created_at": now,
                }
            )
        except Exception:
            logger.warning("Failed to deliver WS message to %s", req.to_agent)

    return MessageResponse(message_id=msg_id, status="delivered")


# ── 3. POST /api/agents/handoff ───────────────────────────────────────


@router.post("/handoff", response_model=HandoffResponse)
async def handoff_context(req: HandoffRequest):
    """Filter full_context down to what's relevant for the target agent's goal."""
    # Goal-driven keyword extraction for filtering
    goal_words = set(_tokenize(req.goal.lower()))
    sections = _split_sections(req.full_context)

    kept: list[str] = []
    removed: list[str] = []

    for title, body in sections:
        section_text = (title + " " + body).lower()
        section_words = set(_tokenize(section_text))
        overlap = len(goal_words & section_words)
        # Keep section if it shares >= 1 keyword with goal, or if it's short enough to keep
        if overlap > 0 or len(body.strip()) < 40:
            kept.append(f"## {title}\n{body}" if title else body)
        else:
            removed.append(title or body[:60])

    # If everything was removed, fall back to first 30% of context as a safety net
    if not kept:
        cutoff = max(1, len(sections) // 3)
        for title, body in sections[:cutoff]:
            kept.append(f"## {title}\n{body}" if title else body)
        removed = removed[: len(sections) - cutoff]

    return HandoffResponse(
        filtered_context="\n\n".join(kept),
        removed_sections=removed,
    )


def _tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation splitter."""
    import re

    return [w for w in re.split(r"[^\w]+", text) if len(w) > 2]


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown-style text into (title, body) pairs."""
    import re

    parts = re.split(r"^(#{1,4}\s+.+)$", text, flags=re.MULTILINE)
    sections: list[tuple[str, str]] = []
    i = 0
    # Leading text before any heading
    if parts and not parts[0].startswith("#"):
        sections.append(("", parts[0]))
        i = 1
    while i < len(parts):
        title = parts[i].strip("# ").strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        sections.append((title, body))
        i += 2
    return sections


# ── 4. GET /api/agents/status ─────────────────────────────────────────


@router.get("/status", response_model=list[AgentStatus])
async def get_agent_status(authorization: str | None = Header(None)):
    """Return all registered agents with their current status.

    F3b-③c-2②d：恒按请求者租户过滤（租户只见自己的 agent 行，修前全量列表=跨租户
    身份泄露），bulk 访问走隔离引擎审计留痕。"""
    identity = bind_identity(authorization)
    row_tenant = _row_tenant(identity.tenant_id)
    _gate_bulk(row_tenant, "list")
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM agents WHERE tenant_id = ? ORDER BY last_seen DESC", (row_tenant,)
        ).fetchall()
        result: list[AgentStatus] = []
        for row in rows:
            caps = conn.execute(
                "SELECT capability FROM agent_capabilities WHERE agent_id = ?",
                (row["agent_id"],),
            ).fetchall()
            result.append(
                AgentStatus(
                    agent_id=row["agent_id"],
                    name=row["name"],
                    model=row["model"],
                    role=row["role"],
                    capabilities=[c["capability"] for c in caps],
                    status=row["status"],
                    last_seen=row["last_seen"],
                    tenant_id=_row_tenant(row["tenant_id"]),
                )
            )
        return result
    finally:
        conn.close()


# ── 5. WebSocket /ws/agent/{agent_id} ─────────────────────────────────


def _ws_client_host(websocket: WebSocket) -> str:
    """审计事件 client 来源（terminal_ws/ws_chat 同款判法）。"""
    return websocket.client.host if websocket.client else ""


def _audit(event_type: str, summary: str, detail: dict, tenant_id: str | None = None):
    """DEC-⑱-④ WS 连接审计：collab agent WS 连接生命周期事件落 timeline/活动流
    （organ=immune 🛡 安全归口，与 terminal_ws_* / ws_chat_* / link_* /
    intrusion_detected / permission_denied 同流可见）。

    闭 DEC-⑱-③ 留档件「collab 面 ⑱-④」——agent 协作通道谁被拒/谁连上/会话
    何时以何因结束必须可审计回放（顶号拒绝、凭据撤销关闭此前只打 logger）。

    **同步口（push_event：活动流缓冲+持久 timeline 双落，nerve 总线既有
    fire-and-forget）而非 await 型 emit——取消免疫是硬要求**：会话收尾路径的宿主
    任务可被取消（DEC-⑱ 教训：await 型收尾把审计连同收尾一起吞掉=静默丢审计），
    审计真源不赌 await 活到落盘。

    纪律：①detail/summary 绝不携带凭据材料（token 只字不进事件，has_token 只记
    在场与否）②审计失败绝不影响连接主路径（旁路纪律，失败 logger.warning 可见
    禁静默丢审计）。
    """
    try:
        push_event(
            {
                "organ": "immune",
                "emoji": "🛡",
                "type": event_type,
                "summary": summary,
                "detail": detail,
                "timestamp": time.time(),
            },
            tenant_id=tenant_id,
        )
    except Exception as exc:
        logger.warning("agent WS audit event dropped (%s): %s", event_type, exc)


def _ws_presented_token(websocket: WebSocket) -> str:
    """WS 身份凭证取参双源（F3b-④）：`?token=` query（浏览器 WS 无法自设头，主口）优先，
    Authorization: Bearer 头（非浏览器客户端）兜底。"""
    token = (websocket.query_params.get("token") or "").strip()
    if token:
        return token
    auth = websocket.headers.get("authorization") or ""
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return ""


def _agent_token_verdict(conn: sqlite3.Connection, agent_id: str, provided: str) -> bool | None:
    """agent 身份证明判定（单一判定口，F3b-④ WS 面 + F3b-⑤ HTTP /message 面共用）。

    - agent 行存在 → 该身份已注册，claim 它必须持 register 签发的 agents.token
      （修前 token 签发后全仓零消费=死凭证）：缺失/不匹配 = False——修前任意人可
      顶号（WS：改该行 status/last_seen、以其名义落消息、劫持 _ws_connections 投递；
      HTTP /message：from_agent 自报冒名落库+以其名义投递）。
    - agent 行不存在 → None（未注册 agent_id 保留既有语义：无注册身份可顶，行归属
      'default' 兜底与 HTTP /message 未注册语义同轴）。
    """
    row = conn.execute("SELECT token FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
    if row is None:
        return None
    expected = str(row["token"] or "")
    return bool(provided) and hmac.compare_digest(provided, expected)


async def _agent_ws_token_watchdog(
    websocket: WebSocket,
    agent_id: str,
    token: str,
    tenant_id: str = DEFAULT_TENANT,
):
    """WS 连接期凭据周期重校验（F3b-④b；link_gateway 1c-⑤ 同族语义）。

    每 TOKEN_REVALIDATE_INTERVAL_S 秒重跑 _agent_token_verdict（单一判定口，
    与握手/HTTP /message 同源禁第二套判法）：
    - verdict False（claim 注册身份但凭据缺失/不匹配——token 轮换、重注册换
      token、连接后才注册的顶号重开面）→ close 1008（与握手拒绝同语义），禁
      持失效凭据继续收发/顶号；看门狗自行清理 _ws_connections（仅当映射的连接
      是自己——禁误删同名新连接的投递注册），finally 幂等兜底。
    - verdict True（凭据仍有效）/None（未注册语义，②d 契约）→ 继续服务；
      行删除=降级未注册语义（同 ②d 轴），重注册后持旧凭据连接随 False 关闭。
    - 判定口瞬断（DB 故障）→ 记录可见后继续，故障≠失效凭据禁误杀。
    诚实边界：失效与关闭之间 <= TOKEN_REVALIDATE_INTERVAL_S 秒有界滞后（周期
    重校验语义固有）；连接期内凭据字符串不变（重校验的是同一凭据当前效力）。
    """
    while True:
        await asyncio.sleep(TOKEN_REVALIDATE_INTERVAL_S)
        # G1-⑤ 协调心跳保活（连接=活跃代理）：节拍与凭据重校验同一循环，
        # 失败旁路绝不阻断重校验主路径。
        await _coord_heartbeat(tenant_id, agent_id, status="active")
        try:
            conn = _get_db()
            try:
                verdict = _agent_token_verdict(conn, agent_id, token)
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("agent WS token revalidation probe failed for %s: %s", agent_id, exc)
            continue
        if verdict is False:
            logger.warning(
                "Agent WS closed by revalidation: invalid/missing token for registered agent %s",
                agent_id,
            )
            try:
                await websocket.close(code=1008, reason="invalid agent token")
            except Exception:
                pass
            if _ws_connections.get(agent_id) is websocket:
                del _ws_connections[agent_id]
            return


@router.websocket("/ws/agent/{agent_id}")
async def agent_ws(websocket: WebSocket, agent_id: str):
    """Real-time bidirectional channel for an agent.

    F3b-④：agent 身份轴收口——claim 注册身份必须持 register 签发 token
    （_ws_presented_token 双源取参 → _agent_token_verdict 单一判定），未注册
    agent_id 保留既有语义。

    G1-⑤b 协议事件登记（协调面）：heartbeat 可携 current_task/current_files
    （声明工作集，缺省保留现值）、check_conflicts {files} → conflict_check 帧
    （check_file_conflict 真消费+冲突通知落协调收件箱）、inbox {unread_only}
    → inbox 帧（get_messages 真消费，拉取即送达）；新帧类型 coord_message
    （连接接取积压协调消息），既有客户端按未知 type 忽略即兼容。
    """
    conn = _get_db()
    presented = _ws_presented_token(websocket)
    try:
        verdict = _agent_token_verdict(conn, agent_id, presented)
    finally:
        conn.close()
    if verdict is False:
        logger.warning("Agent WS rejected: invalid/missing token for registered agent %s", agent_id)
        # DEC-⑱-④ 连接审计：顶号/冒名拒绝是安全事件本体必留痕（cause 单列真因）。
        # 同步口置于 close 之前=取消免疫；被拒无派生身份 tenant='default' 兜底
        # （③b-2 旁路纪律），agent_id=被 claim 的目标身份（非凭据材料）。
        _audit(
            "collab_ws_rejected",
            "🛡 Agent WS 握手拒绝（claim 注册身份凭证无效）",
            {
                "face": "collab_ws",
                "agent_id": agent_id,
                "client": _ws_client_host(websocket),
                "has_token": bool(presented),
                "cause": "missing_token" if not presented else "invalid_token",
            },
        )
        # 握手拒绝（accept 前 close）：顶号者拿不到通道，不改行、不进 _ws_connections
        await websocket.close(code=1008, reason="invalid agent token")
        return
    await websocket.accept()
    _ws_connections[agent_id] = websocket
    logger.info("Agent WS connected: %s", agent_id)

    # Mark agent online
    now = datetime.now(UTC).isoformat()
    conn = _get_db()
    conn.execute(
        "UPDATE agents SET status = 'online', last_seen = ? WHERE agent_id = ?", (now, agent_id)
    )
    conn.commit()
    # DEC-⑱-④ 连接审计：connected=会话真建立（行已标 online）。租户键=agent 行
    # 租户单一源（未注册 agent 行缺失→'default' 兜底，②d/③b-2 既有语义）；
    # session_started 供 closed 事件算时长。
    row_tenant = _agent_row_tenant(conn, agent_id) or DEFAULT_TENANT
    conn.close()
    session_started = time.time()
    _audit(
        "collab_ws_connected",
        f"🤝 Agent WS 连接建立: agent={agent_id}",
        {
            "face": "collab_ws",
            "agent_id": agent_id,
            "client": _ws_client_host(websocket),
        },
        tenant_id=row_tenant,
    )
    # G1-⑤ 连接=活跃代理：协调心跳落行（租户键=agent 行单一源；未注册 agent
    # heartbeat UPDATE-only 绝不 fabricate 协调行）。
    await _coord_heartbeat(row_tenant, agent_id, status="active")
    # G1-⑤b 收件箱接取：连接即投递离线期积压的协调消息（get_messages 真消费），
    # 逐条 coord_message 帧+标 read（送达语义）；失败旁路绝不吃掉连接主路径。
    await _coord_flush_inbox(row_tenant, agent_id, websocket)

    # 连接期凭据周期重校验（F3b-④b）：所有连接起看门狗——claim 注册身份的
    # 凭据失效（轮换/重注册/连接后才注册）=close 1008；凭据有效/未注册继续服务。
    # G1-⑤：看门狗节拍兼协调心跳（连接=活跃代理，60s 节拍保活进 5min 活跃窗）。
    watchdog = asyncio.create_task(
        _agent_ws_token_watchdog(websocket, agent_id, _ws_presented_token(websocket), row_tenant)
    )

    # DEC-⑱-④ 会话结束归因缺省（正常收口=客户端断开；异常收口在 except 改写）。
    end_reason = "client_disconnect"
    try:
        while True:
            data = await websocket.receive_json()
            event_type = data.get("event", "")

            if event_type == "ping":
                await websocket.send_json({"event": "pong"})
            elif event_type == "message":
                # Forward message through the message endpoint logic
                target = data.get("to_agent")
                if target:
                    msg_id = uuid.uuid4().hex
                    msg_now = datetime.now(UTC).isoformat()
                    conn = _get_db()
                    denied = None
                    try:
                        # F3b-③c-2②d：消息行归属=发方 agent 行租户（未注册→'default'
                        # 兜底，键派生故障绝不吃掉落库）；跨租户投递与 HTTP /message
                        # 同一条隔离规则——闸门真拒绝=不落库不投递+error 回执可见。
                        sender_tenant = _agent_row_tenant(conn, agent_id) or DEFAULT_TENANT
                        target_tenant = _agent_row_tenant(conn, target)
                        if target_tenant is not None and target_tenant != sender_tenant:
                            try:
                                _gate(sender_tenant, target_tenant, target, "write")
                            except AgentCollabAccessDenied as e:
                                denied = e.reason
                        if denied is None:
                            _gate_bulk(sender_tenant, "write", ResourceType.AGENT_MESSAGE)
                            conn.execute(
                                "INSERT INTO agent_messages (id, from_agent, to_agent, msg_type, payload, created_at, tenant_id) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (
                                    msg_id,
                                    agent_id,
                                    target,
                                    data.get("type", "query"),
                                    json.dumps(data.get("payload", {})),
                                    msg_now,
                                    sender_tenant,
                                ),
                            )
                            conn.commit()
                    finally:
                        conn.close()
                    if denied is not None:
                        await websocket.send_json(
                            {
                                "event": "error",
                                "detail": f"cross-tenant delivery denied: {denied}",
                            }
                        )
                    else:
                        # Deliver to target if connected
                        target_ws = _ws_connections.get(target)
                        if target_ws:
                            try:
                                await target_ws.send_json(
                                    {
                                        "event": "message",
                                        "message_id": msg_id,
                                        "from_agent": agent_id,
                                        "type": data.get("type", "query"),
                                        "payload": data.get("payload", {}),
                                        "created_at": msg_now,
                                    }
                                )
                            except Exception as exc:
                                logging.getLogger(__name__).debug("probe skipped: %s", exc)
            elif event_type == "heartbeat":
                hb_now = datetime.now(UTC).isoformat()
                # G1-⑤b 声明面：heartbeat 事件可携 current_task/current_files
                # （agent 声明当前工作集=check_file_conflict 数据真源）。缺省=
                # 不改声明（保留现值）；显式值非法如实 error 帧禁静默吞声明，
                # liveness 照常（心跳语义与声明语义分开）。
                declared_task = None
                declared_files = None
                invalid = None
                payload_task = data.get("current_task")
                payload_files = data.get("current_files")
                if payload_task is not None:
                    if isinstance(payload_task, str):
                        declared_task = payload_task
                    else:
                        invalid = "current_task must be a string"
                if invalid is None and payload_files is not None:
                    if isinstance(payload_files, list) and all(
                        isinstance(f, str) for f in payload_files
                    ):
                        declared_files = list(payload_files)
                    else:
                        invalid = "current_files must be a list of strings"
                if invalid is not None:
                    await websocket.send_json(
                        {"event": "error", "detail": "invalid heartbeat payload: " + invalid}
                    )
                conn = _get_db()
                hb_tenant = _agent_row_tenant(conn, agent_id) or DEFAULT_TENANT
                conn.execute(
                    "UPDATE agents SET last_seen = ? WHERE agent_id = ?", (hb_now, agent_id)
                )
                conn.commit()
                conn.close()
                # G1-⑤ 协议心跳事件真消费：同步刷协调面活跃窗（行归属租户单一源）。
                await _coord_heartbeat(
                    hb_tenant,
                    agent_id,
                    status="active",
                    current_task=declared_task,
                    current_files=declared_files,
                )
            elif event_type == "check_conflicts":
                # G1-⑤b check_file_conflict 真消费：agent 触碰文件前主动查询同
                # 租户活跃代理的工作集重叠（冲突视图自带租户过滤零跨租户泄露）。
                files = data.get("files")
                if (
                    not isinstance(files, list)
                    or not files
                    or not all(isinstance(f, str) for f in files)
                ):
                    await websocket.send_json(
                        {
                            "event": "error",
                            "detail": "check_conflicts requires files: non-empty list of strings",
                        }
                    )
                    continue
                conflicts, check_error = await _coord_check_conflicts(row_tenant, agent_id, files)
                if check_error is not None:
                    # C2 禁谎报 ok：检查故障如实 error 帧，绝不当空冲突
                    await websocket.send_json(
                        {"event": "error", "detail": "coord conflict check failed: " + check_error}
                    )
                    continue
                # send_message 真消费：冲突通知落对方协调收件箱（inbox 面接取）。
                # 通知先于 ack 落库：客户端收 ack 即断开的取消面绝不吃掉通知
                # （DEC-⑱「取消兑现于 await」家族，实测 ack 后立刻断开=通知被吞）。
                for conflict in conflicts:
                    await _coord_notify_conflict(row_tenant, agent_id, conflict)
                await websocket.send_json(
                    {"event": "conflict_check", "files": files, "conflicts": conflicts}
                )
            elif event_type == "inbox":
                # G1-⑤b get_messages 真消费：协调收件箱拉取（unread_only 缺省
                # True；显式 False=含已读历史）。拉取即送达（mark read）。
                unread_only = data.get("unread_only", True)
                if not isinstance(unread_only, bool):
                    await websocket.send_json(
                        {"event": "error", "detail": "inbox unread_only must be a boolean"}
                    )
                    continue
                messages, inbox_error = await _coord_fetch_inbox(row_tenant, agent_id, unread_only)
                if inbox_error is not None:
                    await websocket.send_json(
                        {"event": "error", "detail": "coord inbox fetch failed: " + inbox_error}
                    )
                    continue
                await websocket.send_json({"event": "inbox", "messages": messages})
                await _coord_mark_read(row_tenant, agent_id)
            else:
                await websocket.send_json(
                    {"event": "error", "detail": f"Unknown event: {event_type}"}
                )

    except WebSocketDisconnect:
        logger.info("Agent WS disconnected: %s", agent_id)
    except Exception as exc:
        end_reason = "error"
        logger.warning("Agent WS error for %s: %s", agent_id, exc)
    finally:
        # DEC-⑱-④ 会话结束归因（同步，先于 task.cancel()——cancel 后 done/cancelled
        # 形态失真）：看门狗正常返回的唯一路径=凭据失效撤销（exception() 非空=看门狗
        # 自身崩溃，如实不算撤销），其余按收口路径归因 error / client_disconnect。
        revoked = watchdog.done() and not watchdog.cancelled() and watchdog.exception() is None
        if revoked:
            end_reason = "credential_revoked"
        # DEC-⑱-④ 连接审计：closed=connected 的对偶（谁连过协作通道、连了多久、
        # 以何因结束可完整回放）。同步口置于任何 await 之前=取消免疫（DEC-⑱ 教训）。
        _audit(
            "collab_ws_closed",
            f"🤝 Agent WS 会话结束（{end_reason}）: agent={agent_id}",
            {
                "face": "collab_ws",
                "agent_id": agent_id,
                "client": _ws_client_host(websocket),
                "reason": end_reason,
                "duration_s": round(time.time() - session_started, 1),
            },
            tenant_id=row_tenant,
        )
        watchdog.cancel()  # 连接结束收看门狗，禁悬挂任务（清理幂等兜底在下方 pop）
        # 清理只摘自己的注册（F3b-④b）：同名后连接已在 _ws_connections 时，
        # 先连接离场禁误删后连接的投递注册。
        if _ws_connections.get(agent_id) is websocket:
            del _ws_connections[agent_id]
        offline_now = datetime.now(UTC).isoformat()
        conn = _get_db()
        conn.execute(
            "UPDATE agents SET status = 'offline', last_seen = ? WHERE agent_id = ?",
            (offline_now, agent_id),
        )
        conn.commit()
        conn.close()
        # G1-⑤ 断开标 offline（协调面如实反映"最近见过但已离线"）——**同步收尾**
        # 置于任何 await 之前（DEC-⑱ 纪律「取消兑现于 finally 首个 await」：实测
        # await 型 offline 写被 teardown 取消连同收尾一起吞掉），sqlite3 直写同一
        # 行同一 SQL（multi_agent_coord._HEARTBEAT_SQL 单一源）。失败旁路只 log。
        try:
            from src.cortex.multi_agent_coord import db_url_for_pool, heartbeat_sync
            from src.database.postgres import db_pool as _coord_pool

            heartbeat_sync(db_url_for_pool(_coord_pool), row_tenant, agent_id, status="offline")
        except Exception as exc:
            logger.warning("coord offline bridge failed for %s: %s", agent_id, exc)
