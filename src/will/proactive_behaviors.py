"""主动行为默认注册 — A3：心跳驱动的定期任务，不只被动响应。

每个行为都是 OpenSoul 已有器官真实代码路径的周期化调用（不新建第二套逻辑）：
- memory_decay      → hippo MemoryStore.run_decay_cycle（短期记忆衰减/归档/遗忘周期）
- experience_collect→ learn ExperienceCollector.collect（执行产物→experiences 采集）
- heredity_triggers → heredity EvolutionEngine.evaluate_triggers（进化触发器+错误风暴熔断巡检）

依赖全部可注入（store/engine/db_path），测试走 tmp 数据面，生产默认零配置。
"""

import asyncio
import logging
from functools import partial
from typing import Any

logger = logging.getLogger("will.proactive-behaviors")


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


# 行为 → 执行间隔（秒）：维护类低频、采集类中频、巡检类高频
DEFAULT_INTERVALS: dict[str, float] = {
    "memory_decay": 6 * 3600,
    "experience_collect": 3600,
    "heredity_triggers": 1800,
}


def register_default_watchers(engine: Any) -> list[str]:
    """把三个默认主动行为注册进 ProactiveEngine（幂等：同名覆盖同一行为）。"""
    specs = {
        "memory_decay": (partial(memory_decay), DEFAULT_INTERVALS["memory_decay"]),
        "experience_collect": (
            partial(experience_collect),
            DEFAULT_INTERVALS["experience_collect"],
        ),
        "heredity_triggers": (partial(heredity_triggers), DEFAULT_INTERVALS["heredity_triggers"]),
    }
    for name, (fn, interval) in specs.items():
        engine.register_watcher(name, fn, interval_seconds=interval)
    logger.info("proactive default watchers registered: %s", sorted(specs))
    return list(specs)
