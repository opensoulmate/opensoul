"""OpenMarrow API — 骨髓系统：备份恢复、数据迁移、定时备份。"""

import logging
import os

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

from src.marrow.backup import BackupManager, BackupScheduler
from src.marrow.migrator import DataMigrator
from src.nerve.event_bridge import push_event
from src.services.tenant_binding import bind_identity

logger = logging.getLogger(__name__)
logger = logging.getLogger(__name__)
router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
backup_manager = BackupManager()
scheduler = BackupScheduler(backup_manager)
migrator = DataMigrator()

# Start the scheduler background thread
scheduler.start()


# ── Request Schemas ────────────────────────────────────────


class BackupCreateRequest(BaseModel):
    source_dirs: list[str]
    name: str = ""
    description: str = ""
    tags: list[str] = []


class RestoreRequest(BaseModel):
    backup_id: str
    target_dir: str


class ExportRequest(BaseModel):
    data: list[dict]
    format: str = "json"  # "json", "jsonl", "csv"
    name: str = "export"


class ScheduleCreateRequest(BaseModel):
    name: str
    source_dirs: list[str]
    interval: str = "daily"  # "hourly", "daily", "weekly"
    interval_seconds: int | None = None  # custom interval
    description: str = ""
    tags: list[str] = []


class ScheduleToggleRequest(BaseModel):
    enabled: bool


# ── Backup Endpoints ───────────────────────────────────────


@router.post("/backup")
async def create_backup(
    req: BackupCreateRequest,
    authorization: str | None = Header(None),
):
    """Create a backup snapshot of specified directories."""
    identity = bind_identity(authorization)
    # Validate paths exist
    valid_dirs = []
    for d in req.source_dirs:
        expanded = os.path.expanduser(d)
        if os.path.exists(expanded):
            valid_dirs.append(expanded)
    if not valid_dirs:
        raise HTTPException(400, "No valid source directories found")

    manifest = backup_manager.create_backup(
        source_dirs=valid_dirs,
        name=req.name,
        description=req.description,
        tags=req.tags,
    )

    push_event(
        {
            "organ": "marrow",
            "emoji": "🦴",
            "type": "backup_created",
            "summary": f"💾 Backup created: {manifest.name} ({manifest.file_count} files)",
            "detail": {
                "backup_id": manifest.backup_id,
                "name": manifest.name,
                "file_count": manifest.file_count,
            },
        },
        tenant_id=identity.tenant_id,
    )
    # Push notification for backup completion
    try:
        from src.api.notifications import push_notification

        push_notification(
            source="marrow",
            title=f"💾 Backup Complete: {manifest.name}",
            body=f"{manifest.file_count} files, {manifest.size_bytes} bytes",
            level="success",
            organ="marrow",
            emoji="🦴",
            action_url="/marrow",
            metadata={
                "backup_id": manifest.backup_id,
                "file_count": manifest.file_count,
                "size_bytes": manifest.size_bytes,
            },
        )
    except Exception as exc:
        logging.getLogger(__name__).debug("probe skipped: %s", exc)

    return {
        "backup_id": manifest.backup_id,
        "name": manifest.name,
        "size_bytes": manifest.size_bytes,
        "file_count": manifest.file_count,
        "checksum": manifest.checksum,
    }


@router.get("/backups")
async def list_backups():
    """List all backups."""
    return {"backups": backup_manager.list_backups()}


@router.get("/backups/{backup_id}")
async def get_backup(backup_id: str):
    """Get backup details."""
    result = backup_manager.get_backup(backup_id)
    if not result:
        raise HTTPException(404, "Backup not found")
    return result


@router.delete("/backups/{backup_id}")
async def delete_backup(backup_id: str):
    """Delete a backup."""
    result = backup_manager.delete_backup(backup_id)
    if not result["success"]:
        raise HTTPException(404, result["error"])
    return result


@router.post("/restore/{backup_id}")
async def restore_backup(
    backup_id: str,
    req: RestoreRequest | None = None,
    authorization: str | None = Header(None),
):
    """Restore a backup to target directory."""
    identity = bind_identity(authorization)
    target = (req.target_dir if req else None) or os.path.expanduser("~/.opensoul/restore")
    result = backup_manager.restore_backup(backup_id, target)
    if not result["success"]:
        raise HTTPException(400, result["error"])
    push_event(
        {
            "organ": "marrow",
            "emoji": "🦴",
            "type": "backup_restored",
            "summary": f"♻️ Backup restored: {backup_id}",
            "detail": {"backup_id": backup_id, "target_dir": target},
        },
        tenant_id=identity.tenant_id,
    )
    return result


# ── Scheduled Backup Endpoints ─────────────────────────────


@router.post("/schedules")
async def create_schedule(
    req: ScheduleCreateRequest,
    authorization: str | None = Header(None),
):
    """Create a scheduled backup job."""
    identity = bind_identity(authorization)
    # Validate interval
    valid_intervals = list(BackupScheduler.INTERVALS.keys())
    if req.interval not in valid_intervals and not req.interval_seconds:
        raise HTTPException(
            400, f"Invalid interval. Choose from: {valid_intervals} or provide interval_seconds"
        )

    # Validate source dirs
    valid_dirs = []
    for d in req.source_dirs:
        expanded = os.path.expanduser(d)
        if os.path.exists(expanded):
            valid_dirs.append(expanded)
    if not valid_dirs:
        raise HTTPException(400, "No valid source directories found")

    schedule = scheduler.create_schedule(
        name=req.name,
        source_dirs=valid_dirs,
        interval=req.interval,
        interval_seconds=req.interval_seconds,
        description=req.description,
        tags=req.tags,
    )

    push_event(
        {
            "organ": "marrow",
            "emoji": "🦴",
            "type": "schedule_created",
            "summary": f"⏰ Backup schedule created: {schedule.name} ({schedule.cron_expr})",
            "detail": {
                "schedule_id": schedule.schedule_id,
                "name": schedule.name,
                "interval": schedule.cron_expr,
            },
        },
        tenant_id=identity.tenant_id,
    )

    return {
        "schedule_id": schedule.schedule_id,
        "name": schedule.name,
        "cron_expr": schedule.cron_expr,
        "interval_seconds": schedule.interval_seconds,
        "next_run_at": schedule.next_run_at,
        "enabled": schedule.enabled,
    }


@router.get("/schedules")
async def list_schedules():
    """List all scheduled backup jobs."""
    return {"schedules": scheduler.list_schedules()}


@router.put("/schedules/{schedule_id}/toggle")
async def toggle_schedule(schedule_id: str, req: ScheduleToggleRequest):
    """Enable or disable a scheduled backup."""
    result = scheduler.toggle_schedule(schedule_id, req.enabled)
    if not result:
        raise HTTPException(404, "Schedule not found")
    return {
        "schedule_id": result.schedule_id,
        "enabled": result.enabled,
        "next_run_at": result.next_run_at,
    }


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str):
    """Delete a scheduled backup job."""
    if not scheduler.delete_schedule(schedule_id):
        raise HTTPException(404, "Schedule not found")
    return {"status": "ok", "schedule_id": schedule_id}


@router.post("/schedules/run-due")
async def run_due_schedules():
    """Manually trigger all due scheduled backups."""
    results = scheduler.run_due_backups()
    return {"results": results, "count": len(results)}


# ── Data Migration Endpoints ───────────────────────────────


@router.post("/export")
async def export_data(req: ExportRequest):
    """Export data in specified format (json/jsonl/csv)."""
    if not req.data:
        raise HTTPException(400, "No data to export")

    if req.format == "json":
        job = migrator.export_json(req.data, req.name)
    elif req.format == "jsonl":
        job = migrator.export_jsonl(req.data, req.name)
    elif req.format == "csv":
        job = migrator.export_csv(req.data, req.name)
    else:
        raise HTTPException(400, f"Unsupported format: {req.format}")

    return {
        "job_id": job.job_id,
        "format": job.format,
        "record_count": job.record_count,
        "size_bytes": job.size_bytes,
        "file_path": job.file_path,
    }


@router.post("/import")
async def import_data(
    file: UploadFile = File(...),
    format: str = Form(default="json"),
):
    """Import data from uploaded file."""
    import os
    import tempfile

    suffix = {"json": ".json", "jsonl": ".jsonl", "csv": ".csv"}.get(format, ".txt")
    data = await file.read()

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="wb") as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        if format == "json":
            records = migrator.import_json(tmp_path)
        elif format == "jsonl":
            records = migrator.import_jsonl(tmp_path)
        elif format == "csv":
            records = migrator.import_csv(tmp_path)
        else:
            raise HTTPException(400, f"Unsupported format: {format}")
    finally:
        os.unlink(tmp_path)

    return {"records": len(records), "data": records}


@router.get("/exports")
async def list_exports():
    """List export jobs."""
    return {"exports": migrator.list_exports()}


# ── Stats ──────────────────────────────────────────────────


@router.get("/stats")
async def marrow_stats():
    """Get OpenMarrow statistics."""
    return {
        "status": "ok",
        "component": "OpenMarrow",
        "backup": backup_manager.stats(),
        "scheduler": {
            "active_schedules": len([s for s in scheduler.list_schedules() if s["enabled"]]),
            "total_schedules": len(scheduler.list_schedules()),
            "running": scheduler._running,
        },
        "export_dir": str(migrator.export_dir),
    }


# ── Health ─────────────────────────────────────────────────


@router.get("/health")
async def marrow_health():
    """OpenMarrow health check."""
    return {
        "status": "ok",
        "component": "OpenMarrow",
        "backup": backup_manager.stats(),
        "scheduler": {
            "active_schedules": len([s for s in scheduler.list_schedules() if s["enabled"]]),
            "total_schedules": len(scheduler.list_schedules()),
            "running": scheduler._running,
        },
        "export_dir": str(migrator.export_dir),
    }
