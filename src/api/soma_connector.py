"""OpenSoma Connector API — 标准化外部组件注册接口.

Provides a unified registration, heartbeat, and data-push interface for
external components (OpenSoma, custom collectors, etc.) to self-register
with OpenSoul and push collected data into the knowledge base.

This is the key enabler for the "松耦合、即插即用" architecture:
any component that implements this API contract can plug into the ecosystem.
"""

import hashlib
import logging
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from src.nerve.event_bridge import push_event

router = APIRouter()
logger = logging.getLogger(__name__)


# ── In-Memory Component Registry ────────────────────────────────────


class ComponentRegistry:
    """Registry for externally-connected components."""

    def __init__(self, offline_timeout: int = 120):
        self._components: dict[str, dict[str, Any]] = {}
        self._tokens: dict[str, str] = {}  # component_id -> secret token
        self._offline_timeout = offline_timeout

    def register(
        self,
        component_id: str,
        name: str,
        component_type: str = "collector",
        version: str = "0.0.0",
        capabilities: list[str] | None = None,
        metadata: dict | None = None,
        secret_token: str | None = None,
    ) -> dict[str, Any]:
        """Register or update a component."""
        now = datetime.now(UTC).isoformat()
        token = (
            secret_token
            or hashlib.sha256(f"{component_id}:{time.time()}".encode()).hexdigest()[:32]
        )

        if component_id in self._components:
            # Update existing registration
            comp = self._components[component_id]
            comp["name"] = name
            comp["component_type"] = component_type
            comp["version"] = version
            comp["capabilities"] = capabilities or []
            comp["metadata"] = metadata or {}
            comp["last_heartbeat"] = now
            comp["status"] = "online"
        else:
            comp = {
                "component_id": component_id,
                "name": name,
                "component_type": component_type,
                "version": version,
                "capabilities": capabilities or [],
                "metadata": metadata or {},
                "status": "online",
                "registered_at": now,
                "last_heartbeat": now,
                "data_push_count": 0,
                "error_count": 0,
                "last_error": None,
            }
            self._components[component_id] = comp

        self._tokens[component_id] = token
        return {**comp, "secret_token": token}

    def heartbeat(self, component_id: str) -> bool:
        if component_id not in self._components:
            return False
        self._components[component_id]["last_heartbeat"] = datetime.now(UTC).isoformat()
        self._components[component_id]["status"] = "online"
        return True

    def push_data(self, component_id: str, data_type: str, payload: dict) -> dict[str, Any]:
        """Record a data push from a component."""
        if component_id not in self._components:
            return {"success": False, "error": "Component not registered"}

        comp = self._components[component_id]
        comp["data_push_count"] = comp.get("data_push_count", 0) + 1

        return {
            "success": True,
            "received_at": datetime.now(UTC).isoformat(),
            "data_type": data_type,
            "component_id": component_id,
        }

    def mark_error(self, component_id: str, error: str):
        if component_id in self._components:
            comp = self._components[component_id]
            comp["error_count"] = comp.get("error_count", 0) + 1
            comp["last_error"] = error

    def unregister(self, component_id: str) -> bool:
        if component_id not in self._components:
            return False
        del self._components[component_id]
        self._tokens.pop(component_id, None)
        return True

    def get(self, component_id: str) -> dict[str, Any] | None:
        return self._components.get(component_id)

    def list_components(
        self,
        component_type: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        comps = list(self._components.values())
        if component_type:
            comps = [c for c in comps if c["component_type"] == component_type]
        if status:
            comps = [c for c in comps if c["status"] == status]
        return comps

    def check_offline(self):
        """Mark components as offline if heartbeat expired."""
        now = time.time()
        for comp in self._components.values():
            try:
                last = datetime.fromisoformat(comp["last_heartbeat"]).timestamp()
                if now - last > self._offline_timeout:
                    comp["status"] = "offline"
            except (ValueError, KeyError):
                pass

    def stats(self) -> dict:
        self.check_offline()
        statuses = {}
        types = {}
        for c in self._components.values():
            statuses[c["status"]] = statuses.get(c["status"], 0) + 1
            types[c["component_type"]] = types.get(c["component_type"], 0) + 1
        return {
            "total": len(self._components),
            "online": statuses.get("online", 0),
            "offline": statuses.get("offline", 0),
            "by_type": types,
        }


registry = ComponentRegistry()


# ── Request Schemas ─────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    component_id: str
    name: str
    component_type: str = "collector"  # collector, processor, connector, agent, custom
    version: str = "0.0.0"
    capabilities: list[str] = []
    metadata: dict = {}
    secret_token: str | None = None


class HeartbeatRequest(BaseModel):
    component_id: str
    status: str = "ok"
    metrics: dict = {}


class DataPushRequest(BaseModel):
    data_type: str  # document, event, metric, log, custom
    payload: dict
    source: str = ""
    tags: list[str] = []


class StatusUpdateRequest(BaseModel):
    status: str  # online, busy, error, maintenance
    message: str = ""


# ── Helper ──────────────────────────────────────────────────────────


def _verify_token(component_id: str, token: str | None) -> bool:
    """Verify component token (optional — lenient for dev)."""
    if not token:
        return True  # Allow unauthenticated in dev
    stored = registry._tokens.get(component_id)
    return stored is None or stored == token


# ── Registration Endpoints ──────────────────────────────────────────


@router.post("/register")
async def register_component(req: RegisterRequest):
    """Register an external component with OpenSoul.

    This is the primary entry point for the "即插即用" architecture.
    External components call this on startup to announce themselves.
    """
    result = registry.register(
        component_id=req.component_id,
        name=req.name,
        component_type=req.component_type,
        version=req.version,
        capabilities=req.capabilities,
        metadata=req.metadata,
        secret_token=req.secret_token,
    )

    # 系统事件归属（DEC-① 1b 裁决＝维持 'default' 兜底，本文件 4 处 push_event
    # 同语义）：组件是系统资源（component token 自有鉴权轴，非用户 JWT），事件
    # 刻意不带 tenant_id（record() 落 'default'）；Authorization 头不改变归属。
    push_event(
        {
            "organ": "soma",
            "emoji": "🤖",
            "type": "component_registered",
            "summary": f"🔌 Component registered: {req.name} ({req.component_type})",
            "detail": {
                "component_id": req.component_id,
                "name": req.name,
                "type": req.component_type,
                "version": req.version,
                "capabilities": req.capabilities,
            },
        }
    )

    return {
        "component_id": result["component_id"],
        "name": result["name"],
        "status": result["status"],
        "secret_token": result["secret_token"],
        "registered_at": result["registered_at"],
    }


@router.post("/heartbeat")
async def component_heartbeat(
    req: HeartbeatRequest,
    x_component_token: str | None = Header(default=None),
):
    """Send heartbeat from a registered component.

    Components should call this periodically (e.g., every 30s) to signal liveness.
    """
    if not _verify_token(req.component_id, x_component_token):
        raise HTTPException(401, "Invalid component token")

    if not registry.heartbeat(req.component_id):
        raise HTTPException(
            404, f"Component '{req.component_id}' not registered. Call /register first."
        )

    return {
        "component_id": req.component_id,
        "status": "acknowledged",
        "server_time": datetime.now(UTC).isoformat(),
    }


@router.post("/push")
async def push_data(
    req: DataPushRequest,
    x_component_id: str | None = Header(default=None),
    x_component_token: str | None = Header(default=None),
):
    """Push collected data from an external component into OpenSoul.

    External components use this to send documents, events, metrics, or
    any collected data into the central brain for processing.
    """
    component_id = x_component_id
    if not component_id:
        raise HTTPException(400, "X-Component-Id header required")

    if not _verify_token(component_id, x_component_token):
        raise HTTPException(401, "Invalid component token")

    result = registry.push_data(component_id, req.data_type, req.payload)
    if not result["success"]:
        raise HTTPException(404, result["error"])

    # Emit event for the activity feed
    push_event(
        {
            "organ": "soma",
            "emoji": "🤖",
            "type": "data_pushed",
            "summary": f"📥 Data pushed from {component_id}: {req.data_type}",
            "detail": {
                "component_id": component_id,
                "data_type": req.data_type,
                "source": req.source,
                "tags": req.tags,
                "payload_keys": list(req.payload.keys())[:10],
            },
        }
    )

    return result


# ── Status & Discovery ─────────────────────────────────────────────


@router.get("/components")
async def list_components(
    type: str = Query(default=None, alias="type"),
    status: str = Query(default=None),
):
    """List all registered external components."""
    return {"components": registry.list_components(component_type=type, status=status)}


@router.get("/components/{component_id}")
async def get_component(component_id: str):
    """Get details of a specific registered component."""
    comp = registry.get(component_id)
    if not comp:
        raise HTTPException(404, f"Component '{component_id}' not found")
    return comp


@router.patch("/components/{component_id}/status")
async def update_status(
    component_id: str,
    req: StatusUpdateRequest,
    x_component_token: str | None = Header(default=None),
):
    """Update component status (online, busy, error, maintenance)."""
    if not _verify_token(component_id, x_component_token):
        raise HTTPException(401, "Invalid component token")

    comp = registry.get(component_id)
    if not comp:
        raise HTTPException(404, f"Component '{component_id}' not found")

    comp["status"] = req.status
    if req.message:
        comp["metadata"]["status_message"] = req.message

    return {"component_id": component_id, "status": req.status}


@router.delete("/components/{component_id}")
async def unregister_component(component_id: str):
    """Unregister a component."""
    if not registry.unregister(component_id):
        raise HTTPException(404, f"Component '{component_id}' not found")

    push_event(
        {
            "organ": "soma",
            "emoji": "🤖",
            "type": "component_unregistered",
            "summary": f"🔌 Component unregistered: {component_id}",
            "detail": {"component_id": component_id},
        }
    )

    return {"status": "unregistered", "component_id": component_id}


@router.post("/error")
async def report_error(
    component_id: str = Query(...),
    error: str = Query(...),
):
    """Report an error from a component."""
    registry.mark_error(component_id, error)
    push_event(
        {
            "organ": "soma",
            "emoji": "🤖",
            "type": "component_error",
            "summary": f"⚠️ Component error: {component_id} — {error[:100]}",
            "detail": {"component_id": component_id, "error": error},
        }
    )
    return {"acknowledged": True}


# ── Discovery: What can this component connect to? ──────────────────


@router.get("/capabilities")
async def get_platform_capabilities():
    """Return platform capabilities for component auto-configuration.

    External components call this to discover what the platform supports,
    so they can auto-configure their behavior.
    """
    return {
        "platform": "OpenSoul",
        "version": "0.1.0",
        "capabilities": {
            "knowledge_base": True,
            "rag_search": True,
            "vector_db": "qdrant",
            "fulltext_search": "meilisearch",
            "event_bus": True,
            "file_storage": True,
            "ocr": True,
            "asr": True,
            "llm_gateway": True,
            "workflow_engine": True,
            "backup_restore": True,
            "multi_tenant": True,
        },
        "api_version": "1.0",
        "endpoints": {
            "register": "/api/soma/register",
            "heartbeat": "/api/soma/heartbeat",
            "push_data": "/api/soma/push",
            "knowledge": "/api/knowledge/",
            "search": "/api/search/",
            "events": "/api/nerve/events",
        },
    }


# ── Health ──────────────────────────────────────────────────────────


@router.get("/health")
async def soma_health():
    """OpenSoma connector health check."""
    return {
        "status": "ok",
        "component": "OpenSomaConnector",
        "registry": registry.stats(),
    }


@router.get("/stats")
async def soma_stats():
    """Get Soma connector statistics."""
    return {
        "status": "ok",
        "component": "OpenSomaConnector",
        **registry.stats(),
    }
