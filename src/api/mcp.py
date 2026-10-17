"""OpenMCP API — Model Context Protocol server management.

Endpoints for registering, connecting, and managing MCP servers and their tools.
"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from src.mcp.server_registry import McpServerRegistry
from src.mcp.session_grants import SessionToolGate, default_gate

router = APIRouter()

# ── Singleton ──────────────────────────────────────────────
registry = McpServerRegistry()


# ── Request Schemas ────────────────────────────────────────


class ServerCreateRequest(BaseModel):
    name: str
    url: str
    description: str = ""
    transport: str = "stdio"
    config: dict = {}
    tools: list[dict] = []


class ServerUpdateRequest(BaseModel):
    name: str | None = None
    url: str | None = None
    description: str | None = None
    transport: str | None = None
    enabled: bool | None = None
    config: dict | None = None
    tools: list[dict] | None = None


class GrantRequest(BaseModel):
    server_id: str
    tool_name: str = "*"
    granted_by: str = ""


class ConsumerCreateRequest(BaseModel):
    consumer_id: str
    scopes: list[str] = ["*"]


class ToolCheckRequest(BaseModel):
    server_id: str
    tool_name: str


# ── Health ─────────────────────────────────────────────────


@router.get("/health")
async def health():
    return {"status": "ok", "component": "mcp"}


# ── Server CRUD ────────────────────────────────────────────


@router.get("/servers")
async def list_servers(enabled_only: bool = Query(default=False)):
    """List all registered MCP servers."""
    servers = registry.list_servers(enabled_only=enabled_only)
    return {"servers": servers, "total": len(servers)}


@router.get("/servers/{server_id}")
async def get_server(server_id: str):
    """Get details of a specific MCP server."""
    srv = registry.get_server(server_id)
    if not srv:
        raise HTTPException(404, "MCP server not found")
    return srv.to_dict()


@router.post("/servers")
async def add_server(req: ServerCreateRequest):
    """Register a new MCP server."""
    srv = registry.add_server(
        name=req.name,
        url=req.url,
        description=req.description,
        transport=req.transport,
        config=req.config,
        tools=req.tools,
    )
    return srv.to_dict()


@router.patch("/servers/{server_id}")
async def update_server(server_id: str, req: ServerUpdateRequest):
    """Update an MCP server's configuration."""
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    srv = registry.update_server(server_id, **updates)
    if not srv:
        raise HTTPException(404, "MCP server not found")
    return srv.to_dict()


@router.delete("/servers/{server_id}")
async def delete_server(server_id: str):
    """Remove an MCP server."""
    if not registry.delete_server(server_id):
        raise HTTPException(404, "MCP server not found")
    return {"deleted": True, "id": server_id}


# ── Connection ─────────────────────────────────────────────


@router.post("/servers/{server_id}/connect")
async def connect_server(server_id: str):
    """Connect to an MCP server and discover its tools."""
    result = registry.connect(server_id)
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "Connection failed"))
    return result


@router.post("/servers/{server_id}/disconnect")
async def disconnect_server(server_id: str):
    """Disconnect from an MCP server."""
    result = registry.disconnect(server_id)
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "Disconnect failed"))
    return result


# ── Tools ──────────────────────────────────────────────────


@router.get("/tools")
async def list_tools(server_id: str | None = Query(default=None)):
    """List all tools from enabled MCP servers."""
    tools = registry.list_tools(server_id=server_id)
    return {"tools": tools, "total": len(tools)}


# ── Stats ──────────────────────────────────────────────────


@router.get("/stats")
async def get_stats():
    """Get MCP registry statistics."""
    stats = registry.get_stats()
    stats.update(default_gate().stats())
    return stats


# ── 会话级工具隔离 + 发布侧auth + 消费侧白名单（P1） ─────────
# SUMMARY.md §五 mcp行：参照 Composio toolkits auth + swarms MCPDeployer 发布侧鉴权
# + ODR MCPConfig 按agent白名单。/servers CRUD仍是管理面（同既有约定）；
# 工具消费面（本节）必须持consumer token——发布侧auth。

gate: SessionToolGate = default_gate()


def _auth_consumer(token: str | None, scope: str | None = None) -> dict:
    """发布侧auth：token缺失/无效/已吊销 → 401；scope不含 → 403（fail-closed）。

    scope=None 表示只验token不做scope约束（列表类端点按server逐项过滤scope）。
    """
    if not token:
        raise HTTPException(401, "missing X-MCP-Token header")
    consumer = gate.authenticate(token)
    if consumer is None:
        raise HTTPException(401, "invalid or revoked consumer token")
    if scope is not None and not SessionToolGate.consumer_may_use(consumer, scope):
        raise HTTPException(403, f"consumer scope does not include '{scope}'")
    return consumer


@router.post("/consumers")
async def issue_consumer(req: ConsumerCreateRequest):
    """签发consumer token（发布侧auth凭证）。明文只返回这一次。"""
    try:
        token, record = gate.issue_consumer(req.consumer_id, req.scopes)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"token": token, **record}


@router.get("/consumers")
async def list_consumers():
    """列出consumer（绝不回显token/token_hash）。"""
    consumers = gate.list_consumers()
    return {"consumers": consumers, "total": len(consumers)}


@router.delete("/consumers/{consumer_id}")
async def revoke_consumer(consumer_id: str):
    """吊销consumer token（已签发token立即失效）。"""
    if not gate.revoke_consumer(consumer_id):
        raise HTTPException(404, "consumer not found or already revoked")
    return {"revoked": True, "consumer_id": consumer_id}


@router.post("/sessions/{session_id}/grants")
async def grant_tool(session_id: str, req: GrantRequest):
    """消费侧白名单：显式授权session可用的server/tool（tool_name='*' = 整个server）。"""
    try:
        return gate.grant(session_id, req.server_id, req.tool_name, req.granted_by)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/sessions/{session_id}/grants")
async def list_session_grants(session_id: str):
    """查看session的授权清单（隔离面可观测——为什么看不到某工具一目了然）。"""
    grants = gate.list_grants(session_id)
    return {"session_id": session_id, "grants": grants, "total": len(grants)}


@router.delete("/sessions/{session_id}/grants/{server_id}")
async def revoke_grant(session_id: str, server_id: str, tool_name: str = Query(default="*")):
    """收回授权。"""
    if not gate.revoke(session_id, server_id, tool_name):
        raise HTTPException(404, "grant not found")
    return {
        "revoked": True,
        "session_id": session_id,
        "server_id": server_id,
        "tool_name": tool_name,
    }


@router.delete("/sessions/{session_id}/grants")
async def clear_session_grants(session_id: str):
    """整批清理会话授权（H1-②b-⑤ clear_session 出口面）。

    会话结束/删除 = 该会话全部授权行随之清除（fail-closed：会话没了授权
    不得残留）。修前 clear_session 全仓零消费 = 授权行永不清，撤一个会话
    的授权只能逐 (server, tool) revoke 循环。

    幂等清理：无授权行 = cleared 0 如实计数（批量清理无单资源身份，非 404）。
    """
    cleared = gate.clear_session(session_id)
    return {"session_id": session_id, "cleared": cleared}


@router.get("/sessions/{session_id}/tools")
async def session_tools(session_id: str, x_mcp_token: str | None = Header(default=None)):
    """会话级工具隔离endpoint：只返回该session被授权的工具（fail-closed零grant=零可见）。

    双重过滤：consumer scope（发布侧auth）∩ session grants（消费侧白名单）。
    """
    consumer = _auth_consumer(x_mcp_token)  # token有效性先验（401），scope按server逐项过滤
    tools = registry.list_tools()
    visible = []
    for t in tools:
        if not SessionToolGate.consumer_may_use(consumer, t.get("server_id", "")):
            continue
        if gate.is_tool_allowed(session_id, t.get("server_id", ""), t.get("name", "")):
            visible.append(t)
    return {
        "session_id": session_id,
        "consumer_id": consumer["consumer_id"],
        "tools": visible,
        "total": len(visible),
        "granted_servers": sorted(gate.allowed_server_ids(session_id)),
    }


@router.post("/sessions/{session_id}/tools/check")
async def check_session_tool(
    session_id: str, req: ToolCheckRequest, x_mcp_token: str | None = Header(default=None)
):
    """调用前强制闸门：工具执行前必须过此check（拒绝带reason，禁止静默）。"""
    consumer = _auth_consumer(x_mcp_token, req.server_id)
    allowed = gate.is_tool_allowed(session_id, req.server_id, req.tool_name)
    if allowed:
        reason = "granted"
    else:
        reason = (
            f"denied: session '{session_id}' has no grant for "
            f"{req.server_id}/{req.tool_name} (fail-closed)"
        )
    return {
        "session_id": session_id,
        "consumer_id": consumer["consumer_id"],
        "server_id": req.server_id,
        "tool_name": req.tool_name,
        "allowed": allowed,
        "reason": reason,
        "behavior": "allow" if allowed else "reject_content",
    }
