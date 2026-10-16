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
from src.services.callback_token import callback_token_ok
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
# 连接期凭据重校验节拍（F3b-⑨b-③-1c-⑤）：WS /ws 与 SSE /stream 长连接
# 握手后每此秒数重跑 bind_identity；吊销/过期与关闭之间有界滞后。
TOKEN_REVALIDATE_INTERVAL_S = 60.0


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
    # register 签发回调凭据（DEC-⑬ 外部回调入口鉴权化，明文只回注册响应一次）
    callback_token: str = ""
    headers: dict = field(default_factory=dict)
    payload_parser: str = ""  # optional parser expression (e.g. jq-like path)
    description: str = ""
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    last_triggered: float = 0.0
    trigger_count: int = 0
    error_count: int = 0
    tenant_id: str = "default"  # 行租户键（F3b-⑨b-③-1c-④ 注册面加轴）


@dataclass
class ExternalSystem:
    """A registered external system connected to OpenMate."""

    system_id: str
    name: str
    type: str  # e.g. "gitlab", "jira", "feishu", "custom"
    url: str = ""
    auth_type: str = ""  # "bearer", "basic", "api_key", "none"
    auth_value: str = ""  # token, "user:pass", api key, etc.
    # register 签发回调凭据（DEC-⑬ 外部回调入口鉴权化，明文只回注册响应一次）
    callback_token: str = ""
    headers: dict = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)
    description: str = ""
    status: str = "online"  # "online", "offline", "error"
    created_at: float = field(default_factory=time.time)
    last_heartbeat: float = 0.0
    heartbeat_count: int = 0
    tenant_id: str = "default"  # 行租户键（F3b-⑨b-③-1c-④ 注册面加轴）


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


def _webhook_row_tenant(wh: WebhookEndpoint) -> str:
    """webhook 注册行租户键单一判法（DEC-⑦-① _event_row_tenant / ②~㉕b 家族
    同款，F3b-⑨b-③-1c-④ 注册面加轴）：行自带键、缺失/空白→'default' 兜底。"""
    return str(getattr(wh, "tenant_id", "") or "").strip() or "default"


def _system_row_tenant(sys: ExternalSystem) -> str:
    """外部系统注册行租户键单一判法（同家族同款）。"""
    return str(getattr(sys, "tenant_id", "") or "").strip() or "default"


def _scoped_webhooks(tenant_id: str) -> list[WebhookEndpoint]:
    """webhook 注册表读面恒过滤单一判法（/webhooks list 真消费）：只回行
    租户键==请求者派生租户的注册行。"""
    return [w for w in _webhooks.values() if _webhook_row_tenant(w) == tenant_id]


def _scoped_systems(tenant_id: str) -> list[ExternalSystem]:
    """外部系统注册表恒过滤单一判法（/systems list 与 /broadcast 选靶共用，
    禁两套判法漂移）：只回行租户键==请求者派生租户的注册行。"""
    return [s for s in _systems.values() if _system_row_tenant(s) == tenant_id]


def _gate_webhook(webhook_id: str, tenant_id: str) -> WebhookEndpoint:
    """webhook by-ID 归属闸（DEC-⑦-⑩ 家族「跨租户与 not-found 同契约」）：
    跨租户与缺行同回 404 同文案，禁差分泄露存在性；命中回行。"""
    wh = _webhooks.get(webhook_id)
    if wh is None or _webhook_row_tenant(wh) != tenant_id:
        raise HTTPException(404, "Webhook endpoint not found")
    return wh


def _callback_token_ok(row_token: str, presented: str) -> bool:
    """外部回调凭据单一判定口（DEC-⑬ heartbeat/incoming 共用，禁两套判法漂移）。

    判定语义单一源=src/services/callback_token.callback_token_ok（DEC-⑲
    link.py receive_webhook 同轴共用，禁两套判法漂移）：claim 回调身份必须持
    register 签发 callback_token（F3b-④/⑤ 同族
    "claim 注册身份必须持 register 签发 token"），hmac 恒时比较；
    缺失/空白/不匹配一律 False（fail-closed：无证可验=拒）。
    """
    return callback_token_ok(row_token, presented)


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
    callback_token = uuid.uuid4().hex  # register 签发回调凭据（DEC-⑬，F3b-④ 同款）
    wh = WebhookEndpoint(
        webhook_id=webhook_id,
        name=req.name,
        direction=WebhookDirection(req.direction),
        url=req.url,
        secret=req.secret,
        callback_token=callback_token,
        headers=req.headers,
        payload_parser=req.payload_parser,
        description=req.description,
        tenant_id=identity.tenant_id,
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
        # 明文只回这一次（list 面不回吐，F3b-④ register token 同款）
        "callback_token": wh.callback_token,
    }


@router.get("/webhooks")
async def list_webhooks(authorization: str | None = Header(None)):
    """List all registered webhook endpoints.

    F3b-⑨b-③-1c-④：tenant_id=bind_identity 派生租户（匿名='default' 契约
    不变），注册表读面恒按行租户键过滤（_scoped_webhooks 单一判法，跨租户
    行不可见）；行内加性键 tenant_id（行归属可观测）。
    """
    identity = bind_identity(authorization)  # 身份证明先于一切（F3b-⑤）
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
                "tenant_id": _webhook_row_tenant(w),
            }
            for w in _scoped_webhooks(identity.tenant_id)
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

    # 凭据证明先于一切副作用（DEC-⑬ 外部回调入口鉴权化统一拍板取 B）：
    # claim 回调身份必须持 register 签发凭据——X-Callback-Token=注册签发
    # callback_token（F3b-④/⑤ 同族），或既有 HMAC 签名=wh.secret 等效证明
    # （发方持 secret 即持证，签名面契约零变化）；两证全无/全错=401 零副作用
    # （trigger_count 不动、零事件落库）。fail-closed：无证可验=拒（register
    # 恒签发 token=生产不可达"无证"行）。
    sig = request.headers.get("X-Signature", "") or request.headers.get("X-Hub-Signature-256", "")
    sig_ok = False
    if wh.secret:
        expected = f"sha256={hmac.new(wh.secret.encode(), body, hashlib.sha256).hexdigest()}"
        sig_ok = hmac.compare_digest(sig, expected)
    presented = (request.headers.get("X-Callback-Token") or "").strip()
    if not (sig_ok or _callback_token_ok(wh.callback_token, presented)):
        if not sig and not presented:
            raise HTTPException(
                401,
                "callback credential missing: present X-Callback-Token "
                "(or X-Signature when secret is configured)",
            )
        raise HTTPException(401, "invalid callback credential")

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
    wh = _gate_webhook(
        req.webhook_id, identity.tenant_id
    )  # by-ID 归属闸：跨租户与 not-found 同契约

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
    callback_token = uuid.uuid4().hex  # register 签发回调凭据（DEC-⑬，F3b-④ 同款）
    sys = ExternalSystem(
        system_id=system_id,
        name=req.name,
        type=req.type,
        url=req.url,
        auth_type=req.auth_type,
        auth_value=req.auth_value,
        callback_token=callback_token,
        headers=req.headers,
        capabilities=req.capabilities,
        description=req.description,
        tenant_id=identity.tenant_id,
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
        # 明文只回这一次（list 面不回吐，F3b-④ register token 同款）
        "callback_token": sys.callback_token,
    }


@router.get("/systems")
async def list_systems(authorization: str | None = Header(None)):
    """List all registered external systems.

    F3b-⑨b-③-1c-④：tenant_id=bind_identity 派生租户（匿名='default' 契约
    不变），注册表读面恒按行租户键过滤（_scoped_systems 单一判法，跨租户
    行不可见）；行内加性键 tenant_id（行归属可观测）。
    """
    identity = bind_identity(authorization)  # 身份证明先于一切（F3b-⑤）
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
                "tenant_id": _system_row_tenant(s),
            }
            for s in _scoped_systems(identity.tenant_id)
        ]
    }


@router.post("/systems/{system_id}/heartbeat")
async def system_heartbeat(system_id: str, request: Request):
    """
    Record a heartbeat from an external system.
    If no heartbeat received within 2× the interval, system is marked offline.

    DEC-⑬ 外部回调入口鉴权化：claim 心跳身份必须持 register 签发
    callback_token（X-Callback-Token），缺/错=401 零副作用。
    """
    sys = _systems.get(system_id)
    if not sys:
        raise HTTPException(404, "External system not found")

    # 凭据证明先于一切副作用（DEC-⑬）：修前任意人可伪造心跳把 offline 顶成
    # online、抬 heartbeat_count——缺/错凭据 401（last_heartbeat/heartbeat_count/
    # status 一字不动）；fail-closed 同 incoming。
    presented = (request.headers.get("X-Callback-Token") or "").strip()
    if not _callback_token_ok(sys.callback_token, presented):
        if not presented:
            raise HTTPException(401, "callback credential missing: present X-Callback-Token")
        raise HTTPException(401, "invalid callback credential")

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


@router.get("/gateway/events")
async def get_events(
    limit: int = Query(default=50, ge=1, le=1000),
    source: str = Query(default=""),
    direction: str = Query(default=""),
    event_type: str = Query(default=""),
    authorization: str | None = Header(None),
):
    """Get integration event history with optional filters.

    路径归属（F3b-⑨b-③-1c-⑥ 路由遮蔽收口 DEC-⑫ 自决）：/api/link/events
    归 OpenLink（link.py 活消费方：admin-ui link 页/event_stream 轮询/健康
    监测），本面迁 /api/link/gateway/events——修前同前缀先挂 link_router
    遮蔽本面=生产死路由（1c-③ 边界② 留档）。

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
    # 选靶恒按行租户键过滤（_scoped_systems 单一判法，F3b-⑨b-③-1c-④）：
    # 跨租户系统零投递；显式 target_system_ids 里的跨租户/未知 id 与缺行同
    # 契约（与既有未知 id 同款跳过，禁差分泄露存在性）。
    scoped_by_id = {s.system_id: s for s in _scoped_systems(identity.tenant_id)}
    if req.target_system_ids:
        targets = [scoped_by_id[sid] for sid in req.target_system_ids if sid in scoped_by_id]
    else:
        targets = list(scoped_by_id.values())

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


def _ws_client_host(ws) -> str:
    """审计事件 client 来源（terminal_ws/ws_chat 同款判法）。"""
    return ws.client.host if getattr(ws, "client", None) else ""


def _audit(event_type: str, summary: str, detail: dict, tenant_id: str | None = None):
    """DEC-⑱-③ WS/SSE 连接审计：link_gateway 长连接面（WS /ws + SSE /stream）
    连接生命周期事件落 timeline/活动流（organ=immune 🛡 安全归口，与
    terminal_ws_* / ws_chat_* / intrusion_detected / permission_denied 同流可见）。

    闭 DEC-⑱-② 诚实边界②留档件「link_gateway/collab 长连接面审计留待拍（⑱-③/
    ⑱-④）」——中继通道谁被拒/谁连上/连接何时以何因结束必须可审计回放。

    **同步口（push_event：活动流缓冲+持久 timeline 双落，nerve 总线既有
    fire-and-forget）而非 await 型 emit——取消免疫是硬要求**：连接收尾路径的宿主
    任务可被取消（client 断连/流取消兑现于 await 点，await 型审计会被连同收尾
    一起吞掉=静默丢审计），审计真源不赌 await 活到落盘（DEC-⑱ 教训）。

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
        logger.warning("link audit event dropped (%s): %s", event_type, exc)


def _sse_event_lines(
    tenant_id: str,
    source: str,
    event_type: str,
    token=None,
    user_id: str = "",
    authed: bool = False,
):
    """SSE 帧生成器（F3b-⑨b-③-1c-③ 提取为模块级可测面）：只发行租户键==
    tenant_id 的事件（_scoped_events 单一判法，跨租户零帧）；source /
    event_type 过滤语义字节不变。

    token：连接期凭据重校验（F3b-⑨b-③-1c-⑤）——提供了 token 的连接每
    TOKEN_REVALIDATE_INTERVAL_S 秒重跑 bind_identity（单一派生口）：凭据
    失效（JWT 过期/用户停用或消失）=结束流（禁凭失效凭据继续收帧），派生
    租户变化=跟随新派生（scope re-key，禁持陈旧绑定继续服务）。未提供
    token（匿名 'default' 面）=无凭据可失效，不重校验，行为字节不变。
    """

    async def event_generator():
        last_seen = 0
        scope = tenant_id
        next_check = time.time() + TOKEN_REVALIDATE_INTERVAL_S
        # DEC-⑱-③ 连接审计：connected=流真建立（首次迭代即开播）；closed=connected
        # 的对偶（何时以何因收流可回放）。user/tenant=bind_identity 派生单一源。
        started = time.time()
        end_reason = "client_disconnect"
        _audit(
            "link_sse_connected",
            f"🔗 Link SSE 订阅建立: user={user_id}",
            {
                "face": "link_sse",
                "user_id": user_id,
                "authed": authed,
                "has_token": bool(token),
            },
            tenant_id=scope,
        )
        try:
            while True:
                if token and time.time() >= next_check:
                    try:
                        fresh = bind_identity(token)
                    except HTTPException as exc:
                        logger.warning("link SSE token revalidation failed: %s", exc.detail)
                        end_reason = "credential_revoked"
                        return
                    scope = fresh.tenant_id
                    next_check = time.time() + TOKEN_REVALIDATE_INTERVAL_S
                current = _scoped_events(scope)
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
        except Exception:
            # 非断开型异常收口如实归因 error（client 断连/流取消=GeneratorExit/
            # CancelledError 均 BaseException 不入此分支，按缺省 client_disconnect 如实）。
            end_reason = "error"
            raise
        finally:
            # DEC-⑱-③ 会话结束归因：同步口置于任何 await 之前=取消免疫（DEC-⑱ 教训）。
            _audit(
                "link_sse_closed",
                f"🔗 Link SSE 订阅结束（{end_reason}）: user={user_id}",
                {
                    "face": "link_sse",
                    "user_id": user_id,
                    "authed": authed,
                    "has_token": bool(token),
                    "reason": end_reason,
                    "duration_s": round(time.time() - started, 1),
                },
                tenant_id=scope,
            )

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

    presented = _http_presented_token(request)
    try:
        identity = bind_identity(presented or None)
    except HTTPException:
        # DEC-⑱-③ 连接审计：被拒尝试是安全事件本体必留痕（cause=invalid_token；
        # 匿名缺凭据=契约放行非拒绝面）。零行为变化：审计后原样上抛→既有 401。
        _audit(
            "link_sse_rejected",
            "🛡 Link SSE 订阅拒绝（无效凭据）",
            {
                "face": "link_sse",
                "client": request.client.host if request.client else "",
                "has_token": bool(presented),
                "cause": "invalid_token",
            },
        )
        raise
    return StreamingResponse(
        _sse_event_lines(
            identity.tenant_id,
            source,
            event_type,
            token=presented,
            user_id=identity.user_id,
            authed=identity.authed,
        ),
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


async def _ws_token_watchdog(ws, me, token):
    """WS 连接期凭据周期重校验（F3b-⑨b-③-1c-⑤）。

    每 TOKEN_REVALIDATE_INTERVAL_S 秒重跑 bind_identity（单一派生口）：
    - 凭据失效（JWT 过期/签名失效/用户停用或消失）→ close 1008（与握手
      拒绝同语义），禁凭失效凭据继续收发；主循环 receive 因此结束，finally
      清理 _ws_clients。
    - 派生租户变化 → me.tenant_id re-key（键来源=bind_identity 单一派生口，
      禁持陈旧绑定继续服务；消息循环每条消息取最新键，路由/事件归属随之）。
    诚实边界：吊销/过期与关闭之间有 ≤ TOKEN_REVALIDATE_INTERVAL_S 秒有界
    滞后（周期重校验语义固有）；连接期内 bearer token 字符串不变（重校验
    的是同一凭据的当前效力，非轮换）。
    """
    while True:
        await asyncio.sleep(TOKEN_REVALIDATE_INTERVAL_S)
        try:
            fresh = bind_identity(token)
        except HTTPException as exc:
            logger.warning("link WS token revalidation failed: %s", exc.detail)
            try:
                await ws.close(code=1008, reason="invalid token")
            except Exception:
                pass
            # 看门狗自行清理订阅条目：服务端 close 后主循环 receive 未必即刻
            # 退出（不能把清理挂在 receive 抛错上=禁泄漏），finally 清理幂等兜底。
            if me in _ws_clients:
                _ws_clients.remove(me)
            return
        if fresh.tenant_id != me.tenant_id:
            logger.info("link WS tenant re-key: %s -> %s", me.tenant_id, fresh.tenant_id)
            me.tenant_id = fresh.tenant_id


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
        # DEC-⑱-③ 连接审计：被拒尝试是安全事件本体必留痕（cause=invalid_token；
        # 匿名缺凭据=契约放行非拒绝面）。凭据材料只字不进事件（has_token 只记
        # 在场与否）。被拒无派生身份→tenant='default' 兜底（③b-2 旁路纪律）。
        _audit(
            "link_ws_rejected",
            "🛡 Link WS 握手拒绝（无效凭据）",
            {
                "face": "link_ws",
                "client": _ws_client_host(ws),
                "has_token": bool(_ws_presented_token(ws)),
                "cause": "invalid_token",
            },
        )
        await ws.close(code=1008, reason="invalid token")
        return
    tenant = identity.tenant_id

    await ws.accept()
    me = WsSubscriber(ws=ws, tenant_id=tenant)
    _ws_clients.append(me)
    logger.info("WS client connected (%d active)", len(_ws_clients))

    # DEC-⑱-③ 连接审计：connected=会话真建立（accept+订阅入表）；user/tenant=
    # bind_identity 派生单一源；session_started 供 closed 事件算时长。
    session_started = time.time()
    _audit(
        "link_ws_connected",
        f"🔗 Link WS 连接建立: user={identity.user_id}",
        {
            "face": "link_ws",
            "user_id": str(identity.user_id),
            "authed": bool(identity.authed),
            "client": _ws_client_host(ws),
        },
        tenant_id=identity.tenant_id,
    )

    # 连接期凭据重校验（F3b-⑨b-③-1c-⑤）：持 token 的连接起看门狗周期重跑
    # bind_identity——凭据失效=close 1008（与握手拒绝同语义，禁凭失效凭据
    # 继续收发），派生租户变化=me.tenant_id re-key（消息循环每条消息取最新
    # 键，路由/事件归属随之）。匿名连接无凭据可失效，不看门狗，行为字节不变。
    ws_token = _ws_presented_token(ws)
    watchdog = asyncio.create_task(_ws_token_watchdog(ws, me, ws_token)) if ws_token else None

    # Notify via event bus
    _record_event(
        source="ws-client",
        direction="inbound",
        event_type="ws_connected",
        payload={"total_clients": len(_ws_clients)},
        tenant_id=tenant,
    )

    # DEC-⑱-③ 会话结束归因缺省（正常收口=客户端断开；异常收口在 except 改写）。
    end_reason = "client_disconnect"
    try:
        while True:
            data = await ws.receive_text()
            tenant = me.tenant_id  # 重校验 re-key 生效点：每条消息取最新派生键
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
        end_reason = "error"
        logger.debug("WS error: %s", e)
    finally:
        # DEC-⑱-③ 会话结束归因（同步口置于任何 await 之前=取消免疫，DEC-⑱ 教训）：
        # 看门狗正常返回的唯一路径=凭据失效撤销（exception() 非空=看门狗自身崩溃，
        # 如实不算撤销），其余按收口路径归因 error / client_disconnect。
        revoked = (
            watchdog is not None
            and watchdog.done()
            and not watchdog.cancelled()
            and watchdog.exception() is None
        )
        if revoked:
            end_reason = "credential_revoked"
        _audit(
            "link_ws_closed",
            f"🔗 Link WS 会话结束（{end_reason}）: user={identity.user_id}",
            {
                "face": "link_ws",
                "user_id": str(identity.user_id),
                "authed": bool(identity.authed),
                "client": _ws_client_host(ws),
                "reason": end_reason,
                "duration_s": round(time.time() - session_started, 1),
            },
            tenant_id=me.tenant_id,
        )
        if watchdog is not None:
            watchdog.cancel()
        for sub in list(_ws_clients):
            if sub.ws is ws:
                _ws_clients.remove(sub)
        logger.info("WS client disconnected (%d active)", len(_ws_clients))


# ── Gateway Stats ───────────────────────────────────────────


@router.get("/gateway/stats")
async def gateway_stats():
    """Get Link Gateway statistics.

    路径归属（F3b-⑨b-③-1c-⑥）：/api/link/stats 归 OpenLink，本面迁
    /api/link/gateway/stats（修前被同前缀先挂的 link_router 遮蔽=生产死路由）。"""
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


@router.get("/gateway/health")
async def gateway_health():
    """Link Gateway health check.

    路径归属（F3b-⑨b-③-1c-⑥）：/api/link/health 归 OpenLink，本面迁
    /api/link/gateway/health（修前被同前缀先挂的 link_router 遮蔽=生产死路由）。"""
    return {
        "status": "ok",
        "component": "LinkGateway",
        "webhooks": len(_webhooks),
        "systems": len(_systems),
        "ws_clients": len(_ws_clients),
    }
