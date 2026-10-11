"""Context API：上下文窗口管理（docs/roadmap.md P0 缺失能力"上下文管理"）。

- POST /api/context/fit {messages, budget_tokens} → 适配后的上下文窗口 + 诚实统计；
- POST /api/context/fit-text {text, budget_tokens} → 单文本头尾截断 + truncated 标志；
- POST /api/context/estimate {text|messages} → token 估算（估算值，非真 tokenizer）；
- 回包 status 字段说成败（C2 纪律：失败回 {"status": "error", "error": ...}，
  HTTP 保持 200 沿用统一降级惯例，禁谎报 ok）。
"""

from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from src.hippo.context_manager import (
    DEFAULT_BUDGET,
    ESTIMATE_METHOD,
    ContextFitError,
    estimate_message_tokens,
    estimate_tokens,
    fit_messages,
    fit_text,
)

router = APIRouter()


class Message(BaseModel):
    role: str
    content: str


class FitRequest(BaseModel):
    messages: list[Message]
    budget_tokens: int = DEFAULT_BUDGET


class FitTextRequest(BaseModel):
    text: str
    budget_tokens: int = DEFAULT_BUDGET


class EstimateRequest(BaseModel):
    text: str | None = None
    messages: list[Message] | None = None


@router.get("/health")
async def context_health():
    """ContextManager health check."""
    return {"status": "ok", "component": "ContextManager"}


@router.post("/fit")
async def context_fit(req: FitRequest):
    """适配消息列表到 token 预算（保新弃旧+system 优先+占位摘要，裁剪全部如实标注）。"""
    try:
        result = fit_messages([m.model_dump() for m in req.messages], req.budget_tokens)
    except ContextFitError as exc:
        return {"status": "error", "component": "ContextManager", "error": str(exc)}
    return {"status": "ok", "component": "ContextManager", **result}


@router.post("/fit-text")
async def context_fit_text(req: FitTextRequest):
    """单文本适配到 token 预算（头尾保留+截断标记，truncated 如实标注）。"""
    try:
        result = fit_text(req.text, req.budget_tokens)
    except ContextFitError as exc:
        return {"status": "error", "component": "ContextManager", "error": str(exc)}
    return {"status": "ok", "component": "ContextManager", **result}


@router.post("/estimate")
async def context_estimate(req: EstimateRequest):
    """token 估算（text/messages 二选一）；估算口径如实回 estimate_method。"""
    has_text = req.text is not None
    has_messages = req.messages is not None
    if has_text == has_messages:
        return {
            "status": "error",
            "component": "ContextManager",
            "error": "provide exactly one of text / messages",
        }
    if req.text is not None:
        tokens = estimate_tokens(req.text)
    else:
        assert req.messages is not None  # has_text == has_messages 已排除双空
        tokens = sum(estimate_message_tokens(m.model_dump()) for m in req.messages)
    return {
        "status": "ok",
        "component": "ContextManager",
        "tokens": tokens,
        "estimate_method": ESTIMATE_METHOD,
    }
