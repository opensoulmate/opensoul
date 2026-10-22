"""OpenPulse API — 脉搏：高精度时序节拍器、亚秒级周期性轮询、时间信号分发。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.pulse.signal_persist import SignalPersistence
from src.pulse.timer import PulseEngine

router = APIRouter()
engine = PulseEngine(persist=SignalPersistence())


class SignalCreateRequest(BaseModel):
    name: str
    signal_type: str = "tick"  # tick, interval, cron, once
    interval_ms: float = 1000.0
    callback_url: str = ""
    payload: dict = {}
    max_fires: int = 0


class TickRequest(BaseModel):
    signal_id: str
    latency_ms: float = 0.0


@router.get("/health")
async def health():
    """OpenPulse health check."""
    return {"status": "ok", "component": "OpenPulse", **engine.get_stats()}


@router.post("/signals")
async def create_signal(req: SignalCreateRequest):
    """Create a new pulse signal."""
    if req.signal_type not in ("tick", "interval", "cron", "once"):
        raise HTTPException(400, "signal_type must be tick, interval, cron, or once")
    if req.interval_ms < 1:
        raise HTTPException(400, "interval_ms must be >= 1")
    sig = engine.create_signal(
        req.name,
        req.signal_type,
        req.interval_ms,
        callback_url=req.callback_url,
        payload=req.payload,
        max_fires=req.max_fires,
    )
    return {
        "signal_id": sig.signal_id,
        "name": sig.name,
        "signal_type": sig.signal_type.value,
        "interval_ms": sig.interval_ms,
        "status": sig.status.value,
        "next_fire_at": sig.next_fire_at,
    }


@router.get("/signals")
async def list_signals(status: str = Query(default=None)):
    """List all pulse signals."""
    signals = engine.list_signals(status=status)
    return {
        "signals": [
            {
                "signal_id": s.signal_id,
                "name": s.name,
                "signal_type": s.signal_type.value,
                "interval_ms": s.interval_ms,
                "status": s.status.value,
                "fire_count": s.fire_count,
                "max_fires": s.max_fires,
                "last_fired_at": s.last_fired_at,
                "next_fire_at": s.next_fire_at,
                "drift_correction": round(s.drift_correction, 3),
                "created_at": s.created_at,
            }
            for s in signals
        ],
        "total": len(signals),
    }


@router.get("/signals/{signal_id}")
async def get_signal(signal_id: str):
    """Get signal details."""
    sig = engine.get_signal(signal_id)
    if not sig:
        raise HTTPException(404, "Signal not found")
    return {
        "signal_id": sig.signal_id,
        "name": sig.name,
        "signal_type": sig.signal_type.value,
        "interval_ms": sig.interval_ms,
        "status": sig.status.value,
        "fire_count": sig.fire_count,
        "max_fires": sig.max_fires,
        "last_fired_at": sig.last_fired_at,
        "next_fire_at": sig.next_fire_at,
        "drift_correction": round(sig.drift_correction, 3),
        "precision_ms": sig.precision_ms,
        "callback_url": sig.callback_url,
        "payload": sig.payload,
        "created_at": sig.created_at,
    }


@router.post("/signals/{signal_id}/tick")
async def fire_tick(signal_id: str):
    """Manually fire a tick for a signal."""
    record = engine.tick(signal_id)
    if not record:
        raise HTTPException(404, "Signal not found or not active")
    return {
        "tick_id": record.tick_id,
        "signal_id": record.signal_id,
        "timestamp": record.timestamp,
        "drift_ms": round(record.drift_ms, 3),
    }


@router.post("/signals/{signal_id}/pause")
async def pause_signal(signal_id: str):
    """Pause a signal."""
    if not engine.pause_signal(signal_id):
        raise HTTPException(404, "Signal not found or not active")
    return {"paused": True}


@router.post("/signals/{signal_id}/resume")
async def resume_signal(signal_id: str):
    """Resume a paused signal."""
    if not engine.resume_signal(signal_id):
        raise HTTPException(404, "Signal not found or not paused")
    return {"resumed": True}


@router.delete("/signals/{signal_id}")
async def cancel_signal(signal_id: str):
    """Cancel/delete a signal."""
    if not engine.delete_signal(signal_id):
        raise HTTPException(404, "Signal not found")
    return {"deleted": True}


@router.post("/tick")
async def fire_tick_direct(req: TickRequest):
    """Fire a tick directly (for external timer integrations)."""
    record = engine.tick(req.signal_id)
    if not record:
        raise HTTPException(404, "Signal not found or not active")
    record.latency_ms = req.latency_ms
    return {"tick_id": record.tick_id, "drift_ms": round(record.drift_ms, 3)}


@router.get("/ticks")
async def get_tick_history(signal_id: str = Query(default=None), limit: int = Query(default=100)):
    """Get tick history."""
    return {"ticks": engine.get_tick_history(signal_id=signal_id, limit=limit)}


@router.get("/stats")
async def get_stats():
    """Get pulse engine statistics."""
    return engine.get_stats()
