"""OpenHealer API — Auto-diagnosis and self-healing for all organs.

Endpoints:
  POST /diagnose/{organ}    — Diagnose a single organ
  POST /diagnose-all        — Diagnose all organs in parallel
  POST /heal/{organ}        — Diagnose + attempt healing
  POST /heal-all            — Diagnose + heal all failed organs
  POST /cycle               — Full cycle: diagnose → heal → notify → audit
  GET  /stats               — Healing statistics
  GET  /history             — Diagnosis/healing history
  GET  /health              — Healer health check
"""

import time

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel

from src.healer.diagnose import DiagnosisResult, OrganHealer, Severity
from src.healer.history_persist import HealerPersistence
from src.nerve.event_bridge import push_event
from src.services.tenant_binding import bind_identity

router = APIRouter()

# Singleton（RV-①b-⑬：修复史持久化真源接线，重启修复史/统计不丢）
healer = OrganHealer(persist=HealerPersistence())

# ── Organ health endpoints (same as diagnostics) ──────────────

_ORGANS = {
    "soul": "/api/health",
    "cortex": "/api/cortex/health",
    "nerve": "/api/nerve/health",
    "vein": "/api/vein/health",
    "sense": "/api/sense/health",
    "will": "/api/will/health",
    "immune": "/api/immune/health",
    "vital": "/api/vital/health",
    "marrow": "/api/marrow/health",
    "gland": "/api/gland/health",
    "gene": "/api/gene/health",
    "echo": "/api/echo/health",
    "mirror": "/api/mirror/health",
    "link": "/api/link/health",
    "hippo": "/api/hippo/health",
    "reflex": "/api/reflex/health",
    "heredity": "/api/heredity/health",
    "pulse": "/api/pulse/health",
    "nest": "/api/nest/health",
    "limb": "/api/limb/health",
    "voice": "/api/voice/health",
    "vision": "/api/vision/health",
    "mind": "/api/mind/health",
    "capture": "/api/capture/health",
    "pipeline": "/api/pipeline/health",
}


def _result_to_dict(r: DiagnosisResult) -> dict:
    return {
        "organ": r.organ,
        "healthy": r.healthy,
        "severity": r.severity.value,
        "symptoms": r.symptoms,
        "root_cause": r.root_cause,
        "recommended_action": r.recommended_action.value,
        "action_taken": r.action_taken.value,
        "action_success": r.action_success,
        "response_time_ms": r.response_time_ms,
        "timestamp": r.timestamp,
    }


# ── Request Schemas ───────────────────────────────────────────


class DiagnoseRequest(BaseModel):
    organ: str
    auto_heal: bool = False


class CycleRequest(BaseModel):
    organs: list[str] | None = None  # None = all
    auto_heal: bool = True
    notify: bool = True
    audit: bool = True


# ── Endpoints ─────────────────────────────────────────────────


@router.post("/diagnose/{organ}")
async def diagnose_organ(organ: str, authorization: str | None = Header(None)):
    """Diagnose a single organ — check health, detect symptoms, recommend action."""
    identity = bind_identity(authorization)
    endpoint = _ORGANS.get(organ)
    if not endpoint:
        from fastapi import HTTPException

        raise HTTPException(404, f"Unknown organ: {organ}. Valid: {list(_ORGANS.keys())}")

    result = await healer.diagnose(organ, endpoint)

    push_event(
        {
            "organ": "healer",
            "emoji": "💊",
            "type": "diagnosis",
            "summary": f"🔍 Diagnosed {organ}: {'✅ healthy' if result.healthy else '❌ ' + result.root_cause}",
            "detail": {
                "organ": organ,
                "healthy": result.healthy,
                "severity": result.severity.value,
            },
        },
        tenant_id=identity.tenant_id,
    )

    return _result_to_dict(result)


@router.post("/diagnose-all")
async def diagnose_all_organs(authorization: str | None = Header(None)):
    """Diagnose all organs in parallel."""
    identity = bind_identity(authorization)
    results = await healer.diagnose_all(_ORGANS)

    healthy = sum(1 for r in results if r.healthy)
    unhealthy = len(results) - healthy

    push_event(
        {
            "organ": "healer",
            "emoji": "💊",
            "type": "diagnosis_all",
            "summary": f"🔍 Diagnosed {len(results)} organs: {healthy} healthy, {unhealthy} unhealthy",
            "detail": {"total": len(results), "healthy": healthy, "unhealthy": unhealthy},
        },
        tenant_id=identity.tenant_id,
    )

    return {
        "total": len(results),
        "healthy": healthy,
        "unhealthy": unhealthy,
        "organs": [_result_to_dict(r) for r in results],
    }


@router.post("/heal/{organ}")
async def heal_organ(organ: str, authorization: str | None = Header(None)):
    """Diagnose + attempt to heal a single organ."""
    identity = bind_identity(authorization)
    endpoint = _ORGANS.get(organ)
    if not endpoint:
        from fastapi import HTTPException

        raise HTTPException(404, f"Unknown organ: {organ}")

    result = await healer.diagnose_and_heal(organ, endpoint)

    push_event(
        {
            "organ": "healer",
            "emoji": "💊",
            "type": "heal",
            "summary": f"{'✅' if result.healthy else '❌'} Heal {organ}: {result.action_taken.value} → {'success' if result.action_success else 'failed'}",
            "detail": _result_to_dict(result),
        },
        tenant_id=identity.tenant_id,
    )

    return _result_to_dict(result)


@router.post("/heal-all")
async def heal_all_organs(authorization: str | None = Header(None)):
    """Diagnose all organs, attempt healing on failures."""
    identity = bind_identity(authorization)
    # Phase 1: Diagnose all
    results = await healer.diagnose_all(_ORGANS)

    # Phase 2: Heal failures
    to_heal = [r for r in results if not r.healthy]
    healed_results = []
    for r in to_heal:
        healed = await healer.heal(r)
        healed_results.append(healed)

        # Re-check after healing
        if healed.action_success:
            endpoint = _ORGANS.get(healed.organ)
            if endpoint:
                recheck = await healer.diagnose(healed.organ, endpoint)
                if recheck.healthy:
                    healer.mark_recovered(healed)  # RV-①b-⑬ 终值随影落行

    # Merge results
    all_results = []
    for r in results:
        if r in to_heal:
            # Find the healed version
            for h in healed_results:
                if h.organ == r.organ:
                    all_results.append(h)
                    break
            else:
                all_results.append(r)
        else:
            all_results.append(r)

    healthy = sum(1 for r in all_results if r.healthy)
    healed_count = sum(1 for r in healed_results if r.severity == Severity.RECOVERED)

    push_event(
        {
            "organ": "healer",
            "emoji": "💊",
            "type": "heal_all",
            "summary": f"💊 Heal cycle: {len(to_heal)} failures, {healed_count} recovered",
            "detail": {"total": len(all_results), "healthy": healthy, "healed": healed_count},
        },
        tenant_id=identity.tenant_id,
    )

    return {
        "total": len(all_results),
        "healthy": healthy,
        "failures": len(to_heal),
        "healed": healed_count,
        "organs": [_result_to_dict(r) for r in all_results],
    }


@router.post("/cycle")
async def full_healing_cycle(req: CycleRequest | None = None):
    """Full healing cycle: diagnose → heal → notify → audit.

    This is the main entrypoint for automated healing runs.
    """
    if req is None:
        req = CycleRequest()

    # Select organs
    if req.organs:
        selected = {k: v for k, v in _ORGANS.items() if k in req.organs}
    else:
        selected = _ORGANS

    start_time = time.time()

    # Phase 1: Diagnose all
    results = await healer.diagnose_all(selected)

    # Phase 2: Heal failures (if enabled)
    healed_count = 0
    if req.auto_heal:
        for i, r in enumerate(results):
            if not r.healthy and r.recommended_action.value != "none":
                healed = await healer.heal(r)
                results[i] = healed
                if healed.action_success:
                    healed_count += 1
                    # Re-check
                    endpoint = _ORGANS.get(healed.organ)
                    if endpoint:
                        recheck = await healer.diagnose(healed.organ, endpoint)
                        if recheck.healthy:
                            healer.mark_recovered(results[i])  # RV-①b-⑬ 终值随影落行

    # Phase 3: Notify (if enabled)
    notify_result = None
    if req.notify:
        notify_result = await healer.notify(results)

    # Phase 4: Audit (if enabled)
    if req.audit:
        await healer.audit_log(results)

    elapsed = round(time.time() - start_time, 2)
    healthy = sum(1 for r in results if r.healthy)

    return {
        "cycle_complete": True,
        "elapsed_seconds": elapsed,
        "total_organs": len(results),
        "healthy": healthy,
        "unhealthy": len(results) - healthy,
        "healed": healed_count,
        "notified": notify_result,
        "organs": [_result_to_dict(r) for r in results],
    }


@router.get("/stats")
async def healer_stats():
    """Get healing statistics."""
    return {
        "status": "ok",
        "component": "OpenHealer",
        **healer.stats(),
    }


@router.get("/history")
async def healer_history(limit: int = Query(default=50, ge=1, le=200)):
    """Get diagnosis/healing history."""
    return {
        "history": healer.history[-limit:],
        "total": len(healer.history),
    }


@router.get("/organs")
async def list_monitored_organs():
    """List all monitored organs and their health endpoints."""
    return {
        "organs": {k: {"health_endpoint": v} for k, v in _ORGANS.items()},
        "total": len(_ORGANS),
    }


@router.get("/health")
async def healer_health():
    """OpenHealer health check."""
    return {
        "status": "ok",
        "component": "OpenHealer",
        "description": "Auto-diagnosis and self-healing for all organs",
        "monitored_organs": len(_ORGANS),
        "stats": healer.stats(),
    }
