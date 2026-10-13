"""OpenLink API — 突触系统：双向集成网关、Webhook管理。"""

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel

from src.link.connector import IntegrationManager
from src.nerve.event_bridge import push_event
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


# ── Webhook Ingress ────────────────────────────────────────


@router.post("/webhook/{connector_id}")
async def receive_webhook(connector_id: str, request: Request):
    """Receive an incoming webhook from an external system."""
    connector = manager.get_connector(connector_id)
    if not connector:
        raise HTTPException(404, "Connector not found")
    if connector.status.value != "active":
        raise HTTPException(503, f"Connector is {connector.status.value}")

    # Verify signature if secret exists
    if connector.secret:
        signature = request.headers.get("X-Signature", "")
        body = await request.body()
        expected = f"sha256={__import__('hmac').new(connector.secret.encode(), body, __import__('hashlib').sha256).hexdigest()}"
        if not __import__("hmac").compare_digest(signature, expected):
            raise HTTPException(401, "Invalid signature")

    try:
        payload = await request.json()
    except Exception:
        payload = await request.body()
        payload = payload.decode("utf-8", errors="replace")

    source_ip = request.client.host if request.client else ""
    event = manager.record_event(
        connector_id=connector_id,
        method=request.method,
        headers=dict(request.headers),
        payload=payload,
        source_ip=source_ip,
    )

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
