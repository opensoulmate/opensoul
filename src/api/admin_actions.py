"""OpenSoul Admin Actions API — one-click system maintenance operations.

Provides common admin tasks as single API calls:
- Clear all caches
- Run immediate backup
- Clean up expired data across all components
- Export/import system configuration
- Bulk health check with detailed diagnostics
"""

import logging
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
logger = logging.getLogger(__name__)
router = APIRouter()

_BASE = "http://127.0.0.1:8090"


# ── Request Schemas ─────────────────────────────────────────


class BackupRequest(BaseModel):
    name: str = "admin-backup"
    description: str = "Manual backup via admin actions"
    include_knowledge: bool = True
    include_config: bool = True
    include_memories: bool = True


# ── Cache Management ────────────────────────────────────────


@router.post("/caches/clear")
async def clear_all_caches():
    """Clear all component caches in one call."""
    results = {}
    cache_endpoints = {
        "vein": "/api/vein/cache/clear",
        "reflex": "/api/reflex/cache/clear",
        "voice": "/api/voice/cache/clear",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        for name, endpoint in cache_endpoints.items():
            try:
                res = await client.post(f"{_BASE}{endpoint}")
                results[name] = {
                    "status": "ok" if res.status_code == 200 else "error",
                    "code": res.status_code,
                    "data": res.json() if res.status_code == 200 else None,
                }
            except Exception as e:
                results[name] = {"status": "error", "error": str(e)}

    cleared = sum(1 for r in results.values() if r["status"] == "ok")
    return {
        "action": "clear_caches",
        "results": results,
        "cleared": cleared,
        "total": len(results),
    }


# ── Cleanup Expired Data ────────────────────────────────────


@router.post("/cleanup")
async def cleanup_expired():
    """Clean up expired data across all components."""
    results = {}
    cleanup_endpoints = {
        "vein_cache": ("/api/vein/cache/cleanup", "POST"),
        "mirror_sandboxes": ("/api/mirror/cleanup", "POST"),
        "hippo_sessions": ("/api/hippo/sessions/lifecycle-check", "POST"),
        "reflex_cache": ("/api/reflex/cleanup", "POST"),
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        for name, (endpoint, method) in cleanup_endpoints.items():
            try:
                if method == "POST":
                    res = await client.post(f"{_BASE}{endpoint}")
                else:
                    res = await client.get(f"{_BASE}{endpoint}")
                results[name] = {
                    "status": "ok" if res.status_code == 200 else "error",
                    "code": res.status_code,
                    "data": res.json() if res.status_code == 200 else None,
                }
            except Exception as e:
                results[name] = {"status": "error", "error": str(e)}

    cleaned = sum(1 for r in results.values() if r["status"] == "ok")
    return {
        "action": "cleanup_expired",
        "results": results,
        "cleaned": cleaned,
        "total": len(results),
    }


# ── Backup ──────────────────────────────────────────────────


@router.post("/backup")
async def run_backup(req: BackupRequest):
    """Run an immediate backup of system data."""
    source_dirs = []

    if req.include_knowledge:
        source_dirs.extend(
            [
                str(Path.home() / "opensoul" / "data"),
            ]
        )

    if req.include_config:
        source_dirs.extend(
            [
                str(Path.home() / "opensoul" / "src" / "config.py"),
                str(Path.home() / ".hermes"),
            ]
        )

    if req.include_memories:
        source_dirs.append(str(Path.home() / "opensoul" / "data" / "vein"))

    if not source_dirs:
        raise HTTPException(400, "No sources selected for backup")

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            res = await client.post(
                f"{_BASE}/api/marrow/backups",
                json={
                    "source_dirs": source_dirs,
                    "name": req.name,
                    "description": req.description,
                    "tags": ["admin", "manual"],
                },
            )
            if res.status_code == 200:
                return {
                    "action": "backup",
                    "status": "ok",
                    "data": res.json(),
                }
            else:
                return {
                    "action": "backup",
                    "status": "error",
                    "code": res.status_code,
                    "error": res.text,
                }
        except Exception as e:
            return {
                "action": "backup",
                "status": "error",
                "error": str(e),
            }


# ── System Overview ─────────────────────────────────────────


@router.get("/overview")
async def system_overview():
    """Get a comprehensive system overview in a single call."""
    overview = {
        "timestamp": time.time(),
        "health": {},
        "stats": {},
    }

    async with httpx.AsyncClient(timeout=8.0) as client:
        # Health check
        try:
            res = await client.get(f"{_BASE}/api/health/all")
            if res.status_code == 200:
                overview["health"] = res.json()
        except Exception:
            overview["health"] = {"status": "error"}

        # Stats from key components
        stat_endpoints = {
            "vein": "/api/vein/stats",
            "gland": "/api/gland/stats",
            "gene": "/api/gene/stats",
            "hippo": "/api/hippo/stats",
            "immune": "/api/immune/stats",
            "trajectory": "/api/trajectory/stats",
            "vital": "/api/vital/stats",
            "vision": "/api/vision/stats",
            "mind": "/api/mind/stats",
            "pipeline": "/api/pipeline/stats",
            "mirror": "/api/mirror/stats",
            "echo": "/api/echo/stats",
            "link": "/api/link/stats",
            "marrow": "/api/marrow/stats",
            "sense": "/api/sense/stats",
            "reflex": "/api/reflex/stats",
            "heredity": "/api/heredity/stats",
            "nerve": "/api/nerve/stats",
            "will": "/api/will/stats",
            "limb": "/api/limb/stats",
            "nest": "/api/nest/stats",
            "pulse": "/api/pulse/stats",
            "cortex": "/api/cortex/stats",
            "voice": "/api/voice/stats",
            # New stats endpoints
            "knowledge": "/api/knowledge/stats",
            "agent": "/api/agent/stats",
            "graph": "/api/graph/stats",
            "entity": "/api/entity/stats",
            "search": "/api/search/stats",
            "capture": "/api/capture/stats",
            "workflow": "/api/workflow/stats",
            "healer": "/api/healer/stats",
        }

        for name, endpoint in stat_endpoints.items():
            try:
                res = await client.get(f"{_BASE}{endpoint}")
                if res.status_code == 200:
                    overview["stats"][name] = res.json()
            except Exception as exc:
                logging.getLogger(__name__).debug("probe skipped: %s", exc)
    return overview


# ── Export System Config ─────────────────────────────────────


@router.get("/export/config")
async def export_config():
    """Export system configuration as JSON."""
    config = {
        "exported_at": time.time(),
        "version": "1.0.0",
        "components": {},
    }

    async with httpx.AsyncClient(timeout=8.0) as client:
        health_endpoints = {
            "soul": "/api/health",
            "gland": "/api/gland/health",
            "gene": "/api/gene/health",
            "vital": "/api/vital/health",
            "nerve": "/api/nerve/health",
            "immune": "/api/immune/health",
            "vein": "/api/vein/health",
            "sense": "/api/sense/health",
            "marrow": "/api/marrow/health",
            "echo": "/api/echo/health",
            "mirror": "/api/mirror/health",
            "link": "/api/link/health",
            "hippo": "/api/hippo/health",
            "reflex": "/api/reflex/health",
            "heredity": "/api/heredity/health",
            "will": "/api/will/health",
            "cortex": "/api/cortex/health",
        }

        for name, endpoint in health_endpoints.items():
            try:
                res = await client.get(f"{_BASE}{endpoint}")
                if res.status_code == 200:
                    config["components"][name] = res.json()
            except Exception as exc:
                logging.getLogger(__name__).debug("probe skipped: %s", exc)
    # Include gland providers
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(f"{_BASE}/api/gland/providers")
            if res.status_code == 200:
                config["providers"] = res.json()
    except Exception as exc:
        logging.getLogger(__name__).debug("probe skipped: %s", exc)

    # Include gene templates count
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(f"{_BASE}/api/gene/stats")
            if res.status_code == 200:
                config["templates"] = res.json()
    except Exception as exc:
        logging.getLogger(__name__).debug("probe skipped: %s", exc)

    return config


# ── System Report ──────────────────────────────────────────


@router.get("/report")
async def system_report():
    """Generate a comprehensive system report with all component statuses, stats, and recent events."""
    report = {
        "generated_at": time.time(),
        "platform": "Open-Soulmate",
        "version": "1.0.0",
        "health": {},
        "components": {},
        "recent_events": [],
        "summary": {},
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Overall health
        try:
            res = await client.get(f"{_BASE}/api/health/all")
            if res.status_code == 200:
                report["health"] = res.json()
        except Exception:
            report["health"] = {"status": "error"}

        # 2. Component details from registry
        try:
            res = await client.get(f"{_BASE}/api/registry/components")
            if res.status_code == 200:
                report["components"] = res.json()
        except Exception as exc:
            logging.getLogger(__name__).debug("probe skipped: %s", exc)
        # 3. Recent events
        try:
            res = await client.get(f"{_BASE}/api/events/summary")
            if res.status_code == 200:
                report["recent_events"] = res.json()
        except Exception as exc:
            logging.getLogger(__name__).debug("probe skipped: %s", exc)
        # 4. Key stats
        stats = {}
        stat_endpoints = {
            "vein": "/api/vein/stats",
            "gland": "/api/gland/stats",
            "gene": "/api/gene/stats",
            "immune": "/api/immune/stats",
            "trajectory": "/api/trajectory/stats",
            "heredity": "/api/heredity/stats",
            "cortex": "/api/cortex/stats",
            "voice": "/api/voice/stats",
        }
        for name, endpoint in stat_endpoints.items():
            try:
                res = await client.get(f"{_BASE}{endpoint}")
                if res.status_code == 200:
                    stats[name] = res.json()
            except Exception as exc:
                logging.getLogger(__name__).debug("probe skipped: %s", exc)
        report["stats"] = stats

    # 5. Summary
    healthy = int(report["health"].get("healthy", 0) or 0)
    total = int(report["health"].get("total", 0) or 0)
    report["summary"] = {
        "health_status": report["health"].get("status", "unknown"),
        "healthy_organs": healthy,
        "total_organs": total,
        "health_percentage": round(healthy / total * 100, 1) if total > 0 else 0,
        "total_components": report["components"].get("total", 0),
    }

    return report


# ── Health ──────────────────────────────────────────────────


@router.get("/health")
async def admin_health():
    """Admin actions health check."""
    return {
        "status": "ok",
        "component": "AdminActions",
        "actions": [
            "clear_caches",
            "cleanup",
            "backup",
            "overview",
            "export_config",
        ],
    }
