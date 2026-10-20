import hashlib
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Query

from src.database.postgres import db_pool
from src.models.tag import TagCreate, TagResponse, TagUpdate
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
async def tag_health():
    """Tag system health check."""
    return {"status": "ok", "component": "TagSystem"}


@router.post("/", response_model=TagResponse)
async def create(
    data: TagCreate,
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
):
    """F3b-⑮ 身份轴收口（F3b-⑥ 同族语义）：user_id 从认证派生，自报只可确认
    派生绑定（矛盾=403 冒写面收口），经 _resolve_user_id 家族正典归一。"""
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    # id 显式生成（C3-⑪ 2026-10-11 根因修复）：生产 SQLite tags.id 无默认值，
    # 原写法依赖 PG 端 id 默认→NULL 落库（live 实测 id=None，TagResponse UUID 校验炸）。
    # 显式 str(uuid4()) 双库同义，且过 SQLite UUID 绑参铁律。
    row = await db_pool.fetchrow(
        "INSERT INTO tags (id, name, color, user_id) VALUES ($1, $2, $3, $4) RETURNING *",
        str(uuid4()),
        data.name,
        data.color,
        str(uid),
    )
    return dict(row)


@router.get("/", response_model=list[TagResponse])
async def list_all(
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
):
    """F3b-⑮ 身份轴收口（F3b-⑥ 同族语义，冒读面收口）。"""
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    rows = await db_pool.fetch(
        "SELECT t.*, COUNT(kt.knowledge_id) as usage_count FROM tags t "
        "LEFT JOIN knowledge_tags kt ON t.id = kt.tag_id "
        "WHERE t.user_id = $1 GROUP BY t.id ORDER BY t.name",
        str(uid),
    )
    return [dict(r) for r in rows]


@router.patch("/{tag_id}", response_model=TagResponse)
async def update(
    tag_id: UUID,
    data: TagUpdate,
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
):
    """F3b-⑮ 身份轴收口（F3b-⑥ 同族语义，冒写面收口）。"""
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        row = await db_pool.fetchrow(
            "SELECT * FROM tags WHERE id = $1 AND user_id = $2", str(tag_id), str(uid)
        )
        if not row:
            raise HTTPException(status_code=404, detail="Tag not found")
        return dict(row)

    set_parts = []
    values = []
    idx = 1
    for field, value in fields.items():
        set_parts.append(f"{field} = ${idx}")
        values.append(value)
        idx += 1
    values.extend([str(tag_id), str(uid)])
    row = await db_pool.fetchrow(
        f"UPDATE tags SET {', '.join(set_parts)} WHERE id = ${idx} AND user_id = ${idx + 1} RETURNING *",
        *values,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Tag not found")
    return dict(row)


@router.delete("/{tag_id}")
async def delete(
    tag_id: UUID,
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
):
    """F3b-⑮ 身份轴收口（F3b-⑥ 同族语义，冒删面收口）。"""
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    result = await db_pool.execute(
        "DELETE FROM tags WHERE id = $1 AND user_id = $2", str(tag_id), str(uid)
    )
    if "DELETE 1" not in result:
        raise HTTPException(status_code=404, detail="Tag not found")
    return {"deleted": True}
