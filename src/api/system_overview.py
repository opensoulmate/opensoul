"""OpenSystem Overview API — single endpoint for complete system status.

Aggregates: organ health, system metrics, recent events, component stats,
active agents, knowledge stats, and plugin status into one response.
Designed for dashboard consumption — one call instead of many.
"""

import asyncio
import logging
import time

from fastapi import APIRouter, Header

from src.services.tenant_binding import bind_identity

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Organ registry — ALL 25+ organs ──────────────────────────
_CORE_ORGANS = [
    # Core brain
    ("soul", "/api/health"),
    ("cortex", "/api/cortex/health"),
    ("cortex-enhanced", "/api/cortex/enhanced/health"),
    # Nervous system
    ("nerve", "/api/nerve/health"),
    # Circulatory
    ("vein", "/api/vein/health"),
    # Sensory
    ("sense", "/api/sense/health"),
    # Will / automation
    ("will", "/api/will/health"),
    # Vital signs
    ("vital", "/api/vital/health"),
    # Model gateway
    ("gland", "/api/gland/health"),
    # Security
    ("immune", "/api/immune/health"),
    # Backup / disaster recovery
    ("marrow", "/api/marrow/health"),
    # Templates
    ("gene", "/api/gene/health"),
    # Messaging
    ("echo", "/api/echo/health"),
    # Sandbox
    ("mirror", "/api/mirror/health"),
    # Integration gateway
    ("link", "/api/link/health"),
    # Phase 4 organs
    ("hippo", "/api/hippo/health"),
    ("reflex", "/api/reflex/health"),
    ("heredity", "/api/heredity/health"),
    ("nest", "/api/nest/health"),
    ("pulse", "/api/pulse/health"),
    ("limb", "/api/limb/health"),
    ("voice", "/api/voice/health"),
    ("vision", "/api/vision/health"),
    ("mind", "/api/mind/health"),
    # Intelligence / analytics
    ("intelligence", "/api/intelligence/health"),
    # Trajectory tracking
    ("trajectory", "/api/trajectory/health"),
    # MCP protocol
    ("mcp", "/api/mcp/health"),
    # Learning
    ("learn", "/api/learn/health"),
    # Diagnostics
    ("diagnostics", "/api/diagnostics/health"),
    # Soma connector
    ("soma-connector", "/api/soma/health"),
    # Event stream
    ("event-stream", "/api/events/health"),
    # Capture / data ingestion
    ("capture", "/api/capture/health"),
    # Pipeline
    ("pipeline", "/api/pipeline/health"),
    # Topology
    ("topology", "/api/topology/health"),
    # Graph
    ("graph", "/api/graph/health"),
    # Entity
    ("entity", "/api/entity/health"),
    # Tags
    ("tag", "/api/tags/health"),
    # User
    ("user", "/api/user/health"),
    # LLM
    ("llm", "/api/llm/health"),
    # Agent
    ("agent", "/api/agent/health"),
    # Export
    ("export", "/api/export/health"),
    # Search
    ("search", "/api/search/health"),
    # Chat
    ("chat", "/api/chat/health"),
    # Workspace
    ("workspace", "/api/workspace/health"),
    # Workflow
    ("workflow", "/api/workflow/health"),
    # Collaboration
    ("collab", "/api/collab/health"),
    # Marketplace
    ("marketplace", "/api/marketplace/health"),
    # Skills
    ("skills", "/api/skills/health"),
    # Notifications
    ("notifications", "/api/notifications/health"),
    # Knowledge
    ("knowledge", "/api/knowledge/health"),
    # Healer
    ("healer", "/api/healer/health"),
    # Timeline
    ("timeline", "/api/timeline/health"),
    # Benchmark
    ("benchmark", "/api/benchmark/health"),
    # System overview
    ("system-overview", "/api/system/health"),
]


async def _check_organs() -> dict:
    """Check health of all organs in parallel, tracking response times."""
    import time as _time

    import httpx

    base = "http://127.0.0.1:8090"
    results = {}
    timings = {}

    async with httpx.AsyncClient(timeout=5.0) as client:

        async def _check(name: str, path: str):
            start = _time.time()
            try:
                r = await client.get(f"{base}{path}")
                elapsed_ms = round((_time.time() - start) * 1000, 1)
                timings[name] = elapsed_ms
                results[name] = "ok" if r.status_code == 200 else "error"
            except Exception:
                elapsed_ms = round((_time.time() - start) * 1000, 1)
                timings[name] = elapsed_ms
                results[name] = "error"

        await asyncio.gather(*[_check(n, p) for n, p in _CORE_ORGANS])

    ok = sum(1 for v in results.values() if v == "ok")
    error_count = len(results) - ok
    avg_ms = round(sum(timings.values()) / max(len(timings), 1), 1)
    slowest = sorted(timings.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "organs": results,
        "healthy_count": ok,
        "error_count": error_count,
        "total_count": len(results),
        "status": "ok"
        if ok == len(results)
        else "degraded"
        if ok > len(results) // 2
        else "critical",
        "latency": {
            "avg_ms": avg_ms,
            "slowest": [{"organ": name, "ms": ms} for name, ms in slowest],
        },
    }


async def _get_system_metrics() -> dict:
    """Get system resource metrics."""
    try:
        import psutil

        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return {
            "cpu_percent": cpu,
            "memory": {
                "total_mb": round(mem.total / 1024 / 1024),
                "used_mb": round(mem.used / 1024 / 1024),
                "percent": mem.percent,
            },
            "disk": {
                "total_gb": round(disk.total / 1024 / 1024 / 1024, 1),
                "used_gb": round(disk.used / 1024 / 1024 / 1024, 1),
                "percent": round(disk.percent, 1),
            },
        }
    except ImportError:
        return {"error": "psutil not installed"}


async def _get_knowledge_stats() -> dict:
    """Get knowledge base statistics."""
    try:
        from src.database.postgres import db_pool

        if db_pool:
            total = await db_pool.fetchval("SELECT COUNT(*) FROM knowledge") or 0
            return {"total_entries": total}
    except Exception as exc:
        logging.getLogger(__name__).debug("probe skipped: %s", exc)
    return {"total_entries": -1}


async def _get_plugin_stats() -> dict:
    """Get plugin statistics."""
    try:
        from src.plugin_loader import loaded_plugins

        return {
            "total_plugins": len(loaded_plugins),
            "active_plugins": sum(1 for p in loaded_plugins if p.get("has_backend")),
        }
    except Exception as exc:
        # C2统一错误响应：失败带error字段，禁静默0冒充"没插件"
        return {"total_plugins": 0, "active_plugins": 0, "error": str(exc)}


async def _get_gland_usage() -> dict:
    """Get model gateway usage summary."""
    try:
        from src.gland.token_meter import TokenMeter

        meter = TokenMeter()
        summary = meter.summary()
        return {
            "total_tokens": summary.get("total_tokens", 0),
            "call_count": summary.get("call_count", 0),
            "by_model": summary.get("by_model", {}),
        }
    except Exception as exc:
        # C2统一错误响应：失败带error字段，禁静默0冒充"没调用"
        return {"total_tokens": 0, "call_count": 0, "error": str(exc)}


# ── Endpoints ─────────────────────────────────────────────────


@router.get("/health")
async def system_overview_health():
    """System overview health check."""
    return {"status": "ok", "component": "OpenSystem"}


@router.get("/overview")
async def system_overview():
    """Complete system overview — single endpoint for dashboard.

    Returns aggregated data from all subsystems:
    - Organ health status
    - System resource metrics
    - Knowledge base stats
    - Plugin status
    - Model gateway usage
    """
    start = time.time()

    # Run all checks in parallel
    organs, metrics, knowledge, plugins, gland = await asyncio.gather(
        _check_organs(),
        _get_system_metrics(),
        _get_knowledge_stats(),
        _get_plugin_stats(),
        _get_gland_usage(),
        return_exceptions=True,
    )

    # Handle exceptions
    if isinstance(organs, Exception):
        organs = {"organs": {}, "healthy_count": 0, "total_count": 0, "status": "error"}
    if isinstance(metrics, Exception):
        metrics = {"error": str(metrics)}
    if isinstance(knowledge, Exception):
        knowledge = {"total_entries": -1}
    if isinstance(plugins, Exception):
        plugins = {"total_plugins": 0}
    if isinstance(gland, Exception):
        gland = {"total_tokens": 0}

    elapsed = round((time.time() - start) * 1000)

    return {
        "timestamp": time.time(),
        "elapsed_ms": elapsed,
        "version": "2.0.0",
        "system_status": organs.get("status", "unknown"),
        "organs": organs,
        "metrics": metrics,
        "knowledge": knowledge,
        "plugins": plugins,
        "gland": gland,
    }


from pydantic import BaseModel

from src.system.bootstrap import SystemBootstrap

bootstrap = SystemBootstrap()


class BootstrapRequest(BaseModel):
    force: bool = False


@router.get("/quick")
async def system_quick_status():
    """Ultra-lightweight status check — just the essentials."""
    from src.database.postgres import db_pool

    try:
        db_ok = db_pool is not None
    except Exception:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "version": "2.0.0",
        "timestamp": time.time(),
    }


# ── Bootstrap Endpoints ─────────────────────────────────────


@router.get("/bootstrap/status")
async def bootstrap_status():
    """Get current bootstrap state — has the system been initialized?"""
    return {
        "status": "ok",
        "component": "OpenSystem",
        **bootstrap.state,
    }


@router.post("/bootstrap/run")
async def run_bootstrap(
    req: BootstrapRequest = None,
    authorization: str | None = Header(None),
):
    """Run system bootstrap — auto-configure default cross-organ integrations.

    Sets up:
    - OpenMarrow: daily backup schedule for data directory
    - OpenPulse: health check signal every 60s
    - OpenImmune: default rate-limit configuration
    - Vital→Echo: alert notification wiring
    - OpenGene: verify default templates exist

    Idempotent — safe to call multiple times. Use force=true to re-run.
    """
    identity = bind_identity(authorization)
    force = req.force if req else False
    result = bootstrap.run_bootstrap(force=force)

    # Emit event to Nerve bus
    try:
        from src.nerve.event_bridge import push_event

        push_event(
            {
                "organ": "system",
                "emoji": "🚀",
                "type": "bootstrap",
                "summary": f"🔧 System bootstrap: {result['status']}",
                "detail": result,
            },
            tenant_id=identity.tenant_id,
        )
    except Exception as exc:
        logging.getLogger(__name__).debug("probe skipped: %s", exc)

    return result
