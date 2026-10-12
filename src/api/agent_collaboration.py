"""Agent Collaboration Protocol — registration, messaging, handoff, WebSocket channel."""

import json
import logging
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

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
    return AgentRegisterResponse(
        agent_id=req.agent_id, token=token, status="online", tenant_id=row_tenant
    )


# ── 2. POST /api/agents/message ───────────────────────────────────────


@router.post("/message", response_model=MessageResponse)
async def send_message(req: MessageRequest, authorization: str | None = Header(None)):
    """Send a message between agents; persisted to SQLite and forwarded via WebSocket if online.

    F3b-③c-2②d：消息行归属租户=身份派生（bind_identity 禁自报）；收发双方已注册
    agent 行须同租户（跨租户冒名/跨租户投递=403 带真因，禁静默写入/投递），未注册
    agent_id 保留既有语义（行归调用方租户）。"""
    if req.type not in ("task", "result", "query"):
        raise HTTPException(status_code=400, detail="type must be task | result | query")

    identity = bind_identity(authorization)
    row_tenant = _row_tenant(identity.tenant_id)

    msg_id = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()

    conn = _get_db()
    try:
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


@router.websocket("/ws/agent/{agent_id}")
async def agent_ws(websocket: WebSocket, agent_id: str):
    """Real-time bidirectional channel for an agent."""
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
    conn.close()

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
                conn = _get_db()
                conn.execute(
                    "UPDATE agents SET last_seen = ? WHERE agent_id = ?", (hb_now, agent_id)
                )
                conn.commit()
                conn.close()
            else:
                await websocket.send_json(
                    {"event": "error", "detail": f"Unknown event: {event_type}"}
                )

    except WebSocketDisconnect:
        logger.info("Agent WS disconnected: %s", agent_id)
    except Exception as exc:
        logger.warning("Agent WS error for %s: %s", agent_id, exc)
    finally:
        _ws_connections.pop(agent_id, None)
        offline_now = datetime.now(UTC).isoformat()
        conn = _get_db()
        conn.execute(
            "UPDATE agents SET status = 'offline', last_seen = ? WHERE agent_id = ?",
            (offline_now, agent_id),
        )
        conn.commit()
        conn.close()
