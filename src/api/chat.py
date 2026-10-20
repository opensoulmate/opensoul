import asyncio
import hashlib
import json
import logging
from uuid import UUID

import httpx
from fastapi import APIRouter, Header, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.config import settings
from src.cortex.loop_guard import LoopGuard
from src.cortex.token_attribution import (
    KIND_IMPROVEMENT,
    KIND_MEMORY,
    KIND_MESSAGE,
    KIND_RAG,
    ContextItem,
    build_context_usage,
    estimate_tokens,
    get_attributor,
)
from src.services.search import semantic_search
from src.services.tenant_binding import bind_identity

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_user_id(user_id: str) -> UUID:
    """F3b-⑥ 家族 _resolve_user_id 同形（档案键家族正典）：UUID 串原样、
    普通用户名/数字 id 按 md5 归一为档案 UUID。"""
    try:
        return UUID(user_id)
    except ValueError:
        h = hashlib.md5(user_id.encode()).hexdigest()
        return UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


def _llm_headers(api_key: str) -> dict:
    """统一LLM请求头：tp-前缀(token-plan)用api-key头，其余Bearer，空key不带鉴权(local)"""
    headers = {"Content-Type": "application/json"}
    if api_key and api_key.startswith("tp-"):
        headers["api-key"] = api_key
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _route_llm_targets(message: str):
    """模型路由4按钮接线：route_policy决策 → 调用尝试序列(主+备) + routing元数据。
    route_policy不可用时静默回退settings静态配置，不阻断chat。"""
    try:
        from src.gland.route_policy import resolve_target

        d = resolve_target(message)
        attempts = [d["target"]]
        if d.get("backup_target"):
            attempts.append(d["backup_target"])
        meta = {
            k: d.get(k) for k in ("mode", "prefer", "reason", "complexity") if d.get(k) is not None
        }
        return attempts, meta
    except Exception as exc:
        logger.debug("route_policy unavailable, fallback to settings: %s", exc)
        return (
            [
                {
                    "provider": "settings",
                    "base_url": settings.llm_base_url,
                    "model": settings.llm_model,
                    "api_key": settings.llm_api_key,
                }
            ],
            {"mode": "fallback-settings"},
        )


def _get_memory_context(query: str) -> str:
    """P0-6: 从hippo长期记忆检索三因子上下文（recency+importance+relevance）。失败时静默降级。"""
    try:
        from src.hippo.long_term_memory import LongTermMemoryStore

        store = LongTermMemoryStore()
        return store.get_context_prompt(query=query, token_budget=300)
    except Exception as exc:
        logger.debug("hippo memory context unavailable: %s", exc)
        return ""


async def _get_learned_advisory(query: str) -> tuple[str, bool]:
    """F2b-②b 推荐回流对话面：learned_patterns → prompt 软建议块（条数可观测）。

    取数口=src.learn.long_term.learned_advisory_for 单一源（与任务执行面同口径）。
    返回 (建议块, 取数是否失败)。学习层故障不阻断 chat 但必须可见
    （F2b-②"禁静默"纪律：learned-context-error 日志 + 调用方可见面）；
    无推荐=("", False) 零注入（宁缺勿滥，无关建议是噪声）。
    """
    try:
        from src.learn.long_term import learned_advisory_for

        block, _n = await learned_advisory_for(query)
        return block, False
    except Exception as exc:
        logger.warning("learned-context-error: %s", exc)
        return "", True


async def _get_episodic_advisory(query: str) -> tuple[str, bool]:
    """DEC-③-① episodic 回流对话面：相似失败经验 error → prompt 软建议块。

    取数口=src.learn.experience.episodic_advisory_for 单一源（与任务执行面
    同口径，pending-decisions ③ 试水形态 B：仅 failure 行 error 字段）。
    返回 (建议块, 取数是否失败)。episodic 层故障不阻断 chat 但必须可见
    （F2b-②"禁静默"纪律：episodic-context-error 日志 + 调用方可见面）；
    无命中=("", False) 零注入（宁缺勿滥）。
    """
    try:
        from src.learn.experience import episodic_advisory_for

        block, _n = await episodic_advisory_for(query)
        return block, False
    except Exception as exc:
        logger.warning("episodic-context-error: %s", exc)
        return "", True


async def _get_metacognitive_advisory() -> tuple[str, bool]:
    """G1-④b 元认知纠错回流对话面：blind_spots/calibration → prompt 纠偏块。

    取数口=src.mirror.metacognition.metacognitive_advisory_for 单一源（与任务
    执行面 G1-④a 同口径）。chat 面无 agent 身份轴，口径钉 ('default','default')
    ——agent 专属观测（soulmate/echo 等）不进本面（每 agent 独立禁跨 agent
    冒用聚合）。返回 (纠偏块, 取数是否失败)。元认知层故障不阻断 chat 但必须
    可见（F2b-②"禁静默"纪律：metacognitive-context-error 日志 + 调用方可见面）；
    空观测=("", False) 零注入（宁缺勿滥）。块非 query 相关（agent 级观测，
    与 learned/episodic 的 query 相关度门刻意不同源）。
    """
    try:
        from src.mirror.metacognition import metacognitive_advisory_for

        block, _n = await metacognitive_advisory_for("default", tenant_id="default")
        return block, False
    except Exception as exc:
        logger.warning("metacognitive-context-error: %s", exc)
        return "", True


def _redact_outbound(text: str) -> str:
    """P0-4: 出站脱敏，防止API key/token泄漏到LLM provider。失败时原样返回。"""
    try:
        from src.immune.moderator import ContentModerator

        mod = ContentModerator()
        result = mod.moderate(text)
        return result.redacted_text if result.findings else text
    except Exception as exc:
        logger.debug("outbound redaction unavailable: %s", exc)
        return text


async def _compress_if_needed(context: str, budget_chars: int = 12000) -> str:
    """P0-1: 上下文压缩 — 超预算时用ContextCompressor压缩（goose 9段式+kilocode切分）。
    ContextCompressor.compress()签名是messages列表+context_limit，需llm_fn。
    失败时截断降级。"""
    if len(context) <= budget_chars:
        return context
    try:
        import httpx as _httpx

        from src.core.config import settings as _settings
        from src.cortex.context_compression import ContextCompressor

        async def _llm_fn(prompt: str) -> str:
            attempts, _meta = _route_llm_targets(prompt)
            last_exc: Exception | None = None
            for t in attempts:
                if t.get("provider") == "online" and not t.get("api_key"):
                    continue
                try:
                    async with _httpx.AsyncClient(timeout=60) as c:
                        resp = await c.post(
                            f"{t['base_url']}/chat/completions",
                            headers=_llm_headers(t.get("api_key", "")),
                            json={
                                "model": t["model"],
                                "messages": [{"role": "user", "content": prompt}],
                                "temperature": 0.1,
                            },
                        )
                        resp.raise_for_status()
                        return resp.json()["choices"][0]["message"]["content"]
                except Exception as exc:
                    last_exc = exc
                    continue
            raise RuntimeError(f"all routed LLM targets failed: {last_exc}")

        compressor = ContextCompressor(llm_fn=_llm_fn)
        messages = [{"role": "user", "content": context}]
        result = await compressor.compress(messages, context_limit=budget_chars // 3)
        if hasattr(result, "summary") and result.summary:
            return result.summary
        if hasattr(result, "compressed_messages") and result.compressed_messages:
            return "\n".join(m.get("content", "") for m in result.compressed_messages)
        return context[:budget_chars] + "\n...[compressed]"
    except Exception as exc:
        logger.debug("context compression unavailable, truncating: %s", exc)
        return context[:budget_chars] + "\n...[context truncated]"


# ── P0 cortex循环guard按会话持久化 ──────────────────────────────────────
# 修复接线缺陷：原实现每次请求new LoopGuard()——滑窗/去重集合每次请求清零，
# 重复检测永远不触发（guard接线了但检测是死的）。检测语义要求状态跨请求累积
# （同一用户多轮回答才构成"重复输出"），按session_key持久化实例。
_CHAT_LOOP_GUARDS: dict[str, LoopGuard] = {}
_CHAT_LOOP_GUARDS_MAX = 256  # 有界防泄漏；满时淘汰最旧（dict保持插入序）


def _get_chat_loop_guard(session_key: str) -> LoopGuard:
    """按会话key取持久化guard（同key同实例，状态跨请求累积）。"""
    guard = _CHAT_LOOP_GUARDS.get(session_key)
    if guard is None:
        while len(_CHAT_LOOP_GUARDS) >= _CHAT_LOOP_GUARDS_MAX:
            _CHAT_LOOP_GUARDS.pop(next(iter(_CHAT_LOOP_GUARDS)))
        guard = LoopGuard()
        _CHAT_LOOP_GUARDS[session_key] = guard
    return guard


def _check_loop(response_text: str, session_key: str = "default") -> dict | None:
    """P0 cortex: 循环/重复检测guard（持久化实例）。检测到循环返回警告信息，否则None。"""
    try:
        guard = _get_chat_loop_guard(session_key)
        result = guard.check(text_response=response_text)
        if result and result.is_looping:
            return {
                "type": "loop_warning",
                "severity": str(result.severity),
                "message": result.message,
            }
    except Exception as exc:
        logger.debug("loop guard unavailable: %s", exc)
    return None


# ── P0-6 TradingAgents决策延迟回填（pending→update_with_outcome）──────────
# 写路径：LLM路由决策先记pending，调用结果出来后把真实attempts_detail回填到同一条目
# ——"先记决策、后补结果"让决策记忆携带真实反馈信号（SUMMARY.md P0-6"不进化"痛点独有解）。
# 读路径①：src/gland/route_policy.py按provider成功率反馈调整主备（决策记忆影响下一次决策）
# 读路径②：/api/hippo/decisions/* 六个端点 + get_past_context() few-shot素材


def _route_decision_begin(question: str, routing_meta: dict, attempts: list) -> str:
    """记录pending路由决策（TradingAgents store_decision）。失败静默，不阻断chat。"""
    try:
        from src.hippo.decision_log import get_decision_log

        primary = attempts[0] if attempts else {}
        decision = (
            f"mode={routing_meta.get('mode', '')} prefer={routing_meta.get('prefer', '')} "
            f"primary={primary.get('provider', '')}:{primary.get('model', '')} "
            f"q_len={len(question)}"
        )
        entry = get_decision_log().store_decision(
            decision=decision,
            domain="llm_routing",
            agent_id="chat",
            context=question[:200],
            metadata={"attempts": [t.get("provider", "") for t in attempts]},
        )
        return str(entry.get("decision_id", ""))
    except Exception as exc:
        logger.debug("decision log (begin) unavailable: %s", exc)
        return ""


def _route_decision_end(decision_id: str, attempt_log: list, error: str = "") -> None:
    """回填outcome到pending决策（TradingAgents update_with_outcome）。失败静默。"""
    if not decision_id:
        return
    try:
        from src.hippo.decision_log import get_decision_log

        success = any(a.get("ok") for a in attempt_log)
        used = next((a.get("provider", "") for a in attempt_log if a.get("ok")), "")
        failover = success and len(attempt_log) > 1
        if success and not failover:
            reflection = f"首选provider {used} 直接成功"
        elif success:
            reflection = f"首选失败，failover到 {used} 成功（尝试{len(attempt_log)}次）"
        else:
            reflection = f"全部provider失败：{error[:200]}"
        metrics = {
            "success": success,
            "used_provider": used,
            "attempts": len(attempt_log),
            "failover": failover,
            "attempts_detail": attempt_log,
        }
        get_decision_log().update_with_outcome(
            decision_id=decision_id,
            domain="llm_routing",
            outcome=reflection,
            metrics=metrics,
            reflection=reflection,
            success=success,
        )
    except Exception as exc:
        logger.debug("decision log (end) unavailable: %s", exc)


@router.get("/health")
async def chat_health():
    """Chat system health check."""
    return {"status": "ok", "component": "ChatSystem"}


@router.get("/token-attribution")
async def token_attribution(limit: int = 20):
    """P1: 上下文逐项token归因（claude-code SDKContextUsage移植）。

    "上下文被什么吃掉了"的直接答案：最近LLM请求的逐项明细（hippo记忆/RAG chunk/问题）
    + 聚合摘要（top_consumers/超限记录/平均总量）。
    """
    try:
        attributor = get_attributor()
        return {
            "recent": attributor.recent(limit=min(max(limit, 1), 50)),
            "summary": attributor.summary(),
            # d439f163遗留#3：估算器校准状态顶层可见（sample_count/calibrated/factor）
            "calibration": attributor.calibration(),
        }
    except Exception as exc:
        return {"recent": [], "summary": {"total_records": 0, "error": str(exc)}}


def _attribute_chat_context(
    question: str,
    context_parts: list[str],
    memory_ctx: str,
    model: str | None = None,
    provider: str = "",
    session_key: str = "",
    actual_prompt_tokens: int | None = None,
    advisory: str = "",
    episodic: str = "",
    metacog: str = "",
) -> dict | None:
    """P1: 上下文逐项token归因（claude-code SDKContextUsage移植）。

    每次LLM请求把上下文构成逐项记账：hippo记忆逐条 / RAG chunk逐块 / 用户问题
    （F2b-②b 起含学习建议块逐行、DEC-③-① 起含失败教训块逐行、G1-④b 起含元认知纠偏块逐行），并按模型窗口计算percentage与over_limit（hard_limit vs compaction_window）。
    观测性旁路：任何失败只debug日志，不阻断chat主路径（fail-safe）。
    """
    try:
        items: list[ContextItem] = []
        if memory_ctx:
            mem_lines = [ln for ln in memory_ctx.splitlines() if ln.strip()]
            for i, ln in enumerate(mem_lines):
                items.append(
                    ContextItem(
                        kind=KIND_MEMORY,
                        name=f"hippo_memory[{i}]",
                        source="hippo.long_term_memory",
                        tokens=estimate_tokens(ln),
                    )
                )
        if advisory:
            adv_lines = [ln for ln in advisory.splitlines() if ln.strip()]
            for i, ln in enumerate(adv_lines):
                items.append(
                    ContextItem(
                        kind=KIND_IMPROVEMENT,
                        name=f"learned_advisory[{i}]",
                        source="learn.long_term",
                        tokens=estimate_tokens(ln),
                    )
                )
        if episodic:
            epi_lines = [ln for ln in episodic.splitlines() if ln.strip()]
            for i, ln in enumerate(epi_lines):
                items.append(
                    ContextItem(
                        kind=KIND_IMPROVEMENT,
                        name=f"episodic_advisory[{i}]",
                        source="learn.experience",
                        tokens=estimate_tokens(ln),
                    )
                )
        if metacog:
            meta_lines = [ln for ln in metacog.splitlines() if ln.strip()]
            for i, ln in enumerate(meta_lines):
                items.append(
                    ContextItem(
                        kind=KIND_IMPROVEMENT,
                        name=f"metacognitive_advisory[{i}]",
                        source="mirror.metacognition",
                        tokens=estimate_tokens(ln),
                    )
                )
        for i, part in enumerate(context_parts or []):
            items.append(
                ContextItem(
                    kind=KIND_RAG,
                    name=f"rag_chunk[{i}]",
                    source="search.semantic",
                    tokens=estimate_tokens(part),
                )
            )
        items.append(
            ContextItem(
                kind=KIND_MESSAGE,
                name="user_question",
                source="user",
                tokens=estimate_tokens(question),
            )
        )
        usage = build_context_usage(
            items,
            model=model,
            # d439f163遗留#3估算校准（两侧镜像）：provider回填推出的Σactual/Σestimated
            # 因子进入归因记录（calibrated_*字段与raw并排，估算偏差对观测者可见）
            calibration_factor=get_attributor().calibration().get("factor"),
        )
        get_attributor().record(
            usage,
            session_id=session_key,
            provider=provider,
            model=model or "",
            actual_prompt_tokens=actual_prompt_tokens,
        )
        return usage
    except Exception as exc:
        logger.debug("token attribution unavailable: %s", exc)
        return None


def _backfill_token_usage(session_key: str, usage: dict | None) -> None:
    """P1 provider usage回填：流式请求从SSE chunk捕获provider真实prompt_tokens后，
    回填到最近一条匹配session的归因记录，计算estimate_gap（估算vs真实偏差）。

    观测性旁路：任何失败只debug日志，不阻断流式主路径（fail-safe）。
    """
    try:
        if not usage:
            return
        pt = usage.get("prompt_tokens")
        if pt is None:
            return
        get_attributor().backfill_actual(session_key, int(pt))
    except Exception as exc:
        logger.debug("token usage backfill unavailable: %s", exc)


class ChatRequest(BaseModel):
    question: str
    top_k: int = 5
    stream: bool = True


async def rag_stream(question: str, user_id: UUID, top_k: int):
    """Generate SSE streaming response for RAG query."""
    answer_parts: list[str] = []  # P0循环guard：累积流式输出，流结束时做重复检测
    # Retrieve relevant chunks (timeout 8s: RAG不可用时降级为空context)
    try:
        results = await asyncio.wait_for(semantic_search(question, user_id, limit=top_k), timeout=8)
    except (TimeoutError, Exception) as _rag_exc:
        logger.warning("RAG unavailable (stream), degrading to LLM-only: %s", _rag_exc)
        results = []

    if not results:
        yield f"data: {json.dumps({'type': 'info', 'content': 'RAG检索不可用，直接使用LLM回答。'})}\n\n"
        # F2b-②b 推荐回流对话面：learned_patterns → prompt 软建议块（无关=零注入）
        advisory, adv_failed = await _get_learned_advisory(question)
        if adv_failed:
            yield f"data: {json.dumps({'type': 'info', 'content': '学习建议获取失败（不影响回答）。'})}\n\n"
        # DEC-③-① episodic 回流对话面：相似失败经验 error → prompt 软建议块
        # （与 F2b-②b learned 块并列的第二条回流轴，仅 failure 行 error 字段；
        # 无关=零注入。取数故障可见不阻断：info 帧 + episodic-context-error 日志）
        episodic, epi_failed = await _get_episodic_advisory(question)
        if epi_failed:
            yield f"data: {json.dumps({'type': 'info', 'content': '历史失败教训获取失败（不影响回答）。'})}\n\n"
        # G1-④b 元认知纠错回流对话面：blind_spots/calibration → prompt 纠偏块
        # （第三条回流轴，learned→episodic→metacognitive 序稳定；agent 级观测非
        # query 相关=与前两轴相关度门刻意不同源；空观测=零注入。
        # 取数故障可见不阻断：info 帧 + metacognitive-context-error 日志）
        metacog, meta_failed = await _get_metacognitive_advisory()
        if meta_failed:
            yield f"data: {json.dumps({'type': 'info', 'content': '元认知纠偏提醒获取失败（不影响回答）。'})}\n\n"
        advisory_all = "\n\n".join(x for x in (advisory, episodic, metacog) if x)
        # 降级：不返回error，继续走LLM路径（模型路由接线：按mode选target+备target重试）
        prompt = f"{question}\n\n{advisory_all}" if advisory_all else question
        attempts, _meta = _route_llm_targets(question)
        # P0-6决策延迟回填：路由决策先记pending，真实结果在本请求内回填同一条目
        dec_id = _route_decision_begin(question, _meta, attempts)
        # P1: 上下文逐项token归因——降级路径也记账（RAG不可用时上下文仅用户问题）
        _attribute_chat_context(
            question,
            [],
            "",
            model=(attempts[0].get("model") if attempts else None),
            provider=(attempts[0].get("provider", "") if attempts else ""),
            session_key=str(user_id),
            advisory=advisory,
            episodic=episodic,
            metacog=metacog,
        )
        attempt_log: list = []
        last_exc: Exception | None = None
        streamed_ok = False
        captured_usage: dict | None = None
        for t in attempts:
            if t.get("provider") == "online" and not t.get("api_key"):
                continue
            try:
                async with httpx.AsyncClient() as client:
                    async with client.stream(
                        "POST",
                        f"{t['base_url']}/chat/completions",
                        headers=_llm_headers(t.get("api_key", "")),
                        json={
                            "model": t["model"],
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.3,
                            "stream": True,
                        },
                        timeout=60,
                    ) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data = line[6:]
                            if data.strip() == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                if chunk.get("usage"):
                                    captured_usage = chunk["usage"]
                                _choices = chunk.get("choices") or []
                                delta = (_choices[0] or {}).get("delta", {}) if _choices else {}
                                content = delta.get("content")
                                if content:
                                    answer_parts.append(content)
                                    yield f"data: {json.dumps({'type': 'content', 'content': content})}\n\n"
                            except json.JSONDecodeError:
                                continue
                streamed_ok = True
                attempt_log.append({"provider": t.get("provider", ""), "ok": True})
                break
            except Exception as exc:
                last_exc = exc
                attempt_log.append(
                    {"provider": t.get("provider", ""), "ok": False, "error": str(exc)[:120]}
                )
                continue
        _route_decision_end(dec_id, attempt_log, error=str(last_exc) if not streamed_ok else "")
        if streamed_ok and captured_usage:
            _backfill_token_usage(str(user_id), captured_usage)
        if not streamed_ok:
            yield f"data: {json.dumps({'type': 'error', 'content': f'LLM也不可用: {last_exc}'})}\n\n"
        else:
            # P0 cortex: 流式路径循环guard（此前仅非流式路径接了guard）
            _lw = _check_loop("".join(answer_parts), session_key=str(user_id))
            if _lw:
                yield f"data: {json.dumps({'type': 'loop_warning', 'content': _lw})}\n\n"
        yield "data: [DONE]\n\n"
        return

    # Build context
    context_parts = []
    sources = []
    for i, r in enumerate(results):
        context_parts.append(f"[{i + 1}] {r.get('chunk', '')}")
        sources.append({"id": r.get("id"), "score": r.get("score")})

    # P0-1: 压缩过长上下文
    context = await _compress_if_needed("\n\n".join(context_parts))

    # P0-6: 注入hippo三因子记忆上下文
    memory_ctx = _get_memory_context(question)
    # F2b-②b 推荐回流对话面：learned_patterns → prompt 软建议块（无关=零注入）
    advisory, adv_failed = await _get_learned_advisory(question)
    if adv_failed:
        yield f"data: {json.dumps({'type': 'info', 'content': '学习建议获取失败（不影响回答）。'})}\n\n"
    # DEC-③-① episodic 回流对话面：与 learned 块并列的第二条回流轴（无关=零注入）
    episodic, epi_failed = await _get_episodic_advisory(question)
    if epi_failed:
        yield f"data: {json.dumps({'type': 'info', 'content': '历史失败教训获取失败（不影响回答）。'})}\n\n"
    # G1-④b 元认知纠错回流对话面：第三条回流轴（learned→episodic→metacognitive
    # 序稳定；agent 级观测非 query 相关；空观测=零注入。取数故障可见不阻断：
    # info 帧 + metacognitive-context-error 日志）
    metacog, meta_failed = await _get_metacognitive_advisory()
    if meta_failed:
        yield f"data: {json.dumps({'type': 'info', 'content': '元认知纠偏提醒获取失败（不影响回答）。'})}\n\n"
    advisory_all = "\n\n".join(x for x in (advisory, episodic, metacog) if x)

    # Send sources first
    yield f"data: {json.dumps({'type': 'sources', 'content': sources})}\n\n"

    prompt = f"""Based on the following context, answer the question. If the context doesn't contain enough information, say so.
{memory_ctx}
{advisory_all}
Context:
{context}

Question: {question}

Answer:"""

    # P0-4: 出站脱敏
    prompt = _redact_outbound(prompt)

    # 模型路由接线：按mode选LLM target，主target失败自动切备target
    attempts, routing_meta = _route_llm_targets(question)
    # P0-6决策延迟回填：路由决策先记pending，真实结果在本请求内回填同一条目
    dec_id = _route_decision_begin(question, routing_meta, attempts)
    # P1: 上下文逐项token归因（SDKContextUsage）——记忆/RAG chunk/问题逐项记账
    _attribute_chat_context(
        question,
        context_parts,
        memory_ctx,
        model=(attempts[0].get("model") if attempts else None),
        provider=(attempts[0].get("provider", "") if attempts else ""),
        session_key=str(user_id),
        advisory=advisory,
        episodic=episodic,
        metacog=metacog,
    )
    attempt_log = []
    last_exc: Exception | None = None
    streamed_ok = False
    captured_usage: dict | None = None
    for t in attempts:
        if t.get("provider") == "online" and not t.get("api_key"):
            continue
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{t['base_url']}/chat/completions",
                    headers=_llm_headers(t.get("api_key", "")),
                    json={
                        "model": t["model"],
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                        "stream": True,
                    },
                    timeout=120,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            if chunk.get("usage"):
                                captured_usage = chunk["usage"]
                            _choices = chunk.get("choices") or []
                            delta = (_choices[0] or {}).get("delta", {}) if _choices else {}
                            content = delta.get("content")
                            if content:
                                answer_parts.append(content)
                                yield f"data: {json.dumps({'type': 'content', 'content': content})}\n\n"
                        except json.JSONDecodeError:
                            continue
            streamed_ok = True
            attempt_log.append({"provider": t.get("provider", ""), "ok": True})
            break
        except Exception as exc:
            last_exc = exc
            attempt_log.append(
                {"provider": t.get("provider", ""), "ok": False, "error": str(exc)[:120]}
            )
            continue
    _route_decision_end(dec_id, attempt_log, error=str(last_exc) if not streamed_ok else "")
    if streamed_ok and captured_usage:
        _backfill_token_usage(str(user_id), captured_usage)

    if not streamed_ok:
        yield f"data: {json.dumps({'type': 'error', 'content': f'LLM也不可用: {last_exc}'})}\n\n"
    else:
        # P0 cortex: 流式路径循环guard（此前仅非流式路径接了guard）
        loop_warning = _check_loop("".join(answer_parts), session_key=str(user_id))
        if loop_warning:
            yield f"data: {json.dumps({'type': 'loop_warning', 'content': loop_warning})}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/")
async def chat(
    req: ChatRequest,
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
):
    """Chat with RAG. Supports SSE streaming.

    身份轴收口（F3b-⑥ 已裁决语义同族既有面延伸，chat·export·feedback 必填
    自报面执行件）：user_id 从认证派生（bind_identity），自报值只可确认派生
    绑定——认证请求自报他人档案=403（冒读面收口）；'default'/空/占位零 UUID
    =未声明→取派生；匿名保留自报（既有本地多档案语义契约零变化）；坏 token=401
    零副作用。档案键经 _resolve_user_id 家族正典归一（F3b-⑫ 同源）。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    if req.stream:
        return StreamingResponse(
            rag_stream(req.question, uid, req.top_k),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Non-streaming fallback
    try:
        results = await asyncio.wait_for(
            semantic_search(req.question, uid, limit=req.top_k), timeout=8
        )
    except (TimeoutError, Exception) as _rag_exc:
        logger.warning("RAG semantic_search unavailable (non-stream), degrading: %s", _rag_exc)
        results = []
    if not results:
        return {"answer": "No relevant knowledge found.", "sources": []}

    context_parts = []
    sources = []
    for i, r in enumerate(results):
        context_parts.append(f"[{i + 1}] {r.get('chunk', '')}")
        sources.append({"id": r.get("id"), "score": r.get("score")})

    # P0-1: 压缩过长上下文
    context = await _compress_if_needed("\n\n".join(context_parts))

    # P0-6: 注入hippo三因子记忆上下文
    memory_ctx = _get_memory_context(req.question)
    # F2b-②b 推荐回流对话面：learned_patterns → prompt 软建议块（无关=零注入；
    # 取数失败=learned-context-error 日志可见，非流式无 SSE 面故只有日志可见面）
    advisory, _adv_failed = await _get_learned_advisory(req.question)
    # DEC-③-① episodic 回流对话面：与 learned 块并列的第二条回流轴（无关=零注入；
    # 取数失败=episodic-context-error 日志可见，非流式无 SSE 面故只有日志可见面）
    episodic, _epi_failed = await _get_episodic_advisory(req.question)
    # G1-④b 元认知纠错回流对话面：第三条回流轴（learned→episodic→metacognitive
    # 序稳定；取数失败=metacognitive-context-error 日志可见，非流式无 SSE 面故
    # 只有日志可见面）
    metacog, _meta_failed = await _get_metacognitive_advisory()
    advisory_all = "\n\n".join(x for x in (advisory, episodic, metacog) if x)

    prompt = f"""Based on the following context, answer the question. If the context doesn't contain enough information, say so.
{memory_ctx}
{advisory_all}
Context:
{context}

Question: {req.question}

Answer:"""

    # P0-4: 出站脱敏
    prompt = _redact_outbound(prompt)

    # 模型路由接线：按mode选LLM target，失败自动切备target
    attempts, routing_meta = _route_llm_targets(req.question)
    # P0-6决策延迟回填：路由决策先记pending，真实结果在本请求内回填同一条目
    dec_id = _route_decision_begin(req.question, routing_meta, attempts)
    attempt_log = []
    answer = None
    last_exc: Exception | None = None
    used_target = None
    nonstream_usage: int | None = None
    for t in attempts:
        if t.get("provider") == "online" and not t.get("api_key"):
            continue
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{t['base_url']}/chat/completions",
                    headers=_llm_headers(t.get("api_key", "")),
                    json={
                        "model": t["model"],
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                    },
                    timeout=120,
                )
                resp.raise_for_status()
                _njson = resp.json()
                answer = _njson["choices"][0]["message"]["content"]
                nonstream_usage = (_njson.get("usage") or {}).get("prompt_tokens")
                used_target = {"provider": t.get("provider"), "model": t.get("model")}
                attempt_log.append({"provider": t.get("provider", ""), "ok": True})
                break
        except Exception as exc:
            last_exc = exc
            attempt_log.append(
                {"provider": t.get("provider", ""), "ok": False, "error": str(exc)[:120]}
            )
            continue
    _route_decision_end(dec_id, attempt_log, error=str(last_exc) if answer is None else "")
    # P1: 上下文逐项token归因——记录真实命中的provider/model对应的上下文构成
    _attribute_chat_context(
        req.question,
        context_parts,
        memory_ctx,
        model=(used_target or {}).get("model") or (attempts[0].get("model") if attempts else None),
        provider=(used_target or {}).get("provider", "")
        or (attempts[0].get("provider", "") if attempts else ""),
        session_key=str(uid),
        actual_prompt_tokens=nonstream_usage,
        advisory=advisory,
        episodic=episodic,
        metacog=metacog,
    )
    if answer is None:
        return {
            "answer": f"LLM不可用: {last_exc}",
            "sources": sources,
            "routing": {**routing_meta, "error": str(last_exc)},
        }

    # 循环检测guard
    loop_warning = _check_loop(answer, session_key=str(uid))
    result = {
        "answer": answer,
        "sources": sources,
        "routing": {**routing_meta, "used": used_target},
    }
    if loop_warning:
        result["warning"] = loop_warning
    return result
