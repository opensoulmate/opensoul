from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api._errors import stats_error
from src.database.postgres import db_pool
from src.middleware.auth import get_current_user

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "component": "OpenWorkflow"}


@router.get("/stats")
async def workflow_stats():
    """Get workflow statistics."""
    try:
        total = await db_pool.fetchval("SELECT COUNT(*) FROM workflow_tasks") or 0
        active = (
            await db_pool.fetchval("SELECT COUNT(*) FROM workflow_tasks WHERE status = 'active'")
            or 0
        )
        by_type = await db_pool.fetch(
            "SELECT task_type, COUNT(*) as cnt FROM workflow_tasks GROUP BY task_type ORDER BY cnt DESC"
        )
        return {
            "status": "ok",
            "component": "OpenWorkflow",
            "total_tasks": total,
            "active_tasks": active,
            "by_type": {r["task_type"]: r["cnt"] for r in (by_type or [])},
        }
    except Exception as exc:
        # C2统一错误响应：失败说失败、说原因，禁谎报ok（假0冒充"没数据"）
        return stats_error("OpenWorkflow", exc, total_tasks=0, active_tasks=0, by_type={})


# ── Models ─────────────────────────────────────────────────────────────


class WorkflowTaskCreate(BaseModel):
    name: str
    description: str = ""
    task_type: str = "manual"
    config: dict = Field(default_factory=dict)
    schedule: str | None = None  # cron expression, optional


class WorkflowTaskResponse(BaseModel):
    id: UUID
    name: str
    description: str
    task_type: str
    config: dict
    schedule: str | None
    status: str
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime


# ── Endpoints ──────────────────────────────────────────────────────────


@router.get("/tasks")
async def list_tasks(
    status: str | None = None,
    current_user: dict = Depends(get_current_user),
):
    """List workflow tasks, optionally filtered by status."""
    if status:
        rows = await db_pool.fetch(
            "SELECT id, name, description, task_type, config, schedule, status, "
            "last_run_at, next_run_at, created_at "
            "FROM workflow_tasks WHERE user_id = $1 AND status = $2 ORDER BY created_at DESC",
            current_user["id"],
            status,
        )
    else:
        rows = await db_pool.fetch(
            "SELECT id, name, description, task_type, config, schedule, status, "
            "last_run_at, next_run_at, created_at "
            "FROM workflow_tasks WHERE user_id = $1 ORDER BY created_at DESC",
            current_user["id"],
        )
    return [dict(r) for r in rows]


@router.post("/tasks", status_code=201)
async def create_task(
    req: WorkflowTaskCreate,
    current_user: dict = Depends(get_current_user),
):
    """Create a new workflow task."""
    task_id = uuid4()
    row = await db_pool.fetchrow(
        "INSERT INTO workflow_tasks (id, user_id, name, description, task_type, config, schedule, status) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, 'idle') "
        "RETURNING id, name, description, task_type, config, schedule, status, "
        "last_run_at, next_run_at, created_at",
        task_id,
        current_user["id"],
        req.name,
        req.description,
        req.task_type,
        req.config,
        req.schedule,
    )
    if not row:
        raise HTTPException(status_code=500, detail="Failed to create task")
    return dict(row)


@router.post("/tasks/{task_id}/run")
async def run_task(
    task_id: UUID,
    current_user: dict = Depends(get_current_user),
):
    """Manually trigger a workflow task."""
    row = await db_pool.fetchrow(
        "SELECT id, status FROM workflow_tasks WHERE id = $1 AND user_id = $2",
        task_id,
        current_user["id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    if row["status"] == "running":
        raise HTTPException(status_code=409, detail="Task is already running")
    await db_pool.execute(
        "UPDATE workflow_tasks SET status = 'running', last_run_at = NOW() WHERE id = $1",
        task_id,
    )
    return {
        "task_id": str(task_id),
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
    }


@router.post("/tasks/{task_id}/pause")
async def pause_task(
    task_id: UUID,
    current_user: dict = Depends(get_current_user),
):
    """Pause a running or idle task."""
    row = await db_pool.fetchrow(
        "SELECT id, status FROM workflow_tasks WHERE id = $1 AND user_id = $2",
        task_id,
        current_user["id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    if row["status"] not in ("running", "idle"):
        raise HTTPException(status_code=409, detail=f"Cannot pause task in '{row['status']}' state")
    await db_pool.execute(
        "UPDATE workflow_tasks SET status = 'paused' WHERE id = $1",
        task_id,
    )
    return {"task_id": str(task_id), "status": "paused"}


@router.post("/tasks/{task_id}/resume")
async def resume_task(
    task_id: UUID,
    current_user: dict = Depends(get_current_user),
):
    """Resume a paused task."""
    row = await db_pool.fetchrow(
        "SELECT id, status FROM workflow_tasks WHERE id = $1 AND user_id = $2",
        task_id,
        current_user["id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    if row["status"] != "paused":
        raise HTTPException(
            status_code=409, detail=f"Cannot resume task in '{row['status']}' state"
        )
    await db_pool.execute(
        "UPDATE workflow_tasks SET status = 'idle' WHERE id = $1",
        task_id,
    )
    return {"task_id": str(task_id), "status": "idle"}


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: UUID,
    current_user: dict = Depends(get_current_user),
):
    """Delete a workflow task."""
    result = await db_pool.execute(
        "DELETE FROM workflow_tasks WHERE id = $1 AND user_id = $2",
        task_id,
        current_user["id"],
    )
    if "DELETE 0" in result:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": "deleted", "task_id": str(task_id)}
