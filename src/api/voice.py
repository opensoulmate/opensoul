"""OpenVoice API — 声带系统：TTS文字转语音、语音角色管理。"""

import time

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.voice.tts_engine import TTSEngine
from src.voice.voice_profiles import ProfileManager

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
engine = TTSEngine()
profiles = ProfileManager()


# ── Request Schemas ────────────────────────────────────────


class SynthesizeRequest(BaseModel):
    text: str
    profile_id: str = ""  # Use a saved profile
    voice_id: str = ""  # Or directly specify voice
    rate: str = "+0%"
    pitch: str = "+0Hz"
    volume: str = "+0%"
    engine: str = ""  # Force engine, or auto
    save_output: bool = False  # Save to output directory
    output_name: str = ""


class ProfileCreateRequest(BaseModel):
    profile_id: str = ""
    name: str
    description: str = ""
    engine: str = "edge-tts"
    voice_id: str = "zh-CN-XiaoxiaoNeural"
    language: str = "zh-CN"
    rate: str = "+0%"
    pitch: str = "+0Hz"
    volume: str = "+0%"
    tags: list[str] = []


class ProfileUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    voice_id: str | None = None
    rate: str | None = None
    pitch: str | None = None
    volume: str | None = None
    tags: list[str] | None = None


# ── TTS Endpoints ──────────────────────────────────────────


@router.post("/synthesize")
async def synthesize_text(req: SynthesizeRequest):
    """Synthesize text to speech audio. Returns audio data as streaming response."""
    if not req.text.strip():
        raise HTTPException(400, "Text cannot be empty")
    if len(req.text) > 10000:
        raise HTTPException(400, "Text too long (max 10000 characters)")

    # Resolve voice from profile
    voice_id = req.voice_id
    rate = req.rate
    pitch = req.pitch
    volume = req.volume

    if req.profile_id:
        profile = profiles.get(req.profile_id)
        if not profile:
            raise HTTPException(404, f"Profile '{req.profile_id}' not found")
        voice_id = voice_id or profile.voice_id
        rate = req.rate if req.rate != "+0%" else profile.rate
        pitch = req.pitch if req.pitch != "+0Hz" else profile.pitch
        volume = req.volume if req.volume != "+0%" else profile.volume
        profiles.increment_usage(req.profile_id)

    if not voice_id:
        voice_id = "zh-CN-XiaoxiaoNeural"

    result = await engine.synthesize(
        text=req.text,
        voice_id=voice_id,
        rate=rate,
        pitch=pitch,
        volume=volume,
        engine=req.engine,
    )

    # Optionally save output
    output_file = ""
    if req.save_output:
        fname = req.output_name or f"tts-{int(time.time())}"
        output_file = engine.save_output(result.audio_data, fname, result.format)

    media_type = "audio/mpeg" if result.format == "mp3" else "audio/wav"

    return StreamingResponse(
        iter([result.audio_data]),
        media_type=media_type,
        headers={
            "X-TTS-Engine": result.engine,
            "X-TTS-Voice": result.voice_id,
            "X-TTS-Duration": str(round(result.duration_seconds, 2)),
            "X-TTS-Cached": str(result.cached).lower(),
            "X-TTS-Elapsed-Ms": str(int(result.elapsed_seconds * 1000)),
            "X-TTS-Output-File": output_file,
            "Content-Disposition": f'attachment; filename="tts.{result.format}"',
        },
    )


@router.post("/synthesize/json")
async def synthesize_json(req: SynthesizeRequest):
    """Synthesize text, return metadata only (no audio stream)."""
    if not req.text.strip():
        raise HTTPException(400, "Text cannot be empty")
    if len(req.text) > 10000:
        raise HTTPException(400, "Text too long (max 10000 characters)")

    voice_id = req.voice_id or "zh-CN-XiaoxiaoNeural"

    if req.profile_id:
        profile = profiles.get(req.profile_id)
        if not profile:
            raise HTTPException(404, f"Profile '{req.profile_id}' not found")
        voice_id = profile.voice_id
        profiles.increment_usage(req.profile_id)

    result = await engine.synthesize(
        text=req.text,
        voice_id=voice_id,
        rate=req.rate,
        pitch=req.pitch,
        volume=req.volume,
        engine=req.engine,
    )

    output_file = ""
    if req.save_output:
        fname = req.output_name or f"tts-{int(time.time())}"
        output_file = engine.save_output(result.audio_data, fname, result.format)

    return {
        "engine": result.engine,
        "voice_id": result.voice_id,
        "format": result.format,
        "size_bytes": result.size_bytes,
        "duration_seconds": round(result.duration_seconds, 2),
        "cached": result.cached,
        "elapsed_ms": int(result.elapsed_seconds * 1000),
        "output_file": output_file,
    }


@router.get("/voices")
async def list_voices(language: str = Query(default="")):
    """List available TTS voices (from edge-tts if available)."""
    voices = await engine.list_edge_voices(language=language)
    return {
        "voices": voices,
        "count": len(voices),
        "backend": engine.preferred_backend,
    }


# ── Profile Endpoints ──────────────────────────────────────


@router.get("/profiles")
async def list_profiles(language: str = Query(default="")):
    """List all voice profiles."""
    all_profiles = profiles.list_all()
    if language:
        all_profiles = [p for p in all_profiles if p.language.startswith(language)]
    return {
        "profiles": [
            {
                "profile_id": p.profile_id,
                "name": p.name,
                "description": p.description,
                "engine": p.engine,
                "voice_id": p.voice_id,
                "language": p.language,
                "rate": p.rate,
                "pitch": p.pitch,
                "volume": p.volume,
                "tags": p.tags,
                "builtin": p.builtin,
                "usage_count": p.usage_count,
            }
            for p in all_profiles
        ],
        "count": len(all_profiles),
    }


@router.get("/profiles/{profile_id}")
async def get_profile(profile_id: str):
    """Get a voice profile."""
    p = profiles.get(profile_id)
    if not p:
        raise HTTPException(404, "Profile not found")
    return {
        "profile_id": p.profile_id,
        "name": p.name,
        "description": p.description,
        "engine": p.engine,
        "voice_id": p.voice_id,
        "language": p.language,
        "rate": p.rate,
        "pitch": p.pitch,
        "volume": p.volume,
        "tags": p.tags,
        "builtin": p.builtin,
        "usage_count": p.usage_count,
    }


@router.post("/profiles")
async def create_profile(req: ProfileCreateRequest):
    """Create a new voice profile."""
    p = profiles.create(req.model_dump())
    return {
        "profile_id": p.profile_id,
        "name": p.name,
        "voice_id": p.voice_id,
    }


@router.patch("/profiles/{profile_id}")
async def update_profile(profile_id: str, req: ProfileUpdateRequest):
    """Update a voice profile."""
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not profiles.update(profile_id, updates):
        raise HTTPException(404, "Profile not found or is built-in")
    return {"message": "updated", "profile_id": profile_id}


@router.delete("/profiles/{profile_id}")
async def delete_profile(profile_id: str):
    """Delete a user-created voice profile."""
    if not profiles.delete(profile_id):
        raise HTTPException(400, "Cannot delete: not found or is built-in")
    return {"message": f"Profile '{profile_id}' deleted"}


# ── Output Endpoints ───────────────────────────────────────


@router.get("/outputs")
async def list_outputs():
    """List saved TTS output files."""
    return {"outputs": engine.list_outputs()}


@router.delete("/outputs/{filename}")
async def delete_output(filename: str):
    """Delete a saved TTS output file."""
    if not engine.delete_output(filename):
        raise HTTPException(404, "File not found")
    return {"message": "deleted", "filename": filename}


# ── Health ─────────────────────────────────────────────────


@router.post("/cache/clear")
async def clear_tts_cache():
    """Clear all cached TTS audio files."""
    return engine.clear_cache()


@router.get("/health")
async def voice_health():
    """OpenVoice health check."""
    return {
        "status": "ok",
        "component": "OpenVoice",
        **engine.stats(),
        **profiles.stats(),
    }


@router.get("/stats")
async def voice_stats():
    """Get OpenVoice statistics."""
    return {
        "status": "ok",
        "component": "OpenVoice",
        "engine": engine.stats(),
        "profiles": profiles.stats(),
    }
