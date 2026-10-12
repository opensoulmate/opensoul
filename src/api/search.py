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


async def _search_files(query: str, limit: int) -> list[dict]:
    """Search files in Vein."""
    try:
        from src.vein.file_store import FileStore

        store = FileStore()
        files = store.list_files(name_filter=query, limit=limit)
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


async def _search_events(query: str, limit: int) -> list[dict]:
    """Search event stream."""
    try:
        from src.api.event_stream import _event_buffer

        query_lower = query.lower()
        matches = []
        for ev in reversed(_event_buffer):
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


async def _search_agents(query: str, limit: int) -> list[dict]:
    """Search agents list."""
    try:
        import json
        import os

        agents_path = os.path.expanduser("~/.openmate/agents.json")
        if not os.path.exists(agents_path):
            return []
        with open(agents_path) as f:
            agents = json.load(f)
        query_lower = query.lower()
        matches = []
        for a in agents:
            name = str(a.get("name", "")).lower()
            desc = str(a.get("description", "")).lower()
            if query_lower in name or query_lower in desc:
                matches.append(
                    {
                        "source": "agents",
                        "icon": "🤖",
                        "title": a.get("name", "Unknown"),
                        "snippet": a.get("description", ""),
                        "agent_id": a.get("id", ""),
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


async def _search_gene_templates(query: str, limit: int) -> list[dict]:
    """Search gene templates."""
    try:
        from src.gene.templates import TemplateEngine

        engine = TemplateEngine()
        query_lower = query.lower()
        matches = []
        for tpl in engine.list_templates():
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


async def _search_echo_messages(query: str, limit: int) -> list[dict]:
    """Search echo message history."""
    try:
        from src.echo.dispatcher import MessageDispatcher

        dispatcher = MessageDispatcher()
        query_lower = query.lower()
        matches = []
        for msg in dispatcher.history(limit=200):
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
    =跨租户泄露）。其余子搜索（files/events/agents/trajectory/cron/gene/echo）的
    存储层无 user/tenant 轴，保持系统视图（诚实边界）。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    source_list = [s.strip() for s in sources.split(",")]
    search_all = "all" in source_list

    tasks = {}
    if search_all or "knowledge" in source_list:
        tasks["knowledge"] = _search_knowledge(q, limit, uid)
    if search_all or "files" in source_list:
        tasks["files"] = _search_files(q, limit)
    if search_all or "events" in source_list:
        tasks["events"] = _search_events(q, limit)
    if search_all or "agents" in source_list:
        tasks["agents"] = _search_agents(q, limit)
    if search_all or "courses" in source_list:
        tasks["courses"] = _search_courses(q, limit, identity.tenant_id)
    if search_all or "trajectory" in source_list:
        tasks["trajectory"] = _search_trajectory(q, limit)
    if search_all or "cron" in source_list:
        tasks["cron"] = _search_cron_jobs(q, limit)
    if search_all or "gene" in source_list:
        tasks["gene"] = _search_gene_templates(q, limit)
    if search_all or "echo" in source_list:
        tasks["echo"] = _search_echo_messages(q, limit)

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
