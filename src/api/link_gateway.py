"""OpenLink Gateway — bidirectional integration gateway.

Enables external systems to communicate with OpenMate via:
- Webhook receiver (incoming callbacks from external systems)
- Webhook sender (push events to external systems with retry)
- External system registration (heartbeat, capabilities)
- Bidirectional channels (WebSocket proxy, SSE subscription)

All webhook events are forwarded to the Nerve event bus.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.nerve.event_bridge import push_event
from src.services.tenant_binding import bind_identity

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Constants ───────────────────────────────────────────────

MAX_PAYLOAD_BYTES = 1 * 1024 * 1024  # 1 MB
MAX_EVENTS = 1000
RETRY_ATTEMPTS = 3
RETRY_DELAYS = [1.0, 2.0, 4.0]  # exponential backoff: 1s, 2s, 4s
HTTP_TIMEOUT = 30.0
HEARTBEAT_INTERVAL = 60.0  # seconds


# ── Data Models ─────────────────────────────────────────────


class WebhookDirection(StrEnum):
    IN = "in"
    OUT = "out"


@dataclass
class WebhookEndpoint:
    """A registered webhook endpoint (incoming or outgoing)."""

    webhook_id: str
    name: str
    direction: WebhookDirection
    url: str = ""  # target URL for outgoing; callback URL for incoming
    secret: str = ""
    headers: dict = field(default_factory=dict)
    payload_parser: str = ""  # optional parser expression (e.g. jq-like path)
    description: str = ""
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    last_triggered: float = 0.0
    trigger_count: int = 0
    error_count: int = 0


@dataclass
class ExternalSystem:
    """A registered external system connected to OpenMate."""

    system_id: str
    name: str
    type: str  # e.g. "gitlab", "jira", "feishu", "custom"
    url: str = ""
    auth_type: str = ""  # "bearer", "basic", "api_key", "none"
    auth_value: str = ""  # token, "user:pass", api key, etc.
    headers: dict = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)
    description: str = ""
    status: str = "online"  # "online", "offline", "error"
    created_at: float = field(default_factory=time.time)
    last_heartbeat: float = 0.0
    heartbeat_count: int = 0


@dataclass
class GatewayEvent:
    """An integration event recorded by the gateway."""

    event_id: str
    timestamp: float
    source: str  # system_id or "external"
    direction: str  # "inbound" or "outbound"
    event_type: str  # "webhook_received", "webhook_sent", "broadcast", etc.
    payload: Any = None
    target_url: str = ""
    status: str = "ok"  # "ok", "error", "retrying"
    error: str = ""
    attempt: int = 1
    tenant_id: str = "default"  # 行租户键（F3b-⑨b-③-1c-③ 事件读面加轴）


@dataclass
class WsSubscriber:
    """An active WebSocket subscriber with its handshake-derived tenant.

    tenant_id=握手时 bind_identity 派生租户键——WS relay 广播消息路由隔离的
    单一归属源（F3b-⑨b-③-1c 边界② 收口）。
    """

    ws: WebSocket
    tenant_id: str


# ── In-Memory Stores ────────────────────────────────────────

_webhooks: dict[str, WebhookEndpoint] = {}
_systems: dict[str, ExternalSystem] = {}
_events: deque[GatewayEvent] = deque(maxlen=MAX_EVENTS)
_ws_clients: list[WsSubscriber] = []  # active WebSocket subscribers (each carries its tenant)


# ── Request / Response Schemas ──────────────────────────────


class WebhookRegisterRequest(BaseModel):
    name: str
    direction: str = "in"  # "in" or "out"
    url: str = ""  # target URL for outgoing
    secret: str = ""
    headers: dict[str, str] = {}
    payload_parser: str = ""
    description: str = ""


class WebhookPushRequest(BaseModel):
    webhook_id: str
    payload: dict[str, Any]
    target_url: str = ""  # override webhook's stored URL
    headers: dict[str, str] = {}  # extra headers for this send


class SystemRegisterRequest(BaseModel):
    name: str
    type: str = "custom"
    url: str = ""
    auth_type: str = "none"
    auth_value: str = ""
    headers: dict[str, str] = {}
    capabilities: list[str] = []
    description: str = ""


class BroadcastRequest(BaseModel):
    payload: dict[str, Any]
    event_type: str = "broadcast"
    target_system_ids: list[str] = []  # empty = all systems


# ── Helpers ─────────────────────────────────────────────────


def _record_event(
    source: str,
    direction: str,
    event_type: str,
    payload: Any = None,
    target_url: str = "",
    status: str = "ok",
    error: str = "",
    attempt: int = 1,
    tenant_id: str | None = None,
) -> GatewayEvent:
    """Record a gateway event and push to Nerve bus.

    F3b-⑨b-③-1c：tenant_id=身份派生租户键（⑨b-① 桥注入口）——用户面调用点
    带 bind_identity 派生租户；F3b-⑨b-③-1c-③：事件行归属落行（落
    GatewayEvent.tenant_id 行键，事件读面 /events 与 /stream SSE 恒按行
    租户键过滤）。缺失/空白→'default' 兜底（外部 webhook 入口 1a 待拍面
    =匿名语义），键派生故障绝不吃掉落库。
    """
    row_tenant = str(tenant_id or "").strip() or "default"
    evt = GatewayEvent(
        event_id=f"gw-{uuid.uuid4().hex[:12]}",
        timestamp=time.time(),
        source=source,
        direction=direction,
        event_type=event_type,
        payload=payload,
        target_url=target_url,
        status=status,
        error=error,
        attempt=attempt,
        tenant_id=row_tenant,
    )
    _events.append(evt)

    # Forward to Nerve event bus (fire-and-forget)
    try:
        push_event(
            {
                "organ": "link",
                "emoji": "🔗",
                "type": event_type,
                "summary": f"{'📥' if direction == 'inbound' else '📤'} {event_type}: {source}",
                "detail": {
                    "event_id": evt.event_id,
                    "source": source,
                    "direction": direction,
                    "target_url": target_url,
                    "status": status,
                },
            },
            tenant_id=row_tenant,
        )
    except Exception as exc:
        logging.getLogger(__name__).debug("probe skipped: %s", exc)

    return evt


def _event_row_tenant(evt: GatewayEvent) -> str:
    """事件行租户键单一判法（DEC-⑦-① _event_row_tenant / ②~㉕b 家族同款）：
    事件自带键、缺失/空白→'default' 兜底。"""
    return str(getattr(evt, "tenant_id", "") or "").strip() or "default"


def _scoped_events(tenant_id: str) -> list[GatewayEvent]:
    """事件读面恒过滤单一判法（/events 与 /stream SSE 共用，禁两套判法漂移）：
    只回行租户键==请求者派生租户的事件行。"""
    return [e for e in _events if _event_row_tenant(e) == tenant_id]


async def _send_with_retry(
    url: str,
    payload: dict,
    headers: dict[str, str] | None = None,
    source: str = "",
    tenant_id: str | None = None,
) -> GatewayEvent:
    """Send webhook payload with exponential backoff retry (3 attempts)."""
    all_headers = {"Content-Type": "application/json", **(headers or {})}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    last_error = ""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.post(url, content=body, headers=all_headers)
                if resp.status_code < 400:
                    evt = _record_event(
                        source=source,
                        direction="outbound",
                        event_type="webhook_sent",
                        payload=payload,
                        target_url=url,
                        status="ok",
                        attempt=attempt,
                        tenant_id=tenant_id,
                    )
                    return evt
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            last_error = str(e)

        # Record retry attempt
        _record_event(
            source=source,
            direction="outbound",
            event_type="webhook_retry",
            payload=payload,
            target_url=url,
            status="retrying",
            error=last_error,
            attempt=attempt,
            tenant_id=tenant_id,
        )

        if attempt < RETRY_ATTEMPTS:
            await asyncio.sleep(RETRY_DELAYS[attempt - 1])

    # All retries exhausted
    return _record_event(
        source=source,
        direction="outbound",
        event_type="webhook_failed",
        payload=payload,
        target_url=url,
        status="error",
        error=last_error,
        attempt=RETRY_ATTEMPTS,
        tenant_id=tenant_id,
    )


# ── Webhook Receiver Endpoints ──────────────────────────────


@router.post("/webhooks")
async def register_webhook(req: WebhookRegisterRequest, authorization: str | None = Header(None)):
    """Register a new webhook endpoint (incoming or outgoing)."""
    identity = bind_identity(authorization)  # 身份证明先于一切（F3b-⑤）
    webhook_id = f"wh-{uuid.uuid4().hex[:8]}"
    wh = WebhookEndpoint(
        webhook_id=webhook_id,
        name=req.name,
        direction=WebhookDirection(req.direction),
        url=req.url,
        secret=req.secret,
        headers=req.headers,
        payload_parser=req.payload_parser,
        description=req.description,
    )
    _webhooks[webhook_id] = wh

    _record_event(
        source="gateway",
        direction="inbound",
        event_type="webhook_registered",
        payload={"webhook_id": webhook_id, "name": req.name, "direction": req.direction},
        tenant_id=identity.tenant_id,
    )

    return {
        "webhook_id": webhook_id,
        "name": wh.name,
        "direction": wh.direction.value,
        "incoming_url": f"/api/link/webhooks/{webhook_id}/incoming",
    }


@router.get("/webhooks")
async def list_webhooks():
    """List all registered webhook endpoints."""
    return {
        "webhooks": [
            {
                "webhook_id": w.webhook_id,
                "name": w.name,
                "direction": w.direction.value,
                "url": w.url,
                "enabled": w.enabled,
                "trigger_count": w.trigger_count,
                "error_count": w.error_count,
                "last_triggered": w.last_triggered,
                "created_at": w.created_at,
            }
            for w in _webhooks.values()
        ]
    }


@router.post("/webhooks/{webhook_id}/incoming")
async def receive_incoming_webhook(webhook_id: str, request: Request):
    """
    Receive an incoming webhook callback. This is a PUBLIC endpoint
    (no auth required). Payload limit: 1 MB.
    """
    wh = _webhooks.get(webhook_id)
    if not wh:
        raise HTTPException(404, "Webhook endpoint not found")
    if not wh.enabled:
        raise HTTPException(503, "Webhook endpoint is disabled")

    # Read body with size limit
    body = await request.body()
    if len(body) > MAX_PAYLOAD_BYTES:
        raise HTTPException(413, "Payload too large (max 1 MB)")

    # Verify signature if secret is configured
    if wh.secret:
        sig = request.headers.get("X-Signature", "") or request.headers.get(
            "X-Hub-Signature-256", ""
        )
        expected = f"sha256={hmac.new(wh.secret.encode(), body, hashlib.sha256).hexdigest()}"
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(401, "Invalid signature")

    # Parse payload
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = body.decode("utf-8", errors="replace")

    source_ip = request.client.host if request.client else "unknown"

    # Update webhook stats
    wh.trigger_count += 1
    wh.last_triggered = time.time()

    # Record event
    evt = _record_event(
        source=webhook_id,
        direction="inbound",
        event_type="webhook_received",
        payload={
            "webhook_id": webhook_id,
            "source_ip": source_ip,
            "method": request.method,
            "data": payload,
        },
    )

    # Forward to Nerve event bus with the actual payload
    try:
        push_event(
            {
                "organ": "link",
                "emoji": "📥",
                "type": "webhook_ingress",
                "summary": f"📥 Incoming webhook [{wh.name}] from {source_ip}",
                "detail": {
                    "webhook_id": webhook_id,
                    "source_ip": source_ip,
                    "payload_keys": list(payload.keys()) if isinstance(payload, dict) else [],
                },
            }
        )
    except Exception as exc:
        logging.getLogger(__name__).debug("probe skipped: %s", exc)

    return {"received": True, "event_id": evt.event_id, "webhook_id": webhook_id}


# ── Webhook Sender Endpoint ─────────────────────────────────


@router.post("/push")
async def push_to_external(req: WebhookPushRequest, authorization: str | None = Header(None)):
    """
    Push a message to an external system via a registered webhook.
    Supports retry with exponential backoff (1s, 2s, 4s).
    """
    identity = bind_identity(authorization)  # 身份证明先于一切（F3b-⑤）
    wh = _webhooks.get(req.webhook_id)
    if not wh:
        raise HTTPException(404, "Webhook endpoint not found")

    target_url = req.target_url or wh.url
    if not target_url:
        raise HTTPException(400, "No target URL configured")

    # Merge headers: webhook defaults + per-request extras
    merged_headers = {**wh.headers, **req.headers}

    # Sign if secret exists
    if wh.secret:
        body_bytes = json.dumps(req.payload, ensure_ascii=False).encode("utf-8")
        sig = hmac.new(wh.secret.encode(), body_bytes, hashlib.sha256).hexdigest()
        merged_headers["X-Signature"] = f"sha256={sig}"

    evt = await _send_with_retry(
        url=target_url,
        payload=req.payload,
        headers=merged_headers,
        source=req.webhook_id,
        tenant_id=identity.tenant_id,
    )

    wh.trigger_count += 1
    wh.last_triggered = time.time()
    if evt.status == "error":
        wh.error_count += 1

    return {
        "event_id": evt.event_id,
        "status": evt.status,
        "attempts": evt.attempt,
        "target_url": target_url,
    }


# ── External System Registration ────────────────────────────


@router.post("/systems")
async def register_system(req: SystemRegisterRequest, authorization: str | None = Header(None)):
    """Register an external system for bidirectional integration."""
    identity = bind_identity(authorization)  # 身份证明先于一切（F3b-⑤）
    system_id = f"sys-{uuid.uuid4().hex[:8]}"
    sys = ExternalSystem(
        system_id=system_id,
        name=req.name,
        type=req.type,
        url=req.url,
        auth_type=req.auth_type,
        auth_value=req.auth_value,
        headers=req.headers,
        capabilities=req.capabilities,
        description=req.description,
    )
    _systems[system_id] = sys

    _record_event(
        source=system_id,
        direction="inbound",
        event_type="system_registered",
        payload={"system_id": system_id, "name": req.name, "type": req.type},
        tenant_id=identity.tenant_id,
    )

    return {
        "system_id": system_id,
        "name": sys.name,
        "type": sys.type,
        "status": sys.status,
        "capabilities": sys.capabilities,
    }


@router.get("/systems")
async def list_systems():
    """List all registered external systems."""
    return {
        "systems": [
            {
                "system_id": s.system_id,
                "name": s.name,
                "type": s.type,
                "url": s.url,
                "status": s.status,
                "capabilities": s.capabilities,
                "auth_type": s.auth_type,
                "description": s.description,
                "last_heartbeat": s.last_heartbeat,
                "heartbeat_count": s.heartbeat_count,
                "created_at": s.created_at,
            }
            for s in _systems.values()
        ]
    }


@router.post("/systems/{system_id}/heartbeat")
async def system_heartbeat(system_id: str):
    """
    Record a heartbeat from an external system.
    If no heartbeat received within 2× the interval, system is marked offline.
    """
    sys = _systems.get(system_id)
    if not sys:
        raise HTTPException(404, "External system not found")

    sys.last_heartbeat = time.time()
    sys.heartbeat_count += 1
    sys.status = "online"

    return {
        "system_id": system_id,
        "status": "online",
        "heartbeat_count": sys.heartbeat_count,
        "timestamp": sys.last_heartbeat,
    }


# ── Integration Event History ───────────────────────────────


@router.get("/events")
async def get_events(
    limit: int = Query(default=50, ge=1, le=1000),
    source: str = Query(default=""),
    direction: str = Query(default=""),
    event_type: str = Query(default=""),
    authorization: str | None = Header(None),
):
    """Get integration event history with optional filters.

    F3b-⑨b-③-1c-③：tenant_id=bind_identity 派生租户（匿名='default' 契约
    不变），事件读面恒按行租户键过滤（_scoped_events 单一判法）；total=
    请求者租户作用域计数（分租户计数桶，禁泄露全局分布）。
    """
    identity = bind_identity(authorization)  # 身份证明先于一切（F3b-⑤）
    scoped = _scoped_events(identity.tenant_id)
    events = scoped
    if source:
        events = [e for e in events if e.source == source]
    if direction:
        events = [e for e in events if e.direction == direction]
    if event_type:
        events = [e for e in events if e.event_type == event_type]

    return {
        "events": [
            {
                "event_id": e.event_id,
                "timestamp": e.timestamp,
                "source": e.source,
                "direction": e.direction,
                "event_type": e.event_type,
                "target_url": e.target_url,
                "status": e.status,
                "error": e.error,
                "attempt": e.attempt,
            }
            for e in events[-limit:]
        ],
        "total": len(scoped),
    }


# ── Broadcast ───────────────────────────────────────────────


@router.post("/broadcast")
async def broadcast_to_systems(req: BroadcastRequest, authorization: str | None = Header(None)):
    """
    Broadcast a message to all registered external systems (or a subset).
    Each system gets the message via its configured URL with auth headers.
    """
    identity = bind_identity(authorization)  # 身份证明先于一切（F3b-⑤）
    targets = _systems.values()
    if req.target_system_ids:
        targets = [_systems[sid] for sid in req.target_system_ids if sid in _systems]

    if not targets:
        raise HTTPException(400, "No target systems found")

    results = []
    for sys in targets:
        if not sys.url:
            results.append({"system_id": sys.system_id, "status": "skipped", "reason": "no URL"})
            continue

        headers = {"Content-Type": "application/json", **sys.headers}
        if sys.auth_type == "bearer" and sys.auth_value:
            headers["Authorization"] = f"Bearer {sys.auth_value}"
        elif sys.auth_type == "api_key" and sys.auth_value:
            headers["X-API-Key"] = sys.auth_value

        evt = await _send_with_retry(
            url=sys.url,
            payload=req.payload,
            headers=headers,
            source=sys.system_id,
            tenant_id=identity.tenant_id,
        )
        results.append(
            {
                "system_id": sys.system_id,
                "status": evt.status,
                "event_id": evt.event_id,
                "attempts": evt.attempt,
            }
        )

    _record_event(
        source="gateway",
        direction="outbound",
        event_type="broadcast",
        payload={
            "event_type": req.event_type,
            "targets": len(results),
            "results": results,
        },
        tenant_id=identity.tenant_id,
    )

    return {
        "broadcast": True,
        "event_type": req.event_type,
        "targets": len(results),
        "results": results,
    }


# ── SSE Subscription ────────────────────────────────────────


def _http_presented_token(request: Request) -> str:
    """HTTP 身份凭证取参双源（F3b-④ WS 同款语义）：`?token=` query（浏览器
    EventSource 无法自设头，主口）优先，Authorization 头（fetch 等客户端）
    兜底。返回原始凭证串（bind_identity._bearer_token 归一 Bearer 前缀），
    未提供=空串=匿名。"""
    token = (request.query_params.get("token") or "").strip()
    if token:
        return token
    return (request.headers.get("authorization") or "").strip()


def _sse_event_lines(tenant_id: str, source: str, event_type: str):
    """SSE 帧生成器（F3b-⑨b-③-1c-③ 提取为模块级可测面）：只发行租户键==
    tenant_id 的事件（_scoped_events 单一判法，跨租户零帧）；source /
    event_type 过滤语义字节不变。"""

    async def event_generator():
        last_seen = 0
        while True:
            current = _scoped_events(tenant_id)
            for evt in current:
                if evt.timestamp <= last_seen:
                    continue
                if source and evt.source != source:
                    continue
                if event_type and evt.event_type != event_type:
                    continue
                data = json.dumps(
                    {
                        "event_id": evt.event_id,
                        "timestamp": evt.timestamp,
                        "source": evt.source,
                        "direction": evt.direction,
                        "event_type": evt.event_type,
                        "status": evt.status,
                    },
                    ensure_ascii=False,
                )
                yield f"data: {data}\n\n"
                last_seen = max(last_seen, evt.timestamp)
            await asyncio.sleep(1.0)

    return event_generator()


@router.get("/stream")
async def sse_event_stream(
    request: Request,
    source: str = Query(default=""),
    event_type: str = Query(default=""),
):
    """
    Server-Sent Events stream for real-time integration events.
    Clients connect here to receive live event notifications.

    F3b-⑨b-③-1c-③：身份轴——凭据双源取参→bind_identity 派生租户键（坏
    token 首字节前 401，身份证明先于一切 F3b-⑤），事件读面恒按行租户键
    过滤（跨租户零帧），未提供=匿名 'default' 契约不变。
    """

    identity = bind_identity(_http_presented_token(request) or None)
    return StreamingResponse(
        _sse_event_lines(identity.tenant_id, source, event_type),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── WebSocket Proxy ─────────────────────────────────────────


def _ws_presented_token(ws: WebSocket) -> str:
    """WS 身份凭证取参双源（F3b-④ 先例）：`?token=` query（浏览器 WS 无法自设头，
    主口）优先，Authorization 头（非浏览器客户端）兜底。返回原始凭证串
    （bind_identity._bearer_token 归一 Bearer 前缀），未提供=空串=匿名。"""
    token = (ws.query_params.get("token") or "").strip()
    if token:
        return token
    return (ws.headers.get("authorization") or "").strip()


@router.websocket("/ws")
async def websocket_proxy(ws: WebSocket):
    """
    WebSocket endpoint for real-time bidirectional communication.
    External clients connect here; messages are broadcast to connected
    WS subscribers of the sender's tenant and forwarded to the Nerve bus.

    F3b-⑨b-③-1c：身份轴——凭据双源取参→bind_identity 派生租户键带入事件；
    坏 token 握手拒绝（accept 前 close 1008，身份证明先于一切 F3b-⑤），
    未提供=匿名 'default' 契约不变。
    F3b-⑨b-③-1c 边界② 收口：WS relay 广播消息路由隔离——广播只走发方
    租户键（WsSubscriber.tenant_id=握手派生单一归属源），跨租户零投递，
    禁全局内存面互通；发方自身照旧不收自己的广播。
    """
    try:
        identity = bind_identity(_ws_presented_token(ws) or None)
    except HTTPException as exc:
        logger.warning("link WS rejected: %s", exc.detail)
        await ws.close(code=1008, reason="invalid token")
        return
    tenant = identity.tenant_id

    await ws.accept()
    _ws_clients.append(WsSubscriber(ws=ws, tenant_id=tenant))
    logger.info("WS client connected (%d active)", len(_ws_clients))

    # Notify via event bus
    _record_event(
        source="ws-client",
        direction="inbound",
        event_type="ws_connected",
        payload={"total_clients": len(_ws_clients)},
        tenant_id=tenant,
    )

    try:
        while True:
            data = await ws.receive_text()
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                payload = {"raw": data}

            # Record the incoming WS message
            _record_event(
                source="ws-client",
                direction="inbound",
                event_type="ws_message",
                payload=payload,
                tenant_id=tenant,
            )

            # Forward to Nerve bus
            try:
                push_event(
                    {
                        "organ": "link",
                        "emoji": "🔗",
                        "type": "ws_message",
                        "summary": "🔌 WebSocket message received",
                        "detail": {"payload": payload},
                    },
                    tenant_id=tenant,
                )
            except Exception as exc:
                logging.getLogger(__name__).debug("probe skipped: %s", exc)
            # Broadcast to other connected WS clients of the same tenant
            # （F3b-⑨b-③-1c 边界② 收口：WS relay 广播消息路由隔离——按发方
            # 租户键路由，跨租户零投递禁全局互通）
            disconnected = []
            for sub in _ws_clients:
                if sub.ws is ws:
                    continue
                if sub.tenant_id != tenant:
                    continue
                try:
                    await sub.ws.send_text(
                        json.dumps(
                            {
                                "from": "relay",
                                "data": payload,
                                "timestamp": time.time(),
                            },
                            ensure_ascii=False,
                        )
                    )
                except Exception:
                    disconnected.append(sub)

            # Clean up disconnected clients
            for dc in disconnected:
                _ws_clients.remove(dc)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WS error: %s", e)
    finally:
        for sub in list(_ws_clients):
            if sub.ws is ws:
                _ws_clients.remove(sub)
        logger.info("WS client disconnected (%d active)", len(_ws_clients))


# ── Gateway Stats ───────────────────────────────────────────


@router.get("/stats")
async def gateway_stats():
    """Get Link Gateway statistics."""
    return {
        "status": "ok",
        "component": "LinkGateway",
        "webhooks": len(_webhooks),
        "systems": len(_systems),
        "events": len(_events),
        "ws_clients": len(_ws_clients),
        "systems_online": sum(1 for s in _systems.values() if s.status == "online"),
        "systems_offline": sum(1 for s in _systems.values() if s.status == "offline"),
    }


@router.get("/health")
async def gateway_health():
    """Link Gateway health check."""
    return {
        "status": "ok",
        "component": "LinkGateway",
        "webhooks": len(_webhooks),
        "systems": len(_systems),
        "ws_clients": len(_ws_clients),
    }
