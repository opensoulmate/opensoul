"""主动行为默认注册 — A3：心跳驱动的定期任务，不只被动响应。

每个行为都是 OpenSoul 已有器官真实代码路径的周期化调用（不新建第二套逻辑）：
- memory_decay      → hippo MemoryStore.run_decay_cycle（短期记忆衰减/归档/遗忘周期）
- experience_collect→ learn ExperienceCollector.collect（执行产物→experiences 采集）
- heredity_triggers → heredity EvolutionEngine.evaluate_triggers（进化触发器+错误风暴熔断巡检）
- pattern_extract   → learn LongTermLearning.extract_patterns（experiences→learned_patterns 模式提取，F2b-①）
- advisory_review   → learn LongTermLearning.get_recommendations + ai_groups.submit_advisory_task（推荐回流消费面：改进任务上板+回群聊，DEC-④ 建议默认 B）
- self_evolve         → heredity run_evolution_cycle（自我进化周期：采集→分析→提案声明进审批管线，G1-②）

依赖全部可注入（store/engine/db_path），测试走 tmp 数据面，生产默认零配置。
"""

import asyncio
import logging
import os
from functools import partial
from typing import Any

logger = logging.getLogger("will.proactive-behaviors")


async def _default_learn_db_pool() -> Any:
    """学习面默认数据池单一解析（PROACT-③ 行为执行面数据面隔离）：

    OPENSOUL_DB 环境变量优先→EphemeralSQLitePool 显式路径临时池（call-time
    读取，与 experience_collector._default_opensoul_db 同一 env 单一源；按查询
    开合零常驻连接——行为回调逐 tick 重建临时池，常驻池会逐 tick 泄漏 aiosqlite
    工作线程）——测试进程里 lifespan 心跳 tick 跑 pattern_extract/advisory_review
    时 learned_patterns 写入面归 env 指定库，绝不写生产 opensoul.db（修前：行为
    缺省走 src.database.postgres.db_pool 单例，lifespan connect 即生产写面）。
    缺省（无 env）返回生产单例（main lifespan 已 connect，行为不重复建连），
    生产部署零配置行为字节不变。"""
    env = os.environ.get("OPENSOUL_DB")
    if env:
        from pathlib import Path

        from src.database.postgres import EphemeralSQLitePool

        return EphemeralSQLitePool(str(Path(env).resolve()))
    from src.database.postgres import db_pool as _db_pool

    return _db_pool


async def memory_decay(store: Any = None) -> dict:
    """短期记忆衰减周期：retention更新→低归档→低于遗忘线清除（同步逻辑to_thread隔离）。"""
    if store is None:
        from src.api import brain as _brain  # 认知层短期记忆单例（think/verify共用）

        store = _brain.store

    return await asyncio.to_thread(store.run_decay_cycle)


async def experience_collect(
    db_path: str | None = None,
    job_db_path: Any = None,
    eval_db_path: Any = None,
) -> dict:
    """定期学习：agent_messages/jobs/eval → experiences 表（source_ref幂等去重，重复采集零副作用）。"""
    from src.learn.experience_collector import ExperienceCollector

    kwargs: dict[str, Any] = {"tenant_id": "default", "agent_id": "default"}
    if db_path is not None:
        kwargs["db_path"] = db_path
    if job_db_path is not None:
        kwargs["job_db_path"] = job_db_path
    if eval_db_path is not None:
        kwargs["eval_db_path"] = eval_db_path
    collector = ExperienceCollector(**kwargs)
    return await asyncio.to_thread(collector.collect)


async def heredity_triggers(engine: Any = None, **trigger_params: Any) -> dict:
    """进化触发器周期评估：错误风暴熔断开合巡检 + idle进化触发，auto_propose=False 只评估不落提案。"""
    if engine is None:
        from src.api.heredity import evolution_engine as engine

    trigger_params.setdefault("auto_propose", False)
    return await asyncio.to_thread(lambda: engine.evaluate_triggers(**trigger_params))


async def pattern_extract(
    tenant_id: str = "default",
    agent_id: str = "default",
    db_pool: Any = None,
) -> dict:
    """定期学习收口（F2b-①）：experiences → learned_patterns 模式提取周期化。

    F2 审计定性半断点：extract_patterns 全仓唯一调用点=POST /api/brain/learn
    手动 API，无任何调度/执行流程触发 → learned_patterns 长期停更（生产实证
    2026-10-06 后不再更新，而 experiences 持续积累）。本行为把提取挂进心跳
    调度——提取本体零改动，周期化=同一代码路径的定期调用（与 /api/brain/learn
    共用 LongTermLearning.extract_patterns，不产生第二套提取逻辑）。
    """
    if db_pool is None:
        db_pool = await _default_learn_db_pool()
    from src.learn.long_term import LongTermLearning

    learning = LongTermLearning(db_pool, tenant_id, agent_id)
    extraction = await learning.extract_patterns()
    return {"extraction": extraction, **await learning.get_stats()}


def advisory_task_goal(rec: dict) -> str:
    """推荐 → 改进任务 goal 文本（确定性派生：只取 type·key 不可变身份段）。

    置信度/样本数/pattern_data 会随学习更新而变——进 goal 会让同一条推荐反复
    派生出不同 goal 绕过 at-most-once 去重（去重键=goal 全文），故稳定身份段
    只用 pattern_type·pattern_key，动态细节进任务 constraints。
    """
    return "[改进建议] {}·{}".format(rec.get("type", "?"), rec.get("key", "?"))


async def advisory_review(top_k: int = 3, db_pool: Any = None) -> dict:
    """DEC-④ 推荐回流面消费点（建议默认 B，自决例外记 pending-decisions.md ④）：
    定期读 learned_patterns 推荐（get_recommendations 同一读真源、ADVISORY_SCOPE
    同一口径）→ 生成改进类任务上板 + 回群聊（ai_groups.submit_advisory_task，
    与 B0 任务链回群聊同面）。

    形态自决（可一句改道，见 pending-decisions.md ④）：
    - 改进任务=提案上板不自动跑任务链（执行由人/既有 execute 口触发；回「④改B2」
      即接 _run_task_chain 自动执行）
    - at-most-once：同组同 goal 上过板（含终态）不重复上（宁少勿刷屏；回「④改B3」
      即改为仅对未终结行去重=周期性重提）
    - 无群组=如实空转可观测（生产无群组时零副作用）
    - 推荐读取失败如实抛（run_watcher 记 ok=False 可见，禁静默空转）；单组单条
      上板失败收进 errors 不掀翻其余（可见不阻断 F2b-②c-1 纪律）
    """
    if db_pool is None:
        db_pool = await _default_learn_db_pool()
    from src.api import ai_groups
    from src.learn.long_term import ADVISORY_SCOPE, LongTermLearning

    learning = LongTermLearning(db_pool, *ADVISORY_SCOPE)
    recs = (await learning.get_recommendations())[:top_k]
    conn = ai_groups.get_db()
    try:
        groups = [
            r["id"]
            for r in conn.execute("SELECT id FROM ai_groups ORDER BY created_at, id").fetchall()
        ]
    finally:
        conn.close()
    result: dict = {
        "groups": len(groups),
        "recommendations": len(recs),
        "created": 0,
        "task_ids": [],
        "skipped_existing": 0,
        "errors": [],
    }
    if not groups:
        result["reason"] = "no_groups"
        return result
    for gid in groups:
        for rec in recs:
            goal = advisory_task_goal(rec)
            try:
                res = await ai_groups.submit_advisory_task(gid, goal, rec)
            except Exception as e:  # 单组单条失败不掀翻其余上板
                result["errors"].append(f"{gid}: {type(e).__name__}: {e}")
                continue
            if res.get("created"):
                result["created"] += 1
                result["task_ids"].append(res.get("task_id"))
            else:
                result["skipped_existing"] += 1
    return result


async def self_evolve(
    tenant_id: str = "default", agent_id: str = "default", db_pool: Any = None
) -> dict:
    """G1-② 自我进化自动调度：run_evolution_cycle 周期化。

    G0 审计定性半断点：analyze_and_evolve→进化审批管线真链只经
    POST /api/brain/evolve 手动触发（触发面全手动）。本行为把整条
    进化周期（采集→分析→提案声明）挂进心跳调度——与 /api/brain/evolve
    共用 run_evolution_cycle 同一代码路径（F2b-① 纪律，不产生第二套
    编排逻辑）；提案仍是声明式进审批管线（审批后才落盘），
    自动调度不等于自动执行。
    """
    if db_pool is None:
        db_pool = await _default_learn_db_pool()
    from src.heredity.self_evolution import run_evolution_cycle

    return await run_evolution_cycle(tenant_id=tenant_id, agent_id=agent_id, db_pool=db_pool)


# 行为 → 执行间隔（秒）：维护类低频、采集类中频、巡检类高频
DEFAULT_INTERVALS: dict[str, float] = {
    "memory_decay": 6 * 3600,
    "experience_collect": 3600,
    "heredity_triggers": 1800,
    "pattern_extract": 2 * 3600,  # 滞后采集一拍（采集1h/提取2h）：积累≥2轮经验再提
    "advisory_review": 4
    * 3600,  # 再滞后提取一拍（提取2h/回流4h）：有模式再消费，at-most-once 不刷屏
    # G1-② 全链最低频（采集1h/提取2h/回流4h/进化8h）：基于积累数据进化，
    # 提案签名去重+审批管线（声明不等于执行）不刷屏
    "self_evolve": 8 * 3600,
}


def register_default_watchers(engine: Any) -> list[str]:
    """把六个默认主动行为注册进 ProactiveEngine（幂等：同名覆盖同一行为）。"""
    specs = {
        "memory_decay": (partial(memory_decay), DEFAULT_INTERVALS["memory_decay"]),
        "experience_collect": (
            partial(experience_collect),
            DEFAULT_INTERVALS["experience_collect"],
        ),
        "heredity_triggers": (partial(heredity_triggers), DEFAULT_INTERVALS["heredity_triggers"]),
        "pattern_extract": (partial(pattern_extract), DEFAULT_INTERVALS["pattern_extract"]),
        "advisory_review": (partial(advisory_review), DEFAULT_INTERVALS["advisory_review"]),
        "self_evolve": (partial(self_evolve), DEFAULT_INTERVALS["self_evolve"]),
    }
    for name, (fn, interval) in specs.items():
        engine.register_watcher(name, fn, interval_seconds=interval)
    logger.info("proactive default watchers registered: %s", sorted(specs))
    return list(specs)
