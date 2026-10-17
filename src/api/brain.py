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


class ExecutionResultRequest(BaseModel):
    """执行结果回传协议（DEC-⑤-①，pending-decisions ⑤ 取 B）——agent 执行完回传
    执行证据，服务端 Reflector 汇总真 Verification（成败由检查派生，禁客户端自报
    success）→ ExperienceMemory.record 落 experiences（fix 列闭环——修前
    reflect→record 整链全仓零生产调用、experiences.fix 恒空）。证据字段与
    Reflector 检查一一对应：exception/error→系统异常、file_written/file_path→
    语法校验、test_output（{"passed": bool, "error": ...}）→功能校验、
    exit_code→退出码、diff+target_files→意图匹配。"""

    tenant_id: str = "default"
    agent_id: str = "default"
    task_id: str = ""
    action: str = Field(min_length=1)  # 做了什么（experiences.action）
    goal: str = ""  # 意图摘要（experiences.intent_summary；空→"unknown"）
    target_files: list[str] = []
    exit_code: int | None = None
    exception: bool = False
    error: str | None = None
    file_written: bool = False
    file_path: str = ""
    test_output: dict | None = None
    diff: str = ""
    # DEC-⑤-①b 幂等键（自决见 pending-decisions ⑪）：显式声明"这一份报告"的
    # 身份，同 result_id 重复回传不重复落库；未给=证据指纹派生（同份证据的重试
    # 同样幂等，证据变化=另一次观测各记一行）
    result_id: str = ""


class FeedbackRequest(BaseModel):
    tenant_id: str = "default"
    user_id: str = "default"
    action: str = ""
    feedback: str = ""
    # rating必填且1-5（POST handler校验）：省略时静默记中性分会污染
    # SelfEvolution._analyze_feedback的满意度均值——评分是显式人类信号
    rating: int | None = None


# H1-②b-⑥ 用户风险偏好声明面：UserMemory.get_preferences 的 5 键单一源
# （值域校验只收已知语义，未知键/越界值如实 422 拒禁静默吞声明）。
# notification_level 无既有值域定义，如实收非空串不 fabricate 枚举。
_PREF_ALLOWED_KEYS = {
    "risk_tolerance": ("low", "medium", "high"),
    "edit_mode_preference": ("patch", "full"),
    "notification_level": (),
    "auto_confirm_low_risk": (),
    "always_backup_before_edit": (),
}
_PREF_BOOL_KEYS = ("auto_confirm_low_risk", "always_backup_before_edit")


class PreferenceUpdateRequest(BaseModel):
    tenant_id: str = "default"
    user_id: str = "default"
    preferences: dict = {}


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
    from src.cortex.project_memory import acquire_project_memory
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
    # H1-②b-⑩ project_memory 调度：修前每请求构造 ProjectMemory=全仓重扫
    # （H1-② 定性「缓存增量无调度，think 每请求全仓重扫」）→共享实例 TTL 节流
    # 增量刷新，同 repo_root 请求间零扫描。
    assessor = RiskAssessor(
        await acquire_project_memory(req.repo_root) if req.repo_root else None,
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

    # 5a''. H1-②b-⑥ 用户风险偏好接线（UserMemory.should_auto_execute 修前全仓
    # 零消费=F2「策略写了不被读」同型）：偏好真进策略决策。语义（接线前定形，
    # 修前零消费方=无行为影响面）：
    # - deny 风险底线不动（与 should_auto_execute(high/critical)=False 自洽）；
    # - 收紧：偏好不容许自动时 auto→confirm_required，且 5b 之后再兜一道底线
    #   （用户要求的确认是底线，情绪/信心不得放宽——F4b-㉒「deny 不得放宽」同哲学）；
    # - 放宽：偏好容许自动时只作用于风险映射给出的 confirm_required（medium+
    #   risk_tolerance=high 用户显式选择），不作用于 5b 低信心/情绪收紧（那是
    #   独立信号——agent 没把握时仍要问人）；
    # - 读取故障→偏好不参与（回落接线前行为）+日志可见禁静默（F2b-②c-1 纪律）。
    from src.mind.user_memory import UserMemory

    pref_allows_auto = None
    try:
        pref_allows_auto = await UserMemory(
            db_pool, identity.tenant_id, identity.user_id
        ).should_auto_execute(risk.overall_level)
    except Exception as e:
        logger.warning("用户风险偏好读取失败（不阻断思考，偏好不参与）: %s", e)
    if pref_allows_auto is False and execute_mode == "auto":
        execute_mode = "confirm_required"
        confirm_prompt = (
            f"用户偏好收紧：不自动执行 {risk.overall_level} 风险操作，请确认：{risk.recommendation}"
        )
    elif pref_allows_auto is True and execute_mode == "confirm_required":
        execute_mode, confirm_prompt = "auto", None

    # 5a'. F4b-㉕ agent_confidence 生产源派生 + G1-④c 校准纠偏：声明优先（声明值
    # 是用户显式值非派生产物，不被纠偏顶替）；未声明从元认知账本真源派生（成败观测
    # 经验收缩，无观测回落 0.8 缺省=行为契约零变化），且失准观测（get_confidence_calibration
    # 'miscalibrated'）按预测误差方向（实际成功率−预测均值）纠 prior=派生自我纠错。
    # 账本不可读不阻断思考，回落缺省并留痕可见。（db_pool 已在本函数顶部导入，F4b-㉚）
    from src.mirror.metacognition import (
        Metacognition,
        calibration_prior_correction,
        derive_agent_confidence,
    )

    meta = Metacognition(db_pool, identity.tenant_id, req.agent_id)
    effective_confidence = req.agent_confidence
    confidence_correction = 0.0
    if effective_confidence is None:
        try:
            stats = await meta.confidence_stats()
            calibration = await meta.get_confidence_calibration()
            confidence_correction = calibration_prior_correction(calibration)
            effective_confidence = derive_agent_confidence(stats, calibration=calibration)
        except Exception as e:
            logger.warning("agent_confidence 派生失败（不阻断思考），回落缺省 0.8: %s", e)
            effective_confidence = 0.8
            confidence_correction = 0.0

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

    # H1-②b-⑥ 用户偏好底线：偏好收紧不被 5b 情绪/信心放宽——adjust_decision 的
    # 「高信心+正面情绪+低风险→auto」放宽规则不得撤销用户显式要求的确认
    # （用户偏好是底线，与 F4b-㉒「deny 不得放宽」同哲学）。
    if pref_allows_auto is False and execute_mode == "auto":
        execute_mode = "confirm_required"
        confirm_prompt = (
            f"用户偏好收紧：不自动执行 {risk.overall_level} 风险操作，请确认：{risk.recommendation}"
        )

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
            # F3b-⑨b-② 调用点接派生：身份派生租户键→record 行归属（闭"非 default
            # 租户动作事件落 'default' 自我不可见"缺口首个调用点；匿名='default' 行值不变）
            tenant_id=identity.tenant_id,
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
        # G1-④c 校准纠偏量（加性契约键）：生效派生先验上的预测误差校正量
        # （实际成功率−预测均值，负=历史上预测偏高本次向下纠）；声明路径恒 0.0
        # （声明值不被纠偏顶替）——纠偏可观测非静默
        "confidence_correction": confidence_correction,
        # H1-②b-⑥ 偏好生效值（加性契约键）：should_auto_execute 对本次风险等级
        # 的判定（True 容许自动/False 不容许/None=读取故障偏好不参与）——偏好
        # 影响了什么可观测非静默
        "preference_allows_auto": pref_allows_auto,
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
        await emit(
            "brain",
            "verify",
            f"🔍 {status}: {req.action[:100]}",
            req.result,
            # F3b-⑨b-② 调用点接派生（同 think：身份真源→行归属）
            tenant_id=identity.tenant_id,
        )
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


@router.post("/execution-result")
async def execution_result(req: ExecutionResultRequest, authorization: str | None = Header(None)):
    """执行结果回传协议接收面（DEC-⑤-①）：回传证据→Reflector 真 Verification→
    experiences 落库（含 fix）。与 /verify 分工：/verify=元认知成败账本
    （metacognition_log），本面=经验+Verification（experiences 含 fix 列），互补
    非重复。读取消费走既有面（get_relevant_experience/get_similar_failures/
    get_failure_fixes→think/推荐/evolve），本面只写不读。
    """
    # F3b-② 身份派生收口（同 think/verify：tenant 从认证派生禁自报）
    identity = bind_identity(authorization, req.tenant_id)

    from src.cortex.reflector import Reflector
    from src.database.postgres import db_pool
    from src.learn.experience import ExperienceMemory, execution_result_key
    from src.models.cognitive import Intent, TaskContext

    if db_pool is None:
        # C2 禁谎报：record() 对 db=None 是静默 no-op，若不拦则响应 recorded=True
        # 是假的——经验库没连上必须如实报错
        raise HTTPException(status_code=500, detail="经验库未连接（db_pool 为 None）")

    # 证据 dict=Reflector 既有消费形状（只带回传方真给的证据，未给=不造检查项）
    result: dict = {}
    if req.exception:
        result["exception"] = True
    if req.error:
        result["error"] = req.error
    if req.file_written:
        result["file_written"] = True
        result["file_path"] = req.file_path
    if req.test_output is not None:
        result["test_output"] = req.test_output
    if req.exit_code is not None:
        result["exit_code"] = req.exit_code
    if req.diff:
        result["diff"] = req.diff

    intent = Intent(
        user_prompt=req.action,
        target_files=list(req.target_files),
        modify_scope="single_file" if len(req.target_files) <= 1 else "multi_file",
        goal=req.goal or "unknown",
        change_size="small",
    )
    task_ctx = TaskContext(task_id=req.task_id, user_input=req.action, intent=intent)

    # 真 Verification：成败由检查派生（_check_intent_match 为确定性判定无 LLM）
    verification = await Reflector().reflect(req.action, result, task_ctx)

    # DEC-⑤-①b 幂等键单一派生口（key 语义自决见 pending-decisions ⑪）：
    # 显式 result_id 优先，未给=证据指纹派生（网络重试/重复回传同键）
    result_key = execution_result_key(req.result_id, req.model_dump())

    # 落库失败如实 500（禁谎报 ok）；行归属=派生租户（匿名='default' 契约不变）
    try:
        rec = await ExperienceMemory(db_pool, identity.tenant_id, req.agent_id).record(
            req.action, result, verification, task_ctx, result_key=result_key
        )
    except Exception as e:
        logger.warning("执行结果经验落库失败: %s", e)
        raise HTTPException(status_code=500, detail=f"经验落库失败: {e}")

    if rec.get("duplicate"):
        # 同键重复回传：不落新行，如实回报首行结果（禁谎报 recorded=True）
        return {
            "status": "ok",
            "duplicate": True,
            "recorded": False,
            "outcome": rec.get("outcome"),
            "checks": [],
            "error": rec.get("error"),
            "fix": rec.get("fix"),
            "result_key": result_key,
        }

    return {
        "status": "ok",
        "outcome": "success" if verification.success else "failure",
        "checks": [
            {"name": c.name, "passed": c.passed, "error": c.error} for c in verification.checks
        ],
        "error": verification.error,
        "fix": verification.fix,
        "recorded": True,
        "duplicate": False,
        "result_key": result_key,
    }


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
    """刷新项目记忆（H1-②b-⑩：共享实例强制增量刷新+落盘——与 think 同一真源，
    修前构造一次性 ProjectMemory 丢弃=刷新不影响 think 用的实例）"""
    if not repo_root:
        return {"status": "no_repo_root"}
    from src.cortex.project_memory import acquire_project_memory

    pm = await acquire_project_memory(repo_root, force_refresh=True)
    stats = pm.get_stats()
    return {"status": "refreshed", **stats}


@router.get("/preferences")
async def get_user_preferences(
    tenant_id: str = "default",
    user_id: str = "default",
    authorization: str | None = Header(None),
):
    """用户显式偏好读面（UserMemory.get_preferences 5 键单一源）。

    H1-②b-⑥ 声明面：修前 UserMemory.set 全仓零调用=偏好连写入面都没有（用户
    无法表达偏好，should_auto_execute 接线无从谈起）。身份派生 F3b-② 同款：
    tenant/user 从 token 身份派生，自报只可确认绑定不得矛盾。
    """
    identity = bind_identity(authorization, tenant_id, user_id)
    from src.database.postgres import db_pool
    from src.mind.user_memory import UserMemory

    um = UserMemory(db_pool, identity.tenant_id, identity.user_id)
    await um._ensure_table()  # 读路径自愈建表（老部署首读不炸）
    return {
        "preferences": await um.get_preferences(),
        "tenant_id": identity.tenant_id,
        "user_id": identity.user_id,
    }


@router.put("/preferences")
async def put_user_preferences(
    req: PreferenceUpdateRequest, authorization: str | None = Header(None)
):
    """用户显式偏好写面（部分更新：只写给出的键，其余保持）。

    值域校验只收已知语义（_PREF_ALLOWED_KEYS 单一源），未知键/越界值/类型错
    如实 422 拒禁静默吞声明（G1-⑤c-④ 纪律）。
    """
    identity = bind_identity(authorization, req.tenant_id, req.user_id)
    prefs = req.preferences
    if not isinstance(prefs, dict) or not prefs:
        raise HTTPException(status_code=422, detail="preferences must be a non-empty object")
    for key, value in prefs.items():
        if key not in _PREF_ALLOWED_KEYS:
            raise HTTPException(status_code=422, detail=f"unknown preference key: {key}")
        if key in _PREF_BOOL_KEYS:
            if not isinstance(value, bool):
                raise HTTPException(status_code=422, detail=f"{key} must be a boolean")
        elif key == "notification_level":
            if not isinstance(value, str) or not value.strip():
                raise HTTPException(
                    status_code=422, detail="notification_level must be a non-empty string"
                )
        else:
            allowed = _PREF_ALLOWED_KEYS[key]
            if not isinstance(value, str) or value not in allowed:
                raise HTTPException(status_code=422, detail=f"{key} must be one of {list(allowed)}")
    from src.database.postgres import db_pool
    from src.mind.user_memory import UserMemory

    um = UserMemory(db_pool, identity.tenant_id, identity.user_id)
    for key, value in prefs.items():
        await um.set(key, value)
    return {
        "status": "updated",
        "preferences": await um.get_preferences(),
        "tenant_id": identity.tenant_id,
        "user_id": identity.user_id,
    }


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
    from src.heredity.self_evolution import run_evolution_cycle

    # G1-② 单一代码路径：端点与 proactive self_evolve 行为共用
    # run_evolution_cycle（编排逻辑只此一份，禁两套漂移）。
    return await run_evolution_cycle(tenant_id=tenant_id, agent_id=agent_id)


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
    """创造力引擎。

    G1-③b-2 备选方案 UI 数据面：返回载荷加性键 payload（alternatives_payload
    归一——哨兵「错误」/「无LLM配置」如实归 error 键禁当真方案 fabricate），
    admin-ui 备选方案展示层消费归一载荷（前端零哨兵分类禁第二套判法）；
    alternatives 原始键契约字节不变（既有消费面零破坏）。
    """
    from src.cortex.creativity import CreativityEngine, alternatives_payload

    engine = CreativityEngine()
    constraint_list = constraints.split(",") if constraints else None
    alternatives = await engine.generate_alternatives(problem, constraint_list, count)
    return {"alternatives": alternatives, "payload": alternatives_payload(alternatives)}


@router.post("/creativity/cross-domain")
async def creativity_cross_domain(domain: str, problem: str):
    """跨领域联想（G1-① 死方法收口）——cross_domain_idea 唯一真消费面。

    修前 cross_domain_idea 全仓零调用零端点（wired-but-not-functional 死方法，
    G0 审计⑤）；本口为声明面：方法真被调用才可达。失败不谎报 ok（C2）：方法层
    如实带回 error 时端点 status=error（HTTP 200 in-body 报错沿用 diagnostics
    惯例），禁把故障伪装成空结果。domain/problem 必填（缺失=422 如实拒）。
    """
    from src.cortex.creativity import CreativityEngine

    engine = CreativityEngine()
    result = await engine.cross_domain_idea(domain, problem)
    if "error" in result:
        return {
            "status": "error",
            "error": str(result["error"]),
            "ideas": result.get("ideas", []),
        }
    return {
        "status": "ok",
        "ideas": result.get("ideas", []),
        "best_idea": result.get("best_idea"),
    }


@router.get("/health")
async def health():
    return {"status": "healthy", "module": "brain"}
