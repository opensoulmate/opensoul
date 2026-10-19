"""System Event Stream — aggregates recent activity from all organs into a unified timeline.

Features:
- Polling endpoint: GET /stream
- **Real-time SSE**: GET /sse (Server-Sent Events for live push)
- Event buffer with deduplication
- Per-organ filtering
"""

import asyncio
import json
import logging
import time
import uuid
from collections import deque

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory event ring buffer (max 1000 events)
_event_buffer: deque[dict] = deque(maxlen=1000)

# ── SSE subscriber management ──────────────────────────────────
# Each subscriber is an asyncio.Queue that receives events in real-time
_sse_subscribers: dict[str, asyncio.Queue] = {}
_sse_lock = asyncio.Lock()


async def _broadcast_event(event: dict) -> None:
    """Broadcast an event to all SSE subscribers."""
    dead = []
    for sid, queue in _sse_subscribers.items():
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(sid)
    for sid in dead:
        _sse_subscribers.pop(sid, None)


def push_event(event: dict) -> None:
    """Push an event directly into the ring buffer AND broadcast to SSE subscribers.

    Called by the event_bridge so organ actions appear in the Activity feed
    without waiting for the next probe cycle.
    """
    import uuid as _uuid

    event.setdefault("id", f"evt_{_uuid.uuid4().hex[:12]}")
    event.setdefault("collected_at", time.time())
    _event_buffer.append(event)

    # Schedule broadcast to SSE subscribers (non-blocking)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_broadcast_event(event))
    except RuntimeError:
        pass  # No event loop running (e.g. during tests)


_BASE = "http://127.0.0.1:8090"

# ── Organ Activity Probes ──────────────────────────────────────
# Each probe fetches recent activity from an organ's API


async def _probe_vein(client: httpx.AsyncClient) -> list[dict]:
    """Get recent file operations from Vein."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/vein/stats", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            store = data.get("store", {})
            if store.get("total_files", 0) > 0:
                events.append(
                    {
                        "organ": "vein",
                        "emoji": "🩸",
                        "type": "stats",
                        "summary": f"{store['total_files']} files stored, {store['unique_blobs']} unique blobs",
                        "detail": store,
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_gland(client: httpx.AsyncClient) -> list[dict]:
    """Get recent LLM usage from Gland."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/gland/usage/recent?limit=5", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            for rec in data.get("records", [])[:3]:
                events.append(
                    {
                        "organ": "gland",
                        "emoji": "🧪",
                        "type": "llm_call",
                        "summary": f"{rec.get('model', 'unknown')} — {rec.get('total_tokens', 0)} tokens",
                        "detail": rec,
                        "timestamp": rec.get("timestamp"),
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_immune(client: httpx.AsyncClient) -> list[dict]:
    """Get recent security events from Immune."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/immune/audit/log?limit=5", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            for entry in data.get("entries", [])[:3]:
                events.append(
                    {
                        "organ": "immune",
                        "emoji": "🛡",
                        "type": "security",
                        "summary": f"{entry.get('action', 'unknown')} — {entry.get('detail', '')}",
                        "detail": entry,
                        "timestamp": entry.get("timestamp"),
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_trajectory(client: httpx.AsyncClient) -> list[dict]:
    """Get recent trajectory events."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/trajectory/events?limit=5", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            for ev in data.get("events", [])[:3]:
                events.append(
                    {
                        "organ": "trajectory",
                        "emoji": "📊",
                        "type": "agent_event",
                        "summary": f"[{ev.get('agent_id', '')}] {ev.get('event_type', '')}: {ev.get('content', '')[:80]}",
                        "detail": ev,
                        "timestamp": ev.get("timestamp"),
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_echo(client: httpx.AsyncClient) -> list[dict]:
    """Get recent message dispatches from Echo."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/echo/history?limit=5", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            for msg in data.get("messages", [])[:3]:
                events.append(
                    {
                        "organ": "echo",
                        "emoji": "🔊",
                        "type": "message",
                        "summary": f"[{msg.get('channel', '')}] {msg.get('title', '')}",
                        "detail": msg,
                        "timestamp": msg.get("timestamp"),
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_mirror(client: httpx.AsyncClient) -> list[dict]:
    """Get recent sandbox activities from Mirror."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/mirror/sandboxes", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            for sb in data.get("sandboxes", [])[:3]:
                events.append(
                    {
                        "organ": "mirror",
                        "emoji": "🪞",
                        "type": "sandbox",
                        "summary": f"Sandbox '{sb.get('name', sb.get('sandbox_id', ''))}' — {sb.get('status', '')}",
                        "detail": sb,
                        "timestamp": sb.get("created_at"),
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_link(client: httpx.AsyncClient) -> list[dict]:
    """Get recent webhook events from Link."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/link/events?limit=5", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            for ev in data.get("events", [])[:3]:
                events.append(
                    {
                        "organ": "link",
                        "emoji": "🔗",
                        "type": "webhook",
                        "summary": f"[{ev.get('direction', '')}] {ev.get('event_type', '')}: {ev.get('payload_summary', '')[:60]}",
                        "detail": ev,
                        "timestamp": ev.get("timestamp"),
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_limb(client: httpx.AsyncClient) -> list[dict]:
    """Get recent RPA tasks from Limb."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/limb/tasks?limit=5", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            for task in data.get("tasks", [])[:3]:
                events.append(
                    {
                        "organ": "limb",
                        "emoji": "💪",
                        "type": "rpa_task",
                        "summary": f"Task '{task.get('name', '')}' — {task.get('status', '')}",
                        "detail": task,
                        "timestamp": task.get("created_at"),
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_cron(client: httpx.AsyncClient) -> list[dict]:
    """Get recent cron job executions."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/cron/jobs", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            for job in (data.get("jobs", data) if isinstance(data, (list, dict)) else [])[:3]:
                if isinstance(job, dict):
                    events.append(
                        {
                            "organ": "will",
                            "emoji": "✨",
                            "type": "cron_job",
                            "summary": f"Cron '{job.get('name', job.get('id', ''))}' — last: {job.get('last_run', 'never')}",
                            "detail": job,
                        }
                    )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_hippo(client: httpx.AsyncClient) -> list[dict]:
    """Get recent memory operations from Hippo."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/hippo/memories?limit=5", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            total = data.get("total", 0)
            if total > 0:
                events.append(
                    {
                        "organ": "hippo",
                        "emoji": "🧠",
                        "type": "memory",
                        "summary": f"{total} memories tracked ({data.get('active', 0)} active, {data.get('archived', 0)} archived)",
                        "detail": data,
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_reflex(client: httpx.AsyncClient) -> list[dict]:
    """Get recent reflex cache activity."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/reflex/health", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            cache = data.get("cache", data)
            total_hits = cache.get("total_hits", 0)
            if total_hits > 0:
                events.append(
                    {
                        "organ": "reflex",
                        "emoji": "⚡",
                        "type": "cache_stats",
                        "summary": f"Reflex cache: {cache.get('active_entries', 0)} entries, {total_hits} hits ({cache.get('hit_rate_percent', 0):.1f}% hit rate)",
                        "detail": cache,
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_nerve(client: httpx.AsyncClient) -> list[dict]:
    """Get recent nerve bus activity."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/nerve/events?limit=5", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            for ev in data.get("events", [])[:3]:
                events.append(
                    {
                        "organ": "nerve",
                        "emoji": "⚡",
                        "type": "bus_event",
                        "summary": f"[{ev.get('topic', '')}] {ev.get('event_type', '')}: {str(ev.get('payload', ''))[:60]}",
                        "detail": ev,
                        "timestamp": ev.get("timestamp"),
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_cortex(client: httpx.AsyncClient) -> list[dict]:
    """Get recent cortex task activity."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/cortex/health", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            modules = data.get("modules", {})
            avail = [k for k, v in modules.items() if v == "available"]
            if avail:
                events.append(
                    {
                        "organ": "cortex",
                        "emoji": "🧩",
                        "type": "cortex_status",
                        "summary": f"Cortex modules active: {', '.join(avail)}",
                        "detail": modules,
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_sense(client: httpx.AsyncClient) -> list[dict]:
    """Get recent sense (OCR/ASR) activity."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/sense/health", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            engines = data.get("engines", {})
            available = [
                k for k, v in engines.items() if isinstance(v, dict) and v.get("available")
            ]
            if available:
                events.append(
                    {
                        "organ": "sense",
                        "emoji": "👁",
                        "type": "sense_status",
                        "summary": f"Sense engines active: {', '.join(available)}",
                        "detail": engines,
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_vital(client: httpx.AsyncClient) -> list[dict]:
    """Get recent vital metrics."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/vital/health", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            components = data.get("components", [])
            down = [c for c in components if isinstance(c, dict) and c.get("status") == "down"]
            if down:
                events.append(
                    {
                        "organ": "vital",
                        "emoji": "📊",
                        "type": "health_alert",
                        "summary": f"⚠️ {len(down)} component(s) DOWN: {', '.join(c.get('name', '') for c in down)}",
                        "detail": {"down_components": down},
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_pulse(client: httpx.AsyncClient) -> list[dict]:
    """Get recent pulse signal activity."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/pulse/signals", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            signals = data.get("signals", [])
            active = [s for s in signals if isinstance(s, dict) and s.get("status") == "active"]
            if active:
                events.append(
                    {
                        "organ": "pulse",
                        "emoji": "💓",
                        "type": "pulse_signals",
                        "summary": f"{len(active)} active pulse signal(s): {', '.join(s.get('name', s.get('signal_id', ''))[:20] for s in active[:3])}",
                        "detail": {"active_count": len(active), "total": len(signals)},
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_voice(client: httpx.AsyncClient) -> list[dict]:
    """Get recent voice synthesis activity."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/voice/health", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            total = data.get("total_synthesized", 0)
            if total > 0:
                events.append(
                    {
                        "organ": "voice",
                        "emoji": "🎤",
                        "type": "tts_stats",
                        "summary": f"Voice: {total} syntheses, {data.get('total_characters', 0)} chars, backend: {data.get('preferred_backend', 'unknown')}",
                        "detail": data,
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_vision(client: httpx.AsyncClient) -> list[dict]:
    """Get recent vision generation activity."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/vision/health", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            total = data.get("total_generated", 0)
            if total > 0:
                events.append(
                    {
                        "organ": "vision",
                        "emoji": "🎨",
                        "type": "vision_stats",
                        "summary": f"Vision: {total} images generated ({data.get('errors', 0)} errors)",
                        "detail": data,
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_mind(client: httpx.AsyncClient) -> list[dict]:
    """Get recent mind/emotion analysis activity."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/mind/health", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            emotion = data.get("emotion", {})
            total = emotion.get("total_analyzed", 0)
            if total > 0:
                events.append(
                    {
                        "organ": "mind",
                        "emoji": "💭",
                        "type": "emotion_stats",
                        "summary": f"Mind: {total} emotions analyzed, active personality: {data.get('personality', {}).get('active', 'default')}",
                        "detail": emotion,
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_nest(client: httpx.AsyncClient) -> list[dict]:
    """Get recent multi-tenant activity."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/nest/tenants", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            tenants = data.get("tenants", [])
            if tenants:
                events.append(
                    {
                        "organ": "nest",
                        "emoji": "🏠",
                        "type": "tenant_activity",
                        "summary": f"Nest: {len(tenants)} tenant(s) configured",
                        "detail": {"count": len(tenants)},
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_marrow(client: httpx.AsyncClient) -> list[dict]:
    """Get recent backup activity from Marrow."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/marrow/backups", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            backups = data.get("backups", [])
            if backups:
                latest = backups[0] if isinstance(backups[0], dict) else {}
                events.append(
                    {
                        "organ": "marrow",
                        "emoji": "🦴",
                        "type": "backup",
                        "summary": f"Marrow: {len(backups)} backup(s), latest: {latest.get('name', 'unknown')} ({latest.get('status', '')})",
                        "detail": {"total": len(backups), "latest": latest},
                        "timestamp": latest.get("created_at"),
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_heredity(client: httpx.AsyncClient) -> list[dict]:
    """Get recent version/evolution activity from Heredity."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/heredity/health", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            reg = data.get("registry", {})
            total_migrations = reg.get("total_migrations", 0)
            if total_migrations > 0:
                events.append(
                    {
                        "organ": "heredity",
                        "emoji": "🔗",
                        "type": "evolution",
                        "summary": f"Heredity: {reg.get('total_components', 0)} components, {total_migrations} migrations",
                        "detail": reg,
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_gene(client: httpx.AsyncClient) -> list[dict]:
    """Get recent gene template activity."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/gene/health", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            total = data.get("total_templates", 0)
            user_count = data.get("user_count", 0)
            if user_count > 0:
                events.append(
                    {
                        "organ": "gene",
                        "emoji": "🧬",
                        "type": "template_stats",
                        "summary": f"Gene: {total} templates ({data.get('builtin_count', 0)} builtin, {user_count} user)",
                        "detail": data,
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_learn(client: httpx.AsyncClient) -> list[dict]:
    """Get recent learning activity."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/learn/stats", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            total = data.get("total_courses", 0)
            if total > 0:
                events.append(
                    {
                        "organ": "learn",
                        "emoji": "📚",
                        "type": "learn_stats",
                        "summary": f"Learn: {total} courses, {data.get('completed_chapters', 0)} chapters completed",
                        "detail": data,
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_mcp(client: httpx.AsyncClient) -> list[dict]:
    """Get recent MCP server activity."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/mcp/stats", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            total = data.get("total_servers", 0)
            connected = data.get("connected", 0)
            if total > 0:
                events.append(
                    {
                        "organ": "mcp",
                        "emoji": "🔌",
                        "type": "mcp_stats",
                        "summary": f"MCP: {total} servers ({connected} connected), {data.get('total_tools', 0)} tools",
                        "detail": data,
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


async def _probe_plugins(client: httpx.AsyncClient) -> list[dict]:
    """Get recent plugin activity."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/plugins", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                events.append(
                    {
                        "organ": "plugins",
                        "emoji": "🧩",
                        "type": "plugin_stats",
                        "summary": f"Plugins: {len(data)} installed",
                        "detail": {
                            "total": len(data),
                            "plugins": [p.get("name", "") for p in data[:5]],
                        },
                    }
                )
    except Exception as exc:
        logger.debug("stream probe skipped: %s", exc)
    return events


_PROBES = [
    _probe_vein,
    _probe_gland,
    _probe_immune,
    _probe_trajectory,
    _probe_echo,
    _probe_mirror,
    _probe_link,
    _probe_limb,
    _probe_cron,
    _probe_hippo,
    _probe_reflex,
    _probe_nerve,
    _probe_cortex,
    _probe_sense,
    _probe_vital,
    _probe_pulse,
    _probe_voice,
    _probe_vision,
    _probe_mind,
    _probe_nest,
    _probe_marrow,
    _probe_heredity,
    _probe_gene,
    _probe_learn,
    _probe_mcp,
    _probe_plugins,
]


# recent_count 时间窗（DEC-㉗a-① 拍板取 B1=近 24h 窗口计数，窗口单一源）
RECENT_WINDOW_S = 24 * 3600


def _event_ts(e: dict) -> float:
    """事件时刻单一源解释（排序与 recent_count 窗口计数共用，禁两套解释漂移）。

    显式 timestamp（探针透传的源记录时刻）优先，否则 collected_at（采集时刻）；
    ISO 串按 fromisoformat 解析，解析失败/空值=0.0（与既有排序语义逐字一致：
    无法定位时刻的事件视为最旧，不入 recent 窗口）。
    """
    ts = e.get("timestamp") or e.get("collected_at", 0)
    if isinstance(ts, str):
        try:
            from datetime import datetime

            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except (ValueError, AttributeError):
            return 0.0
    return float(ts) if ts else 0.0


async def _collect_all_events() -> list[dict]:
    """Run all probes in parallel and collect events."""
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[p(client) for p in _PROBES], return_exceptions=True)

    events = []
    now = time.time()
    for result in results:
        if isinstance(result, list):
            for ev in result:
                ev.setdefault("id", uuid.uuid4().hex[:12])
                ev.setdefault("collected_at", now)
                events.append(ev)

    # Sort by timestamp (most recent first)
    events.sort(key=_event_ts, reverse=True)
    return events


# ── Endpoints ──────────────────────────────────────────────────


@router.get("/stream")
async def get_event_stream(
    limit: int = Query(default=50, ge=1, le=200),
    organ: str = Query(default=None, description="Filter by organ name"),
):
    """Get the latest aggregated event stream from all organs."""
    events = await _collect_all_events()

    # Also include cached events from buffer
    all_events = list(_event_buffer) + events
    # Deduplicate by id
    seen = set()
    unique = []
    for ev in all_events:
        eid = ev.get("id", "")
        if eid not in seen:
            seen.add(eid)
            unique.append(ev)

    # Filter by organ if specified
    if organ:
        unique = [e for e in unique if e.get("organ") == organ]

    # Update buffer
    for ev in events:
        _event_buffer.appendleft(ev)

    return {
        "events": unique[:limit],
        "total": len(unique),
        "organs_probed": len(_PROBES),
        "collected_at": time.time(),
    }


@router.get("/stream/summary")
async def get_stream_summary():
    """Get a summary of recent system activity."""
    events = await _collect_all_events()

    # Aggregate by organ
    by_organ: dict[str, int] = {}
    for ev in events:
        organ = ev.get("organ", "unknown")
        by_organ[organ] = by_organ.get(organ, 0) + 1

    # Aggregate by type
    by_type: dict[str, int] = {}
    for ev in events:
        etype = ev.get("type", "unknown")
        by_type[etype] = by_type.get(etype, 0) + 1

    # DEC-㉗a-① recent_count（B1 近 24h 窗口计数）：事件时刻解释走 _event_ts 单一源
    recent_cutoff = time.time() - RECENT_WINDOW_S
    recent_count = sum(1 for ev in events if _event_ts(ev) >= recent_cutoff)

    return {
        "total_events": len(events),
        "by_organ": by_organ,
        "by_type": by_type,
        "most_active_organ": max(by_organ, key=by_organ.get) if by_organ else None,
        "recent_count": recent_count,
        "collected_at": time.time(),
    }


@router.post("/stream/refresh")
async def refresh_stream():
    """Force refresh the event stream cache."""
    events = await _collect_all_events()
    _event_buffer.clear()
    for ev in events:
        _event_buffer.appendleft(ev)
    return {
        "refreshed": True,
        "events_collected": len(events),
    }


@router.get("/summary")
async def get_summary():
    """Get a summary of recent system activity (alias for /stream/summary)."""
    return await get_stream_summary()


@router.get("/health")
async def stream_health():
    """Event stream health check."""
    return {
        "status": "ok",
        "component": "EventStream",
        "buffer_size": len(_event_buffer),
        "probes_configured": len(_PROBES),
        "sse_subscribers": len(_sse_subscribers),
    }


# ── Server-Sent Events (SSE) ─────────────────────────────────


async def _sse_generator(subscriber_id: str, organ_filter: str | None = None):
    """Generate SSE events for a connected client."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    _sse_subscribers[subscriber_id] = queue

    try:
        # Send initial connection event
        yield f"event: connected\ndata: {json.dumps({'subscriber_id': subscriber_id, 'timestamp': time.time()})}\n\n"

        # Send current buffer as initial state
        recent = list(_event_buffer)[-20:]
        if organ_filter:
            recent = [e for e in recent if e.get("organ") == organ_filter]
        for ev in recent:
            yield f"event: message\ndata: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"

        # Stream live events
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30)
                if organ_filter and event.get("organ") != organ_filter:
                    continue
                yield f"event: message\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
            except TimeoutError:
                # Send keepalive ping every 30s
                yield f": keepalive {time.time()}\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        _sse_subscribers.pop(subscriber_id, None)


@router.get("/sse")
async def sse_stream(
    organ: str = Query(default=None, description="Filter events by organ name"),
):
    """Real-time event stream using Server-Sent Events (SSE).

    Connect to this endpoint to receive live events as they happen.
    Events are pushed immediately when organs report activity.

    Usage:
      const es = new EventSource('/api/events/sse');
      es.addEventListener('message', (e) => console.log(JSON.parse(e.data)));
      es.addEventListener('connected', (e) => console.log('SSE connected'));
    """
    subscriber_id = f"sse_{uuid.uuid4().hex[:8]}"
    return StreamingResponse(
        _sse_generator(subscriber_id, organ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx passthrough
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.get("/sse/clients")
async def list_sse_clients():
    """List active SSE connections."""
    return {
        "clients": [
            {"subscriber_id": sid, "queue_size": q.qsize()} for sid, q in _sse_subscribers.items()
        ],
        "total": len(_sse_subscribers),
    }
