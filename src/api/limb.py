"""OpenLimb API — 四肢：RPA执行器、浏览器自动化、表单填报。"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from src.limb.executor import RPAExecutor
from src.limb.tasks import TemplateTenantDenied
from src.services.tenant_binding import bind_identity

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
executor = RPAExecutor()


# ── Request Schemas ────────────────────────────────────────


class TaskCreateRequest(BaseModel):
    name: str
    actions: list[dict]
    priority: int = 5
    tags: list[str] = []
    variables: dict = {}


class TemplateCreateRequest(BaseModel):
    name: str
    description: str = ""
    category: str = "custom"
    actions: list[dict] = []
    variables: list[dict] = []
    tags: list[str] = []


class TemplateInstantiateRequest(BaseModel):
    variables: dict = {}
    name: str = ""


# ── Task Endpoints ─────────────────────────────────────────


@router.post("/tasks")
async def create_task(req: TaskCreateRequest, authorization: str | None = Header(default=None)):
    """Create a new RPA task."""
    identity = bind_identity(authorization)
    task = executor.create_task(
        name=req.name,
        actions=req.actions,
        priority=req.priority,
        tags=req.tags,
        variables=req.variables,
        tenant_id=identity.tenant_id,
    )
    return task.to_dict()


@router.get("/tasks")
async def list_tasks(
    status: str = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    authorization: str | None = Header(default=None),
):
    """List RPA tasks."""
    identity = bind_identity(authorization)
    return {"tasks": executor.list_tasks(status=status, limit=limit, tenant_id=identity.tenant_id)}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, authorization: str | None = Header(default=None)):
    """Get task details with results."""
    identity = bind_identity(authorization)
    task = executor.get_task(task_id, tenant_id=identity.tenant_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task.to_dict(include_results=True)


@router.post("/tasks/{task_id}/execute")
async def execute_task(task_id: str, authorization: str | None = Header(default=None)):
    """Execute a queued task."""
    identity = bind_identity(authorization)
    task = executor.execute_task(task_id, tenant_id=identity.tenant_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task.to_dict(include_results=True)


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, authorization: str | None = Header(default=None)):
    """Cancel a task."""
    identity = bind_identity(authorization)
    if not executor.cancel_task(task_id, tenant_id=identity.tenant_id):
        raise HTTPException(400, "Task cannot be cancelled")
    return {"message": "cancelled", "task_id": task_id}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, authorization: str | None = Header(default=None)):
    """Delete a task."""
    identity = bind_identity(authorization)
    if not executor.delete_task(task_id, tenant_id=identity.tenant_id):
        raise HTTPException(400, "Cannot delete running task")
    return {"message": "deleted", "task_id": task_id}


# ── Template Endpoints ─────────────────────────────────────


@router.get("/templates")
async def list_templates(
    category: str = Query(default=None),
    authorization: str | None = Header(default=None),
):
    """List available RPA task templates."""
    identity = bind_identity(authorization)
    return {"templates": executor.list_templates(category=category, tenant_id=identity.tenant_id)}


@router.get("/templates/{template_id}")
async def get_template(template_id: str, authorization: str | None = Header(default=None)):
    """Get template details."""
    identity = bind_identity(authorization)
    template = executor.get_template(template_id, tenant_id=identity.tenant_id)
    if not template:
        raise HTTPException(404, "Template not found")
    return template.to_dict()


@router.post("/templates")
async def create_template(
    req: TemplateCreateRequest, authorization: str | None = Header(default=None)
):
    """Create a custom template."""
    identity = bind_identity(authorization)
    try:
        template = executor.create_template(req.model_dump(), tenant_id=identity.tenant_id)
    except TemplateTenantDenied as e:
        raise HTTPException(403, str(e))
    return template.to_dict()


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str, authorization: str | None = Header(default=None)):
    """Delete a custom template."""
    identity = bind_identity(authorization)
    if not executor.delete_template(template_id, tenant_id=identity.tenant_id):
        raise HTTPException(400, "Cannot delete built-in template")
    return {"message": "deleted"}


@router.post("/templates/{template_id}/instantiate")
async def instantiate_template(
    template_id: str,
    req: TemplateInstantiateRequest,
    authorization: str | None = Header(default=None),
):
    """Create a task from a template."""
    identity = bind_identity(authorization)
    task = executor.create_from_template(
        template_id, req.variables, req.name, tenant_id=identity.tenant_id
    )
    if not task:
        raise HTTPException(404, "Template not found")
    return task.to_dict()


# ── History ─────────────────────────────────────────────────


@router.get("/history")
async def get_history(
    limit: int = Query(default=50), authorization: str | None = Header(default=None)
):
    """Get task execution history."""
    identity = bind_identity(authorization)
    return {"history": executor.get_history(limit=limit, tenant_id=identity.tenant_id)}


# ── Health / Stats ─────────────────────────────────────────


@router.get("/health")
async def limb_health():
    """OpenLimb health check."""
    return {
        "status": "ok",
        "component": "OpenLimb",
        **executor.stats(),
    }


@router.get("/stats")
async def limb_stats(authorization: str | None = Header(default=None)):
    """Get OpenLimb statistics."""
    identity = bind_identity(authorization)
    return executor.stats(tenant_id=identity.tenant_id)
