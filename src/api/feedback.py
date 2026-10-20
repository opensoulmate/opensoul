"""Feedback API — extract and query knowledge from AI interactions."""

import hashlib
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query

from src.models.feedback import (
    FeedbackExtractRequest,
    FeedbackExtractResponse,
    FeedbackListResponse,
)
from src.services import feedback as feedback_service
from src.services.tenant_binding import bind_identity

router = APIRouter()


def _resolve_user_id(user_id: str) -> UUID:
    """F3b-⑥ 家族 _resolve_user_id 同形（档案键家族正典）：UUID 串原样、
    普通用户名/数字 id 按 md5 归一为档案 UUID。"""
    try:
        return UUID(user_id)
    except ValueError:
        h = hashlib.md5(user_id.encode()).hexdigest()
        return UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


@router.get("/health")
async def health():
    return {"status": "ok", "component": "Feedback"}


@router.post("/extract", response_model=FeedbackExtractResponse)
async def extract_knowledge(
    data: FeedbackExtractRequest,
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
):
    """Extract knowledge from a conversation and store it.

    Sends the conversation text to the extraction pipeline (rule-based + optional LLM),
    then stores the results in the knowledge table with feedback metadata.

    身份轴收口（F3b-⑥ 同族语义）：user_id 从认证派生，自报只可确认派生绑定
    （矛盾=403 冒写面收口）；'default'/空/占位零 UUID=未声明→取派生；匿名
    保留自报（既有本地多档案语义契约零变化）；坏 token=401 零副作用。写键=
    档案键家族正典 _resolve_user_id（F3b-⑫ 同源）。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    try:
        # Extract entries
        entries = await feedback_service.extract_knowledge(data)

        if not entries:
            return FeedbackExtractResponse(entries=[], stored_count=0, skipped_count=0)

        # Store entries
        stored, skipped = await feedback_service.store_entries(
            entries=entries,
            user_id=str(uid),
            source=data.source,
            session_id=data.session_id,
        )

        return FeedbackExtractResponse(
            entries=entries,
            stored_count=stored,
            skipped_count=skipped,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/entries", response_model=list[FeedbackListResponse])
async def list_entries(
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
    type: str | None = Query(None, description="Filter by type: knowledge|pattern|decision"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List feedback entries for a user.（身份轴同 /extract，F3b-⑥ 同族语义）"""
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    try:
        entries = await feedback_service.list_entries(
            user_id=str(uid),
            feedback_type=type,
            limit=limit,
            offset=offset,
        )
        return entries
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def feedback_stats(
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
):
    """Get feedback statistics.（身份轴同 /extract，F3b-⑥ 同族语义）"""
    from src.database.postgres import db_pool

    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    try:
        total = (
            await db_pool.fetchval(
                "SELECT COUNT(*) FROM knowledge WHERE user_id = ? AND content_type = 'feedback'",
                str(uid),
            )
            or 0
        )

        by_type = await db_pool.fetch(
            "SELECT json_extract(metadata, '$.feedback_type') as type, COUNT(*) as cnt "
            "FROM knowledge WHERE user_id = ? AND content_type = 'feedback' "
            "GROUP BY json_extract(metadata, '$.feedback_type')",
            str(uid),
        )

        by_confidence = await db_pool.fetch(
            "SELECT json_extract(metadata, '$.confidence') as conf, COUNT(*) as cnt "
            "FROM knowledge WHERE user_id = ? AND content_type = 'feedback' "
            "GROUP BY json_extract(metadata, '$.confidence')",
            str(uid),
        )

        return {
            "status": "ok",
            "total": total,
            "by_type": {r["type"]: r["cnt"] for r in by_type if r["type"]},
            "by_confidence": {r["conf"]: r["cnt"] for r in by_confidence if r["conf"]},
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
