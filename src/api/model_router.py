"""模型路由配置 API"""

import os
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


# 路由模式配置
ROUTER_MODES = {
    "cost": {"models": ["mimo-7b-local", "mimo-v2.5-lite"]},
    "balance": {"models": ["mimo-v2.5-pro", "mimo-7b-local"]},
    "intelligence": {"models": ["mimo-v2.5-pro", "mimo-v2.5-pro-beta"]},
    "auto": {"models": ["mimo-v2.5-pro", "mimo-7b-local", "mimo-v2.5-lite"]},
}

current_mode = "balance"


class RouterModeRequest(BaseModel):
    mode: str


class RoutingConfigRequest(BaseModel):
    enabled: bool
    mode: str
    defaultStrategy: str
    autoParams: dict
    rules: list


@router.get("/config")
async def get_router_config():
    """获取当前路由模式配置（接线route_policy持久层）"""
    from src.gland import route_policy

    mode = route_policy.get_mode()
    return {
        "mode": mode,
        # 返回全部模式的模型表：前端line379按 models[mode] 取值
        "models": {m: cfg["models"] for m, cfg in ROUTER_MODES.items()},
    }


@router.post("/mode")
async def set_router_mode(req: RouterModeRequest):
    """切换路由模式 — 持久化，运行时chat/gland经route_policy消费"""
    from src.gland import route_policy

    if req.mode not in ROUTER_MODES:
        return {"error": f"Invalid mode: {req.mode}"}
    mode = route_policy.save_mode(req.mode)
    return {
        "mode": mode,
        "models": {m: cfg["models"] for m, cfg in ROUTER_MODES.items()},
        "persisted": True,
    }


@router.get("/routing-config")
async def get_routing_config():
    """获取自动路由策略配置"""
    from src.gland import route_policy

    return route_policy.get_routing_rules()


@router.post("/routing-config")
async def save_routing_config(req: RoutingConfigRequest):
    """保存自动路由策略配置 — route_policy消费"""
    from src.gland import route_policy

    config = req.dict()
    route_policy.save_routing_rules(config)
    return {"status": "ok", "message": "配置已保存"}


@router.get("/resolve")
async def resolve_route(message: str = ""):
    """可观测性：查看当前mode+消息会被路由到哪个LLM（不发起真实调用）"""
    from src.gland import route_policy

    decision = route_policy.resolve_target(message)
    # 外发脱敏：api_key只显示前缀
    for key in ("target", "backup_target"):
        t = decision.get(key)
        if t and t.get("api_key"):
            t["api_key"] = t["api_key"][:6] + "***"
    return decision
