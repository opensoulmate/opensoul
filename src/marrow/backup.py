"""Backup manager — snapshot creation, listing, restore, scheduled backups."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tarfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BackupManifest:
    backup_id: str
    name: str
    created_at: float
    size_bytes: int
    file_count: int
    description: str = ""
    tags: list[str] = field(default_factory=list)
    checksum: str = ""
    status: str = "complete"


@dataclass
class ScheduledBackup:
    """A scheduled backup job."""

    schedule_id: str
    name: str
    source_dirs: list[str]
    cron_expr: str  # "hourly", "daily", "weekly", or "every_Ns"
    interval_seconds: int
    description: str = ""
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    created_at: float = 0.0
    last_run_at: float = 0.0
    next_run_at: float = 0.0
    run_count: int = 0
    last_backup_id: str = ""


class BackupManager:
    """Manage backup snapshots of knowledge base data."""

    def __init__(self, backup_dir: str | Path | None = None):
        self.backup_dir = Path(backup_dir or os.path.expanduser("~/.opensoul/backups"))
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._manifest_file = self.backup_dir / "manifests.json"
        self._manifests: dict[str, BackupManifest] = {}
        self._load_manifests()

    def _load_manifests(self):
        if self._manifest_file.exists():
            try:
                data = json.loads(self._manifest_file.read_text())
                for k, v in data.items():
                    self._manifests[k] = BackupManifest(**v)
            except Exception:
                self._manifests = {}

    def _save_manifests(self):
        data = {
            k: {
                "backup_id": v.backup_id,
                "name": v.name,
                "created_at": v.created_at,
                "size_bytes": v.size_bytes,
                "file_count": v.file_count,
                "description": v.description,
                "tags": v.tags,
                "checksum": v.checksum,
                "status": v.status,
            }
            for k, v in self._manifests.items()
        }
        self._manifest_file.write_text(json.dumps(data, indent=2))

    def create_backup(
        self,
        source_dirs: list[str | Path],
        name: str = "",
        description: str = "",
        tags: list[str] | None = None,
    ) -> BackupManifest:
        """Create a tar.gz backup of specified directories."""
        timestamp = int(time.time())
        backup_id = f"backup_{timestamp}"
        backup_name = name or f"backup_{time.strftime('%Y%m%d_%H%M%S')}"
        archive_path = self.backup_dir / f"{backup_id}.tar.gz"

        file_count = 0
        # Never pack our own output. Without this, a schedule whose source
        # covers the backup dir (e.g. "~/.opensoul") recursively packs every
        # previous backup tar into the next one — archive size doubles each run.
        backup_root = self.backup_dir.resolve()
        with tarfile.open(archive_path, "w:gz") as tar:
            for src in source_dirs:
                src_path = Path(src)
                if src_path.exists():
                    for item in src_path.rglob("*"):
                        if not item.is_file():
                            continue
                        if item.resolve().is_relative_to(backup_root):
                            continue  # inside backup output dir — skip
                        tar.add(item, arcname=str(item.relative_to(src_path.parent)))
                        file_count += 1

        size = archive_path.stat().st_size
        checksum = self._checksum(archive_path)

        manifest = BackupManifest(
            backup_id=backup_id,
            name=backup_name,
            created_at=time.time(),
            size_bytes=size,
            file_count=file_count,
            description=description,
            tags=tags or [],
            checksum=checksum,
        )

        with self._lock:
            self._manifests[backup_id] = manifest
            self._save_manifests()

        return manifest

    def restore_backup(self, backup_id: str, target_dir: str | Path) -> dict:
        """Restore a backup to target directory."""
        with self._lock:
            manifest = self._manifests.get(backup_id)
        if not manifest:
            return {"success": False, "error": f"Backup '{backup_id}' not found"}

        archive_path = self.backup_dir / f"{backup_id}.tar.gz"
        if not archive_path.exists():
            return {"success": False, "error": "Archive file missing"}

        # Verify checksum
        current_checksum = self._checksum(archive_path)
        if current_checksum != manifest.checksum:
            return {"success": False, "error": "Checksum mismatch — archive may be corrupted"}

        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)

        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=target)

        return {
            "success": True,
            "backup_id": backup_id,
            "target": str(target),
            "files_restored": manifest.file_count,
        }

    def list_backups(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "backup_id": m.backup_id,
                    "name": m.name,
                    "created_at": m.created_at,
                    "size_bytes": m.size_bytes,
                    "file_count": m.file_count,
                    "description": m.description,
                    "tags": m.tags,
                    "status": m.status,
                }
                for m in sorted(self._manifests.values(), key=lambda x: x.created_at, reverse=True)
            ]

    def delete_backup(self, backup_id: str) -> dict:
        with self._lock:
            manifest = self._manifests.pop(backup_id, None)
            if not manifest:
                return {"success": False, "error": "Not found"}
            self._save_manifests()

        archive_path = self.backup_dir / f"{backup_id}.tar.gz"
        if archive_path.exists():
            archive_path.unlink()

        return {"success": True, "backup_id": backup_id}

    def get_backup(self, backup_id: str) -> dict | None:
        with self._lock:
            m = self._manifests.get(backup_id)
        if not m:
            return None
        return {
            "backup_id": m.backup_id,
            "name": m.name,
            "created_at": m.created_at,
            "size_bytes": m.size_bytes,
            "file_count": m.file_count,
            "description": m.description,
            "tags": m.tags,
            "checksum": m.checksum,
            "status": m.status,
        }

    def stats(self) -> dict:
        with self._lock:
            total_size = sum(m.size_bytes for m in self._manifests.values())
            return {
                "total_backups": len(self._manifests),
                "total_size_bytes": total_size,
                "backup_dir": str(self.backup_dir),
            }

    @staticmethod
    def _checksum(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()[:16]


class BackupScheduler:
    """Schedule automatic backups at intervals."""

    INTERVALS = {
        "hourly": 3600,
        "daily": 86400,
        "weekly": 604800,
    }

    def __init__(self, backup_manager: BackupManager):
        self._manager = backup_manager
        self._schedules: dict[str, ScheduledBackup] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._manifest_file = backup_manager.backup_dir / "schedules.json"
        self._load_schedules()

    def _load_schedules(self):
        if self._manifest_file.exists():
            try:
                data = json.loads(self._manifest_file.read_text())
                for k, v in data.items():
                    self._schedules[k] = ScheduledBackup(**v)
            except Exception:
                self._schedules = {}

    def _save_schedules(self):
        data = {
            k: {
                "schedule_id": v.schedule_id,
                "name": v.name,
                "source_dirs": v.source_dirs,
                "cron_expr": v.cron_expr,
                "interval_seconds": v.interval_seconds,
                "description": v.description,
                "tags": v.tags,
                "enabled": v.enabled,
                "created_at": v.created_at,
                "last_run_at": v.last_run_at,
                "next_run_at": v.next_run_at,
                "run_count": v.run_count,
                "last_backup_id": v.last_backup_id,
            }
            for k, v in self._schedules.items()
        }
        self._manifest_file.write_text(json.dumps(data, indent=2))

    def create_schedule(
        self,
        name: str,
        source_dirs: list[str],
        interval: str = "daily",
        interval_seconds: int | None = None,
        description: str = "",
        tags: list[str] | None = None,
    ) -> ScheduledBackup:
        """Create a scheduled backup job."""
        now = time.time()
        schedule_id = f"sch_{int(now)}"

        if interval_seconds:
            secs = interval_seconds
            cron_expr = f"every_{secs}s"
        else:
            secs = self.INTERVALS.get(interval, 86400)
            cron_expr = interval

        schedule = ScheduledBackup(
            schedule_id=schedule_id,
            name=name,
            source_dirs=source_dirs,
            cron_expr=cron_expr,
            interval_seconds=secs,
            description=description,
            tags=tags or [],
            enabled=True,
            created_at=now,
            next_run_at=now + secs,
        )

        with self._lock:
            self._schedules[schedule_id] = schedule
            self._save_schedules()

        return schedule

    def delete_schedule(self, schedule_id: str) -> bool:
        with self._lock:
            if schedule_id not in self._schedules:
                return False
            del self._schedules[schedule_id]
            self._save_schedules()
        return True

    def toggle_schedule(self, schedule_id: str, enabled: bool) -> ScheduledBackup | None:
        with self._lock:
            s = self._schedules.get(schedule_id)
            if not s:
                return None
            s.enabled = enabled
            if enabled:
                s.next_run_at = time.time() + s.interval_seconds
            self._save_schedules()
        return s

    def list_schedules(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "schedule_id": s.schedule_id,
                    "name": s.name,
                    "source_dirs": s.source_dirs,
                    "cron_expr": s.cron_expr,
                    "interval_seconds": s.interval_seconds,
                    "description": s.description,
                    "tags": s.tags,
                    "enabled": s.enabled,
                    "created_at": s.created_at,
                    "last_run_at": s.last_run_at,
                    "next_run_at": s.next_run_at,
                    "run_count": s.run_count,
                    "last_backup_id": s.last_backup_id,
                }
                for s in sorted(self._schedules.values(), key=lambda x: x.created_at, reverse=True)
            ]

    def run_due_backups(self) -> list[dict]:
        """Check and run any due scheduled backups. Returns list of results."""
        now = time.time()
        results = []
        with self._lock:
            due = [s for s in self._schedules.values() if s.enabled and s.next_run_at <= now]

        for schedule in due:
            valid_dirs: list[str | Path] = [
                d for d in schedule.source_dirs if os.path.exists(os.path.expanduser(d))
            ]
            if not valid_dirs:
                results.append(
                    {
                        "schedule_id": schedule.schedule_id,
                        "success": False,
                        "error": "No valid source dirs",
                    }
                )
                continue

            manifest = self._manager.create_backup(
                source_dirs=valid_dirs,
                name=f"{schedule.name}_auto_{int(now)}",
                description=f"Auto-backup from schedule: {schedule.name}",
                tags=schedule.tags + ["scheduled"],
            )

            with self._lock:
                schedule.last_run_at = now
                schedule.next_run_at = now + schedule.interval_seconds
                schedule.run_count += 1
                schedule.last_backup_id = manifest.backup_id
                self._save_schedules()

            results.append(
                {
                    "schedule_id": schedule.schedule_id,
                    "success": True,
                    "backup_id": manifest.backup_id,
                    "name": manifest.name,
                    "size_bytes": manifest.size_bytes,
                }
            )

        return results

    def start(self):
        """Start the background scheduler thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self.run_due_backups()
            except Exception as exc:
                logging.getLogger(__name__).debug("probe skipped: %s", exc)
            time.sleep(60)  # Check every minute
