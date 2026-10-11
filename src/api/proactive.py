"""Proactive API — 主动行为引擎的观测/手动触发入口（A3）。

只读+手动触发，不持状态：面板读 src.will.proactive.proactive_engine 单例，
与 main.py lifespan 心跳调度共用同一条 run_watcher 执行路径。

F3b-③c-2②a：事件留痕面按认证派生租户隔离（bind_identity，匿名='default'）——
/events 恒按派生租户过滤，/run 的事件行归属派生租户；/status 是引擎全局状态
（watcher 调度态，系统视图，不闸不滤）。
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from src.services.tenant_binding import bind_identity
from src.will import proactive as proactive_mod

router = APIRouter()


class RunResponse(BaseModel):
    watcher: str
    ok: bool
    duration_ms: float
    result: dict
    error: str = ""


@router.get("/status")
async def status():
    """引擎状态：心跳/各watcher间隔+上次执行+成功失败计数+最近结果。"""
    return await proactive_mod.proactive_engine.get_status()


@router.get("/events")
async def events(limit: int = 50, authorization: str | None = Header(None)):
    """主动行为事件留痕（proactive_events倒序回读，失败也可查）。

    F3b-③c-2②a：恒按派生租户过滤（匿名='default' 只见 default 行）。
    """
    identity = bind_identity(authorization)
    limit = max(1, min(int(limit), 200))
    return {
        "events": proactive_mod.proactive_engine.recent_events(limit, tenant_id=identity.tenant_id)
    }


@router.post("/run/{name}", response_model=RunResponse)
async def run(name: str, authorization: str | None = Header(None)):
    """手动触发某个主动行为（与心跳到期执行同路径，留痕同表）。

    F3b-③c-2②a：事件行归属=调用方派生租户（bind_identity，匿名='default'）。
    """
    identity = bind_identity(authorization)
    try:
        result = await proactive_mod.proactive_engine.run_watcher(
            name, tenant_id=identity.tenant_id
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown watcher: {name}")
    return result
