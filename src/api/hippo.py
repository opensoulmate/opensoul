"""OpenHippo API — 海马体：记忆生命周期管理、会话管理、衰减遗忘。"""

import logging
import time

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from src.hippo import memory_model as _memory_model
from src.hippo.decay import DecayStrategy
from src.hippo.memory_store import MemoryStore
from src.hippo.session import SessionManager, SessionStatus
from src.mcp.session_grants import default_gate
from src.services.tenant_binding import bind_identity

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
store = MemoryStore(strategy=DecayStrategy.ACCESS_REINFORCED, half_life_hours=24.0)
sessions = SessionManager()

# Long-term memory singleton
from src.hippo.long_term_memory import LongTermMemoryStore

_lt_store = LongTermMemoryStore()


# ── Request Schemas ────────────────────────────────────────


class MemoryCreateRequest(BaseModel):
    session_id: str
    content: str
    importance: float = 0.5
    tags: list[str] = []
    metadata: dict = {}


class MemoryUpdateRequest(BaseModel):
    content: str | None = None
    importance: float | None = None
    tags: list[str] | None = None


class SessionCreateRequest(BaseModel):
    user_id: str = ""
    title: str = ""
    metadata: dict = {}


class DecayConfigRequest(BaseModel):
    strategy: str | None = None
    half_life_hours: float | None = None
    archive_threshold: float | None = None
    forget_threshold: float | None = None


class LongTermMemoryRequest(BaseModel):
    tenant_id: str = "default"
    agent_id: str = "default"
    memory_type: str = "semantic"  # episodic/semantic/procedural/working
    content: str
    tags: list[str] = []
    metadata: dict = {}
    # DeerMem写侧安全（18-deer-flow-source.md #10/#11）
    dup_policy: str = "reject"  # reject | merge（近重复并入既有fact而非追加）
    safety_tags: dict | None = None  # {scope,durability,authority}；缺省=确定性推断
    write_mode: str = "auto"  # auto | explicit（人工路径可写非user+durable+descriptive组合）
    # kilocode防记忆回声：回合digest写入需过回声闸（本轮召回过记忆→跳过）；
    # 显式remember等人工写入可传False绕过
    echo_guard: bool = True


class LongTermSearchRequest(BaseModel):
    tenant_id: str = "default"
    agent_id: str = "default"
    query: str
    memory_types: list[str] = []
    limit: int = 10


class ConsolidateRequest(BaseModel):
    tenant_id: str = "default"
    agent_id: str = "default"
    max_age_hours: float = 24.0


# ── Health ─────────────────────────────────────────────────


@router.get("/health")
async def health():
    """OpenHippo health check."""
    return {
        "status": "ok",
        "component": "OpenHippo",
        "memory": store.get_stats(),
        "sessions": sessions.get_stats(),
        "long_term_memory": _lt_store.get_stats(),
        "dream_distiller": _dream_distiller.get_stats(),
        "memory_pipeline": _memory_pipeline.get_stats(),
        # kilocode supplement3 #8：记忆模型独立解析链快照（configured/resolved/
        # source/fallback/timeout/sampling——"记忆蒸馏用什么模型、有没有回退"可观测）
        "memory_model": _memory_model.describe(),
        "decision_log": _decision_log.get_stats(),
    }


# ── Memory CRUD ────────────────────────────────────────────


@router.post("/memories")
async def create_memory(req: MemoryCreateRequest):
    """Create a new short-term memory."""
    # Auto-create session if needed
    session = sessions.get(req.session_id)
    if not session:
        session = sessions.create(user_id="", title=f"Auto: {req.session_id}")
        # Use the provided session_id
        session.session_id = req.session_id

    mem = store.add(
        session_id=req.session_id,
        content=req.content,
        importance=req.importance,
        tags=req.tags,
        metadata=req.metadata,
    )
    sessions.increment_memory_count(req.session_id)
    return {
        "memory_id": mem.memory_id,
        "session_id": mem.session_id,
        "retention": mem.retention,
        "created_at": mem.created_at,
    }


@router.get("/memories")
async def list_memories(
    session_id: str = Query(default=None),
    tag: str = Query(default=None),
    min_retention: float = Query(default=None),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List/search memories."""
    tags = [tag] if tag else None
    memories = store.search(
        session_id=session_id,
        tags=tags,
        min_retention=min_retention,
        include_archived=include_archived,
        limit=limit,
    )
    return {
        "memories": [
            {
                "memory_id": m.memory_id,
                "session_id": m.session_id,
                "content": m.content,
                "importance": m.importance,
                "tags": m.tags,
                "retention": m.retention,
                "access_count": m.access_count,
                "archived": m.archived,
                "created_at": m.created_at,
                "last_accessed_at": m.last_accessed_at,
            }
            for m in memories
        ],
        "total": len(memories),
    }


@router.get("/memories/{memory_id}")
async def get_memory(memory_id: str):
    """Get a single memory (refreshes access)."""
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(404, "Memory not found")
    # 会话活动接线（H1-②b-②）：记忆访问=所属会话活动——store.get 在记忆级
    # "refreshes access"，此处把同一语义在会话级镜像（touch=时间戳刷新+
    # IDLE/EXPIRED 复活，会话缺席如实 no-op）。
    sessions.touch(mem.session_id)
    decay = store.decay_engine.calculate(
        mem.created_at, mem.last_accessed_at, mem.access_count, mem.importance
    )
    return {
        "memory_id": mem.memory_id,
        "session_id": mem.session_id,
        "content": mem.content,
        "importance": mem.importance,
        "tags": mem.tags,
        "metadata": mem.metadata,
        "retention": decay.retention,
        "access_count": mem.access_count,
        "archived": mem.archived,
        "created_at": mem.created_at,
        "last_accessed_at": mem.last_accessed_at,
    }


@router.patch("/memories/{memory_id}")
async def update_memory(memory_id: str, req: MemoryUpdateRequest):
    """Update a memory's content, importance, or tags."""
    kwargs = {}
    if req.content is not None:
        kwargs["content"] = req.content
    if req.importance is not None:
        kwargs["importance"] = req.importance
    if req.tags is not None:
        kwargs["tags"] = req.tags
    mem = store.update(memory_id, **kwargs)
    if not mem:
        raise HTTPException(404, "Memory not found")
    return {"memory_id": mem.memory_id, "updated": True}


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str):
    """Delete a memory."""
    if not store.delete(memory_id):
        raise HTTPException(404, "Memory not found")
    return {"deleted": True}


# ── Decay Control ──────────────────────────────────────────


@router.post("/decay/run")
async def run_decay_cycle():
    """Run a decay cycle: update retention, archive, forget."""
    result = store.run_decay_cycle()
    return result


@router.get("/decay/config")
async def get_decay_config():
    """Get current decay configuration."""
    return {
        "strategy": store.decay_engine.strategy.value,
        "half_life_hours": store.decay_engine.half_life_hours,
        "archive_threshold": store.decay_engine.archive_threshold,
        "forget_threshold": store.decay_engine.forget_threshold,
    }


@router.put("/decay/config")
async def update_decay_config(req: DecayConfigRequest):
    """Update decay configuration."""
    if req.strategy:
        try:
            store.decay_engine.strategy = DecayStrategy(req.strategy)
        except ValueError:
            raise HTTPException(400, f"Invalid strategy. Valid: {[s.value for s in DecayStrategy]}")
    if req.half_life_hours is not None:
        store.decay_engine.half_life_hours = req.half_life_hours
    if req.archive_threshold is not None:
        store.decay_engine.archive_threshold = req.archive_threshold
    if req.forget_threshold is not None:
        store.decay_engine.forget_threshold = req.forget_threshold
    return await get_decay_config()


@router.post("/decay/simulate")
async def simulate_decay(
    age_hours: float = Query(default=24.0),
    importance: float = Query(default=0.5),
    access_count: int = Query(default=0),
):
    """Simulate decay for given parameters without affecting real memories."""
    now = time.time()
    created_at = now - age_hours * 3600
    last_accessed = now - age_hours * 0.5 * 3600  # accessed halfway through
    result = store.decay_engine.calculate(created_at, last_accessed, access_count, importance)
    return {
        "retention": result.retention,
        "should_archive": result.should_archive,
        "should_forget": result.should_forget,
        "strategy": result.strategy,
        "simulated_age_hours": age_hours,
        "importance": importance,
        "access_count": access_count,
    }


# ── Session Management ─────────────────────────────────────


@router.post("/sessions")
async def create_session(req: SessionCreateRequest):
    """Create a new session."""
    session = sessions.create(user_id=req.user_id, title=req.title, metadata=req.metadata)
    return {
        "session_id": session.session_id,
        "title": session.title,
        "status": session.status.value,
        "created_at": session.created_at,
    }


@router.get("/sessions")
async def list_sessions(
    user_id: str = Query(default=None),
    status: str = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List sessions."""
    session_status = None
    if status:
        try:
            session_status = SessionStatus(status)
        except ValueError:
            raise HTTPException(400, f"Invalid status. Valid: {[s.value for s in SessionStatus]}")
    session_list = sessions.list_sessions(user_id=user_id, status=session_status, limit=limit)
    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "user_id": s.user_id,
                "title": s.title,
                "status": s.status.value,
                "memory_count": s.memory_count,
                "created_at": s.created_at,
                "last_active_at": s.last_active_at,
            }
            for s in session_list
        ],
        "total": len(session_list),
    }


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get session details."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return {
        "session_id": session.session_id,
        "user_id": session.user_id,
        "title": session.title,
        "status": session.status.value,
        "memory_count": session.memory_count,
        "total_tokens": session.total_tokens,
        "created_at": session.created_at,
        "last_active_at": session.last_active_at,
        "metadata": session.metadata,
    }


@router.post("/sessions/{session_id}/archive")
async def archive_session(session_id: str):
    """Archive a session."""
    if not sessions.archive(session_id):
        raise HTTPException(404, "Session not found")
    return {"archived": True}


def _clear_mcp_grants(session_id: str) -> None:
    """会话删除 = 会话级工具授权随之清理（H1-②b-⑤ clear_session 接线）。

    MCP 会话级工具隔离按 session_id 键授权（fail-closed 零信任）；会话删除后
    授权行不得残留——修前 clear_session 全仓零消费 = 授权行永不清。
    旁路纪律（G1-⑤ 同族）：清理故障 logger.warning 可见，绝不吃掉删除主路径。
    """
    try:
        cleared = default_gate().clear_session(session_id)
    except Exception as exc:
        logger.warning("mcp grant cleanup failed for session %s: %s", session_id, exc)
        return
    if cleared:
        logger.info("session %s deleted: cleared %d mcp tool grants", session_id, cleared)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session."""
    if not sessions.delete(session_id):
        raise HTTPException(404, "Session not found")
    _clear_mcp_grants(session_id)
    return {"deleted": True}


@router.post("/sessions/lifecycle-check")
async def run_lifecycle_check():
    """Run session lifecycle check (idle detection, expiry)."""
    result = sessions.run_lifecycle_check()
    return result


# ── Session Import (P3-② 会话导入) ─────────────────────────


class SessionImportRequest(BaseModel):
    limit: int = 200
    min_messages: int = 2
    dry_run: bool = False


@router.post("/sessions/import")
async def import_sessions(req: SessionImportRequest):
    """P3-②: 导入历史agent会话到海马长期记忆

    数据源：agent_sessions/agent_messages（acp-proxy ws_chat写入）
    dry_run=true时只统计不写入
    """
    from src.hippo.session_importer import SessionImporter

    importer = SessionImporter(ltm_store=_lt_store)
    result = importer.import_sessions(
        limit=req.limit,
        min_messages=req.min_messages,
        dry_run=req.dry_run,
    )
    return {"ok": True, "component": "session_importer", **result}


# ── Long-term Memory ───────────────────────────────────────


@router.post("/ltm/add")
async def ltm_add(req: LongTermMemoryRequest):
    """Add a long-term memory entry.

    P1 gatekeeper：准入判定拒绝时返回added=False+拒绝原因（非错误，是判定）。
    DeerMem写侧安全：outcome标注入库方式（added/merged/rejected_tags/rejected_gate），
    dup_policy="merge"时近重复并入既有fact（响应memory_id=既有fact的id）。
    kilocode防记忆回声：echo_guard=True且本回合召回过记忆→跳过写入并返回
    outcome=echo_blocked（"答案来自记忆的回合不能再蒸馏回记忆"，显式reason非静默）。
    """
    if req.echo_guard and _dream_distiller.should_skip_digest():
        return {
            "added": False,
            "memory_id": "",
            "outcome": "echo_blocked",
            "reason": (
                "memories were recalled this turn — answers from memory must not be "
                "distilled back (kilocode echo blocker); pass echo_guard=False to override"
            ),
            "echo": _dream_distiller.echo_stats,
        }
    mem = _lt_store.store(
        content=req.content,
        memory_type=req.memory_type,
        importance=0.5,
        tags=req.tags,
        metadata=req.metadata,
        source_session="",
        safety_tags=req.safety_tags,
        write_mode=req.write_mode,
        dup_policy=req.dup_policy,
    )
    outcome = _lt_store.last_write_outcome
    if mem is None:
        dec = _lt_store.gatekeeper.last_decision
        tag_dec = _lt_store.last_tag_decision
        return {
            "added": False,
            "memory_id": "",
            "outcome": outcome,
            "gatekeeper": dec.to_dict() if dec else {"verdict": "reject"},
            "deermem_tags": tag_dec.to_dict() if tag_dec else None,
        }
    return {
        "memory_id": mem.memory_id,
        "added": True,
        "outcome": outcome,
        "merged": outcome == "merged",
    }


@router.get("/ltm/gatekeeper/stats")
async def ltm_gatekeeper_stats():
    """P1 gatekeeper准入统计（LobeChat记忆守门员）：拒了什么、为什么拒、可审计。

    响应含deermem节：TAG_REJECT/DELETE_BLOCKED/fact_dedup MERGE计数。
    """
    return _lt_store.get_gatekeeper_stats()


@router.get("/ltm/deermem/stats")
async def ltm_deermem_stats():
    """DeerMem安全标签门统计+词表（18-deer-flow-source.md #10/#11可观测性）。"""
    from src.hippo.extractors.deermem_tags import tags_vocab

    stats = _lt_store.get_gatekeeper_stats()
    return {"deermem": stats.get("deermem", {}), "vocab": tags_vocab()}


@router.post("/ltm/search")
async def ltm_search(req: LongTermSearchRequest):
    """Search long-term memories with Khoj-style NL filter support.

    The query string is parsed for natural language filters (dates, types,
    importance, word matches) before being passed to the retrieval engine.
    Response includes `nl_filters` showing what was extracted.
    """
    from src.hippo.nl_filters import apply_post_filters, parse_nl_query

    nl = parse_nl_query(req.query)
    effective_query = nl.clean_query if nl.clean_query else req.query
    effective_type = req.memory_types[0] if req.memory_types else ""
    if not effective_type and nl.memory_types:
        effective_type = nl.memory_types[0]

    # If NL filters left an empty clean query but we have filters, list instead of search
    if not nl.clean_query and nl.has_filters:
        results = _lt_store.list_memories(
            memory_type=effective_type,
            include_deleted=False,
            limit=req.limit,
        )
        results = apply_post_filters(results, nl)
    else:
        results = _lt_store.retrieve(
            query=effective_query,
            memory_type=effective_type,
            limit=req.limit,
        )
        if nl.has_filters:
            results = apply_post_filters(results, nl)

    return {
        "results": [
            {
                "memory_id": m.get("memory_id", ""),
                "content": m.get("content", "")[:200],
                "memory_type": m.get("memory_type", ""),
                "importance": m.get("importance", 0.0),
                "tags": m.get("tags", []),
                "created_at": m.get("created_at", 0.0),
            }
            for m in results
        ],
        "count": len(results),
        "nl_filters": nl.to_dict(),
    }


@router.post("/ltm/consolidate")
async def ltm_consolidate(req: ConsolidateRequest):
    """Consolidate old memories (dedup + merge + decay)."""
    result = _lt_store.consolidate()
    return result


@router.get("/ltm/stats")
async def ltm_stats():
    """Long-term memory statistics."""
    return _lt_store.get_stats()


@router.post("/ltm/context")
async def ltm_context(req: LongTermSearchRequest):
    """Get context-formatted memories for prompt injection (NL filter aware)."""
    from src.hippo.nl_filters import parse_nl_query

    nl = parse_nl_query(req.query)
    effective_query = nl.clean_query if nl.clean_query else req.query
    effective_type = req.memory_types[0] if req.memory_types else ""
    if not effective_type and nl.memory_types:
        effective_type = nl.memory_types[0]

    context = _lt_store.get_context_prompt(
        query=effective_query,
        memory_type=effective_type,
    )
    return {
        "context": context,
        "nl_filters": nl.to_dict(),
        # kilocode防记忆回声：实际注入的记忆id（调用方按此mark_recall，digest据此跳过）
        "memory_ids": list(_lt_store.last_context_memory_ids),
    }


# ── Long-term Memory CRUD + Audit (Khoj + mem0 pattern) ────


class LTMUpdateRequest(BaseModel):
    content: str | None = None
    memory_type: str | None = None
    importance: float | None = None
    tags: list[str] | None = None
    metadata: dict | None = None
    reason: str = "user_edit"


class LTMDeleteRequest(BaseModel):
    reason: str = "user_delete"
    hard_delete: bool = False
    replacement: str = ""  # DeerMem #11：矛盾事实删除必须带replacement


# NOTE: /ltm/list and /ltm/audit/* MUST be registered BEFORE /ltm/{memory_id}
# to avoid FastAPI matching "list"/"audit" as a memory_id path parameter.


@router.get("/ltm/list")
async def ltm_list(
    memory_type: str = Query(default=""),
    include_deleted: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List all long-term memories (Khoj CRUD: user can see what AI remembers)."""
    results = _lt_store.list_memories(
        memory_type=memory_type,
        include_deleted=include_deleted,
        limit=limit,
        offset=offset,
    )
    return {"memories": results, "count": len(results)}


@router.get("/ltm/audit/history")
async def ltm_audit_history(
    memory_id: str = Query(default=""),
    event: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Get memory audit trail history (mem0 pattern)."""
    history = _lt_store.get_history(
        memory_id=memory_id,
        event=event,
        limit=limit,
    )
    return {"history": history, "count": len(history)}


@router.get("/ltm/audit/stats")
async def ltm_audit_stats():
    """Get audit trail statistics."""
    return _lt_store.get_audit_stats()


# ── Dream Distillation (CowAgent + nanobot + kilocode echo blocker) ──

from src.hippo.dream_distiller import DreamDistiller

_dream_distiller = DreamDistiller(ltm_store=_lt_store)


class DreamRequest(BaseModel):
    messages: list[dict] = []
    force: bool = False  # bypass echo blocker for manual triggers
    background: bool = False  # P0-8接线：True→提交hippo.dream后台作业，立即返回job_id


class RecallMarkRequest(BaseModel):
    memory_ids: list[str]


@router.post("/ltm/dream")
async def ltm_dream(req: DreamRequest, authorization: str | None = Header(None)):
    """Run Dream memory distillation from conversation history.

    CowAgent 5-step prompt + nanobot archive-as-tool-call +
    kilocode memory echo blocker (unless force=True).
    background=True → P0-8后台作业（will job_queue）：LLM长任务不阻塞请求方，
    作业状态经 /api/will/jobs/{id} 与monitoring面板可见。
    """
    if req.background:
        from src.will.job_handlers import HANDLER_SPECS, register_default_handlers
        from src.will.job_queue import get_job_queue

        jq = get_job_queue()
        register_default_handlers(jq)  # 幂等；跨模块不依赖api/will.py是否已加载
        await jq.start()
        job_id = await jq.submit(
            "hippo.dream",
            {"messages": req.messages, "force": req.force},
            # F3b-③a：作业归属租户从认证身份派生（禁自报），只在提交作业路径绑定
            tenant_id=bind_identity(authorization).tenant_id,
        )
        return {
            "queued": True,
            "job_id": job_id,
            "queue": "will.job_queue",
            "handler": "hippo.dream" if "hippo.dream" in HANDLER_SPECS else "",
            "status_url": f"/api/will/jobs/{job_id}",
        }
    result = await _dream_distiller.dream(
        messages=req.messages,
        force=req.force,
    )
    return result.to_dict()


@router.post("/ltm/dream/recall-mark")
async def ltm_dream_recall_mark(req: RecallMarkRequest):
    """Mark memories as recalled this turn (kilocode echo blocker).

    Call this after memory retrieval so the Dream distiller knows
    to skip digest for this turn (prevent self-pollution).
    """
    _dream_distiller.mark_recall(req.memory_ids)
    return {"marked": len(req.memory_ids), "echo_stats": _dream_distiller.echo_stats}


@router.post("/ltm/dream/reset-turn")
async def ltm_dream_reset_turn():
    """Reset per-turn echo tracking (call at turn boundary)."""
    _dream_distiller.reset_turn()
    return {"reset": True, "echo_stats": _dream_distiller.echo_stats}


@router.get("/ltm/dream/stats")
async def ltm_dream_stats():
    """Dream distillation statistics."""
    return _dream_distiller.get_stats()


# ── codex两阶段记忆管线（11-openai-codex-source.md #1）────────────
# Phase1结构化提取→Phase2 consolidation agent→MemoryVersion版本化落库→workspace diff。
# NOTE: /ltm/pipeline/* MUST be registered BEFORE /ltm/{memory_id}（同list/audit先例，
# 防"pipeline"被路径参数捕获）。

from src.hippo.memory_pipeline import MemoryPipeline

# echo_check接DreamDistiller的回合回声状态（kilocode：整合入口一行判断）
_memory_pipeline = MemoryPipeline(
    ltm_store=_lt_store, echo_check=_dream_distiller.should_skip_digest
)


class PipelineExtractRequest(BaseModel):
    messages: list[dict] = []
    session_id: str = ""


class PipelineRunRequest(BaseModel):
    messages: list[dict] = []
    candidates: list[dict] = []  # 直供候选（mode=import，跳过Phase1）
    session_id: str = ""
    apply: bool = True  # False=dry-run（只产workspace diff不落库）
    use_llm_phase2: bool = True  # False=确定性整合（不依赖LLM）
    background: bool = False  # P0-8接线：True→提交hippo.memory_pipeline后台作业
    force: bool = False  # kilocode防记忆回声：True=绕过回声闸（手动触发）


class PipelinePruneRequest(BaseModel):
    max_age_hours: float = 72.0
    memory_type: str = "working"


class LTMRollbackRequest(BaseModel):
    version: int
    reason: str = "user_rollback"


@router.post("/ltm/pipeline/extract")
async def ltm_pipeline_extract(req: PipelineExtractRequest):
    """Phase1：会话历史→候选事实（codex每会话结构化提取）。LLM故障→error可见。"""
    result = await _memory_pipeline.extract(messages=req.messages, session_id=req.session_id)
    return result.to_dict()


@router.post("/ltm/pipeline/run")
async def ltm_pipeline_run(req: PipelineRunRequest, authorization: str | None = Header(None)):
    """端到端管线：Phase1→Phase2整合→版本化落库→workspace diff。

    background=True → will job_queue后台作业（LLM长任务不阻塞请求方，
    作业状态经/api/will/jobs/{id}可见）。
    """
    if req.background:
        from src.will.job_handlers import HANDLER_SPECS, register_default_handlers
        from src.will.job_queue import get_job_queue

        jq = get_job_queue()
        register_default_handlers(jq)  # 幂等
        await jq.start()
        job_id = await jq.submit(
            "hippo.memory_pipeline",
            {
                "messages": req.messages,
                "candidates": req.candidates,
                "session_id": req.session_id,
                "apply": req.apply,
                "use_llm_phase2": req.use_llm_phase2,
                "force": req.force,
            },
            # F3b-③a：作业归属租户从认证身份派生（禁自报），只在提交作业路径绑定
            tenant_id=bind_identity(authorization).tenant_id,
        )
        return {
            "queued": True,
            "job_id": job_id,
            "queue": "will.job_queue",
            "handler": "hippo.memory_pipeline" if "hippo.memory_pipeline" in HANDLER_SPECS else "",
            "status_url": f"/api/will/jobs/{job_id}",
        }
    result = await _memory_pipeline.run(
        messages=req.messages,
        session_id=req.session_id,
        candidates=req.candidates,
        apply=req.apply,
        use_llm_phase2=req.use_llm_phase2,
        force=req.force,
    )
    return result.to_dict()


@router.get("/ltm/pipeline/runs")
async def ltm_pipeline_runs(limit: int = Query(default=20, ge=1, le=200)):
    """最近管线运行记录（codex workspace diff持久化——管线跑过什么、改了什么）。"""
    runs = _memory_pipeline.get_runs(limit=limit)
    return {"runs": runs, "count": len(runs)}


@router.get("/ltm/pipeline/stats")
async def ltm_pipeline_stats():
    """管线统计：runs/候选数/diff四类计数/llm与import模式分布。"""
    return _memory_pipeline.get_stats()


@router.post("/ltm/pipeline/prune")
async def ltm_pipeline_prune(req: PipelinePruneRequest):
    """旧资源修剪（codex workspace修剪本地化）：过期transient工作记忆软删。
    task/project域被DeerMem删除门拦截→blocked计数可见（fail-closed不绕过）。"""
    return _memory_pipeline.prune(max_age_hours=req.max_age_hours, memory_type=req.memory_type)


@router.get("/ltm/{memory_id}/versions")
async def ltm_memory_versions(memory_id: str):
    """MemoryVersion版本历史（codex storage.rs：这条记忆被谁、因为什么、从什么改成什么）。"""
    versions = _lt_store.get_versions(memory_id)
    return {
        "memory_id": memory_id,
        "versions": versions,
        "current_version": _lt_store.get_current_version(memory_id),
        "count": len(versions),
    }


@router.post("/ltm/{memory_id}/rollback")
async def ltm_memory_rollback(memory_id: str, req: LTMRollbackRequest):
    """回滚到指定历史版本（经update_memory写新版本+审计，历史链只增不改）。
    版本/记忆不存在→444（mem0 §1.1失败可见，不静默假成功）。"""
    result = _lt_store.rollback_version(memory_id=memory_id, version=req.version, reason=req.reason)
    if result is None:
        raise HTTPException(444, f"Version v{req.version} for memory {memory_id} not found")
    return {
        "memory_id": memory_id,
        "rolled_back_to": req.version,
        "memory": result,
        "current_version": _lt_store.get_current_version(memory_id),
    }


@router.get("/ltm/{memory_id}")
async def ltm_get(memory_id: str):
    """Get a specific long-term memory by ID."""
    memories = _lt_store.list_memories(include_deleted=True, limit=10000)
    for m in memories:
        if m["memory_id"] == memory_id:
            return m
    raise HTTPException(444, f"Long-term memory {memory_id} not found")


@router.patch("/ltm/{memory_id}")
async def ltm_update(memory_id: str, req: LTMUpdateRequest):
    """Update a long-term memory with audit trail (Khoj CRUD + mem0 audit)."""
    result = _lt_store.update_memory(
        memory_id=memory_id,
        content=req.content,
        memory_type=req.memory_type,
        importance=req.importance,
        tags=req.tags,
        metadata=req.metadata,
        reason=req.reason,
    )
    if result is None:
        raise HTTPException(444, f"Long-term memory {memory_id} not found")
    return {"memory_id": memory_id, "updated": True, "memory": result}


@router.delete("/ltm/{memory_id}")
async def ltm_delete(memory_id: str, req: LTMDeleteRequest = LTMDeleteRequest()):
    """Delete a long-term memory with audit trail.

    DeerMem #11删除安全门（人工CRUD=explicit模式）：矛盾事实删除必须带
    replacement；被拦截→409+判定详情（DELETE_BLOCKED审计已落库）。
    """
    success = _lt_store.delete_memory(
        memory_id=memory_id,
        reason=req.reason,
        hard_delete=req.hard_delete,
        delete_mode="explicit",
        replacement=req.replacement,
    )
    if not success:
        dec = _lt_store.last_delete_decision
        if dec is not None and not dec.accepted:
            raise HTTPException(
                409,
                {
                    "error": "deermem_delete_blocked",
                    "decision": dec.to_dict(),
                },
            )
        raise HTTPException(444, f"Long-term memory {memory_id} not found")
    return {"deleted": True, "memory_id": memory_id, "hard_delete": req.hard_delete}


# ── Decision Log (TradingAgents延迟回填: pending→update_with_outcome) ──
# 调研来源：14-tradingagents-source.md #1/#2/#3/#4 + SUMMARY.md P0-6。
# 运行时写路径：src/api/chat.py路由决策自动记录；读路径①：src/gland/route_policy.py反馈。
from src.hippo.decision_log import get_decision_log

_decision_log = get_decision_log()


class DecisionStoreRequest(BaseModel):
    decision: str
    domain: str = "general"
    agent_id: str = "default"
    context: str = ""
    rating: str = ""
    metadata: dict = {}
    source_session: str = ""


class DecisionOutcomeRequest(BaseModel):
    decision_id: str = ""
    domain: str = ""
    outcome: str = ""
    metrics: dict = {}
    reflection: str = ""
    success: bool | None = None
    resolution_date: str = ""  # YYYY-MM-DD结果已知日期；缺省=今天（as_of时间旅行过滤事实源）


@router.post("/decisions")
async def decision_store(req: DecisionStoreRequest):
    """记录一条pending决策（TradingAgents store_decision）。

    幂等：同domain+同decision已有pending条目时不重复创建（返回既有条目+deduped=True）。
    """
    if not req.decision.strip():
        raise HTTPException(422, "decision must be non-empty")
    return _decision_log.store_decision(
        decision=req.decision,
        domain=req.domain,
        agent_id=req.agent_id,
        context=req.context,
        rating=req.rating,
        metadata=req.metadata,
        source_session=req.source_session,
    )


@router.get("/decisions")
async def decision_list(
    status: str = Query(default=""),
    domain: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=500),
):
    """列出决策记录（status=pending/resolved过滤）。"""
    entries = _decision_log.list_decisions(status=status, domain=domain, limit=limit)
    return {"decisions": entries, "count": len(entries)}


@router.get("/decisions/pending")
async def decision_pending(
    domain: str = Query(default=""),
    agent_id: str = Query(default=""),
):
    """待回填盘点（TradingAgents get_pending_entries）：pending 复盘查询面。

    与 GET /decisions?status=pending 的差异：agent_id 过滤 + created_at 升序
    （先进先出复盘顺序，list_decisions 是降序+limit 截断）。
    entries[].decision_id 可直接喂 POST /decisions/{decision_id}/outcome 回填闭环
    ——盘点出待回填项后按 id 回填，不再只靠调用方自留 decision_id。
    """
    entries = _decision_log.get_pending_entries(domain=domain, agent_id=agent_id)
    return {"entries": entries, "count": len(entries), "domain": domain, "agent_id": agent_id}


@router.get("/decisions/stats")
async def decision_stats():
    """决策日志统计：success_rate/by_domain/provider成功率——"有没有进化"的度量面。"""
    return _decision_log.get_stats()


@router.get("/decisions/context")
async def decision_context(
    domain: str = Query(default=""),
    n_same: int = Query(default=5, ge=0, le=20),
    n_cross: int = Query(default=3, ge=0, le=20),
    as_of: str = Query(default=""),
    token_budget: int = Query(default=800, ge=100, le=8000),
):
    """带真实反馈信号的few-shot上下文（同域全量/跨域反思两档 + as_of时间旅行过滤）。"""
    context = _decision_log.get_past_context(
        domain=domain,
        n_same=n_same,
        n_cross=n_cross,
        as_of=as_of,
        token_budget=token_budget,
    )
    return {"context": context}


@router.get("/decisions/audit")
async def decision_audit(
    decision_id: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=500),
):
    """决策审计轨迹（mem0 §1.2：谁在何时把哪条决策从什么改成什么）。"""
    history = _decision_log.get_audit(decision_id=decision_id, limit=limit)
    return {"history": history, "count": len(history)}


@router.post("/decisions/{decision_id}/outcome")
async def decision_outcome(decision_id: str, req: DecisionOutcomeRequest):
    """把真实结果回填到pending决策（TradingAgents update_with_outcome）。

    只更新pending条目；已resolved条目原样返回且带_already_resolved标志（幂等）。
    未知decision_id返回444（mem0 §1.1：失败必须可见，不静默假成功）。
    """
    result = _decision_log.update_with_outcome(
        decision_id=decision_id,
        domain=req.domain,
        outcome=req.outcome,
        metrics=req.metrics,
        reflection=req.reflection,
        success=req.success,
        resolution_date=req.resolution_date,
    )
    if result is None:
        raise HTTPException(
            444, f"Decision {decision_id} not found (or no pending decision in domain)"
        )
    return result


# ── Stats ──────────────────────────────────────────────────


@router.get("/stats")
async def hippo_stats():
    """OpenHippo detailed statistics."""
    mem_stats = store.get_stats()
    sess_stats = sessions.get_stats()
    all_memories = store.search(limit=1000)
    by_tag = {}
    for m in all_memories:
        for t in m.tags or []:
            by_tag[t] = by_tag.get(t, 0) + 1

    return {
        "status": "ok",
        "component": "OpenHippo",
        "memory": mem_stats,
        "sessions": sess_stats,
        "by_tag": by_tag,
        "decay_config": {
            "strategy": store.decay_engine.strategy.value,
            "half_life_hours": store.decay_engine.half_life_hours,
            "archive_threshold": store.decay_engine.archive_threshold,
            "forget_threshold": store.decay_engine.forget_threshold,
        },
    }
