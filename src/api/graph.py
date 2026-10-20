from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query

from src.api._errors import stats_error
from src.database.postgres import db_pool
from src.models.entity import GraphData, RelationCreate, RelationResponse
from src.services.entity import get_entity_with_relations, list_entities
from src.services.graph import (
    create_relation,
    find_path,
    get_entity_neighbors,
    get_graph,
    list_relations,
)
from src.services.tenant_binding import bind_identity

router = APIRouter()


@router.get("/health")
async def graph_health():
    """OpenGraph health check."""
    return {"status": "ok", "component": "OpenGraph", "module": "knowledge-graph"}


@router.get("/stats")
async def graph_stats():
    """Get knowledge graph statistics."""
    try:
        entities = await db_pool.fetchval("SELECT COUNT(*) FROM entities") or 0
        relations = await db_pool.fetchval("SELECT COUNT(*) FROM relations") or 0
        by_type = await db_pool.fetch(
            # C2根因修复：entities正典列是type，原SQL误写entity_type必炸（同entity.py）
            "SELECT type, COUNT(*) as cnt FROM entities GROUP BY type ORDER BY cnt DESC LIMIT 10"
        )
        return {
            "status": "ok",
            "component": "OpenGraph",
            "total_entities": entities,
            "total_relations": relations,
            "by_type": {r["type"]: r["cnt"] for r in (by_type or [])},
        }
    except Exception as exc:
        # C2统一错误响应：失败说失败、说原因，禁谎报ok（假0冒充"没数据"）
        return stats_error("OpenGraph", exc, total_entities=0, total_relations=0, by_type={})


def _resolve_user_id(user_id: str) -> UUID:
    """Convert user_id to UUID — accept both UUID strings and plain usernames."""
    try:
        return UUID(user_id)
    except ValueError:
        import hashlib

        h = hashlib.md5(user_id.encode()).hexdigest()
        return UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


@router.get("/entities", response_model=list)
async def get_entities(
    user_id: str = Query("default", description="User ID (UUID or username)"),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    authorization: str | None = Header(None),
):
    """Entity list, optionally filtered by type.

    身份轴收口：user_id 从认证派生（bind_identity），自报值只可确认派生绑定
    ——认证请求自报他人档案=403（冒读面收口）；匿名保留自报（既有本地多
    档案语义，F3b-⑥ 同款）。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    return await list_entities(uid, entity_type, offset, limit)


@router.get("/entities/{entity_id}")
async def get_entity_detail(
    entity_id: UUID,
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
):
    """Entity detail with its relations."""
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    entity = await get_entity_with_relations(entity_id, uid)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


@router.get("/entities/{entity_id}/neighbors")
async def get_neighbors(
    entity_id: UUID,
    user_id: str = Query("default", description="User ID (UUID or username)"),
    authorization: str | None = Header(None),
):
    """Entity neighbors: directly connected entities + relation types/direction.

    H1-②b-⑦a 查询面出口（get_entity_neighbors 首个 API 消费点，修前零出口）。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    result = await get_entity_neighbors(uid, entity_id)
    if result["entity"] is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return result


@router.get("/paths")
async def get_paths(
    source_id: UUID,
    target_id: UUID,
    user_id: str = Query("default", description="User ID (UUID or username)"),
    max_depth: int = Query(5, ge=1, le=10, description="Max path length in nodes"),
    authorization: str | None = Header(None),
):
    """All paths between two entities (BFS). H1-②b-⑦a 查询面出口（find_path 首个
    API 消费点，修前零出口）。两端实体都须可见，否则 404（缺/跨用户同形=无存在性
    探测面）；无路可走=空 paths 如实回，不谎报错误。
    路径爆炸防护（H1-②b-⑦a 留档④）：truncated 加性键如实报告搜索是否完成——
    False=搜索已完成（空 paths=确证无路）、True=探索预算耗尽（paths=已发现部分
    如实回，空 paths=预算内未发现禁当无路断言）；返回上限 10 条既有语义不变。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    if (await get_entity_with_relations(source_id, uid)) is None or (
        await get_entity_with_relations(target_id, uid)
    ) is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    paths, truncated = await find_path(uid, source_id, target_id, max_depth)
    return {
        "source_id": source_id,
        "target_id": target_id,
        "max_depth": max_depth,
        "paths": paths,
        "count": len(paths),
        "truncated": truncated,
    }


@router.post("/relations", response_model=RelationResponse)
async def add_relation(
    data: RelationCreate,
    user_id: str = Query("default", description="User ID (UUID or username)"),
    authorization: str | None = Header(None),
):
    """Relation create（身份轴 + 写面归属闸）。

    H1-②b-⑦a-⑤ 诚实边界②留档件收口：修前无身份轴无归属检查——任意人可给
    任意实体 id（含他人实体/不存在实体）挂关系。现：user_id 从认证派生
    （bind_identity 同轴收口，匿名保留自报=既有本地多档案语义），两端实体须
    真属派生档案键，缺/跨用户同形=404 "Entity not found"（无存在性探测面，
    entity detail/paths 同契约）。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    rel = await create_relation(data, uid)
    if rel is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return rel


@router.get("/relations")
async def get_relations(
    user_id: str = Query("default", description="User ID (UUID or username)"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    authorization: str | None = Header(None),
):
    """Relation list for a user."""
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    return await list_relations(uid, offset, limit)


@router.get("/full", response_model=GraphData)
async def get_full_graph(
    user_id: str = Query("default", description="User ID (UUID or username)"),
    depth: int = Query(2, ge=1, le=5),
    authorization: str | None = Header(None),
):
    """Full graph data (nodes + edges) for frontend G6 rendering."""
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    return await get_graph(uid, depth)


@router.get("/", response_model=GraphData)
async def get_graph_data(
    user_id: str = Query("default", description="User ID (UUID or username)"),
    depth: int = 2,
    entity_id: UUID | None = None,
    authorization: str | None = Header(None),
):
    """Graph data for a user（身份轴=bind_identity 派生，F3b-⑥ 同款）。"""
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    return await get_graph(uid, depth, entity_id)
