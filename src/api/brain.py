"""Brain API — 认知层入口，调用OpenSoul已有模块。

不持有状态，不自己实例化模块。所有能力来自：
- hippo: 记忆系统（短期+衰减）
- cortex: 推理、风险评估、反思
- mind: 情绪、人格
- learn: 经验、学习
- mirror: 元认知
- heredity: 进化
- nerve: 事件总线
"""

import logging
import os
import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from src.services.tenant_binding import bind_identity

logger = logging.getLogger(__name__)

router = APIRouter()


# ── 请求模型 ──────────────────────────────────────────


class ThinkRequest(BaseModel):
    tenant_id: str = "default"
    agent_id: str = "default"
    user_input: str = ""
    repo_root: str = ""
    session_id: str = ""
    # F4b-㉕ agent_confidence 生产源派生：调用方可显式声明（0~1，越界如实 422 拒），
    # 未声明=None（派生哨兵）→ 从元认知账本真源派生（derive_agent_confidence：
    # 成败观测经验收缩；无观测回落 0.8"信心充足"=行为契约零变化）；声明优先
    # 不被派生顶替（F4b-㉒ 声明轴语义延续）
    agent_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class VerifyRequest(BaseModel):
    tenant_id: str = "default"
    agent_id: str = "default"
    task_id: str = ""
    action: str = ""
    result: dict = {}


class FeedbackRequest(BaseModel):
    tenant_id: str = "default"
    user_id: str = "default"
    action: str = ""
    feedback: str = ""
    # rating必填且1-5（POST handler校验）：省略时静默记中性分会污染
    # SelfEvolution._analyze_feedback的满意度均值——评分是显式人类信号
    rating: int | None = None


# ── 模块级单例（和hippo/api.py一样的模式）──────────────

from src.hippo.memory_store import MemoryStore
from src.hippo.session import SessionManager

store = MemoryStore()
sessions = SessionManager()


@router.post("/think")
async def think(req: ThinkRequest, authorization: str | None = Header(None)):
    """认知思考：调用已有模块完成意图理解+风险评估+策略决策"""
    # F3b-② 身份派生收口：tenant 声明只可确认绑定、不得矛盾（本端点不消费
    # tenant，收口在声明面；进程内直调不带 authorization=匿名语义，A1 接线不变）
    identity = bind_identity(authorization, req.tenant_id)
    from src.cortex.project_memory import ProjectMemory
    from src.cortex.risk_assessor import RiskAssessor
    from src.database.postgres import db_pool
    from src.learn.experience import ExperienceMemory
    from src.learn.long_term import ADVISORY_SCOPE
    from src.mind.emotion import EmotionAnalyzer
    from src.mind.emotion_decision import EmotionDrivenDecision
    from src.models.cognitive import Decision, Intent, TaskContext

    # 1. 情绪识别（mind模块）
    emotion = EmotionAnalyzer()
    emotion_result = emotion.analyze(req.user_input)

    # 2. 短期记忆（hippo模块）— 从会话记忆获取上下文
    session_memories = []
    if req.session_id:
        session_memories = store.search(session_id=req.session_id)

    # 3. 意图理解（规则+上下文）
    text = req.user_input.lower()
    goal = "fix_bug"
    if any(w in text for w in ["创建", "新建", "create"]):
        goal = "create"
    elif any(w in text for w in ["重构", "refactor", "优化"]):
        goal = "refactor"
    elif any(w in text for w in ["修复", "fix", "bug"]):
        goal = "fix_bug"
    elif any(w in text for w in ["添加", "add", "feature"]):
        goal = "add_feature"

    # 提取目标文件
    import re
    from pathlib import Path

    target_files = []
    if req.repo_root:
        for m in re.finditer(r"[\w/\\.-]+\.\w+", req.user_input):
            candidate = m.group(0).strip("\"'")
            full = Path(req.repo_root) / candidate
            if full.exists():
                target_files.append(candidate)

    intent = Intent(
        user_prompt=req.user_input,
        target_files=target_files,
        modify_scope="single_file" if len(target_files) <= 1 else "multi_file",
        goal=goal,
        change_size="small",
    )

    # 4. 风险评估（cortex模块）
    # F4b-㉚ 失败经验风险轴接线：修前第二参硬编码 None（全仓唯一构造点）=
    # 「历史失败经验」消费缝构造上不可达、ExperienceMemory 修前全仓零实例。
    # 真源口径 ADVISORY_SCOPE（default/default）= experience_collect 采集口径
    # （学习产出在哪写就去哪读，F2b-②c-1 单一源；按 req.agent_id 读恒空集——
    # 生产行 agent_id 全 'default'，接线了≠跑通了）。
    # F4b-㉜：同实例供 5d 相关经验观测面复用（get_relevant_experience 修前全仓
    # 零消费=wired-but-not-functional 产出面）。
    experience_memory = ExperienceMemory(db_pool, *ADVISORY_SCOPE)
    assessor = RiskAssessor(
        ProjectMemory(req.repo_root) if req.repo_root else None,
        experience_memory,
    )
    task_ctx = TaskContext(user_input=req.user_input, intent=intent)
    risk = await assessor.assess(intent, task_ctx)

    # 5. 策略决策
    edit_mode = "full" if goal == "create" else "patch"
    if risk.overall_level == "critical":
        execute_mode, confirm_prompt = "deny", f"风险过高：{risk.recommendation}"
    elif risk.overall_level in ("high", "medium"):
        execute_mode = "confirm_required"
        confirm_prompt = f"需要确认：{risk.recommendation}"
    else:
        execute_mode, confirm_prompt = "auto", None

    # 5a'. F4b-㉕ agent_confidence 生产源派生：声明优先；未声明从元认知账本真源
    # 派生（成败观测经验收缩，无观测回落 0.8 缺省=行为契约零变化）。账本不可读
    # 不阻断思考，回落缺省并留痕可见。（db_pool 已在本函数顶部导入，F4b-㉚）
    from src.mirror.metacognition import Metacognition, derive_agent_confidence

    meta = Metacognition(db_pool, identity.tenant_id, req.agent_id)
    effective_confidence = req.agent_confidence
    if effective_confidence is None:
        try:
            effective_confidence = derive_agent_confidence(await meta.confidence_stats())
        except Exception as e:
            logger.warning("agent_confidence 派生失败（不阻断思考），回落缺省 0.8: %s", e)
            effective_confidence = 0.8

    # 5b. 情感驱动决策（mind模块，F4b-㉒ 接线——修前 adjust_decision 全仓零调用点）：
    # 情绪/信心影响决策权重：负面情绪+低信心收紧门禁（auto→confirm_required）、
    # 正面情绪+高信心可放宽到 auto；deny 风险底线不被放宽（adjust_decision 自身语义）。
    decision = EmotionDrivenDecision().adjust_decision(
        Decision(
            intent=intent,
            risk=risk,
            edit_mode=edit_mode,
            execute_mode=execute_mode,
            confirm_prompt=confirm_prompt,
        ),
        user_emotion=emotion_result.primary_emotion,
        agent_confidence=effective_confidence,
    )
    execute_mode, confirm_prompt = decision.execute_mode, decision.confirm_prompt

    # 5c. F4b-㉕ 决策落元认知账本（log_decision 修前全仓零调用点=wired-but-not-
    # functional，与 emotion_decision 同型）：决策+自评信心入账=校准对比的决策轴；
    # 记账失败不阻断思考但必须可见。ref=配对键（F4b-㉖）：ai_groups 同一 task_id
    # 双写点（think session_id=verify task_id）→ get_confidence_calibration 配对校准。
    try:
        await meta.log_decision(
            decision=execute_mode,
            reasoning=(
                f"goal={goal}, risk={risk.overall_level}, "
                f"emotion={emotion_result.primary_emotion}, files={target_files}"
            ),
            confidence=effective_confidence,
            ref=req.session_id or "",
        )
    except Exception as e:
        logger.warning("元认知决策记账失败（不阻断思考）: %s", e)

    # 5d. F4b-㉜ 相关经验观测面（get_relevant_experience 修前全仓零消费=
    # wired-but-not-functional 产出面，F4b-㉒/㉕/㉚ 同型）：think 是真实执行
    # 路径（ai_groups 任务执行每任务调用），把相关历史经验（成败/错误/相关度）
    # 作为**加性观测键**回出——只观测不决策（execute_mode/confirm 门禁、risk
    # 语义零变化，经验→prompt 执行回流消费点待拍）；读取故障可见不阻断
    # （F4b-㉚ 纪律：logger 真因，experience.error 如实带因）。
    experience_items: list[dict] = []
    experience_total = 0
    experience_error = None
    try:
        relevant = await experience_memory.get_relevant_experience(intent)
        experience_total = len(relevant)
        experience_items = [
            {
                "intent_summary": e.intent_summary,
                "outcome": e.outcome,
                "error": e.error,
                "score": round(e.relevance_score, 3),
            }
            for e in relevant[:3]
        ]
    except Exception as e:
        experience_error = str(e)
        logger.warning("相关经验读取失败（不阻断思考）: %s", e)

    # 6. 存入短期记忆（hippo模块）
    if req.session_id:
        store.add(
            session_id=req.session_id,
            content=f"[think] goal={goal}, risk={risk.overall_level}, files={target_files}",
            importance=0.6,
            tags=["think", goal],
        )

    # 7. 发布事件（nerve模块）
    try:
        from src.nerve.event_bridge import emit

        await emit(
            "brain",
            "think",
            f"🧠 意图分析: {goal}, 风险: {risk.overall_level}",
            {
                "goal": goal,
                "risk": risk.overall_level,
                "files": target_files,
            },
        )
    except Exception:
        pass

    return {
        "intent": {
            "goal": intent.goal,
            "target_files": intent.target_files,
            "change_size": intent.change_size,
            "modify_scope": intent.modify_scope,
        },
        "risk": {
            "risks": [{"title": r.title, "level": r.level, "desc": r.desc} for r in risk.risks],
            "overall_level": risk.overall_level,
            "recommendation": risk.recommendation,
        },
        "decision": {
            "edit_mode": edit_mode,
            "execute_mode": execute_mode,
            "confirm_prompt": confirm_prompt,
        },
        "emotion": {
            "primary": emotion_result.primary_emotion,
            "confidence": emotion_result.confidence,
            "sentiment": emotion_result.sentiment,
        },
        # F4b-㉕ 生效信心（加性契约键）：声明值或账本派生值，可观测低信心收紧真因
        "agent_confidence": effective_confidence,
        # F4b-㉜ 相关经验观测键（加性契约键）：top-3 相关历史经验 + 相关总数
        # （count=相关命中总数、items=前 3 条），故障如实带 error 键（禁静默空表）
        "experience": {
            "count": experience_total,
            "items": experience_items,
            **({"error": experience_error} if experience_error else {}),
        },
        "memory_count": len(session_memories),
    }


@router.post("/verify")
async def verify(req: VerifyRequest, authorization: str | None = Header(None)):
    """执行验证：反思+记忆+事件"""
    # F3b-② 身份派生收口（同 think：声明面收口，本端点不消费 tenant）
    identity = bind_identity(authorization, req.tenant_id)
    # 1. 存入短期记忆
    store.add(
        session_id=req.task_id,
        content=f"[verify] action={req.action[:200]}, success={req.result.get('success', False)}",
        importance=0.7 if req.result.get("success") else 0.9,  # 失败记忆更重要
        tags=["verify", "success" if req.result.get("success") else "failure"],
    )

    # 2. 发布事件
    try:
        from src.nerve.event_bridge import emit

        status = "✅ 成功" if req.result.get("success") else "❌ 失败"
        await emit("brain", "verify", f"🔍 {status}: {req.action[:100]}", req.result)
    except Exception:
        pass

    # 3. F4b-㉕ 结果落元认知账本（log_outcome 修前全仓零调用点=wired-but-not-
    # functional）：成败观测=agent_confidence 生产源派生的真源轴（think 未声明
    # 信心经 derive_agent_confidence 消费本账本）；记账失败不阻断验证但必须可见。
    # ref=配对键（F4b-㉖）：与 think 决策行同键配对（ai_groups 同一 task_id）。
    try:
        from src.database.postgres import db_pool
        from src.mirror.metacognition import FAILURE_OUTCOME, SUCCESS_OUTCOME, Metacognition

        await Metacognition(db_pool, identity.tenant_id, req.agent_id).log_outcome(
            decision=req.action[:200] or "unspecified",
            # F4b-㉗ 成败轴单一源常量（禁字面抄写漂移）：与读面分类同源
            outcome=SUCCESS_OUTCOME if req.result.get("success") else FAILURE_OUTCOME,
            ref=req.task_id or "",
        )
    except Exception as e:
        logger.warning("元认知结果记账失败（不阻断验证）: %s", e)

    return {"status": "verified", "memorized": True}


@router.post("/remember")
async def remember(
    session_id: str = "", content: str = "", importance: float = 0.5, tags: str = ""
):
    """主动记忆：存入hippo"""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    mem = store.add(session_id=session_id, content=content, importance=importance, tags=tag_list)
    return {"memory_id": mem.memory_id, "stored": True}


@router.get("/recall")
async def recall(session_id: str = "", query: str = "", limit: int = 10):
    """回忆：从hippo检索记忆"""
    if session_id:
        memories = store.search(session_id=session_id, limit=limit)
    else:
        memories = store.search(limit=limit)
    return {
        "memories": [
            {
                "id": m.memory_id,
                "content": m.content,
                "importance": m.importance,
                "tags": m.tags,
                "retention": m.retention,
            }
            for m in memories[:limit]
        ],
        "total": len(memories),
    }


@router.get("/status")
async def status():
    """大脑状态"""
    return {
        "memory": store.get_stats(),
        "sessions": {"total": len(sessions._sessions) if hasattr(sessions, "_sessions") else 0},
    }


@router.post("/refresh")
async def refresh(repo_root: str = ""):
    """刷新项目记忆"""
    if not repo_root:
        return {"status": "no_repo_root"}
    from src.cortex.project_memory import ProjectMemory

    pm = ProjectMemory(repo_root)
    stats = pm.get_stats()
    return {"status": "refreshed", **stats}


@router.post("/feedback")
async def feedback(req: FeedbackRequest, authorization: str | None = Header(None)):
    """用户反馈：短期记忆 + user_feedback表（P0-7进化数据层闭环）。

    22:05轮遗留#3销账：此前反馈只进MemoryStore（"[feedback] ..."记忆行，
    可观测但不可分析），user_feedback表零写入方 → SelfEvolution
    ._analyze_feedback永远空表默认值——"人类互通反馈源"到不了进化引擎，
    style_adjustment提案结构性缺失。本端点双写：记忆保留人看的语境 +
    表供进化分析；响应携带evolution_facing=进化引擎同源统计
    （SelfEvolution.feedback_stats，非独立镜像数字）。
    """
    # F3b-② 身份派生收口：tenant/user 从 token 身份派生（单一真源=users 行），
    # 自报只可确认绑定不得矛盾——矛盾=403 冒读面收口
    identity = bind_identity(authorization, req.tenant_id, req.user_id)
    tenant_id, user_id = identity.tenant_id, identity.user_id
    action = (req.action or "").strip()
    if not action:
        raise HTTPException(status_code=400, detail="action is required")
    if req.rating is None or not (1 <= int(req.rating) <= 5):
        raise HTTPException(status_code=400, detail="rating is required and must be an integer 1-5")

    store.add(
        session_id=f"user:{user_id}",
        content=f"[feedback] {req.action}: {req.feedback} (rating={req.rating})",
        importance=0.8,
        tags=["feedback", str(req.rating)],
    )

    from src.database.postgres import db_pool
    from src.heredity.self_evolution import SelfEvolution
    from src.mind.user_memory import UserMemory

    um = UserMemory(db_pool, tenant_id, user_id)
    await um.record_feedback(action, req.feedback or "", int(req.rating))
    evo_stats = await SelfEvolution(db_pool, tenant_id, user_id).feedback_stats()
    return {"status": "recorded", "memorized": True, "evolution_facing": evo_stats}


@router.get("/feedback")
async def get_feedback(
    tenant_id: str = "default",
    user_id: str = "default",
    limit: int = 20,
    authorization: str | None = Header(None),
):
    """查看用户反馈（Khoj信任设计：用户可看AI记录了什么）+ 进化引擎同源统计。"""
    identity = bind_identity(authorization, tenant_id, user_id)
    tenant_id, user_id = identity.tenant_id, identity.user_id
    from src.database.postgres import db_pool
    from src.heredity.self_evolution import SelfEvolution
    from src.mind.user_memory import UserMemory

    um = UserMemory(db_pool, tenant_id, user_id)
    await um._ensure_table()  # 读路径自愈建表（老部署首读不炸）
    rows = await db_pool.fetch(
        "SELECT id, action, feedback, rating, created_at FROM user_feedback "
        "WHERE tenant_id = ? AND user_id = ? ORDER BY created_at DESC LIMIT ?",
        (tenant_id, user_id, max(1, min(int(limit), 200))),
    )
    evo_stats = await SelfEvolution(db_pool, tenant_id, user_id).feedback_stats()
    return {
        "feedback": [dict(r) for r in rows],
        "evolution_facing": evo_stats,
    }


@router.post("/learn")
async def learn_endpoint(
    tenant_id: str = "default",
    agent_id: str = "default",
    authorization: str | None = Header(None),
):
    """从经验中学习（extraction统计=准入门可观测面：pruned存量自愈
    + skipped按原因码计数——被拒的噪声不再静默消失）。"""
    tenant_id = bind_identity(authorization, tenant_id).tenant_id
    from src.database.postgres import db_pool
    from src.learn.long_term import LongTermLearning

    learning = LongTermLearning(db_pool, tenant_id, agent_id)
    extraction = await learning.extract_patterns()
    return {"status": "learned", **await learning.get_stats(), "extraction": extraction}


@router.get("/recommendations")
async def recommendations(
    tenant_id: str = "default",
    agent_id: str = "default",
    intent: str = "",
    authorization: str | None = Header(None),
):
    """学习推荐"""
    tenant_id = bind_identity(authorization, tenant_id).tenant_id
    from src.database.postgres import db_pool
    from src.learn.long_term import LongTermLearning

    learning = LongTermLearning(db_pool, tenant_id, agent_id)
    recs = await learning.get_recommendations(intent)
    return {"recommendations": recs}


@router.post("/evolve")
async def evolve(
    tenant_id: str = "default",
    agent_id: str = "default",
    authorization: str | None = Header(None),
):
    """自我进化 — 分析结果转入进化闭环管线（声明式提案，审批后才落盘）。

    P0-7接线：SelfEvolution.analyze_and_evolve() 的每条发现不再只写日志，
    而是作为声明式提案进入 /api/heredity/evolution/* 审批管线
    （LobeChat范式：声明≠执行，reviewer审批人≠发起人）。
    """
    tenant_id = bind_identity(authorization, tenant_id).tenant_id
    from src.database.postgres import db_pool
    from src.heredity.self_evolution import SelfEvolution
    from src.learn.experience_collector import ExperienceCollector

    KIND_MAP = {
        "failure_avoidance": "failure_avoidance",
        "strategy_adjustment": "policy_adjustment",
        "style_adjustment": "prompt_strategy",
    }

    # P0-7数据层（TradingAgents outcome回填）：先采集真实执行产物
    # （agent_messages/jobs/eval实验）→ experiences，再分析——进化永远基于
    # 最新真实数据。采集异常不阻断分析（表可能已有历史数据可分析），
    # 错误显式进experience_collection.error（mem0 §1.1失败必须可见）。
    # 采集源三级解析（experience_collector._resolve_source_path）：本端点无HTTP
    # 传参入口，job_queue.db/eval_loop.db经settings.experience_*_db_path注入换源
    # （heredity_client隔离面第四层），生产默认路径零配置不变。
    collection: dict = {}
    try:
        collection = ExperienceCollector(tenant_id=tenant_id, agent_id=agent_id).collect()
    except Exception as e:  # 采集失败可见但不阻断进化分析
        collection = {"status": "failed", "error": str(e), "written": 0}

    evolutions = []
    analysis_error = ""
    try:
        evolution = SelfEvolution(db_pool, tenant_id, agent_id)
        evolutions = await evolution.analyze_and_evolve()
    except Exception as e:  # 分析失败不阻断管线观测
        analysis_error = str(e)

    # 单例接线（2026-10-06 heredity测试隔离轮）：此前每次调用都 EvolutionEngine()
    # 新建引擎——生产里与 /api/heredity/evolution/* 双引擎并存（同一DB文件但状态视图
    # 分裂），测试里换不掉数据面（heredity_api.evolution_engine换tmp对本端点无效
    # →TestEvolutionAPI经本端点把提案写穿生产evolution.db）。改为共享heredity单例：
    # 单一管线（will/job_handlers.py:34同款late import），测试可换数据面。
    from src.api.heredity import evolution_engine as engine

    declared = []
    for evo in evolutions:
        kind = KIND_MAP.get(evo.get("type", ""), "policy_adjustment")
        # 去重键=_digest(kind, title)（evolution_loop.declare_intent:490）。
        # failure模式的action文本同类相同（"自动规避: …"；F2b-②c-1 后随学习
        # 建议取值可变）——只用
        # action做title会让不同失败模式（不同reason）互相误判duplicate，
        # 4条不同发现在live实证中被折叠成1条（证据丢失）。title携带reason后
        # digest按发现内容去重：同一发现重复declare→duplicate，不同发现各建单。
        _action = str(evo.get("action") or "").strip()
        _reason = str(evo.get("reason") or "").strip()
        _title = (
            f"{_action}: {_reason}"
            if _action and _reason
            else (_action or _reason or "auto evolution")
        )
        result = engine.declare_intent(
            kind=kind,
            title=_title[:200],
            rationale=str(evo.get("reason", "")),
            confidence=0.4,
            evidence_refs=[f"evolution_log:{evo.get('type', 'unknown')}"],
            proposer=f"self_evolution:{agent_id}",
        )
        declared.append(
            {
                "proposal_id": result.get("proposal_id"),
                "status": result.get("status"),
                "reject_reason": result.get("reject_reason", ""),
                "duplicate": result.get("duplicate", False),
            }
        )
    return {
        "evolutions": evolutions,
        "analysis_error": analysis_error,
        "experience_collection": collection,
        "declared_proposals": declared,
        "pipeline": engine.get_stats(),
    }


@router.get("/metacognition")
async def metacognition(
    tenant_id: str = "default",
    agent_id: str = "default",
    authorization: str | None = Header(None),
):
    """元认知"""
    tenant_id = bind_identity(authorization, tenant_id).tenant_id
    from src.database.postgres import db_pool
    from src.mirror.metacognition import Metacognition

    meta = Metacognition(db_pool, tenant_id, agent_id)
    return {
        "recent_decisions": await meta.reflect_recent(),
        "blind_spots": await meta.get_blind_spots(),
        "calibration": await meta.get_confidence_calibration(),
        # F4b-㉕ 派生真源统计（加性契约键）：think/verify 两写点落账的决策/成败轴，
        # derive_agent_confidence 的消费面
        "confidence_source": await meta.confidence_stats(),
    }


@router.get("/agents")
async def agents(tenant_id: str = "default", authorization: str | None = Header(None)):
    """多Agent协调"""
    tenant_id = bind_identity(authorization, tenant_id).tenant_id
    from src.cortex.multi_agent_coord import MultiAgentCoordinator
    from src.database.postgres import db_pool

    coord = MultiAgentCoordinator(db_pool, tenant_id)
    return {"agents": await coord.get_active_agents()}


@router.post("/creativity")
async def creativity(problem: str = "", constraints: str = "", count: int = 3):
    """创造力引擎"""
    from src.cortex.creativity import CreativityEngine

    engine = CreativityEngine()
    constraint_list = constraints.split(",") if constraints else None
    alternatives = await engine.generate_alternatives(problem, constraint_list, count)
    return {"alternatives": alternatives}


@router.get("/health")
async def health():
    return {"status": "healthy", "module": "brain"}
