from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query

from src.api._errors import stats_error
from src.database.postgres import db_pool
from src.models.entity import EntityCreate, EntityResponse, EntityUpdate
from src.services import entity as entity_service
from src.services.tenant_binding import bind_identity

router = APIRouter()


@router.get("/health")
async def entity_health():
    """Entity system health check."""
    return {"status": "ok", "component": "EntitySystem"}


@router.get("/stats")
async def entity_stats():
    """Get entity system statistics."""
    try:
        total = await db_pool.fetchval("SELECT COUNT(*) FROM entities") or 0
        # C2根因修复：entities正典列是type（migrations/001+services/entity.py:7），
        # 原SQL误写entity_type在SQLite必炸，被假ok吞掉。读出时映射回API契约entity_type。
        by_type = await db_pool.fetch(
            "SELECT type, COUNT(*) as cnt FROM entities GROUP BY type ORDER BY cnt DESC LIMIT 10"
        )
        return {
            "status": "ok",
            "component": "EntitySystem",
            "total_entities": total,
            "by_type": {r["type"]: r["cnt"] for r in (by_type or [])},
        }
    except Exception as exc:
        # C2统一错误响应：失败说失败、说原因，禁谎报ok（假0冒充"没数据"）
        return stats_error("EntitySystem", exc, total_entities=0, by_type={})


def _resolve_user_id(user_id: str) -> UUID:
    """Convert user_id to UUID — accept both UUID strings and plain usernames."""
    try:
        return UUID(user_id)
    except ValueError:
        import hashlib

        h = hashlib.md5(user_id.encode()).hexdigest()
        return UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


@router.post("/", response_model=EntityResponse)
async def create(
    data: EntityCreate,
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
):
    """创建实体。

    身份轴收口（F3b-⑥ 已裁决语义同族既有面延伸，F3b-④b 先例）：
    user_id 从认证派生（bind_identity），自报值只可确认派生绑定——
    认证请求自报他人档案=403（冒写面收口）；'default'/空=未声明→取派生；
    匿名保留自报（既有本地多档案语义，F3b-⑥ 同款）；坏 token=401 零副作用。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    return await entity_service.create_entity(data, uid)


@router.get("/", response_model=list[EntityResponse])
async def list_all(
    user_id: str = Query("default"),
    entity_type: str | None = None,
    offset: int = 0,
    limit: int = 50,
    authorization: str | None = Header(None),
):
    """实体列表。身份轴同 create（F3b-⑥ 同族语义）。"""
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    return await entity_service.list_entities(uid, entity_type, offset, limit)


@router.get("/{entity_id}", response_model=EntityResponse)
async def get_one(
    entity_id: UUID,
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
):
    """实体详情。身份轴同 create（F3b-⑥ 同族语义）。"""
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    row = await entity_service.get_entity(entity_id, uid)
    if not row:
        raise HTTPException(status_code=404, detail="Entity not found")
    return row


@router.patch("/{entity_id}", response_model=EntityResponse)
async def update(
    entity_id: UUID,
    data: EntityUpdate,
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
):
    """更新实体。身份轴同 create（冒写面收口，F3b-⑥ 同族语义）。"""
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    row = await entity_service.update_entity(entity_id, data, uid)
    if not row:
        raise HTTPException(status_code=404, detail="Entity not found")
    return row


@router.delete("/{entity_id}")
async def delete(
    entity_id: UUID,
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
):
    """删除实体。身份轴同 create（冒写面收口，F3b-⑥ 同族语义）。"""
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    deleted = await entity_service.delete_entity(entity_id, uid)
    if not deleted:
        raise HTTPException(status_code=404, detail="Entity not found")
    return {"deleted": True}
