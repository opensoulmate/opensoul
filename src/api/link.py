"""OpenLink API — 突触系统：双向集成网关、Webhook管理。"""

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel

from src.link.connector import IntegrationManager
from src.nerve.event_bridge import push_event
from src.services.callback_token import callback_token_ok
from src.services.tenant_binding import bind_identity

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
manager = IntegrationManager()


# ── Request Schemas ────────────────────────────────────────


class ConnectorCreateRequest(BaseModel):
    name: str
    type: str  # "webhook_in", "webhook_out", "rest_api", "oa_system", "custom"
    endpoint: str = ""
    secret: str = ""
    headers: dict = {}
    config: dict = {}
    description: str = ""
    tags: list[str] = []


class ConnectorUpdateRequest(BaseModel):
    name: str | None = None
    endpoint: str | None = None
    secret: str | None = None
    status: str | None = None
    headers: dict | None = None
    description: str | None = None


class WebhookSendRequest(BaseModel):
    payload: dict


# ── Connector CRUD ─────────────────────────────────────────


@router.post("/connectors")
async def create_connector(
    req: ConnectorCreateRequest,
    authorization: str | None = Header(None),
):
    """Create a new integration connector."""
    identity = bind_identity(authorization)
    connector = manager.create_connector(
        name=req.name,
        type=req.type,
        endpoint=req.endpoint,
        secret=req.secret,
        headers=req.headers,
        config=req.config,
        description=req.description,
        tags=req.tags,
    )
    push_event(
        {
            "organ": "link",
            "emoji": "🔗",
            "type": "connector_created",
            "summary": f"🔌 Connector created: {connector.name} ({connector.type.value})",
            "detail": {
                "connector_id": connector.connector_id,
                "name": connector.name,
                "type": connector.type.value,
            },
        },
        tenant_id=identity.tenant_id,
    )

    return {
        "connector_id": connector.connector_id,
        "name": connector.name,
        "type": connector.type.value,
        "status": connector.status.value,
        # register 签发回调凭据明文只回此注册响应一次（DEC-⑲，DEC-⑬ 同款）：
        # get/list 面不回吐（只回 has_callback_token 布尔）。
        "callback_token": connector.callback_token,
    }


@router.get("/connectors")
async def list_connectors(
    type: str = Query(default=None),
    status: str = Query(default=None),
):
    """List all connectors."""
    return {"connectors": manager.list_connectors(type=type, status=status)}


@router.get("/connectors/{connector_id}")
async def get_connector(connector_id: str):
    """Get connector details."""
    c = manager.get_connector(connector_id)
    if not c:
        raise HTTPException(404, "Connector not found")
    return {
        "connector_id": c.connector_id,
        "name": c.name,
        "type": c.type.value,
        "status": c.status.value,
        "endpoint": c.endpoint,
        "has_secret": bool(c.secret),
        "has_callback_token": bool(c.callback_token),
        "headers": c.headers,
        "config": c.config,
        "description": c.description,
        "tags": c.tags,
        "trigger_count": c.trigger_count,
        "error_count": c.error_count,
        "last_triggered": c.last_triggered,
        "last_error": c.last_error,
        "created_at": c.created_at,
    }


@router.patch("/connectors/{connector_id}")
async def update_connector(connector_id: str, req: ConnectorUpdateRequest):
    """Update a connector."""
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not manager.update_connector(connector_id, **updates):
        raise HTTPException(404, "Connector not found")
    return {"message": "updated"}


@router.delete("/connectors/{connector_id}")
async def delete_connector(connector_id: str):
    """Delete a connector."""
    if not manager.delete_connector(connector_id):
        raise HTTPException(404, "Connector not found")
    return {"message": "deleted"}


# ── Connector Test (frontend calls /connectors/{id}/test) ──


@router.post("/connectors/{connector_id}/test")
async def test_connector(connector_id: str, req: WebhookSendRequest | None = None):
    """Test a connector by sending a test payload."""
    payload = req.payload if req else {"test": True, "timestamp": __import__("time").time()}
    result = manager.send_webhook(connector_id, payload)
    return result


# ── Callback Token Lifecycle (DEC-⑲-② 轮换·撤销) ──────────


@router.post("/connectors/{connector_id}/callback-token/rotate")
async def rotate_callback_token(connector_id: str, authorization: str | None = Header(None)):
    """Rotate (re-issue) the callback credential.

    DEC-⑲-②：轮换=签发新凭据并即刻作废旧凭据（行 token 被替换，旧明文立即
    401）；新明文只回本响应一次（register 同语义，get/list 面不回吐）。撤销后
    经本口可重新签发（⑲-① 留档「凭据丢失=重注册新 connector 或 ⑲-② 轮换端点」
    的恢复路径）。写面 bind_identity（坏 token 401 先于读写零副作用，F3b 家族）。
    """
    identity = bind_identity(authorization)
    token = manager.rotate_callback_token(connector_id)
    if token is None:
        raise HTTPException(404, "Connector not found")
    push_event(
        {
            "organ": "link",
            "emoji": "🔗",
            "type": "connector_token_rotated",
            "summary": f"🔄 Callback token rotated: {connector_id}",
            "detail": {"connector_id": connector_id},
        },
        tenant_id=identity.tenant_id,
    )
    return {"connector_id": connector_id, "callback_token": token, "rotated": True}


@router.delete("/connectors/{connector_id}/callback-token")
async def revoke_callback_token(connector_id: str, authorization: str | None = Header(None)):
    """Revoke the callback credential.

    DEC-⑲-②：撤销=置空行 token，X-Callback-Token 证明 fail-closed 拒；只清
    token 证明面，X-Signature HMAC=secret 等效证明独立轴不动（secret 兼出站
    签名，更换属 PATCH secret 既有面）。撤销后 rotate 可重新签发。
    """
    identity = bind_identity(authorization)
    if not manager.revoke_callback_token(connector_id):
        raise HTTPException(404, "Connector not found")
    push_event(
        {
            "organ": "link",
            "emoji": "🔗",
            "type": "connector_token_revoked",
            "summary": f"🚫 Callback token revoked: {connector_id}",
            "detail": {"connector_id": connector_id},
        },
        tenant_id=identity.tenant_id,
    )
    return {"connector_id": connector_id, "revoked": True, "has_callback_token": False}


# ── Webhook Ingress ────────────────────────────────────────


@router.post("/webhook/{connector_id}")
async def receive_webhook(connector_id: str, request: Request):
    """Receive an incoming webhook from an external system."""
    connector = manager.get_connector(connector_id)
    if not connector:
        raise HTTPException(404, "Connector not found")
    if connector.status.value != "active":
        raise HTTPException(503, f"Connector is {connector.status.value}")

    body = await request.body()

    # 凭据证明先于一切副作用（DEC-⑲ link.py receive_webhook token 化取 B，
    # DEC-⑬ heartbeat/incoming 同轴同哲学）：claim 回调身份必须持 register
    # 签发凭据——X-Callback-Token=注册签发 callback_token（F3b-④/⑤ 同族），
    # 或既有 X-Signature HMAC 签名=connector.secret 等效证明（发方持 secret
    # 即持证，签名面契约零变化）；两证全无/全错=401 零副作用（record_event
    # 不落、trigger_count 不动、零事件/零通知）。fail-closed：无证可验=拒
    # （create_connector 恒签发 token=生产不可达"无证"行）。
    # 注意与 DEC-① 1a 契约的分界：Authorization 头仍绝不触发 401、也绝不
    # 改变事件归属（用户 JWT 非本入口身份轴），本门只认 connector 自有凭据。
    sig_ok = False
    if connector.secret:
        signature = request.headers.get("X-Signature", "")
        expected = f"sha256={__import__('hmac').new(connector.secret.encode(), body, __import__('hashlib').sha256).hexdigest()}"
        sig_ok = __import__("hmac").compare_digest(signature, expected)
    presented = (request.headers.get("X-Callback-Token") or "").strip()
    if not (sig_ok or callback_token_ok(connector.callback_token, presented)):
        if not presented and not request.headers.get("X-Signature"):
            raise HTTPException(
                401,
                "callback credential missing: present X-Callback-Token "
                "(or X-Signature when secret is configured)",
            )
        raise HTTPException(401, "invalid callback credential")

    try:
        payload = __import__("json").loads(body)
    except Exception:
        payload = body.decode("utf-8", errors="replace")

    source_ip = request.client.host if request.client else ""
    event = manager.record_event(
        connector_id=connector_id,
        method=request.method,
        headers=dict(request.headers),
        payload=payload,
        source_ip=source_ip,
    )

    # 系统事件归属（DEC-① 1a 裁决＝维持 'default' 兜底）：外部 webhook 调用方
    # 非用户身份轴，事件刻意不带 tenant_id（record() 落 'default'）＝「connector
    # 系统事件」语义如实；任何 Authorization 头都不改变归属，也绝不接 401
    # （第三方调用方无用户 JWT，接身份轴＝打断外部 webhook 真回归）。
    # 凭据轴与归属轴分开（DEC-⑲）：本入口 2026-10-16 起必持 connector 自有
    # 回调凭据（X-Callback-Token/X-Signature），那是"发方持有注册签发凭据"的
    # 通道证明而非用户身份——持证匿名发方事件照旧落 'default'。
    push_event(
        {
            "organ": "link",
            "emoji": "🔗",
            "type": "webhook_received",
            "summary": f"📥 Webhook received from {source_ip} → {connector_id}",
            "detail": {
                "connector_id": connector_id,
                "source_ip": source_ip,
                "method": request.method,
            },
        }
    )

    # Also push to notification center for immediate visibility
    try:
        from src.api.notifications import push_notification

        push_notification(
            source="link",
            title=f"🔗 Webhook: {connector.name}",
            body=f"Received from {source_ip} ({request.method})",
            level="info",
            organ="link",
            emoji="🔗",
            action_url="/link",
            metadata={
                "connector_id": connector_id,
                "source_ip": source_ip,
                "event_id": event.event_id,
            },
        )
    except Exception:
        pass  # Non-fatal

    return {"received": True, "event_id": event.event_id}


# ── Webhook Egress ─────────────────────────────────────────


@router.post("/connectors/{connector_id}/send")
async def send_webhook(connector_id: str, req: WebhookSendRequest):
    """Send a webhook to a connector's endpoint."""
    result = manager.send_webhook(connector_id, req.payload)
    if not result["success"]:
        raise HTTPException(400, result["error"])
    return result


# ── Events ─────────────────────────────────────────────────


@router.get("/events")
async def get_events(
    connector_id: str = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    """Get webhook event history."""
    return {"events": manager.get_events(connector_id=connector_id, limit=limit)}


# ── Stats ──────────────────────────────────────────────────


@router.get("/stats")
async def link_stats():
    """Get OpenLink statistics."""
    return {
        "status": "ok",
        "component": "OpenLink",
        **manager.stats(),
    }


# ── Health ─────────────────────────────────────────────────


@router.get("/health")
async def link_health():
    """OpenLink health check."""
    return {
        "status": "ok",
        "component": "OpenLink",
        **manager.stats(),
    }
