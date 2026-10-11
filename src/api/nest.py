"""OpenNest API — 细胞巢穴：多租户隔离、资源配额、向量空间逻辑隔离。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.nest.isolation import IsolationEngine, ResourceType
from src.nest.tenant import TenantManager

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
manager = TenantManager()
isolation = IsolationEngine()
# F3b-①：'default' 租户补位（幂等）——生产数据 tenant_id 全='default'，管理面真源
# 必须登记该租户（修前 access-check default → 404 Tenant not found，真源与数据脱节）。
manager.ensure_default_tenant()


# ── Request Schemas ────────────────────────────────────────


class TenantCreateRequest(BaseModel):
    name: str
    tier: str = "free"
    owner_user_id: str = ""
    description: str = ""
    tags: list[str] = []
    config: dict = {}
    custom_quota: dict | None = None


class TenantUpdateRequest(BaseModel):
    name: str | None = None
    tier: str | None = None
    status: str | None = None
    owner_user_id: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    config: dict | None = None
    custom_quota: dict | None = None


class UsageRecordRequest(BaseModel):
    storage_delta: int = 0
    document_delta: int = 0
    api_calls: int = 0
    tokens: int = 0


class QuotaCheckRequest(BaseModel):
    resource: str
    amount: int = 1


class PolicyUpdateRequest(BaseModel):
    namespace_scoped: bool | None = None
    cross_tenant_allowed: bool | None = None
    encryption_enabled: bool | None = None
    audit_access: bool | None = None


class AccessCheckRequest(BaseModel):
    resource_type: str
    resource_id: str
    action: str = "read"


# ── Tenant CRUD ────────────────────────────────────────────


@router.post("/tenants")
async def create_tenant(req: TenantCreateRequest):
    """Create a new isolated tenant."""
    tenant = manager.create(
        name=req.name,
        tier=req.tier,
        owner_user_id=req.owner_user_id,
        description=req.description,
        tags=req.tags,
        config=req.config,
        custom_quota=req.custom_quota,
    )
    return {
        "tenant_id": tenant.tenant_id,
        "name": tenant.name,
        "tier": tenant.tier.value,
        "namespace": tenant.namespace,
        "status": tenant.status.value,
        "quota": tenant.quota.to_dict(),
    }


@router.get("/tenants")
async def list_tenants(
    tier: str = Query(default=None),
    status: str = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """List all tenants."""
    return {"tenants": manager.list_tenants(tier=tier, status=status, limit=limit, offset=offset)}


@router.get("/tenants/{tenant_id}")
async def get_tenant(tenant_id: str):
    """Get tenant details with usage stats."""
    tenant = manager.get(tenant_id)
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    return tenant.to_dict()


@router.patch("/tenants/{tenant_id}")
async def update_tenant(tenant_id: str, req: TenantUpdateRequest):
    """Update tenant configuration."""
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not manager.update(tenant_id, **updates):
        raise HTTPException(404, "Tenant not found")
    return {"message": "updated", "tenant_id": tenant_id}


@router.delete("/tenants/{tenant_id}")
async def delete_tenant(tenant_id: str):
    """Delete a tenant and all its isolated resources."""
    if not manager.delete(tenant_id):
        raise HTTPException(404, "Tenant not found")
    return {"message": "deleted", "tenant_id": tenant_id}


# ── Tenant Actions ─────────────────────────────────────────


@router.post("/tenants/{tenant_id}/suspend")
async def suspend_tenant(tenant_id: str, reason: str = Query(default="")):
    """Suspend a tenant."""
    if not manager.suspend(tenant_id, reason):
        raise HTTPException(404, "Tenant not found")
    return {"message": "suspended", "tenant_id": tenant_id}


@router.post("/tenants/{tenant_id}/reactivate")
async def reactivate_tenant(tenant_id: str):
    """Reactivate a suspended tenant."""
    if not manager.reactivate(tenant_id):
        raise HTTPException(404, "Tenant not found")
    return {"message": "reactivated", "tenant_id": tenant_id}


# ── Resource Quotas ────────────────────────────────────────


@router.get("/tenants/{tenant_id}/quota")
async def get_quota(tenant_id: str):
    """Get tenant resource quota and current usage."""
    tenant = manager.get(tenant_id)
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    return {
        "tenant_id": tenant_id,
        "quota": tenant.quota.to_dict(),
        "usage": tenant.usage.to_dict(),
        "usage_percent": tenant._usage_percent(),
    }


@router.post("/tenants/{tenant_id}/quota/check")
async def check_quota(tenant_id: str, req: QuotaCheckRequest):
    """Check if tenant has quota for a specific resource."""
    result = manager.check_quota(tenant_id, req.resource, req.amount)
    return result


@router.post("/tenants/{tenant_id}/usage")
async def record_usage(tenant_id: str, req: UsageRecordRequest):
    """Record resource usage for a tenant."""
    if not manager.record_usage(
        tenant_id, req.storage_delta, req.document_delta, req.api_calls, req.tokens
    ):
        raise HTTPException(404, "Tenant not found")
    return {"message": "recorded"}


# ── Isolation Engine ───────────────────────────────────────


@router.post("/tenants/{tenant_id}/access-check")
async def check_access(tenant_id: str, req: AccessCheckRequest):
    """Check if a tenant can access a specific resource."""
    tenant = manager.get(tenant_id)
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    try:
        rt = ResourceType(req.resource_type)
    except ValueError:
        raise HTTPException(
            400,
            f"Invalid resource type: {req.resource_type}. Valid: {[r.value for r in ResourceType]}",
        )
    return isolation.check_access(
        tenant_namespace=tenant.namespace,
        resource_type=rt,
        resource_id=req.resource_id,
        action=req.action,
    )


@router.get("/policies")
async def list_policies():
    """List all isolation policies."""
    return {"policies": isolation.list_policies()}


@router.get("/policies/{resource_type}")
async def get_policy(resource_type: str):
    """Get isolation policy for a resource type."""
    result = isolation.get_policy(resource_type)
    if not result:
        raise HTTPException(404, f"Unknown resource type: {resource_type}")
    return result


@router.put("/policies/{resource_type}")
async def update_policy(resource_type: str, req: PolicyUpdateRequest):
    """Update isolation policy for a resource type."""
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not isolation.set_policy(resource_type, **updates):
        raise HTTPException(400, f"Invalid resource type: {resource_type}")
    return {"message": "policy updated", "resource_type": resource_type}


@router.get("/audit")
async def get_audit_log(
    tenant_id: str = Query(default=None),
    action: str = Query(default=None),
    allowed: bool = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
):
    """Get access audit log."""
    return {
        "entries": isolation.get_access_log(
            tenant_id=tenant_id, action=action, allowed=allowed, limit=limit
        )
    }


# ── Health / Stats ─────────────────────────────────────────


@router.get("/health")
async def nest_health():
    """OpenNest health check."""
    return {
        "status": "ok",
        "component": "OpenNest",
        "tenants": manager.stats(),
        "isolation": isolation.stats(),
    }


@router.get("/stats")
async def nest_stats():
    """Get overall OpenNest statistics."""
    return {
        "tenants": manager.stats(),
        "isolation": isolation.stats(),
    }
