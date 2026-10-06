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

import os
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


# ── 请求模型 ──────────────────────────────────────────


class ThinkRequest(BaseModel):
    tenant_id: str = "default"
    agent_id: str = "default"
    user_input: str = ""
    repo_root: str = ""
    session_id: str = ""


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
async def think(req: ThinkRequest):
    """认知思考：调用已有模块完成意图理解+风险评估+策略决策"""
    from src.cortex.project_memory import ProjectMemory
    from src.cortex.reflector import Reflector
    from src.cortex.risk_assessor import RiskAssessor
    from src.mind.emotion import EmotionAnalyzer
    from src.models.cognitive import Intent, TaskContext

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
    assessor = RiskAssessor(
        ProjectMemory(req.repo_root) if req.repo_root else None,
        None,
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
        "memory_count": len(session_memories),
    }


@router.post("/verify")
async def verify(req: VerifyRequest):
    """执行验证：反思+记忆+事件"""
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
async def feedback(req: FeedbackRequest):
    """用户反馈：短期记忆 + user_feedback表（P0-7进化数据层闭环）。

    22:05轮遗留#3销账：此前反馈只进MemoryStore（"[feedback] ..."记忆行，
    可观测但不可分析），user_feedback表零写入方 → SelfEvolution
    ._analyze_feedback永远空表默认值——"人类互通反馈源"到不了进化引擎，
    style_adjustment提案结构性缺失。本端点双写：记忆保留人看的语境 +
    表供进化分析；响应携带evolution_facing=进化引擎同源统计
    （SelfEvolution.feedback_stats，非独立镜像数字）。
    """
    action = (req.action or "").strip()
    if not action:
        raise HTTPException(status_code=400, detail="action is required")
    if req.rating is None or not (1 <= int(req.rating) <= 5):
        raise HTTPException(status_code=400, detail="rating is required and must be an integer 1-5")

    store.add(
        session_id=f"user:{req.user_id}",
        content=f"[feedback] {req.action}: {req.feedback} (rating={req.rating})",
        importance=0.8,
        tags=["feedback", str(req.rating)],
    )

    from src.database.postgres import db_pool
    from src.heredity.self_evolution import SelfEvolution
    from src.mind.user_memory import UserMemory

    um = UserMemory(db_pool, req.tenant_id, req.user_id)
    await um.record_feedback(action, req.feedback or "", int(req.rating))
    evo_stats = await SelfEvolution(db_pool, req.tenant_id, req.user_id).feedback_stats()
    return {"status": "recorded", "memorized": True, "evolution_facing": evo_stats}


@router.get("/feedback")
async def get_feedback(tenant_id: str = "default", user_id: str = "default", limit: int = 20):
    """查看用户反馈（Khoj信任设计：用户可看AI记录了什么）+ 进化引擎同源统计。"""
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
async def learn_endpoint(tenant_id: str = "default", agent_id: str = "default"):
    """从经验中学习（extraction统计=准入门可观测面：pruned存量自愈
    + skipped按原因码计数——被拒的噪声不再静默消失）。"""
    from src.database.postgres import db_pool
    from src.learn.long_term import LongTermLearning

    learning = LongTermLearning(db_pool, tenant_id, agent_id)
    extraction = await learning.extract_patterns()
    return {"status": "learned", **await learning.get_stats(), "extraction": extraction}


@router.get("/recommendations")
async def recommendations(tenant_id: str = "default", agent_id: str = "default", intent: str = ""):
    """学习推荐"""
    from src.database.postgres import db_pool
    from src.learn.long_term import LongTermLearning

    learning = LongTermLearning(db_pool, tenant_id, agent_id)
    recs = await learning.get_recommendations(intent)
    return {"recommendations": recs}


@router.post("/evolve")
async def evolve(tenant_id: str = "default", agent_id: str = "default"):
    """自我进化 — 分析结果转入进化闭环管线（声明式提案，审批后才落盘）。

    P0-7接线：SelfEvolution.analyze_and_evolve() 的每条发现不再只写日志，
    而是作为声明式提案进入 /api/heredity/evolution/* 审批管线
    （LobeChat范式：声明≠执行，reviewer审批人≠发起人）。
    """
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
        # failure模式的action文本全部相同（"自动规避: 增加前置检查"）——只用
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
async def metacognition(tenant_id: str = "default", agent_id: str = "default"):
    """元认知"""
    from src.database.postgres import db_pool
    from src.mirror.metacognition import Metacognition

    meta = Metacognition(db_pool, tenant_id, agent_id)
    return {
        "recent_decisions": await meta.reflect_recent(),
        "blind_spots": await meta.get_blind_spots(),
        "calibration": await meta.get_confidence_calibration(),
    }


@router.get("/agents")
async def agents(tenant_id: str = "default"):
    """多Agent协调"""
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
