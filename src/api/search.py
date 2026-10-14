import asyncio
from uuid import UUID

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel

from src.services.search import fulltext_search, hybrid_search, semantic_search
from src.services.tenant_binding import bind_identity

router = APIRouter()


@router.get("/health")
async def search_health():
    """Search system health check."""
    return {"status": "ok", "component": "SearchSystem"}


@router.get("/stats")
async def search_stats():
    """Get search system statistics."""
    from src.api._errors import stats_error
    from src.database.postgres import db_pool
    from src.services.meili_indexer import get_index_stats

    try:
        knowledge_count = await db_pool.fetchval("SELECT COUNT(*) FROM knowledge") or 0
        return {
            "status": "ok",
            "component": "SearchSystem",
            "searchable_entries": knowledge_count,
            "modes": ["semantic", "fulltext", "hybrid"],
            "meilisearch": get_index_stats(),
        }
    except Exception as exc:
        # C2统一错误响应：失败说失败、说原因，禁谎报ok（假0冒充"没数据"）
        return stats_error(
            "SearchSystem",
            exc,
            searchable_entries=0,
            modes=["semantic", "fulltext", "hybrid"],
            meilisearch=get_index_stats(),
        )


class SearchRequest(BaseModel):
    query: str
    mode: str = "hybrid"  # semantic, fulltext, hybrid
    limit: int = 10


def _resolve_user_id(user_id: str) -> UUID:
    """Convert user_id to UUID — accept both UUID strings and plain usernames."""
    try:
        return UUID(user_id)
    except ValueError:
        # Hash non-UUID strings (e.g. usernames) into a deterministic UUID
        import hashlib

        h = hashlib.md5(user_id.encode()).hexdigest()
        return UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


@router.get("/")
async def search_get(
    q: str = Query(..., description="Search query"),
    user_id: str = Query("default", description="User ID (UUID or username)"),
    mode: str = Query("hybrid", description="Search mode: semantic, fulltext, hybrid"),
    limit: int = Query(10, ge=1, le=50),
    authorization: str | None = Header(None),
):
    """Full-text + vector hybrid search via GET.

    身份轴收口：user_id 从认证派生（bind_identity），自报值只可确认派生绑定
    ——认证请求自报他人档案=403（冒读面收口）；匿名保留自报（既有本地多
    档案语义，F3b-② 契约）。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    if mode == "semantic":
        results = await semantic_search(q, uid, limit)
    elif mode == "fulltext":
        results = await fulltext_search(q, uid, limit)
    else:
        results = await hybrid_search(q, uid, limit)
    return {"query": q, "mode": mode, "results": results}


@router.post("/")
async def search_post(
    req: SearchRequest,
    user_id: str = "default",
    authorization: str | None = Header(None),
):
    """Search via POST body. 身份轴同 GET（bind_identity 派生，禁自报冒读）。"""
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    if req.mode == "semantic":
        results = await semantic_search(req.query, uid, req.limit)
    elif req.mode == "fulltext":
        results = await fulltext_search(req.query, uid, req.limit)
    else:
        results = await hybrid_search(req.query, uid, req.limit)
    return {"query": req.query, "mode": req.mode, "results": results}


# ── Unified Search — searches across multiple subsystems ──────


async def _search_knowledge(query: str, limit: int, uid: UUID) -> list[dict]:
    """Search knowledge base（uid=身份派生的档案键，修前硬编码 'default'）。"""
    try:
        results = await hybrid_search(query, uid, limit)
        return [{**r, "source": "knowledge", "icon": "📚"} for r in results]
    except Exception:
        return []


async def _search_files(query: str, limit: int, tenant_id: str) -> list[dict]:
    """Search files in Vein（tenant_id=身份派生租户，DEC-⑦-④ 加轴）。

    行归属判法=file_row_tenant 单一源（list_files 内过滤，自带键缺失/空白→
    'default' 兜底，DEC-⑦-① _event_row_tenant / ② message_row_tenant /
    ③ template_row_tenant 同款）；过滤先于截断=limit 只计可见命中。读真源=
    src.api.vein 模块单例 store（修前函数内 new FileStore() 逐调用开新 SQLite
    连接——DB 真源无漂移面，收口=与管理面同一实例连接复用，DEC-⑦-②③
    读真源收口同款纪律）。
    """
    try:
        from src.api import vein as vein_api

        store = vein_api.store
        files = store.list_files(name_filter=query, limit=limit, tenant_id=tenant_id)
        return [
            {
                "source": "files",
                "icon": "📄",
                "title": f.name,
                "snippet": f"{f.mime_type} · {f.size} bytes",
                "file_id": f.file_id,
                "size": f.size,
                "mime_type": f.mime_type,
                "created_at": f.created_at,
            }
            for f in files
        ]
    except Exception:
        return []


def _event_row_tenant(ev: dict) -> str:
    """事件行租户键单一判法（timeline record() 同款语义，③c-2①）：
    事件自带 tenant_id 键、缺失/空白→'default' 兜底（器官内生系统事件
    =DEC-① 1a/1b/1d 'default' 语义）。禁在消费点另发明判法（F4b-㉗ 纪律）。"""
    return str(ev.get("tenant_id") or "").strip() or "default"


async def _search_events(query: str, limit: int, tenant_id: str) -> list[dict]:
    """Search event stream（DEC-⑦-①：恒按派生租户过滤，内存缓冲面不再是系统视图）。

    修前零过滤=任意租户可见全缓冲（跨租户泄露，与 timeline 面 ③c-2① 已收的
    查询过滤不对称）。行归属判法=_event_row_tenant 单一源（事件自带键、
    缺失/空白→'default' 兜底）；limit 只计可见命中（跨租户行不占名额）。
    """
    try:
        from src.api.event_stream import _event_buffer

        query_lower = query.lower()
        matches = []
        for ev in reversed(_event_buffer):
            if _event_row_tenant(ev) != tenant_id:
                continue
            summary = str(ev.get("summary", "")).lower()
            organ = str(ev.get("organ", "")).lower()
            etype = str(ev.get("type", "")).lower()
            if query_lower in summary or query_lower in organ or query_lower in etype:
                matches.append(
                    {
                        "source": "events",
                        "icon": ev.get("emoji", "⚡"),
                        "title": ev.get("summary", ""),
                        "snippet": f"[{ev.get('organ', '')}] {ev.get('type', '')}",
                        "timestamp": ev.get("collected_at") or ev.get("timestamp"),
                        "organ": ev.get("organ", ""),
                    }
                )
                if len(matches) >= limit:
                    break
        return matches
    except Exception:
        return []


async def _search_agents(query: str, limit: int, tenant_id: str) -> list[dict]:
    """Search agents registry（tenant_id=身份派生租户，DEC-⑦-⑤ 加轴）。

    行归属判法=agent_row_tenant 单一源（list_registered 内过滤，自带键缺失/
    空白→'default' 兜底，DEC-⑦-① _event_row_tenant / ② message_row_tenant /
    ③ template_row_tenant / ④ file_row_tenant 同款）；过滤先于截断=limit 只计
    可见命中。读真源=src.services.agent_registry 的 acp_agent_registry（修前读
    ~/.openmate/agents.json——该文件本机恒不存在且全仓零写方=幽灵源，子搜索恒
    空集 wired-but-not-functional，DEC-⑦-② 读真源收口同款：加轴前必须先能读到
    真行）；「未注册不进列表」语义延续（只搜 registered=1 行，与 /registry、
    /detect.session_agents 一条真源）。
    """
    try:
        from src.services.agent_registry import list_registered

        query_lower = query.lower()
        matches = []
        for a in list_registered(tenant_id=tenant_id):
            agent_id = str(a.get("agent_id", "")).lower()
            detail = str(a.get("probe_detail", "")).lower()
            if query_lower in agent_id or query_lower in detail:
                matches.append(
                    {
                        "source": "agents",
                        "icon": "🤖",
                        "title": a.get("agent_id", "Unknown"),
                        "snippet": (
                            f"session_ready={a.get('session_ready')} · "
                            f"last probe {a.get('last_probe_at', '')}"
                        ),
                        "agent_id": a.get("agent_id", ""),
                    }
                )
                if len(matches) >= limit:
                    break
        return matches
    except Exception:
        return []


async def _search_courses(query: str, limit: int, tenant_id: str) -> list[dict]:
    """Search learning courses（tenant_id=身份派生租户；修前缺省系统视图=跨租户泄露）。"""
    try:
        from src.learn.course_engine import CourseEngine

        engine = CourseEngine()
        query_lower = query.lower()
        matches = []
        for course in engine.list_courses(tenant_id=tenant_id):
            title = course.title.lower()
            desc = course.description.lower()
            tags = " ".join(course.tags).lower()
            if query_lower in title or query_lower in desc or query_lower in tags:
                matches.append(
                    {
                        "source": "courses",
                        "icon": "📖",
                        "title": course.title,
                        "snippet": course.description[:100],
                        "course_id": course.course_id,
                        "status": course.status,
                    }
                )
                if len(matches) >= limit:
                    break
        return matches
    except Exception:
        return []


async def _search_trajectory(query: str, limit: int) -> list[dict]:
    """Search trajectory sessions and events."""
    try:
        from src.trajectory.store import TrajectoryStore

        store = TrajectoryStore()
        query_lower = query.lower()
        matches = []

        # Search sessions by task description
        sessions = await store.list_sessions(limit=50)
        for s in sessions:
            if query_lower in s.task_description.lower() or query_lower in s.agent_id.lower():
                matches.append(
                    {
                        "source": "trajectory",
                        "icon": "📊",
                        "title": s.task_description or f"Session {s.id[:8]}",
                        "snippet": f"Agent: {s.agent_id} · Events: {s.total_events} · Tokens: {s.total_tokens}",
                        "session_id": s.id,
                        "status": s.status,
                    }
                )
                if len(matches) >= limit:
                    break

        # Search events by content
        if len(matches) < limit:
            events = await store.search_events(keyword=query, limit=limit - len(matches))
            for ev in events:
                matches.append(
                    {
                        "source": "trajectory",
                        "icon": "📋",
                        "title": f"[{ev.event_type}] {ev.content[:80]}",
                        "snippet": f"Agent: {ev.agent_id} · Tokens: {ev.token_usage}",
                        "session_id": ev.session_id,
                        "event_id": ev.id,
                    }
                )

        return matches[:limit]
    except Exception:
        return []


async def _search_cron_jobs(query: str, limit: int) -> list[dict]:
    """Search cron/scheduled jobs."""
    try:
        from src.database.postgres import db_pool

        query_lower = query.lower()
        rows = await db_pool.fetch("SELECT * FROM cron_jobs ORDER BY created_at DESC LIMIT 200")
        matches = []
        for row in rows:
            d = dict(row)
            name = (d.get("name") or "").lower()
            prompt = (d.get("prompt") or "").lower()
            schedule = (d.get("schedule") or "").lower()
            if query_lower in name or query_lower in prompt or query_lower in schedule:
                matches.append(
                    {
                        "source": "cron",
                        "icon": "⏰",
                        "title": d.get("name", "Unnamed Job"),
                        "snippet": f"Schedule: {d.get('schedule', '?')} · {'Enabled' if d.get('enabled') else 'Disabled'}",
                        "job_id": d.get("id"),
                        "enabled": d.get("enabled"),
                    }
                )
                if len(matches) >= limit:
                    break
        return matches
    except Exception:
        return []


async def _search_gene_templates(query: str, limit: int, tenant_id: str) -> list[dict]:
    """Search gene templates（tenant_id=身份派生租户，DEC-⑦-③ 加轴）。

    行归属判法=template_row_tenant 单一源（list_templates 内过滤，自带键缺失/
    空白→'default' 兜底，DEC-⑦-① _event_row_tenant / ② message_row_tenant
    同款）；过滤先于截断=limit 只计可见命中。读真源=src.api.gene 模块单例
    registry（修前函数内 new TemplateEngine() 每调用重扫盘=与管理面内存注册表
    双源漂移，DEC-⑦-② 读真源收口同款）。
    """
    try:
        from src.api import gene as gene_api

        engine = gene_api.engine
        query_lower = query.lower()
        matches = []
        for tpl in engine.list_templates(tenant_id=tenant_id):
            name = tpl.get("name", "").lower()
            desc = tpl.get("description", "").lower()
            category = tpl.get("category", "").lower()
            if query_lower in name or query_lower in desc or query_lower in category:
                matches.append(
                    {
                        "source": "gene",
                        "icon": "🧬",
                        "title": tpl.get("name", "Template"),
                        "snippet": f"{tpl.get('description', '')[:100]} · [{tpl.get('category', '')}]",
                        "template_id": tpl.get("template_id"),
                        "category": tpl.get("category"),
                    }
                )
                if len(matches) >= limit:
                    break
        return matches
    except Exception:
        return []


async def _search_echo_messages(query: str, limit: int, tenant_id: str) -> list[dict]:
    """Search echo message history（tenant_id=身份派生租户，DEC-⑦-② 加轴）。

    行归属判法=message_row_tenant 单一源（dispatcher.history 内过滤，自带键
    缺失/空白→'default' 兜底，DEC-⑦-① _event_row_tenant 同款）；过滤先于
    截断=limit 只计可见命中。读真源=src.api.echo 模块单例 dispatcher 的共享
    历史（修前函数内 new MessageDispatcher() 空历史=echo 子搜索恒空集，
    wired-but-not-functional——加轴前必须先能读到真历史，否则过滤不可观测）。
    """
    try:
        from src.api import echo as echo_api

        dispatcher = echo_api.dispatcher
        query_lower = query.lower()
        matches = []
        for msg in dispatcher.history(limit=200, tenant_id=tenant_id):
            title = msg.get("title", "").lower()
            content = msg.get("content", "").lower()
            channel = msg.get("channel", "").lower()
            if query_lower in title or query_lower in content or query_lower in channel:
                matches.append(
                    {
                        "source": "echo",
                        "icon": "🔊",
                        "title": msg.get("title", "Message"),
                        "snippet": f"Channel: {msg.get('channel', '?')} · Status: {msg.get('status', '?')}",
                        "msg_id": msg.get("msg_id"),
                        "channel": msg.get("channel"),
                    }
                )
                if len(matches) >= limit:
                    break
        return matches
    except Exception:
        return []


@router.post("/reindex")
async def reindex_meilisearch():
    """Manually trigger full Meilisearch reindex from knowledge table."""
    from src.services.meili_indexer import index_all_knowledge

    result = await index_all_knowledge()
    return {"status": "ok" if "error" not in result else "error", **result}


@router.get("/unified")
async def unified_search(
    q: str = Query(..., description="Search query"),
    sources: str = Query(
        "all",
        description="Comma-separated sources: knowledge,files,events,agents,courses,trajectory,cron,gene,echo,all",
    ),
    limit: int = Query(10, ge=1, le=50),
    user_id: str = Query("default", description="User ID (UUID or username)"),
    authorization: str | None = Header(None),
):
    """Unified search across all subsystems: knowledge, files, events, agents, courses, trajectory, cron, gene, echo.

    Returns results grouped by source with relevance ranking.

    身份轴收口：user/tenant 从认证派生（bind_identity），自报值只可确认派生绑定
    ——认证请求自报他人档案=403；匿名保留自报（既有本地多档案语义）。知识子搜索
    用派生档案键（修前硬编码 'default'）、课程子搜索用派生租户过滤（修前系统视图
    =跨租户泄露）、events 子搜索用派生租户过滤（DEC-⑦-①，修前内存缓冲零过滤
    =跨租户泄露；行归属=事件自带键缺失→'default' 兜底，record() 同款）、echo
    子搜索用派生租户过滤（DEC-⑦-②，修前消息历史零过滤=跨租户泄露；行归属=
    消息自带键缺失→'default' 兜底，message_row_tenant 同款）、gene
    子搜索用派生租户过滤（DEC-⑦-③，修前模板注册表零过滤=跨租户泄露；行归属=
    模板自带键缺失→'default' 兜底，template_row_tenant 同款）、files
    子搜索用派生租户过滤（DEC-⑦-④，修前文件列表零过滤=跨租户泄露；行归属=
    文件自带键缺失→'default' 兜底，file_row_tenant 同款）、agents
    子搜索用派生租户过滤（DEC-⑦-⑤，修前注册表零过滤=跨租户泄露且读幽灵源
    ~/.openmate/agents.json 恒空集；行归属=agent 行自带键缺失→'default'
    兜底，agent_row_tenant 同款；读真源=acp_agent_registry 一条真源）。其余
    子搜索（trajectory/cron）的存储层无 user/tenant 轴，
    保持系统视图（诚实边界，DEC-⑦ 分批拆件留待拍）。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    source_list = [s.strip() for s in sources.split(",")]
    search_all = "all" in source_list

    tasks = {}
    if search_all or "knowledge" in source_list:
        tasks["knowledge"] = _search_knowledge(q, limit, uid)
    if search_all or "files" in source_list:
        tasks["files"] = _search_files(q, limit, identity.tenant_id)
    if search_all or "events" in source_list:
        tasks["events"] = _search_events(q, limit, identity.tenant_id)
    if search_all or "agents" in source_list:
        tasks["agents"] = _search_agents(q, limit, identity.tenant_id)
    if search_all or "courses" in source_list:
        tasks["courses"] = _search_courses(q, limit, identity.tenant_id)
    if search_all or "trajectory" in source_list:
        tasks["trajectory"] = _search_trajectory(q, limit)
    if search_all or "cron" in source_list:
        tasks["cron"] = _search_cron_jobs(q, limit)
    if search_all or "gene" in source_list:
        tasks["gene"] = _search_gene_templates(q, limit, identity.tenant_id)
    if search_all or "echo" in source_list:
        tasks["echo"] = _search_echo_messages(q, limit, identity.tenant_id)

    # Run all searches in parallel
    results_list = await asyncio.gather(*tasks.values(), return_exceptions=True)

    by_source = {}
    total = 0
    for source_name, results in zip(tasks.keys(), results_list):
        if isinstance(results, list):
            by_source[source_name] = results
            total += len(results)
        else:
            by_source[source_name] = []

    return {
        "query": q,
        "total": total,
        "by_source": by_source,
        "sources_searched": list(tasks.keys()),
    }
