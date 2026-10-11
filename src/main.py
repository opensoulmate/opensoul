import logging
import os
import stat
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.a2a.api import router as a2a_router
from src.api.admin_actions import router as admin_actions_router
from src.api.agent import router as agent_router
from src.api.agent_collaboration import router as agent_collab_router
from src.api.agent_proxy import router as agent_proxy_router
from src.api.agents import router as agents_router
from src.api.ai_engine import router as ai_engine_router
from src.api.ai_groups import router as ai_groups_router
from src.api.benchmark import router as benchmark_router
from src.api.brain import router as brain_router
from src.api.browser_api import router as browser_router
from src.api.capture import router as capture_router
from src.api.chat import router as chat_router
from src.api.config_api import router as config_api_router
from src.api.context_api import router as context_router
from src.api.cortex import router as cortex_router
from src.api.cortex_enhanced import router as cortex_enhanced_router
from src.api.dedup import router as dedup_router
from src.api.diagnostics import router as diagnostics_router
from src.api.download_api import router as download_router
from src.api.echo import router as echo_router
from src.api.enterprise import router as enterprise_router
from src.api.entity import router as entity_router
from src.api.event_stream import router as event_stream_router
from src.api.export import router as export_router
from src.api.feedback import router as feedback_router
from src.api.gene import router as gene_router
from src.api.gene_templates import router as gene_templates_router
from src.api.git_api import router as git_router
from src.api.gland import gateway as gland_gateway
from src.api.gland import router as gland_router
from src.api.graph import router as graph_router
from src.api.healer import router as healer_router
from src.api.heredity import router as heredity_router
from src.api.hermes_bridge import router as hermes_bridge_router
from src.api.hermes_cron import router as hermes_cron_router
from src.api.hippo import router as hippo_router
from src.api.immune import router as immune_router
from src.api.intelligence import router as intelligence_router
from src.api.kb_sharing import router as kb_sharing_router
from src.api.knowledge import router as knowledge_router
from src.api.knowledge_requests import router as knowledge_requests_router
from src.api.learn import router as learn_router
from src.api.limb import router as limb_router
from src.api.limb_rpa import router as limb_rpa_router
from src.api.link import router as link_router
from src.api.link_gateway import router as link_gateway_router
from src.api.llm import router as llm_router
from src.api.marketplace import router as marketplace_router
from src.api.marrow import router as marrow_router
from src.api.mcp import router as mcp_router
from src.api.metrics_api import router as metrics_router
from src.api.mind import router as mind_router
from src.api.mirror import router as mirror_router
from src.api.model_router import router as model_router_router
from src.api.nerve import router as nerve_router
from src.api.nest import router as nest_router
from src.api.notifications import router as notifications_router
from src.api.permission import router as permission_router
from src.api.pipeline import router as pipeline_router
from src.api.plugins_api import router as plugins_router
from src.api.proactive import router as proactive_router
from src.api.pulse import router as pulse_router
from src.api.reflex import router as reflex_router
from src.api.registry import router as registry_router
from src.api.search import router as search_router
from src.api.sense import router as sense_router
from src.api.sessions_api import router as sessions_router
from src.api.skills import router as skills_router
from src.api.soma_connector import router as soma_connector_router
from src.api.soma_discovery import router as soma_discovery_router
from src.api.system_overview import router as system_overview_router
from src.api.tag import router as tag_router
from src.api.terminal_ws import router as terminal_router
from src.api.timeline import router as timeline_router
from src.api.topology import router as topology_router
from src.api.trajectory import router as trajectory_router
from src.api.trajectory_api import router as trajectory_api_router
from src.api.user import router as user_router
from src.api.vein import router as vein_router
from src.api.vision import router as vision_router
from src.api.vital import router as vital_router
from src.api.voice import router as voice_router
from src.api.will import router as will_router
from src.api.workflow import router as workflow_router
from src.api.workspace_api import router as workspace_router
from src.api.ws_chat import router as ws_chat_router
from src.config import settings
from src.database.meilisearch import meili_client
from src.database.postgres import db_pool
from src.database.qdrant import qdrant_client
from src.middleware.intrusion_middleware import IntrusionDetectionMiddleware
from src.middleware.metrics_middleware import MetricsMiddleware
from src.plugin_loader import load_all_plugins
from src.vital.alert import AlertManager
from src.vital.collector import MetricsCollector
from src.vital.health import HealthChecker

logger = logging.getLogger(__name__)


def configure_app_logging() -> None:
    """给根logger挂handler，让app级INFO+日志到达journal。

    背景（2026-10-06 19:05轮journal实测）：uvicorn只配置自己的loggers
    （propagate=no），src模块的 logging.getLogger(__name__)（144处）传播到
    无handler的root——logging.lastResort只输出WARNING+且是裸消息
    （无级别/无模块名），INFO被整体丢弃："Auto-bootstrap result"等运行证据
    用户在journal里从未见过。挂上handler后：INFO+全量进journal且带
    级别/模块名，lastResort的裸消息问题一并根治。

    幂等：已有root handler时不重复挂，只确保级别放行INFO。第三方噪声
    logger压到WARNING（httpx每请求一条INFO会淹没journal——内网健康探针
    每分钟20+请求）。
    """
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    for noisy in (
        "httpx",
        "httpcore",
        "urllib3",
        "asyncio",
        "charset_normalizer",
        "sqlalchemy",
        "multipart",
        "watchfiles",
        "openai",
        "anthropic",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# 运行时（uvicorn src.main:app）模块导入即配置；pytest下跳过避免测试输出
# 被INFO日志污染——测试直接调用 configure_app_logging() 验证行为
# （tests/test_app_logging_config.py），pytest在sys.modules里故可靠。
if "pytest" not in sys.modules:
    configure_app_logging()


async def _intelligence_auto_collect():
    """Background task: periodically collect metrics from all organs for Intelligence analysis."""
    import time as _time

    import httpx as _httpx

    from src.api.intelligence import _ORGAN_ENDPOINTS
    from src.api.intelligence import intelligence as _intel

    # Wait a bit for the server to fully start
    await asyncio.sleep(10)

    while True:
        try:
            base = "http://127.0.0.1:8090"
            async with _httpx.AsyncClient(timeout=5.0) as client:

                async def _collect(name: str, path: str):
                    start = _time.time()
                    try:
                        r = await client.get(f"{base}{path}")
                        elapsed_ms = (_time.time() - start) * 1000
                        data = r.json() if r.status_code == 200 else {}
                        _intel.record_metrics(
                            name,
                            {
                                "health": "ok" if r.status_code == 200 else "error",
                                "response_time_ms": elapsed_ms,
                                "custom": {k: v for k, v in data.items() if k != "status"},
                            },
                        )
                    except Exception:
                        elapsed_ms = (_time.time() - start) * 1000
                        _intel.record_metrics(
                            name,
                            {
                                "health": "error",
                                "response_time_ms": elapsed_ms,
                                "error_count": 1,
                            },
                        )

                await asyncio.gather(*[_collect(n, p) for n, p in _ORGAN_ENDPOINTS])
        except Exception:
            pass  # Never crash the background task

        await asyncio.sleep(120)  # Collect every 2 minutes


async def _evolution_retention_loop():
    """Background task: 进化提案stale pending周期清扫（agno stale回收的运行时接线）。

    为什么需要（"写了≠接线了"缺口）：EvolutionEngine.store.retire_stale_pending
    （0b5e0372进化预算自愈三件套）此前只挂在 declare_intent 声明路径惰性清扫——
    声明一停清扫就停。生产实证：2026-10-04 23:48批量立案后SelfEvolution因数据面
    干净不再声明，5条假模式pending（test_job 910次等测试噪声）超龄48h TTL无人
    清扫，长期滞留pending面板——"没有进化啊"的度量前提=面板只显示真实待审提案。

    频率：启动后20s首扫（开机即清账）+每3600s一扫（TTL=48h，小时级精度足够）。
    清扫经 src.api.heredity.sweep_stale_proposals（失败不抛出，绝不反噬主进程）；
    退役是账本可见终态（REJECTED actor=retention），非静默丢弃。
    """
    import time as _time

    from src.api.heredity import sweep_stale_proposals

    await asyncio.sleep(20)  # 等器官就绪后首扫（开机即清账）
    while True:
        start = _time.time()
        try:
            result = sweep_stale_proposals()
            retired = result.get("retired") or []
            # 每次清扫都自报（noop也报——claude-code noop自报范式）：
            # journal里能看到"清扫在跑"，空转也是运行证据（用户极度重视可观测性）
            logger.info(
                "[evolution-retention] sweep done: retired=%d in %.0fms%s",
                len(retired),
                (_time.time() - start) * 1000,
                f" {retired}" if retired else "",
            )
            if result.get("error"):
                logger.warning("[evolution-retention] sweep error: %s", result["error"])
        except Exception:
            logger.warning("[evolution-retention] sweep crashed", exc_info=True)
        await asyncio.sleep(3600)  # 每小时清扫一次


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await db_pool.connect()

    # Ensure kb_sharing_requests and knowledge_requests tables exist
    # Ensure workflow_tasks table exists with all columns
    await db_pool.execute("""CREATE TABLE IF NOT EXISTS workflow_tasks (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
        description TEXT DEFAULT '', task_type TEXT DEFAULT 'manual',
        config TEXT DEFAULT '{}', schedule TEXT, status TEXT DEFAULT 'idle',
        last_run_at TEXT, next_run_at TEXT,
        created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')))""")

    await db_pool.execute("""CREATE TABLE IF NOT EXISTS kb_sharing_requests (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, user_name TEXT DEFAULT '',
        kb_id TEXT NOT NULL, kb_name TEXT NOT NULL, status TEXT DEFAULT 'pending',
        reviewer_id TEXT, review_note TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')), reviewed_at TEXT)""")
    await db_pool.execute("""CREATE TABLE IF NOT EXISTS knowledge_requests (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, user_name TEXT DEFAULT '',
        kb_name TEXT NOT NULL, kb_description TEXT DEFAULT '',
        status TEXT DEFAULT 'pending', reviewer_id TEXT, review_note TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')), reviewed_at TEXT)""")

    # 核心业务表自愈初始化（对齐生产data/opensoul.db实际schema——新环境/CI空库自动建表）
    await db_pool.execute("""CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
        email TEXT DEFAULT '', password_hash TEXT NOT NULL,
        role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')))""")
    await db_pool.execute("""CREATE TABLE IF NOT EXISTS knowledge (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
        title TEXT NOT NULL, content TEXT DEFAULT '',
        tags TEXT DEFAULT '[]', source TEXT DEFAULT '',
        content_type TEXT DEFAULT '', metadata TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')))""")
    await db_pool.execute("""CREATE TABLE IF NOT EXISTS knowledge_chunks (
        id TEXT PRIMARY KEY, knowledge_id TEXT NOT NULL,
        chunk_index INTEGER NOT NULL, content TEXT NOT NULL,
        embedding_id TEXT, token_count INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')))""")
    await db_pool.execute("""CREATE TABLE IF NOT EXISTS entities (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
        name TEXT NOT NULL, type TEXT DEFAULT '',
        description TEXT DEFAULT '', properties TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')))""")
    await db_pool.execute("""CREATE TABLE IF NOT EXISTS relations (
        id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
        target_id TEXT NOT NULL, type TEXT NOT NULL,
        properties TEXT DEFAULT '{}', created_at TEXT DEFAULT (datetime('now')))""")

    try:
        qdrant_client.ensure_collection()
    except Exception:
        print("WARNING: Qdrant not available, skipping")
    meili_client.ensure_index()
    # Meilisearch知识库索引管道：启动自动全量索引（fire-and-forget不阻塞启动）
    try:
        from src.services.meili_indexer import index_all_knowledge

        asyncio.create_task(index_all_knowledge())
    except Exception:
        print("WARNING: meilisearch startup indexer failed")
    await gland_gateway.startup()

    # Vital services
    collector = MetricsCollector()
    checker = HealthChecker()
    alert_mgr = AlertManager(collector)
    app.state.vital_collector = collector
    app.state.vital_checker = checker
    app.state.vital_alert_mgr = alert_mgr
    await collector.start()
    await alert_mgr.start()

    # job queue worker池显式启动 — dream周期生产者提交的hippo.dream等job
    # 必须由主进程worker执行（此前worker池从未在主进程启动,job积压pending）
    from src.will.job_handlers import register_default_handlers
    from src.will.job_queue import get_job_queue

    jq = get_job_queue()
    register_default_handlers(jq)
    await jq.start()

    # Intelligence auto-collect background task
    intel_task = asyncio.create_task(_intelligence_auto_collect())

    # Metrics organ health poller
    async def _metrics_organ_poller():
        """Periodically poll organ health and feed to Prometheus metrics."""
        import httpx as _mhttpx

        from src.api.metrics_api import record_organ_health

        _METRIC_ORGANS = {
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
        }

        await asyncio.sleep(10)  # Wait for organs to be ready
        async with _mhttpx.AsyncClient(timeout=3.0) as client:
            while True:
                for organ, endpoint in _METRIC_ORGANS.items():
                    try:
                        r = await client.get(f"http://127.0.0.1:8090{endpoint}")
                        ok = r.status_code == 200
                        record_organ_health(organ, ok)
                    except Exception:
                        record_organ_health(organ, False)
                await asyncio.sleep(60)  # Poll every 60s

    metrics_task = asyncio.create_task(_metrics_organ_poller())

    # Auto-bootstrap on first boot (non-blocking)
    async def _auto_bootstrap():
        await asyncio.sleep(5)  # Wait for all organs to be ready
        try:
            from src.system.bootstrap import SystemBootstrap

            bs = SystemBootstrap()
            if not bs.is_bootstrapped:
                result = bs.run_bootstrap()
                logger.info("Auto-bootstrap result: %s", result.get("status"))
        except Exception as e:
            logger.warning("Auto-bootstrap failed: %s", e)

    bootstrap_task = asyncio.create_task(_auto_bootstrap())

    # Dream记忆蒸馏周期生产者（集成修复#2：hippo.dream此前无调度，全系统仅执行1次）
    # 消息源=opensoul.db agent_messages真实聊天，24h一轮，幂等键按日期防重复
    from src.will.dream_producer import dream_producer_loop

    dream_task = asyncio.create_task(dream_producer_loop())

    # 进化提案stale pending周期清扫（48h TTL契约运行时接线，见_evolution_retention_loop）
    retention_task = asyncio.create_task(_evolution_retention_loop())

    # 主动行为引擎（A3）：心跳驱动的定期任务，不只被动响应——
    # 三个默认行为（memory_decay/experience_collect/heredity_triggers）
    # 按各自interval到期执行，每次执行留痕data/proactive.db，见src/will/proactive.py
    from src.will.proactive import proactive_engine
    from src.will.proactive_behaviors import register_default_watchers

    register_default_watchers(proactive_engine)
    await proactive_engine.start(interval_seconds=30)

    yield

    # Shutdown
    await proactive_engine.stop()
    bootstrap_task.cancel()
    dream_task.cancel()
    retention_task.cancel()
    intel_task.cancel()
    metrics_task.cancel()
    try:
        await intel_task
    except asyncio.CancelledError:
        pass
    await alert_mgr.stop()
    await collector.stop()
    await gland_gateway.shutdown()
    await db_pool.disconnect()


app = FastAPI(
    title="OpenSoul",
    description="Central Memory Kernel — REST API",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware - allow all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Metrics middleware — tracks request count, latency, errors
app.add_middleware(MetricsMiddleware)
app.add_middleware(IntrusionDetectionMiddleware)


class AdminStaticFiles(StaticFiles):
    """StaticFiles + 净URL回退（Next 静态导出产出 <page>.html 形状）。

    直入/刷新 /admin/<page>（无 .html）此前 404（目录转跳后寻 index.html 失败
    或 404.html 兜底），这里对 404 回退到同名 <page>.html；真缺失页仍如实 404，
    带扩展名的资源（.js/.css 等）不做回退。
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code != 404 or path.endswith(".html"):
            return response
        alt = path.rstrip("/") + ".html"
        full_path, stat_result = self.lookup_path(alt)
        if stat_result is not None and stat.S_ISREG(stat_result.st_mode):
            return await super().get_response(alt, scope)
        return response


# Static files
_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_static_dir), name="static")
app.mount(
    "/admin",
    AdminStaticFiles(directory=os.path.join(_static_dir, "admin"), html=True),
    name="admin",
)


# Root route — serve admin dashboard
@app.get("/")
async def index():
    return {
        "message": "OpenSoul API — 前端请访问 OpenMate (http://localhost:3002)",
        "docs": "/docs",
    }


import asyncio

import httpx


# Health check endpoint
@app.get("/api/health")
async def health():
    """Soul health — includes database, knowledge, and system stats."""
    import time

    start = time.time()

    # Collect stats from core subsystems
    stats: dict[str, object] = {"component": "OpenSoul", "status": "ok"}

    # Database stats
    try:
        stats["database"] = {"type": "sqlite", "status": "ok"}
    except Exception:
        stats["database"] = {"status": "error"}

    # Knowledge base stats
    try:
        from src.database.postgres import db_pool

        if db_pool:
            rows = await db_pool.fetch("SELECT COUNT(*) as cnt FROM knowledge")
            stats["knowledge_entries"] = rows[0]["cnt"] if rows else 0
    except Exception:
        stats["knowledge_entries"] = -1

    # Version info
    stats["version"] = "2.0.0"
    stats["uptime_check_ms"] = round((time.time() - start) * 1000, 2)

    return stats


# Unified health — checks all organ endpoints in parallel
_ORGAN_HEALTH_ROUTES = [
    ("soul", "/api/health"),
    ("cortex", "/api/cortex/health"),
    ("cortex-enhanced", "/api/cortex/enhanced/health"),
    ("nerve", "/api/nerve/health"),
    ("vein", "/api/vein/health"),
    ("sense", "/api/sense/health"),
    ("will", "/api/will/health"),
    ("immune", "/api/immune/health"),
    ("vital", "/api/vital/health"),
    ("marrow", "/api/marrow/health"),
    ("gland", "/api/gland/health"),
    ("gene", "/api/gene/health"),
    ("gene-templates", "/api/gene/adapter-health"),
    ("echo", "/api/echo/health"),
    ("mirror", "/api/mirror/health"),
    ("link", "/api/link/health"),
    ("hippo", "/api/hippo/health"),
    ("reflex", "/api/reflex/health"),
    ("heredity", "/api/heredity/health"),
    ("pulse", "/api/pulse/health"),
    ("nest", "/api/nest/health"),
    ("limb", "/api/limb/health"),
    ("limb-rpa", "/api/limb/rpa/health"),
    ("voice", "/api/voice/health"),
    ("vision", "/api/vision/health"),
    ("mind", "/api/mind/health"),
    ("intelligence", "/api/intelligence/health"),
    ("trajectory", "/api/trajectory/health"),
    ("mcp", "/api/mcp/health"),
    ("learn", "/api/learn/health"),
    ("diagnostics", "/api/diagnostics/health"),
    ("soma-connector", "/api/soma/health"),
    ("event-stream", "/api/events/health"),
    ("capture", "/api/capture/health"),
    ("pipeline", "/api/pipeline/health"),
    ("topology", "/api/topology/health"),
    ("graph", "/api/graph/health"),
    ("entity", "/api/entity/health"),
    ("tag", "/api/tags/health"),
    ("user", "/api/user/health"),
    ("llm", "/api/llm/health"),
    ("agent", "/api/agent/health"),
    ("export", "/api/export/health"),
    ("search", "/api/search/health"),
    ("chat", "/api/chat/health"),
    ("workspace", "/api/workspace/health"),
    ("workflow", "/api/workflow/health"),
    ("collab", "/api/collab/health"),
    ("marketplace", "/api/marketplace/health"),
    ("skills", "/api/skills/health"),
    ("notifications", "/api/notifications/health"),
    ("knowledge", "/api/knowledge/health"),
    ("healer", "/api/healer/health"),
    ("timeline", "/api/timeline/health"),
    ("benchmark", "/api/benchmark/health"),
    ("agent-proxy", "/api/agent-proxy/health"),
    ("agents", "/api/agents/health"),
    ("ai-engine", "/api/ai-engine/health"),
    ("ai-groups", "/api/ai-groups/health"),
    ("config", "/api/config/health"),
    ("dedup", "/api/dedup/health"),
    ("download", "/api/download/health"),
    ("enterprise", "/api/enterprise/health"),
    ("git", "/api/git/health"),
    ("hermes-bridge", "/api/hermes/health"),
    ("hermes-cron", "/api/cron/health"),
    ("kb-sharing", "/api/kb-sharing/health"),
    ("knowledge-requests", "/api/knowledge-requests/health"),
    ("permission", "/api/permission/health"),
    ("sessions", "/api/sessions/health"),
    ("terminal", "/api/terminal/health"),
    ("browser", "/api/browser/health"),
    ("context", "/api/context/health"),
    ("system-overview", "/api/system/health"),
]


@app.get("/api/health/all")
async def health_all():
    """Check all organ health endpoints and return aggregated status."""
    base = "http://127.0.0.1:8090"
    results = {}
    async with httpx.AsyncClient(timeout=5.0) as client:

        async def _check(name: str, path: str):
            try:
                r = await client.get(f"{base}{path}")
                results[name] = "ok" if r.status_code == 200 else "error"
            except Exception:
                results[name] = "error"

        await asyncio.gather(*[_check(n, p) for n, p in _ORGAN_HEALTH_ROUTES])
    ok = sum(1 for v in results.values() if v == "ok")
    return {
        "status": "ok" if ok == len(results) else "degraded",
        "healthy": ok,
        "total": len(results),
        "organs": results,
    }


# Version endpoint
@app.get("/api/version")
async def version():
    return {"version": app.version, "name": app.title}


# Register all API routers
app.include_router(knowledge_router, prefix="/api/knowledge", tags=["knowledge"])
app.include_router(feedback_router, prefix="/api/feedback", tags=["feedback"])
app.include_router(
    knowledge_requests_router, prefix="/api/knowledge-requests", tags=["knowledge-requests"]
)
app.include_router(kb_sharing_router, prefix="/api/kb-sharing", tags=["kb-sharing"])
app.include_router(dedup_router, prefix="/api/dedup", tags=["dedup"])
app.include_router(permission_router, prefix="/api/permission", tags=["permission"])
app.include_router(agent_proxy_router, prefix="/api/agent-proxy", tags=["agent-proxy"])
app.include_router(a2a_router, tags=["a2a"])

app.include_router(hermes_bridge_router, prefix="/api/hermes", tags=["hermes-bridge"])
app.include_router(hermes_cron_router, prefix="/api/cron", tags=["cron"])
app.include_router(agents_router, prefix="/api/agents", tags=["agents"])
app.include_router(skills_router, prefix="/api/skills", tags=["skills"])
app.include_router(marketplace_router, prefix="/api/marketplace", tags=["marketplace"])
app.include_router(download_router, prefix="/api/download", tags=["download"])
app.include_router(terminal_router, prefix="/api/terminal", tags=["terminal"])
app.include_router(ai_groups_router, tags=["ai-groups"])
app.include_router(ai_engine_router, tags=["ai-engine"])
app.include_router(agent_collab_router, prefix="/api/collab", tags=["agent-collaboration"])
app.include_router(enterprise_router, tags=["enterprise"])
app.include_router(search_router, prefix="/api/search", tags=["search"])
app.include_router(chat_router, prefix="/api/chat", tags=["chat"])
app.include_router(graph_router, prefix="/api/graph", tags=["graph"])
app.include_router(entity_router, prefix="/api/entity", tags=["entity"])
app.include_router(tag_router, prefix="/api/tags", tags=["tags"])
app.include_router(user_router, prefix="/api/user", tags=["user"])
app.include_router(llm_router, prefix="/api/llm", tags=["llm"])
app.include_router(model_router_router, prefix="/api/model-router", tags=["model-router"])
app.include_router(agent_router, prefix="/api/agent", tags=["agent"])
app.include_router(export_router, prefix="/api/export", tags=["export"])
app.include_router(cortex_router, prefix="/api/cortex", tags=["cortex"])
app.include_router(cortex_enhanced_router, prefix="/api/cortex", tags=["cortex-enhanced"])
app.include_router(nerve_router, prefix="/api/nerve", tags=["nerve"])
app.include_router(will_router, prefix="/api/will", tags=["will"])
app.include_router(gland_router, prefix="/api/gland", tags=["gland"])
app.include_router(vital_router, prefix="/api/vital", tags=["vital"])
app.include_router(vein_router, prefix="/api/vein", tags=["vein"])
app.include_router(sense_router, prefix="/api/sense", tags=["sense"])
app.include_router(immune_router, prefix="/api/immune", tags=["immune"])
app.include_router(marrow_router, prefix="/api/marrow", tags=["marrow"])
app.include_router(gene_router, prefix="/api/gene", tags=["gene"])
app.include_router(gene_templates_router, prefix="/api/gene", tags=["Gene Templates"])
app.include_router(echo_router, prefix="/api/echo", tags=["echo"])
app.include_router(mirror_router, prefix="/api/mirror", tags=["mirror"])
app.include_router(link_router, prefix="/api/link", tags=["link"])
app.include_router(link_gateway_router, prefix="/api/link", tags=["Link Gateway"])
app.include_router(hippo_router, prefix="/api/hippo", tags=["hippo"])
app.include_router(reflex_router, prefix="/api/reflex", tags=["reflex"])
app.include_router(heredity_router, prefix="/api/heredity", tags=["heredity"])
app.include_router(pulse_router, prefix="/api/pulse", tags=["pulse"])
app.include_router(proactive_router, prefix="/api/proactive", tags=["proactive"])
app.include_router(nest_router, prefix="/api/nest", tags=["nest"])
app.include_router(limb_router, prefix="/api/limb", tags=["limb"])
app.include_router(limb_rpa_router, prefix="/api/limb/rpa", tags=["Limb RPA"])
app.include_router(voice_router, prefix="/api/voice", tags=["voice"])
app.include_router(vision_router, prefix="/api/vision", tags=["vision"])
app.include_router(mind_router, prefix="/api/mind", tags=["mind"])
app.include_router(brain_router, prefix="/api/brain", tags=["brain"])
app.include_router(intelligence_router, prefix="/api/intelligence", tags=["intelligence"])
app.include_router(trajectory_router, prefix="/api/trajectory", tags=["trajectory"])
app.include_router(trajectory_api_router, prefix="/api/trajectory-v2", tags=["trajectory-v2"])
app.include_router(plugins_router, prefix="/api/plugins", tags=["plugins"])
app.include_router(mcp_router, prefix="/api/mcp", tags=["mcp"])
app.include_router(learn_router, prefix="/api/learn", tags=["learn"])
app.include_router(registry_router, prefix="/api/registry", tags=["registry"])
app.include_router(soma_connector_router, prefix="/api/soma", tags=["soma-connector"])
app.include_router(soma_discovery_router, prefix="/api/soma/discovery", tags=["Soma Discovery"])
app.include_router(config_api_router, prefix="/api", tags=["config"])
app.include_router(diagnostics_router, prefix="/api/diagnostics", tags=["diagnostics"])
app.include_router(admin_actions_router, prefix="/api/admin", tags=["admin"])
app.include_router(sessions_router, prefix="/api/sessions", tags=["sessions"])
app.include_router(event_stream_router, prefix="/api/events", tags=["event-stream"])
app.include_router(notifications_router, prefix="/api/notifications", tags=["notifications"])
app.include_router(workspace_router, prefix="/api", tags=["workspace"])
app.include_router(git_router, prefix="/api/git", tags=["git"])
app.include_router(capture_router, prefix="/api/capture", tags=["capture"])
app.include_router(browser_router, prefix="/api/browser", tags=["browser"])
app.include_router(context_router, prefix="/api/context", tags=["context"])
app.include_router(pipeline_router, prefix="/api/pipeline", tags=["pipeline"])
app.include_router(topology_router, prefix="/api/topology", tags=["topology"])
app.include_router(workflow_router, prefix="/api/workflow", tags=["workflow"])
app.include_router(healer_router, prefix="/api/healer", tags=["healer"])
app.include_router(timeline_router, prefix="/api/timeline", tags=["timeline"])
app.include_router(benchmark_router, prefix="/api/benchmark", tags=["benchmark"])
app.include_router(system_overview_router, prefix="/api/system", tags=["system-overview"])
app.include_router(metrics_router, tags=["metrics"])
app.include_router(ws_chat_router, prefix="/api/ws", tags=["ws-chat"])

# Load and mount external plugins from ~/.openmate/plugins/
load_all_plugins(app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host=settings.host, port=settings.port, reload=settings.debug)
